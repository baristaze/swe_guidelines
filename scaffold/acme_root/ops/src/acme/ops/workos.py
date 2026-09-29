"""`acme-ops workos-bootstrap`: reconciles one WorkOS application with the
desired state committed in `deployment/workos/environments.yaml`. A dry run
by default; `--apply` writes what the API can write. Rerun against unchanged
config, it changes nothing and says so.

The key is the Acme App application's own API key, made on that
application's API keys tab. The command proves it first: it exchanges a
code WorkOS never issued, with the key as the client secret, and WorkOS
answers `invalid_grant` for the application's key and `invalid_client` for
any other (another application's, the environment's, another
environment's). A key that is not the application's stops the run (exit 2)
before anything is read or written.

The application's redirect list is what the key reads and writes: WorkOS's
redirect URI API works on the application the key belongs to. A redirect
URI is present when AuthKit accepts it for the client: `GET
/user_management/authorize` with the client id and the URI answers with a
redirect that is not to `/redirect-uri-invalid`. That probe is the truth.
A missing URI is added to the list under `--apply` and probed again; one the
probe still refuses fails the run (exit 1) and is named as a dashboard step.
So does a default redirect that is not the one the desired state names:
the API reports which URI is the default and does not change it.

The Redirects tab's other fields have no API. The command prints what each
should hold, as a check to make by eye on the tab: the login initiation
URI, the app homepage URL, and the sign-out URIs with their default. The
sign-out URIs cannot be probed either: WorkOS's logout answers the same for
a return it lists and one it does not until a real session ends. No webhook
is reconciled either: the endpoint is made on the dashboard, and the command
prints the one the desired state names.

The Sessions tab has no API either. It bounds the AuthKit session a sign-in
leaves in the browser, which lets the next sign-in there through with no
prompt. The command prints the two lifetimes Acme gives its own sessions,
as the values to set there, so a new tab asks for a sign-in when a Acme
session would have ended (ADR 0063).

The API key comes from the variable the desired state names for the
environment, and never appears in anything this prints."""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

from acme.ops.environments import repository_root

WORKOS_API = "https://api.workos.com"
TIMEOUT_SECONDS = 10.0
INVALID_REDIRECT_PATH = "/redirect-uri-invalid"
# A code WorkOS never issued: only the application's own key gets as far as
# the code, and hears `invalid_grant`.
CREDENTIAL_CHECK_CODE = "acme-credential-check"
OK, FAILED, USAGE = 0, 1, 2
# The Redirects tab's fields Acme leaves to AuthKit, and why.
LEFT_TO_AUTHKIT = (
    ("sign-up URL", "not set: AuthKit hosts the sign-up page"),
    ("user invitation URL", "not set: AuthKit's page takes it, then the login initiation URI"),
    ("password reset URL", "not set: no password sign-in is on"),
)
# A Acme session's lifetimes, `ACME_SESSION_LIFETIME_SECONDS` and
# `ACME_SESSION_IDLE_LIFETIME_SECONDS`: the Sessions tab's two bounds match them.
SESSION_LIFETIME = timedelta(days=30)
SESSION_IDLE_LIFETIME = timedelta(days=14)


@dataclass(frozen=True)
class Desired:
    environment: str
    api_key_variable: str
    client_id: str
    redirect_uris: tuple[str, ...]
    default_redirect_uri: str
    login_initiation_uri: str
    app_homepage_url: str
    sign_out_uris: tuple[str, ...]
    default_sign_out_uri: str
    webhooks: tuple[str, ...]


def desired_file(root: Path | None = None) -> Path:
    top = root or repository_root()
    if top is None:
        raise FileNotFoundError("run workos-bootstrap from inside the repository")
    return top / "deployment" / "workos" / "environments.yaml"


