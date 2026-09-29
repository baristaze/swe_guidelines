"""`acme-ops token`: a token goes into the env file and nowhere else. The
operator's is minted only in a person's own terminal, after a sign-in through
the identity provider and the second factor; the provisioner's is copied from
the secret the grant job wrote, in a cloud environment. On the local stack
each is the local operator's the seed made, minted by the grant command. The operator lists
their own live tokens and ends one by its id. A requeue mints a `write` token
for its one call, keeps it nowhere, and signs it out after."""

import argparse
import io
import json
import subprocess
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from acme.client.client import ApiClient
from acme.ops import main as ops_main
from acme.ops.environments import LOCAL_OPERATORS, parse_env_file
from acme.ops.main import device_sign_in, token_command, work_requeue_command


def env_file(home: Path, name: str) -> Path:
    file = home / ".config" / "acme" / "ops" / f"{name}.env"
    file.parent.mkdir(parents=True)
    file.write_text("ACME_API_URL=http://test\nACME_OPERATOR_TOKEN=\n")
    file.chmod(0o600)
    return file


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


LOGIN = {"expires_at": "2026-09-20T12:00:00Z", "memberships": []}
MINTED_ID = UUID(int=7)
DEVICE = {
    "device_code": "dev_secret",
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://auth.example.test/device",
    "verification_uri_complete": "https://auth.example.test/device?user_code=ABCD-EFGH",
    "expires_in": 300,
    "interval": 5,
}


class SignInRoutes:
    """The device sign-in, answered pending `pending` times (the first of them
    a slow-down), then the second factor and the mint."""

    def __init__(self, pending: int = 2, mint_status: int = 200) -> None:
        self.pending = pending
        self.mint_status = mint_status
        self.paths: list[str] = []
        self.signed_out: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        body = json.loads(request.content) if request.content else {}
        if path == "/v1/auth/device":
            return httpx.Response(200, json=DEVICE)
        if path == "/v1/auth/device/token":
            assert body == {"device_code": "dev_secret"}
            if self.pending > 0:
                code = "sign_in_slow_down" if self.pending == 2 else "sign_in_pending"
                self.pending -= 1
                return httpx.Response(400, json={"error": {"code": code, "message": "wait"}})
            return httpx.Response(200, json={"token": "lgn_first", **LOGIN})
        if path == "/v1/auth/dev-sign-in":
            assert body["email"] == "ops@example.test"
            return httpx.Response(200, json={"token": "lgn_first", **LOGIN})
        if path == "/v1/auth/second-factor":
            assert request.headers["authorization"] == "Bearer lgn_first"
            assert body == {"totp_code": "123456"}
            return httpx.Response(200, json={"token": "lgn_verified", **LOGIN})
        if path == "/v1/admin/me/tokens":
            assert request.headers["authorization"] == "Bearer lgn_verified"
            # The sign-in is the mint's key: it ends in the write that lands
            # the token, so no Idempotency-Key is sent.
            assert "idempotency-key" not in request.headers
            if self.mint_status != 200:
                refusal = {"code": "not_authorized", "message": "operator lacks write"}
                return httpx.Response(self.mint_status, json={"error": refusal})
            minted = {"id": str(MINTED_ID), "token": "opt_minted", "expires_at": "x"}
            return httpx.Response(200, json=minted)
        if path == "/v1/auth/logout":
            self.signed_out.append(request.headers["authorization"].removeprefix("Bearer "))
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "no"}})


async def test_a_device_sign_in_waits_the_interval_and_longer_after_a_slow_down(
    capsys: pytest.CaptureFixture[str],
) -> None:
    routes = SignInRoutes(pending=2)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async with ApiClient(
        "http://test", app="admin", app_version="ops@test", transport=httpx.MockTransport(routes)
    ) as client:
        token = await device_sign_in(client, sleep=sleep, clock=lambda: 0.0)
    assert token == "lgn_first"
    assert waits == [5.0, 10.0, 10.0]
    err = capsys.readouterr().err
    assert "ABCD-EFGH" in err and "dev_secret" not in err


async def test_a_device_sign_in_stops_when_its_code_expires() -> None:
    routes = SignInRoutes(pending=1000)
    ticks = iter(range(0, 10_000, 100))

    async def sleep(seconds: float) -> None:
        return None

    async with ApiClient(
        "http://test", app="admin", app_version="ops@test", transport=httpx.MockTransport(routes)
    ) as client:
        with pytest.raises(ValueError, match="expired"):
            await device_sign_in(client, sleep=sleep, clock=lambda: float(next(ticks)))


