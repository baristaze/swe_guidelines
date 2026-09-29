"""The commands. Command mode does one thing and returns: sign in and out,
the orgs, the switch between them, who the session is, and a file's upload.
`listen` stays and prints every change in the org as it happens. Every
command is a thin call into the client; the API decides, the CLI shows.
Exit codes: 0 done, 1 the API refused, 2 usage, 3 not signed in, 4 the API
is unreachable."""

import asyncio
import json
import mimetypes
import sys
import time
import webbrowser
from collections.abc import Callable, Coroutine
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any, NoReturn

import httpx
import typer

from acme.apps.cli import config
from acme.apps.cli.listen import listen as run_listener
from acme.apps.cli.model import choose_org, human_size, org_lines
from acme.client.client import ApiClient, ApiError
from acme.client.realtime import ChannelRefused
from acme.client.types import IssuedLoginView, IssuedSessionView

EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_NOT_SIGNED_IN = 3
EXIT_UNREACHABLE = 4


def app_version() -> str:
    try:
        return f"cli@{version('acme-cli')}"
    except PackageNotFoundError:
        return "cli@dev"


def build_client(api_url: str, token: str | None) -> ApiClient:
    """The one place a client is built; tests replace it with one over a test transport."""
    return ApiClient(
        api_url,
        app="cli",
        app_version=app_version(),
        token=token,
        timeout=config.timeout_seconds(),
        retries=config.retries(),
        backoff_seconds=config.backoff_seconds(),
    )


app = typer.Typer(
    help="Acme from the terminal: one command at a time, or `listen` for what the org does.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,  # an unexpected error is a plain traceback, not a panel
)

Api = Annotated[str | None, typer.Option("--api", help="The API, else ACME_API_URL, else local.")]
Json = Annotated[bool, typer.Option("--json", help="Print the API's view as JSON.")]


def setting[T](read: Callable[[], T]) -> T:
    """A setting is read through here wherever it is read outside `_run`, so
    a value the environment got wrong is a usage error there too."""
    try:
        return read()
    except config.BadSetting as error:
        _fail(str(error), EXIT_USAGE)


def run[T](work: Callable[[ApiClient], Coroutine[Any, Any, T]], api: str | None = None) -> T:
    """Runs one command's coroutine under a signed-in client and turns what
    goes wrong into a line on stderr and an exit code."""
    api_url, bearer = setting(lambda: config.credentials(api))
    if bearer is None:
        _fail("not signed in; run `acme login`", EXIT_NOT_SIGNED_IN)

    async def go() -> T:
        async with build_client(api_url, bearer) as client:
            return await work(client)

    return _run(go(), signed_in=True)


def _run[T](coroutine: Coroutine[Any, Any, T], *, signed_in: bool = False) -> T:
    """`signed_in` says a 401 means the kept credential is dead, not that a
    sign-in was refused."""
    try:
        return asyncio.run(coroutine)
    except config.BadSetting as error:
        # The environment names something the CLI cannot use. Nothing was
        # asked of the API, and the caller fixes it where they set it.
        _fail(str(error), EXIT_USAGE)
    except ApiError as error:
        if error.status == 401 and signed_in:
            _fail(
                f"the credential was refused ({error.code}); run `acme login`", EXIT_NOT_SIGNED_IN
            )
        _fail(f"refused: {error}", EXIT_REFUSED)
    except ChannelRefused as error:
        _fail(f"the channel was refused ({error}); run `acme login`", EXIT_NOT_SIGNED_IN)
    except httpx.TransportError as error:
        # Any failure of the wire: refused, timed out, reset, or a proxy
        # that answered nothing. The API did not decide, so it is exit 4.
        _fail(f"cannot reach the API: {error}", EXIT_UNREACHABLE)


def _fail(message: str, code: int) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code)


# Signing in


SLOW_DOWN_SECONDS = 5
"""How much longer the next ask waits when the API says to ask less often."""


def open_browser(url: str) -> None:
    """Opens the confirmation page in the person's browser, when there is one
    to open; a terminal with none, or over SSH, prints the address instead."""
    try:
        webbrowser.open(url)
    except webbrowser.Error:
        pass


async def pause(seconds: float) -> None:
    """The wait between two asks; tests replace it."""
    await asyncio.sleep(seconds)


def now() -> float:
    """The clock the device code's expiry is read on; tests replace it."""
    return time.monotonic()