def load_desired(path: Path, environment: str) -> Desired:
    loaded: Any = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict) or environment not in loaded:
        names = ", ".join(sorted(loaded)) if isinstance(loaded, dict) else "none"
        raise ValueError(f"{path} names no WorkOS environment {environment!r} (it names {names})")
    entry = loaded[environment]
    redirect_uris = tuple(str(u) for u in entry["redirect_uris"])
    default = str(entry["default_redirect_uri"])
    if default not in redirect_uris:
        raise ValueError(
            f"{path}: the default redirect URI of {environment!r}, {default}, "
            "is not one of its redirect URIs"
        )
    sign_out_uris = tuple(str(u) for u in entry["sign_out_uris"])
    default_sign_out = str(entry["default_sign_out_uri"])
    if default_sign_out not in sign_out_uris:
        raise ValueError(
            f"{path}: the default sign-out URI of {environment!r}, {default_sign_out}, "
            "is not one of its sign-out URIs"
        )
    return Desired(
        environment=environment,
        api_key_variable=str(entry["api_key_variable"]),
        client_id=str(entry["client_id"]),
        redirect_uris=redirect_uris,
        default_redirect_uri=default,
        login_initiation_uri=str(entry["login_initiation_uri"]),
        app_homepage_url=str(entry["app_homepage_url"]),
        sign_out_uris=sign_out_uris,
        default_sign_out_uri=default_sign_out,
        webhooks=tuple(str(w) for w in entry.get("webhooks") or ()),
    )


@dataclass(frozen=True)
class Listed:
    uri: str
    default: bool


class WorkOS:
    """The few WorkOS routes the reconcile reads and writes. The key rides
    the authorization header, or the credential check's client secret, and
    nothing else."""

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self._client = client
        self._key = api_key
        self._auth = {"Authorization": f"Bearer {api_key}"}

    async def key_is_the_applications(self, client_id: str) -> tuple[bool, str]:
        """Whether WorkOS takes the key as the application's client secret,
        and what it said."""
        response = await self._client.post(
            "/user_management/authenticate",
            json={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": self._key,
                "code": CREDENTIAL_CHECK_CODE,
            },
        )
        body: Any = response.json() if response.content else {}
        if not isinstance(body, dict):
            body = {}
        error = str(body.get("error") or "")
        said = str(body.get("error_description") or error)
        if response.status_code == 400 and error == "invalid_grant":
            return True, said
        if response.status_code == 400 and error == "invalid_client":
            return False, said
        raise ValueError(f"the credential check answered {response.status_code} {error}".rstrip())

    async def accepts(self, client_id: str, redirect_uri: str) -> bool:
        """Whether AuthKit accepts the redirect for the application."""
        response = await self._client.get(
            "/user_management/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "provider": "authkit",
            },
        )
        if response.status_code not in (301, 302, 303, 307, 308):
            raise ValueError(
                f"the authorize probe answered {response.status_code} for {redirect_uri}"
            )
        location = response.headers.get("location", "")
        return urlsplit(location).path != INVALID_REDIRECT_PATH

    async def redirects(self) -> list[Listed]:
        """The application's redirect list, every page of it."""
        found: list[Listed] = []
        after: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": 100}
            if after:
                params["after"] = after
            response = await self._client.get(
                "/user_management/redirect_uris", params=params, headers=self._auth
            )
            answer = self._json(response, "listing the redirect URIs")
            found.extend(
                Listed(uri=str(item["uri"]), default=bool(item.get("default")))
                for item in answer.get("data", [])
            )
            after = (answer.get("list_metadata") or {}).get("after")
            if not after:
                return found

    async def create_redirect(self, uri: str) -> bool:
        """True when it was created, False when the list held it already (422)."""
        response = await self._client.post(
            "/user_management/redirect_uris", json={"uri": uri}, headers=self._auth
        )
        if response.status_code == 422:
            return False
        self._json(response, f"creating the redirect URI {uri}")
        return True

    @staticmethod
    def _json(response: httpx.Response, doing: str) -> dict[str, Any]:
        if response.is_error:
            # The status, never the request: the request carried the key.
            raise ValueError(f"{doing}: WorkOS answered {response.status_code}")
        body: Any = response.json()
        return body if isinstance(body, dict) else {}


@dataclass
class Outcome:
    lines: list[str]
    created: int = 0
    pending: int = 0
    missing: int = 0
    refused_key: bool = False

    @property
    def exit_code(self) -> int:
        if self.refused_key:
            return USAGE
        return FAILED if self.missing else OK


