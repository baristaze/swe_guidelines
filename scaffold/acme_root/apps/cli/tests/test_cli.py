"""Command mode against the whole API in-process: sign in and out, the orgs
and the switch, a file's upload, JSON output, and the exit codes."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from api_support import OWNER, run, seed_request
from cli_support import BOB, Stack
from typer.testing import CliRunner

from acme.apps.cli import config, main
from acme.client.client import ApiClient
from acme.om.context import Role


def test_login_keeps_a_session_and_whoami_reads_it(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    signed = stack.login(monkeypatch=monkeypatch)
    assert signed.exit_code == 0, signed.output
    # Ann belongs to Ajax and to her personal org; with no --org she enters
    # the personal one, the place every person has.
    assert signed.stdout.startswith("signed in as Ann at Ann (owner)\nsession kept in ")
    session = config.load_session()
    assert session is not None and session.org_slug.startswith("ann-")
    assert session.api_url == "http://test"
    assert (config.home() / "session.json").stat().st_mode & 0o777 == 0o600

    who = stack.acme("whoami", token=None)  # the session file, no ACME_TOKEN
    assert who.exit_code == 0 and who.output == "Ann <ann@example.test> at Ann (owner)\n"

    out = stack.acme("logout", token=None)
    assert out.exit_code == 0 and out.output == "signed out\n"
    assert config.load_session() is None
    assert stack.acme("logout", token=None).output == "no session to forget\n"


def test_login_over_a_kept_session_ends_that_session_and_keeps_the_new_one(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack.login(monkeypatch=monkeypatch)
    first = config.load_session()
    assert first is not None

    again = stack.login("--org", "ajax", monkeypatch=monkeypatch)
    assert again.exit_code == 0, again.output
    assert again.stdout.endswith("the session kept before is ended\n")
    second = config.load_session()
    assert second is not None and second.token != first.token and second.org_slug == "ajax"

    # The old token answers 401; the new one is the kept session and works.
    assert stack.acme("whoami", token=first.token).exit_code == main.EXIT_NOT_SIGNED_IN
    assert stack.acme("whoami", token=None).exit_code == 0


def test_login_over_a_session_already_gone_says_nothing_of_it(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack.login(monkeypatch=monkeypatch)
    first = config.load_session()
    assert first is not None

    async def revoke_elsewhere() -> None:
        async with stack.client(first.token) as client:
            await client.logout()

    asyncio.run(revoke_elsewhere())
    again = stack.login(monkeypatch=monkeypatch)
    assert again.exit_code == 0, again.output
    assert "session kept before" not in again.output
    assert stack.acme("whoami", token=None).exit_code == 0


def test_login_keeps_the_new_session_when_the_old_ones_api_cannot_be_reached(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kept session goes back to the API that issued it and nowhere else;
    when that API is away, the new session is kept all the same and stderr
    says the old one lapses on its own."""
    config.save_session(
        config.Session(
            api_url="https://gone.example.test",
            token="ses_kept",
            email="ann@example.test",
            display_name="Ann",
            org_slug="ajax",
            org_name="Ajax",
        )
    )
    sent_to: list[str] = []

    def build(url: str, token: str | None) -> ApiClient:
        sent_to.append(url)
        if url == "https://gone.example.test":

            def refuse(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("refused")

            return ApiClient(
                url,
                app="cli",
                app_version="cli@test",
                token=token,
                transport=httpx.MockTransport(refuse),
                backoff_seconds=0.0,
            )
        return stack.client(token)

    monkeypatch.setattr(main, "build_client", build)
    result = stack.login(monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert result.stderr.endswith(
        "the session kept before was not ended; cannot reach https://gone.example.test: refused\n"
    )
    assert sent_to == ["http://test", "https://gone.example.test"]
    session = config.load_session()
    assert session is not None and session.token != "ses_kept"


def test_login_naming_an_org_the_person_is_not_in_is_a_usage_error(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = stack.login("--org", "nope", monkeypatch=monkeypatch)
    assert out.exit_code == 2, out.output
    assert "choose an org with --org: ajax" in out.output
    assert config.load_session() is None


def test_logout_forgets_a_session_the_api_already_revoked(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session revoked elsewhere, or expired, answers 401: there is nothing
    left to revoke, so the file goes and the command succeeds."""
    stack.login(monkeypatch=monkeypatch)
    session = config.load_session()
    assert session is not None

    async def revoke_elsewhere() -> None:
        async with stack.client(session.token) as client:
            await client.logout()

    asyncio.run(revoke_elsewhere())
    out = stack.acme("logout", token=None)
    assert out.exit_code == 0, out.output
    assert out.output == "signed out; the session was already gone (not_authenticated)\n"
    assert config.load_session() is None


def test_logout_revokes_the_kept_session_and_leaves_the_environments_token_alone(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack.login(monkeypatch=monkeypatch)
    session = config.load_session()
    assert session is not None
    bob = stack.session_token(BOB["email"])

    out = stack.acme("logout", token=bob)  # ACME_TOKEN is Bob's; the file is Ann's
    assert out.exit_code == 0 and out.output == "signed out\n"
    assert config.load_session() is None
    assert stack.acme("whoami", token=bob).exit_code == 0
    assert stack.acme("whoami", token=session.token).exit_code == 3

    again = stack.acme("logout", token=bob)
    assert again.exit_code == 0
    assert again.output == "no session to forget; ACME_TOKEN is the environment's, unset it\n"
    assert stack.acme("whoami", token=bob).exit_code == 0


def test_logout_never_sends_the_session_to_another_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session's token goes back to the API that issued it: an `--api`
    naming another one is refused, nothing is sent, and the file stays."""
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ACME_TOKEN", raising=False)
    config.save_session(
        config.Session(
            api_url="https://api.example.test",
            token="ses_kept",
            email="ann@example.test",
            display_name="Ann",
            org_slug="ajax",
            org_name="Ajax",
        )
    )
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        main,
        "build_client",
        lambda url, token: ApiClient(
            url,
            app="cli",
            app_version="cli@test",
            token=token,
            transport=httpx.MockTransport(record),
        ),
    )
    result = CliRunner().invoke(main.app, ["logout", "--api", "http://localhost:8000"])
    assert result.exit_code == main.EXIT_USAGE, result.output
    assert sent == []
    assert config.load_session() is not None


def test_logout_forgets_the_session_when_the_api_cannot_be_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The revoke did not happen, which the exit code says; the file goes
    anyway, so the next command is not run as a session the caller gave up."""
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ACME_TOKEN", raising=False)
    config.save_session(
        config.Session(
            api_url="http://test",
            token="ses_kept",
            email="ann@example.test",
            display_name="Ann",
            org_slug="ajax",
            org_name="Ajax",
        )
    )

    def raise_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        main,
        "build_client",
        lambda _url, token: ApiClient(
            "http://test",
            app="cli",
            app_version="cli@test",
            token=token,
            transport=httpx.MockTransport(raise_failure),
            backoff_seconds=0.0,  # the retry still runs; this case is about the exit code
        ),
    )
    result = CliRunner().invoke(main.app, ["logout"])
    assert result.exit_code == 4, result.output
    assert result.output.startswith("session forgotten, not revoked; cannot reach the API:")
    assert config.load_session() is None


def test_login_waits_for_the_person_to_confirm_the_code(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no browser the address is printed and nothing is opened; the CLI
    asks again after the interval, and the person confirming meanwhile signs
    it in."""
    opened: list[str] = []
    monkeypatch.setattr(main, "open_browser", opened.append)
    asks: list[float] = []

    async def confirm_on_the_second_wait(seconds: float) -> None:
        asks.append(seconds)
        if len(asks) == 2:
            user_code = next(iter(stack.twin._devices)).rpartition(":")[2]
            stack.twin.confirm_device(user_code, BOB["email"])

    monkeypatch.setattr(main, "pause", confirm_on_the_second_wait)
    signed = stack.acme("login", "--no-browser", "--org", "ajax", token=None)
    assert signed.exit_code == 0, signed.output
    assert "to sign in, open https://identity.twin.invalid/device?user_code=" in signed.output
    assert opened == [] and len(asks) == 2
    session = config.load_session()
    assert session is not None and session.email == BOB["email"] and session.org_slug == "ajax"


def test_login_opens_the_browser_on_the_confirmation_page(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = stack.confirm_as(OWNER["email"], monkeypatch)
    signed = stack.acme("login", token=None)
    assert signed.exit_code == 0, signed.output
    assert len(opened) == 1 and opened[0].startswith("https://identity.twin.invalid/device?")


def test_a_declined_sign_in_is_refused_with_exit_1(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    def decline(url: str) -> None:
        stack.twin.confirm_device(url.rpartition("user_code=")[2], OWNER["email"], deny=True)

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr(main, "open_browser", decline)
    monkeypatch.setattr(main, "pause", no_wait)
    result = stack.acme("login", token=None)
    assert result.exit_code == 1, result.output
    assert "refused: sign_in_refused" in result.output
    assert config.load_session() is None


def test_a_code_nobody_confirms_expires_with_exit_1(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter([0.0, 1000.0, 2000.0])
    monkeypatch.setattr(main, "now", lambda: next(clock))

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr(main, "open_browser", lambda url: None)
    monkeypatch.setattr(main, "pause", no_wait)
    result = stack.acme("login", token=None)
    assert result.exit_code == 1, result.output
    assert "the code expired before it was confirmed" in result.output


def test_the_local_sign_in_takes_an_address_alone(stack: Stack) -> None:
    signed = stack.acme("login", "--dev-email", BOB["email"], "--org", "ajax", token=None)
    assert signed.exit_code == 0, signed.output
    assert signed.output.startswith("signed in as Bob at Ajax (member)\nsession kept in ")


def test_not_signed_in_is_exit_3(stack: Stack) -> None:
    result = stack.acme("whoami", token=None)
    assert result.exit_code == 3 and "run `acme login`" in result.output
    bad = stack.acme("whoami", token="ses_nope")
    assert bad.exit_code == 3 and "credential was refused" in bad.output


def test_whoami_names_the_person_the_org_and_the_role(stack: Stack) -> None:
    assert stack.acme("whoami").output == "Ann <ann@example.test> at Ajax (owner)\n"
    bob = stack.session_token(BOB["email"])
    assert stack.acme("whoami", token=bob).output == "Bob <bob@example.test> at Ajax (member)\n"


def test_the_client_is_built_with_the_timeout_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACME_HTTP_TIMEOUT_SECONDS", raising=False)
    assert config.timeout_seconds() == config.DEFAULT_TIMEOUT_SECONDS
    assert main.build_client("http://127.0.0.1:1", None).timeout == config.DEFAULT_TIMEOUT_SECONDS
    monkeypatch.setenv("ACME_HTTP_TIMEOUT_SECONDS", "2.5")
    assert config.timeout_seconds() == 2.5
    assert main.build_client("http://127.0.0.1:1", None).timeout == 2.5
    for bad in ("soon", "0", "-1"):
        monkeypatch.setenv("ACME_HTTP_TIMEOUT_SECONDS", bad)
        with pytest.raises(ValueError):
            config.timeout_seconds()


def test_the_client_is_built_with_the_retry_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count and the delay travel from the settings into the client the
    way the timeout does, so the CLI never wraps it in a retry of its own."""
    for name in ("ACME_HTTP_RETRIES", "ACME_HTTP_RETRY_BACKOFF_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    built = main.build_client("http://127.0.0.1:1", None)
    assert built.retries == config.DEFAULT_RETRIES
    assert built.backoff_seconds == config.DEFAULT_BACKOFF_SECONDS
    monkeypatch.setenv("ACME_HTTP_RETRIES", "0")
    monkeypatch.setenv("ACME_HTTP_RETRY_BACKOFF_SECONDS", "1.5")
    built = main.build_client("http://127.0.0.1:1", None)
    assert built.retries == 0 and built.backoff_seconds == 1.5
    for bad in ("two", "-1", "1.5"):
        monkeypatch.setenv("ACME_HTTP_RETRIES", bad)
        with pytest.raises(config.BadSetting):
            config.retries()
    monkeypatch.delenv("ACME_HTTP_RETRIES")
    for bad in ("soon", "0", "-1"):
        monkeypatch.setenv("ACME_HTTP_RETRY_BACKOFF_SECONDS", bad)
        with pytest.raises(config.BadSetting):
            config.backoff_seconds()


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("reset")],
    ids=["refused", "timed out", "reset"],
)
def test_an_api_that_cannot_be_reached_is_exit_4(
    monkeypatch: pytest.MonkeyPatch, failure: httpx.TransportError
) -> None:
    """Every failure of the wire, not only a refused connection, is exit 4."""

    def raise_failure(request: httpx.Request) -> httpx.Response:
        raise failure

    monkeypatch.setattr(
        main,
        "build_client",
        lambda _url, token: ApiClient(
            "http://test",
            app="cli",
            app_version="cli@test",
            token=token,
            transport=httpx.MockTransport(raise_failure),
            backoff_seconds=0.0,  # the retry still runs; this case is about the exit code
        ),
    )
    result = CliRunner().invoke(
        main.app, ["whoami"], env={"ACME_API_URL": "http://test", "ACME_TOKEN": "ses_1"}
    )
    assert result.exit_code == 4, result.output
    assert result.output.startswith("cannot reach the API:")


@pytest.mark.parametrize(
    "bad",
    ["soon", "0", "-1", "1e", " "],
    ids=["word", "zero", "negative", "half a number", "blank"],
)
def test_a_timeout_the_environment_got_wrong_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """A setting the environment got wrong is the caller's input, so it is
    exit 2 and one line, inside the exit-code contract, for every command:
    the signed-in ones, `login`, and `logout`."""
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home"))
    environment = {
        "ACME_API_URL": "http://127.0.0.1:1",
        "ACME_TOKEN": "ses_1",
        "ACME_HTTP_TIMEOUT_SECONDS": bad,
    }
    runner = CliRunner()
    for command in (["whoami"], ["listen"], ["login", "--dev-email", "a@b.test"]):
        result = runner.invoke(main.app, command, env=environment, catch_exceptions=False)
        assert result.exit_code == 2, f"{command}: {result.output}"
        assert result.output.startswith("ACME_HTTP_TIMEOUT_SECONDS"), result.output


def test_a_bad_timeout_does_not_forget_the_session_logout_could_not_revoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`logout` forgets the session whatever the API answers, but a setting
    the environment got wrong means the API was never asked: the session
    stays, so the caller fixes the setting and signs out for real."""
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home"))
    session = config.Session(
        api_url="http://127.0.0.1:1",
        token="ses_1",
        email="ann@example.test",
        display_name="Ann",
        org_slug="ajax",
        org_name="Ajax",
    )
    config.save_session(session)
    result = CliRunner().invoke(
        main.app, ["logout"], env={"ACME_HTTP_TIMEOUT_SECONDS": "soon"}, catch_exceptions=False
    )
    assert result.exit_code == 2, result.output
    assert result.output.startswith("ACME_HTTP_TIMEOUT_SECONDS")
    assert config.load_session() == session


def test_a_token_the_environment_got_wrong_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A credential travels in a header, which carries ascii and nothing else.
    A copy that brought a typographic quote with it is the caller's input: one
    line and exit 2, and the line names the variable, never the secret in it."""
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home"))
    smuggled = "ses_" + chr(0x2019) + "secret"  # the quote a document turned typographic
    result = CliRunner().invoke(
        main.app,
        ["whoami"],
        env={"ACME_API_URL": "http://127.0.0.1:1", "ACME_TOKEN": smuggled},
        catch_exceptions=False,
    )
    assert result.exit_code == 2, result.output
    assert result.output == "ACME_TOKEN has characters a request header cannot carry\n"
    assert "secret" not in result.output


def test_a_session_that_cannot_be_kept_is_a_usage_error(stack: Stack, tmp_path: Path) -> None:
    """The API signed the caller in, but ACME_HOME names a place the CLI
    cannot write: one line naming the place, exit 2, no traceback."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    result = stack.acme(
        "login",
        "--dev-email",
        OWNER["email"],
        token=None,
        env={"ACME_HOME": str(blocked / "acme")},
    )
    assert result.exit_code == 2, result.output
    assert result.output.startswith("the session cannot be kept in "), result.output


def test_a_session_that_cannot_be_forgotten_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`logout` forgets the file whatever the API answers, so a file it cannot
    remove is the one thing left to tell: one line and exit 2."""
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home"))
    config.save_session(
        config.Session(
            api_url="http://127.0.0.1:1",
            token="ses_1",
            email="ann@example.test",
            display_name="Ann",
            org_slug="ajax",
            org_name="Ajax",
        )
    )

    def refuse(self: Path, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "unlink", refuse)
    result = CliRunner().invoke(main.app, ["logout"], catch_exceptions=False)
    assert result.exit_code == 2, result.output
    assert result.output.startswith("the session cannot be forgotten: "), result.output


def second_org(stack: Stack) -> None:
    """The owner also belongs to Beta, as a member."""
    tenancy = stack.container.managers.tenancy
    run(tenancy.bootstrap(seed_request(), "Beta", "beta", "bea@example.test", "Bea"))
    run(tenancy.add_member(seed_request(), "beta", OWNER["email"], OWNER["name"], Role.MEMBER))


def test_orgs_lists_where_the_person_belongs_and_marks_the_current_one(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    second_org(stack)
    stack.login("--org", "ajax", monkeypatch=monkeypatch)
    listed = stack.acme("orgs", token=None)
    assert listed.exit_code == 0, listed.output
    ajax, mine, beta = listed.output.splitlines()
    assert ajax.startswith("* ajax ") and ajax.endswith("  Ajax (owner)")
    assert mine.startswith("  ann-") and mine.endswith("  Ann (owner, personal)")
    assert beta.startswith("  beta ") and beta.endswith("  Beta (member)")
    as_json = stack.acme("orgs", "--json", token=None)
    teams = [m["org"]["slug"] for m in json.loads(as_json.output) if m["org"]["kind"] == "team"]
    assert sorted(teams) == ["ajax", "beta"]


def test_switch_moves_the_kept_session_and_ends_the_old_one(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    second_org(stack)
    stack.login("--org", "ajax", monkeypatch=monkeypatch)
    before = config.load_session()
    assert before is not None
    moved = stack.acme("switch", "beta", token=None)
    assert moved.exit_code == 0, moved.output
    assert moved.output == "switched to Beta as Ann (member); the old session is ended\n"
    after = config.load_session()
    assert after is not None and after.org_slug == "beta" and after.token != before.token
    who = stack.acme("whoami", token=None)
    assert who.output == "Ann <ann@example.test> at Beta (member)\n"
    # The session the file held before is over.
    old = stack.acme("whoami", token=before.token)
    assert old.exit_code == 3, old.output


def test_switch_to_an_org_the_person_is_not_in_is_a_usage_error(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack.login(monkeypatch=monkeypatch)
    before = config.load_session()
    out = stack.acme("switch", "nope", token=None)
    assert out.exit_code == 2, out.output
    assert "no org 'nope' to switch to; yours are: ajax" in out.output
    assert config.load_session() == before


def test_switch_needs_a_kept_session(stack: Stack) -> None:
    out = stack.acme("switch", "ajax")  # ACME_TOKEN is the environment's, not the CLI's
    assert out.exit_code == 3, out.output
    assert "no kept session to switch" in out.output


def test_a_file_is_uploaded_and_kept_by_the_org(stack: Stack, tmp_path: Path) -> None:
    """The whole upload over the in-process API, whose local store cannot sign
    a form: the client moves the bytes through the API instead, held to the
    same bounds, and the stored file is the org's."""
    spec = tmp_path / "spec.pdf"
    spec.write_bytes(b"%PDF-1.7 the spec")
    uploaded = stack.acme("upload", str(spec))
    assert uploaded.exit_code == 0, uploaded.output
    assert uploaded.output.startswith("uploaded spec.pdf (17 B) as ")
    file_id = uploaded.output.split()[-1]

    as_json = stack.acme("upload", str(spec), "--json")
    assert as_json.exit_code == 0, as_json.output
    again = json.loads(as_json.output)
    assert again["status"] == "stored" and again["content_type"] == "application/pdf"

    async def read_back() -> tuple[list[str], bytes]:
        async with stack.client(stack.session_token(BOB["email"])) as as_bob:
            page = await as_bob.files()
            return [str(f.id) for f in page.items], await as_bob.download(page.items[0].id)

    listed, data = asyncio.run(read_back())
    assert listed == [file_id, again["id"]] and data == b"%PDF-1.7 the spec"


def test_a_file_of_a_type_the_org_does_not_keep_is_refused(stack: Stack, tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<html></html>")
    refused = stack.acme("upload", str(page))
    assert refused.exit_code == 1, refused.output
    assert "cannot be of type text/html" in refused.output
    unknown = tmp_path / "blob.nokind"
    unknown.write_bytes(b"x")
    guessless = stack.acme("upload", str(unknown))
    assert guessless.exit_code == 2 and "give --type" in guessless.output