async def device_sign_in(client: ApiClient, *, browser: bool) -> IssuedLoginView:
    """The device sign-in: the API starts it at the identity provider, the
    person confirms the code in any browser, and the CLI asks every interval
    until they do, or the code expires."""
    started = await client.start_device_sign_in()
    typer.echo(
        f"to sign in, open {started.verification_uri_complete}\n"
        f"and confirm the code {started.user_code}",
        err=True,
    )
    if browser:
        open_browser(started.verification_uri_complete)
    interval = float(started.interval)
    deadline = now() + started.expires_in
    while True:
        await pause(interval)
        try:
            return await client.finish_device_sign_in(started.device_code)
        except ApiError as error:
            if error.code == "sign_in_slow_down":
                interval += SLOW_DOWN_SECONDS
            elif error.code != "sign_in_pending":
                raise
        if now() >= deadline:
            _fail("the code expired before it was confirmed; run `acme login` again", EXIT_REFUSED)


@app.command()
def login(
    org: Annotated[
        str | None, typer.Option(help="The org's slug, when you belong to several.")
    ] = None,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Print the address; open no browser.")
    ] = False,
    dev_email: Annotated[
        str | None,
        typer.Option(
            "--dev-email",
            help="Local stack only: sign in as this address with no browser. "
            "A deployed API has no such door and answers 404.",
        ),
    ] = None,
    api: Api = None,
) -> None:
    """Sign in through the browser with a one-time code and keep the session
    for the next commands. A session kept before is ended once the new one is
    kept."""
    api_url = config.api_url(api)
    # Read before the sign-in: keeping the new session overwrites the file.
    replaced = config.load_session()

    async def go() -> None:
        async with build_client(api_url, None) as client:
            if dev_email is not None:
                issued = await client.dev_sign_in(dev_email)
            else:
                issued = await device_sign_in(client, browser=not no_browser)
            try:
                chosen = choose_org(issued.memberships, org)
            except LookupError as slugs:
                # The API signed the person in; what is missing is the flag.
                _fail(f"choose an org with --org: {slugs}", EXIT_USAGE)
            session = await client.exchange_session(issued.token, chosen.org.id)
            path = keep(api_url, session)
            typer.echo(
                f"signed in as {session.user.display_name} at {session.org.name}"
                f" ({session.role.value})\nsession kept in {path}"
            )
        if replaced is not None and replaced.token != session.token:
            await end_replaced(replaced)

    _run(go())


async def end_replaced(replaced: config.Session) -> None:
    """Ends the session a login replaced, at the API that issued it and with
    its own token, as `logout` does. Best effort: the new session is kept
    whatever the API answers, and a session it could not end lapses when it
    expires, which stderr says."""
    try:
        async with build_client(replaced.api_url, replaced.token) as client:
            await client.logout()
    except ApiError as error:
        # Revoked or expired already: nothing is left to end.
        if error.status in (401, 422):
            return
        typer.echo(f"the session kept before was not ended; the API refused: {error}", err=True)
        return
    except httpx.TransportError as error:
        typer.echo(
            f"the session kept before was not ended; cannot reach {replaced.api_url}: {error}",
            err=True,
        )
        return
    typer.echo("the session kept before is ended")


def keep(api_url: str, session: IssuedSessionView) -> Path:
    """The one session the CLI holds, in the file the next command reads."""
    return config.save_session(
        config.Session(
            api_url=api_url,
            token=session.token,
            email=session.user.email,
            display_name=session.user.display_name,
            org_slug=session.org.slug,
            org_name=session.org.name,
        )
    )


@app.command()
def orgs(as_json: Json = False, api: Api = None) -> None:
    """The orgs you belong to; `*` marks the one the session is in."""

    async def go(client: ApiClient) -> None:
        memberships = await client.every_membership()
        if as_json:
            typer.echo(json.dumps([m.model_dump(mode="json") for m in memberships], indent=2))
            return
        current = (await client.me()).org.slug
        typer.echo(org_lines(memberships, current))

    run(go, api)