async def reconcile(desired: Desired, workos: WorkOS, *, apply: bool) -> Outcome:
    outcome = Outcome(lines=[])
    say = outcome.lines.append
    say(f"WorkOS {desired.environment}, application {desired.client_id}")
    ours, said = await workos.key_is_the_applications(desired.client_id)
    if not ours:
        outcome.refused_key = True
        say(
            f"key: not the API key of application {desired.client_id} (WorkOS: {said}). "
            f"Make one on {tab(desired, 'API keys')} and export it; the environment's "
            "API Keys page is not where it lives"
        )
        return outcome
    say(f"key: application {desired.client_id}'s")
    listed = await workos.redirects()
    say(f"the application's list: {len(listed)} redirect URI(s){listing(listed)}")
    known = {item.uri for item in listed}
    for uri in desired.redirect_uris:
        if await workos.accepts(desired.client_id, uri):
            say(f"redirect {uri}: present")
            continue
        if uri in known:
            # The list holds it and AuthKit still refuses it: a write cannot help.
            outcome.missing += 1
            say(f"redirect {uri}: missing (dashboard): {tab(desired, 'Redirects')}")
            continue
        if not apply:
            outcome.pending += 1
            say(f"redirect {uri}: would create")
            continue
        created = await workos.create_redirect(uri)
        if await workos.accepts(desired.client_id, uri):
            if created:
                outcome.created += 1
            say(f"redirect {uri}: {'created' if created else 'present'}")
        else:
            outcome.missing += 1
            say(f"redirect {uri}: missing (dashboard): {tab(desired, 'Redirects')}")
    defaults = [item.uri for item in listed if item.default]
    if defaults == [desired.default_redirect_uri]:
        say(f"default redirect: {desired.default_redirect_uri}")
    else:
        outcome.missing += 1
        say(
            f"default redirect: {', '.join(defaults) or 'none'}; make "
            f"{desired.default_redirect_uri} the default (dashboard): {tab(desired, 'Redirects')}"
        )
    say(
        f"login initiation URI: {desired.login_initiation_uri} "
        "(check it on the Redirects tab; no API reads or writes it)"
    )
    say(f"app homepage URL: {desired.app_homepage_url} (check it on the same tab)")
    say(
        f"sign-out URIs: {', '.join(desired.sign_out_uris)}, default "
        f"{desired.default_sign_out_uri} (check them on the same tab; no API reads or "
        "writes them)"
    )
    for field, why in LEFT_TO_AUTHKIT:
        say(f"{field}: {why}")
    say(
        f"maximum session length: {SESSION_LIFETIME.days} days, a Acme session's absolute "
        f"lifetime (check it on {tab(desired, 'Sessions')}; no API reads or writes it)"
    )
    say(
        f"inactivity timeout: {SESSION_IDLE_LIFETIME.days} days, a Acme session's idle "
        "lifetime (check it on the same tab)"
    )
    say("webhooks: none" if not desired.webhooks else f"webhooks: {', '.join(desired.webhooks)}")
    say(summary(outcome))
    return outcome


def listing(listed: list[Listed]) -> str:
    if not listed:
        return ""
    return ": " + ", ".join(f"{i.uri}{' (default)' if i.default else ''}" for i in listed)


def tab(desired: Desired, name: str) -> str:
    return f"Applications, application {desired.client_id}, its {name} tab"


def summary(outcome: Outcome) -> str:
    if outcome.missing:
        return f"{outcome.missing} change(s) need the dashboard"
    if outcome.pending:
        return f"{outcome.pending} to create (a dry run; --apply makes them)"
    if outcome.created:
        return f"created {outcome.created}"
    return "nothing to change"


async def workos_bootstrap_command(
    args: argparse.Namespace,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    environ: dict[str, str] | None = None,
    root: Path | None = None,
) -> int:
    desired = load_desired(desired_file(root), args.environment)
    values = os.environ if environ is None else environ
    api_key = values.get(desired.api_key_variable, "").strip()
    if not api_key:
        print(
            f"acme-ops: {desired.api_key_variable} is not set; the API key of application "
            f"{desired.client_id} (WorkOS {desired.environment}) is what this command runs "
            "with, and there is none here",
            file=sys.stderr,
        )
        return USAGE
    async with httpx.AsyncClient(
        base_url=WORKOS_API,
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        transport=transport,
    ) as client:
        outcome = await reconcile(desired, WorkOS(client, api_key), apply=args.apply)
    for line in outcome.lines:
        print(line)
    return outcome.exit_code