async def test_the_operator_token_is_minted_in_a_terminal_and_written_unprinted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    file = env_file(tmp_path, "staging")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr(ops_main.getpass, "getpass", lambda prompt: "123456")

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr(ops_main.asyncio, "sleep", no_wait)
    routes = SignInRoutes(pending=1)
    code = await token_command(
        argparse.Namespace(env="staging", identity="operator", profile=None, dev_email=None),
        transport=httpx.MockTransport(routes),
    )
    assert code == 0
    assert routes.paths[0] == "/v1/auth/device"
    assert "ACME_OPERATOR_TOKEN=opt_minted\n" in file.read_text()
    captured = capsys.readouterr()
    assert "opt_minted" not in captured.out + captured.err
    assert "lgn_" not in captured.out + captured.err
    assert f"--revoke {MINTED_ID}" in captured.out, "the id is no secret, and ends it sooner"
    assert routes.signed_out == [], "the mint ended the sign-in itself"


async def test_the_local_stack_signs_the_operator_in_by_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = env_file(tmp_path, "local")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr(ops_main.getpass, "getpass", lambda prompt: "123456")
    routes = SignInRoutes()
    code = await token_command(
        argparse.Namespace(
            env="local", identity="operator", profile=None, dev_email="ops@example.test"
        ),
        transport=httpx.MockTransport(routes),
    )
    assert code == 0
    assert routes.paths[0] == "/v1/auth/dev-sign-in"
    assert "ACME_OPERATOR_TOKEN=opt_minted\n" in file.read_text()


async def test_the_local_sign_in_is_refused_for_a_deployed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path, "staging")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", Terminal())
    code = await token_command(
        argparse.Namespace(
            env="staging", identity="operator", profile=None, dev_email="ops@example.test"
        )
    )
    assert code == 2


async def test_the_operator_token_is_never_minted_without_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path, "staging")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO())
    code = await token_command(argparse.Namespace(env="staging", identity="operator", profile=None))
    assert code == 2