@app.command()
def switch(
    org: Annotated[str, typer.Argument(help="The slug of the org to switch to; see `acme orgs`.")],
) -> None:
    """Move the kept session to another org. The API ends the old session in
    the same write, so the CLI never holds two. `ACME_TOKEN` is the
    environment's credential, not the CLI's to end, so only the kept session
    switches."""
    session = config.load_session()
    if session is None:
        _fail("no kept session to switch; run `acme login`", EXIT_NOT_SIGNED_IN)
    client = setting(lambda: build_client(session.api_url, session.token))

    async def go() -> None:
        async with client:
            try:
                chosen = choose_org(await client.every_membership(), org)
            except LookupError as slugs:
                _fail(f"no org {org!r} to switch to; yours are: {slugs}", EXIT_USAGE)
            switched = await client.switch_session(chosen.org.id)
            keep(session.api_url, switched)
            typer.echo(
                f"switched to {switched.org.name} as {switched.user.display_name}"
                f" ({switched.role.value}); the old session is ended"
            )

    _run(go(), signed_in=True)


@app.command()
def logout(api: Api = None) -> None:
    """Revoke the session `login` kept and forget it. `ACME_TOKEN` is the
    environment's credential, not the CLI's to revoke, and is left alone."""
    session = config.load_session()
    if session is None:
        hint = "; ACME_TOKEN is the environment's, unset it" if setting(config.token) else ""
        typer.echo(f"no session to forget{hint}")
        return

    # The token goes back to the API that issued it and nowhere else: an
    # `--api` naming another one is refused before the file is touched,
    # never handed the session's token.
    if api is not None and api.rstrip("/") != session.api_url.rstrip("/"):
        _fail(
            f"the session was issued by {session.api_url}, not {api}; "
            "run `acme logout` without --api",
            EXIT_USAGE,
        )
    # Before the file is touched: a setting the environment got wrong means
    # the API is never asked, and a session nobody tried to revoke is not
    # forgotten over a typo.
    client = setting(lambda: build_client(session.api_url, session.token))

    async def revoke() -> str:
        async with client:
            try:
                await client.logout()
            except ApiError as error:
                # Revoked or expired already, or no longer a session token:
                # nothing is left to revoke.
                if error.status in (401, 422):
                    return f"signed out; the session was already gone ({error.code})"
                raise
        return "signed out"

    # The file goes whatever the API answered: a session the caller gave up
    # is not run as again, and the exit code says whether it was revoked.
    try:
        try:
            outcome = asyncio.run(revoke())
        finally:
            config.clear_session()
    except ApiError as error:
        _fail(f"session forgotten, not revoked; the API refused: {error}", EXIT_REFUSED)
    except httpx.TransportError as error:
        _fail(f"session forgotten, not revoked; cannot reach the API: {error}", EXIT_UNREACHABLE)
    except config.BadSetting as error:
        # The file is the one thing `logout` owns, so a file it cannot remove
        # is what the caller is told, whatever the API answered.
        _fail(str(error), EXIT_USAGE)
    typer.echo(outcome)


@app.command()
def whoami(api: Api = None) -> None:
    """Who the session belongs to."""
    me = run(lambda client: client.me(), api)
    typer.echo(f"{me.user.display_name} <{me.user.email}> at {me.org.name} ({me.role.value})")


# Files


@app.command()
def upload(
    path: Annotated[Path, typer.Argument(help="The file to upload.", exists=True, dir_okay=False)],
    content_type: Annotated[
        str | None, typer.Option("--type", help="Its type, else guessed from the name.")
    ] = None,
    as_json: Json = False,
    api: Api = None,
) -> None:
    """Upload a file the org keeps. The bytes go straight to the store, or
    through the API where the store takes no form."""
    kind = content_type or mimetypes.guess_type(path.name)[0]
    if kind is None:
        _fail(f"cannot tell the type of {path.name}; give --type", EXIT_USAGE)
    data = path.read_bytes()

    async def go(client: ApiClient) -> None:
        started = await client.start_upload(path.name, kind, len(data))
        stored = await client.upload(started, data)
        if as_json:
            typer.echo(stored.model_dump_json(indent=2))
        else:
            typer.echo(f"uploaded {stored.name} ({human_size(stored.size_bytes)}) as {stored.id}")

    run(go, api)


# Realtime


@app.command()
def listen(api: Api = None) -> None:
    """Stay connected and print every change in the org as it happens: who
    did what to which record. Ctrl-C stops."""
    try:
        run(run_listener, api)
    except KeyboardInterrupt:
        typer.echo("stopped", err=True)


def main() -> int:
    try:
        app(standalone_mode=True)
    except SystemExit as exit_:
        return int(exit_.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