class GrantCommand:
    """The grant command `acme-ops` runs on the local stack: records what it
    was asked and prints a token, as it does on a local database, beside the
    log lines it writes to stderr."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        holder = argv[argv.index("--mint-token") + 1]
        out = f"opr_{holder}_minted\n" if self.returncode == 0 else ""
        err = "WARNING the local sign-in by address alone is on\n"
        if self.returncode:
            err += "acme.om.exceptions.NotAnOperator: no operator holds that email\n"
        return subprocess.CompletedProcess(argv, self.returncode, out, err)


async def test_the_local_operators_tokens_are_minted_into_a_new_owner_only_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the local stack each token is the local operator's that `make seed`
    made, minted by the grant command; the file it lands in is made owner-only,
    in a folder only its owner opens, naming the local stack's addresses, so a
    skill that sources it reaches the local stack. Nothing is printed of it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    grant = GrantCommand()
    monkeypatch.setattr(ops_main.subprocess, "run", grant)
    for identity in ("operator", "provisioner"):
        code = await token_command(
            argparse.Namespace(env="local", identity=identity, profile=None, dev_email=None)
        )
        assert code == 0
    assert [call[call.index("--email") + 1] for call in grant.calls] == [
        LOCAL_OPERATORS["operator"],
        LOCAL_OPERATORS["provisioner"],
    ]
    assert [call[call.index("--mint-token") + 1] for call in grant.calls] == [
        "operator",
        "provisioner",
    ]
    file = tmp_path / ".config" / "acme" / "ops" / "local.env"
    assert file.stat().st_mode & 0o777 == 0o600
    assert file.parent.stat().st_mode & 0o777 == 0o700
    written = parse_env_file(file.read_text())
    assert written["ACME_OPERATOR_TOKEN"] == "opr_operator_minted"
    assert written["ACME_PROVISIONER_TOKEN"] == "opr_provisioner_minted"
    assert written["ACME_API_URL"] == "http://127.0.0.1:8000"
    for key in ("ACME_ERROR_TRACKER_URL", "ACME_PROMETHEUS_URL", "ACME_JAEGER_URL"):
        assert written[key].startswith("http://"), key
    captured = capsys.readouterr()
    assert "opr_" not in captured.out + captured.err
    # A fresh token replaces its line and leaves every other one as it was.
    file.write_text(file.read_text() + "# mine\n")
    code = await token_command(
        argparse.Namespace(env="local", identity="operator", profile=None, dev_email=None)
    )
    assert code == 0
    assert file.read_text().endswith("# mine\n")
    assert file.read_text().count("ACME_OPERATOR_TOKEN=") == 1


async def test_a_local_operator_the_seed_did_not_make_names_the_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ops_main.subprocess, "run", GrantCommand(returncode=1))
    with pytest.raises(ValueError, match="make seed") as refused:
        await token_command(
            argparse.Namespace(env="local", identity="provisioner", profile=None, dev_email=None)
        )
    assert "no operator holds that email" in str(refused.value)
    assert not (tmp_path / ".config" / "acme" / "ops" / "local.env").exists()


async def test_a_person_signs_in_for_the_operators_token_and_never_the_provisioners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path, "local")
    monkeypatch.setenv("HOME", str(tmp_path))
    code = await token_command(
        argparse.Namespace(
            env="local", identity="provisioner", profile=None, dev_email="ops@example.test"
        )
    )
    assert code == 2


async def test_the_provisioner_token_is_never_read_under_an_investigate_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path, "staging")
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="reads no secret"):
        await token_command(
            argparse.Namespace(
                env="staging", identity="provisioner", profile="acme-staging-investigate"
            )
        )


class RequeueRoutes(SignInRoutes):
    """The sign-in, a `write` mint, and the requeue the plane answers with
    `status` and `body`."""

    def __init__(self, status: int, body: dict[str, object]) -> None:
        super().__init__(pending=0)
        self.status = status
        self.body = body
        self.minted: list[object] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/admin/me/tokens":
            self.minted.append(json.loads(request.content)["permission"])
        if request.url.path.endswith("/requeue"):
            self.paths.append(request.url.path)
            assert request.headers["authorization"] == "Bearer opt_minted"
            return httpx.Response(self.status, json=self.body)
        return super().__call__(request)


ORG, ITEM = UUID(int=1), UUID(int=2)


def requeue_args(env: str = "local") -> argparse.Namespace:
    return argparse.Namespace(env=env, org=ORG, item=ITEM, dev_email="ops@example.test")


async def test_a_requeue_mints_a_write_token_for_itself_and_keeps_it_nowhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    file = env_file(tmp_path, "local")
    before = file.read_text()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr(ops_main.getpass, "getpass", lambda prompt: "123456")
    routes = RequeueRoutes(
        200,
        {
            "id": str(ITEM),
            "kind": "DELETE_ACCOUNT",
            "target_id": str(ORG),
            "status": "queued",
            "available_at": "2026-09-26T00:00:00Z",
            "attempts": 0,
            "max_attempts": 3,
            "last_error": None,
            "updated_at": "2026-09-26T00:00:00Z",
        },
    )
    code = await work_requeue_command(requeue_args(), transport=httpx.MockTransport(routes))
    assert code == 0
    assert routes.minted == ["write"]
    assert f"/v1/admin/orgs/{ORG}/work/{ITEM}/requeue" in routes.paths
    assert routes.paths[-1] == "/v1/auth/logout" and routes.signed_out == ["opt_minted"]
    assert file.read_text() == before, "the write token goes into no file"
    captured = capsys.readouterr()
    assert f"requeued {ITEM} (DELETE_ACCOUNT)" in captured.out
    assert "opt_minted" not in captured.out + captured.err


async def test_a_requeue_the_plane_refuses_says_why_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path, "local")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr(ops_main.getpass, "getpass", lambda prompt: "123456")
    refusal: dict[str, object] = {
        "error": {"code": "work_not_failed", "message": "work item is queued, not failed"}
    }
    routes = RequeueRoutes(409, refusal)
    code = await work_requeue_command(requeue_args(), transport=httpx.MockTransport(routes))
    assert code == 1
    assert "work_not_failed" in capsys.readouterr().err
    assert routes.signed_out == ["opt_minted"], "refused or not, the write token ends"


async def test_a_mint_the_plane_refuses_signs_the_sign_in_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused mint ends nothing, so the sign-in with its code would live
    its ten minutes: the command signs it out before it says why."""
    env_file(tmp_path, "local")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr(ops_main.getpass, "getpass", lambda prompt: "123456")
    routes = RequeueRoutes(200, {})
    routes.mint_status = 403
    code = await work_requeue_command(requeue_args(), transport=httpx.MockTransport(routes))
    assert code == 1
    assert "does not carry write" in capsys.readouterr().err
    assert routes.signed_out == ["lgn_verified"]
    assert not any(path.endswith("/requeue") for path in routes.paths)


def token_row(token_id: UUID, permission: str = "read") -> dict[str, object]:
    return {
        "id": str(token_id),
        "permission": permission,
        "created_at": "2026-09-26T10:00:00Z",
        "expires_at": "2026-09-26T11:00:00Z",
        "revoked_at": None,
    }


class OwnTokenRoutes:
    """The plane's list of the caller's tokens in two pages, and the revoke of
    one: `live` is found, any other id is not."""

    def __init__(self, live: UUID, status: int = 200) -> None:
        self.live = live
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        refusal = {"error": {"code": "credential_expired", "message": "operator token revoked"}}
        if self.status == 401:
            return httpx.Response(401, json=refusal)
        path = request.url.path
        if path == "/v1/admin/me/tokens":
            if request.url.params.get("cursor") == "c-2":
                return httpx.Response(
                    200, json={"items": [token_row(UUID(int=9))], "next_cursor": None}
                )
            page = {"items": [token_row(self.live, "write")], "next_cursor": "c-2"}
            return httpx.Response(200, json=page)
        if path == f"/v1/admin/me/tokens/{self.live}" and request.method == "DELETE":
            return httpx.Response(
                200, json={**token_row(self.live), "revoked_at": "2026-09-26T10:30:00Z"}
            )
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "no"}})


def own_file(home: Path) -> Path:
    file = home / ".config" / "acme" / "ops" / "staging.env"
    file.parent.mkdir(parents=True)
    file.write_text("ACME_API_URL=http://test\nACME_OPERATOR_TOKEN=opr_file\n")
    file.chmod(0o600)
    return file


def own_args(**given: object) -> argparse.Namespace:
    return argparse.Namespace(
        **({"env": "staging", "identity": None, "list": False, "revoke": None} | given)
    )


async def test_the_operator_lists_their_own_live_tokens_under_the_files_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    own_file(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    routes = OwnTokenRoutes(UUID(int=8))
    code = await token_command(own_args(list=True), transport=httpx.MockTransport(routes))
    assert code == 0
    out = capsys.readouterr().out
    assert str(UUID(int=8)) in out and str(UUID(int=9)) in out, "every page"
    assert all(r.headers["authorization"] == "Bearer opr_file" for r in routes.requests)
    assert "opr_file" not in out


async def test_the_operator_revokes_one_token_by_its_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    own_file(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    live = UUID(int=8)
    routes = OwnTokenRoutes(live)
    code = await token_command(own_args(revoke=live), transport=httpx.MockTransport(routes))
    assert code == 0
    assert (routes.requests[0].method, routes.requests[0].url.path) == (
        "DELETE",
        f"/v1/admin/me/tokens/{live}",
    )
    assert f"revoked {live}" in capsys.readouterr().out
    # Another operator's token, or no token, is none of the caller's.
    code = await token_command(own_args(revoke=UUID(int=10)), transport=httpx.MockTransport(routes))
    assert code == 1
    assert "no live token of yours" in capsys.readouterr().err
    # The file's own token refused says how to write a fresh one.
    refused = OwnTokenRoutes(live, status=401)
    with pytest.raises(ValueError, match="write a fresh one"):
        await token_command(own_args(revoke=live), transport=httpx.MockTransport(refused))


def test_the_token_command_takes_one_action() -> None:
    parse = ops_main.build_parser().parse_args
    assert parse(["token", "--env", "local", "--list"]).list is True
    assert parse(["token", "--env", "local", "--revoke", str(UUID(int=8))]).revoke == UUID(int=8)
    assert parse(["token", "--env", "local", "--identity", "operator"]).identity == "operator"
    for argv in (
        ["token", "--env", "local"],
        ["token", "--env", "local", "--identity", "operator", "--list"],
        ["token", "--env", "local", "--revoke", "not-an-id"],
    ):
        with pytest.raises(SystemExit):
            parse(argv)


async def test_a_requeue_never_runs_without_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path, "staging")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO())
    assert await work_requeue_command(requeue_args("staging")) == 2
    monkeypatch.setattr("sys.stdin", Terminal())
    assert await work_requeue_command(requeue_args("staging")) == 2, "no local sign-in there"


def test_the_requeue_takes_the_org_and_the_item_by_id() -> None:
    args = ops_main.build_parser().parse_args(
        ["work", "requeue", "--env", "local", "--org", str(ORG), str(ITEM)]
    )
    assert (args.command, args.work_command, args.org, args.item) == ("work", "requeue", ORG, ITEM)
    with pytest.raises(SystemExit):
        ops_main.build_parser().parse_args(["work", "requeue", "--env", "local", "--org", "x", "y"])
