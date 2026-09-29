"""`acme-ops workos-bootstrap`: the desired state in the repository, the
key proven to be the application's before anything else, a dry run by
default, the authorize probe as the truth of what AuthKit accepts, the
application's own list written under --apply, and a second run against
unchanged config that changes nothing."""

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from acme.ops.environments import repository_root
from acme.ops.workos import (
    CREDENTIAL_CHECK_CODE,
    SESSION_IDLE_LIFETIME,
    SESSION_LIFETIME,
    desired_file,
    load_desired,
    workos_bootstrap_command,
)

KEY = "sk_test_never_printed"
APP = "client_app"
LOCAL = "http://localhost:55173/auth/callback"
DEPLOYED = "https://app.staging.example.test/auth/callback"
DESIRED = f"""
staging:
  api_key_variable: WORKOS_API_KEY
  client_id: {APP}
  redirect_uris:
    - {LOCAL}
    - {DEPLOYED}
  default_redirect_uri: {DEPLOYED}
  login_initiation_uri: https://app.staging.example.test/login
  app_homepage_url: https://app.staging.example.test
  sign_out_uris:
    - http://localhost:55173/signed-out
    - https://app.staging.example.test/signed-out
  default_sign_out_uri: https://app.staging.example.test/signed-out
  webhooks: []
"""


class FakeWorkOS:
    """One application: its key, its redirect list, and what AuthKit
    accepts for it. `mirrored` says whether a write to the list reaches
    AuthKit; `default` names the list's default."""

    def __init__(
        self,
        accepted: set[str],
        *,
        listed: set[str] | None = None,
        default: str | None = None,
        mirrored: bool = True,
        key: str = KEY,
    ) -> None:
        self.accepted = set(accepted)
        self.listed: list[str] = sorted(listed if listed is not None else accepted)
        self.default = default
        self.mirrored = mirrored
        self.key = key
        self.created: list[str] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/user_management/authenticate":
            body = json.loads(request.content)
            assert body["code"] == CREDENTIAL_CHECK_CODE and body["client_id"] == APP
            if body["client_secret"] != self.key:
                return httpx.Response(
                    400,
                    json={
                        "error": "invalid_client",
                        "error_description": "a client secret from a different application",
                    },
                )
            return httpx.Response(400, json={"error": "invalid_grant"})
        if path == "/user_management/authorize":
            assert "authorization" not in request.headers
            query = parse_qs(urlsplit(str(request.url)).query)
            assert query["client_id"] == [APP] and query["provider"] == ["authkit"]
            uri = query["redirect_uri"][0]
            where = "/bootstrap" if uri in self.accepted else "/redirect-uri-invalid"
            return httpx.Response(302, headers={"location": f"https://authkit.test{where}"})
        assert request.headers["authorization"] == f"Bearer {KEY}"
        if path == "/user_management/redirect_uris" and request.method == "GET":
            data = [
                {"object": "redirect_uri", "uri": uri, "default": uri == self.default}
                for uri in self.listed
            ]
            return httpx.Response(200, json={"data": data, "list_metadata": {"after": None}})
        if path == "/user_management/redirect_uris" and request.method == "POST":
            uri = json.loads(request.content)["uri"]
            if uri in self.listed:
                return httpx.Response(422, json={"message": "exists"})
            self.listed.append(uri)
            self.created.append(uri)
            if self.mirrored:
                self.accepted.add(uri)
            return httpx.Response(201, json={"object": "redirect_uri", "uri": uri})
        return httpx.Response(404, json={"message": "no"})


def repo(tmp_path: Path, desired: str = DESIRED) -> Path:
    file = tmp_path / "deployment" / "workos" / "environments.yaml"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(desired)
    return tmp_path


async def run(
    tmp_path: Path,
    api: FakeWorkOS | httpx.MockTransport,
    *,
    apply: bool,
    key: str | None = KEY,
) -> int:
    transport = api if isinstance(api, httpx.MockTransport) else httpx.MockTransport(api)
    return await workos_bootstrap_command(
        argparse.Namespace(environment="staging", apply=apply),
        transport=transport,
        environ={} if key is None else {"WORKOS_API_KEY": key},
        root=repo(tmp_path),
    )


def test_the_committed_desired_state_names_both_applications() -> None:
    root = repository_root(Path(__file__).parent)
    assert root is not None
    staging = load_desired(desired_file(root), "staging")
    production = load_desired(desired_file(root), "production")
    assert staging.client_id == "client_STAGING_PLACEHOLDER"
    assert production.client_id == "client_PRODUCTION_PLACEHOLDER"
    assert "http://localhost:55173/auth/callback" in staging.redirect_uris
    assert staging.default_redirect_uri == "https://app.staging.acme.example/auth/callback"
    assert all(u.startswith("https://") for u in production.redirect_uris)
    assert production.default_redirect_uri.startswith("https://")
    assert "http://localhost:55173/signed-out" in staging.sign_out_uris
    assert staging.default_sign_out_uri == "https://app.staging.acme.example/signed-out"
    assert production.sign_out_uris == ("https://app.acme.example/signed-out",)
    assert staging.api_key_variable != production.api_key_variable
    assert staging.webhooks == () and production.webhooks == ()


def test_a_default_that_is_not_a_redirect_is_refused(tmp_path: Path) -> None:
    repo(tmp_path, DESIRED.replace(f"default_redirect_uri: {DEPLOYED}", "default_redirect_uri: x"))
    with pytest.raises(ValueError, match="is not one of its redirect URIs"):
        load_desired(desired_file(tmp_path), "staging")


def test_a_default_that_is_not_a_sign_out_uri_is_refused(tmp_path: Path) -> None:
    repo(
        tmp_path,
        DESIRED.replace(
            "default_sign_out_uri: https://app.staging.example.test/signed-out",
            "default_sign_out_uri: https://app.staging.example.test/",
        ),
    )
    with pytest.raises(ValueError, match="is not one of its sign-out URIs"):
        load_desired(desired_file(tmp_path), "staging")


async def test_a_key_of_another_application_stops_the_run_before_anything_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The environment's key, or another application's, is refused by the
    exchange as this application's client secret; nothing is read after."""
    api = FakeWorkOS({LOCAL}, key="sk_test_the_applications")
    assert await run(tmp_path, api, apply=True) == 2
    out = capsys.readouterr().out
    assert f"key: not the API key of application {APP}" in out
    assert "a client secret from a different application" in out
    assert f"Applications, application {APP}, its API keys tab" in out
    assert [r.url.path for r in api.requests] == ["/user_management/authenticate"]


async def test_a_dry_run_says_what_it_would_create_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeWorkOS({LOCAL}, default=LOCAL)
    assert await run(tmp_path, api, apply=False) == 1
    out = capsys.readouterr().out
    assert f"key: application {APP}'s" in out
    assert f"the application's list: 1 redirect URI(s): {LOCAL} (default)" in out
    assert f"redirect {LOCAL}: present" in out
    assert f"redirect {DEPLOYED}: would create" in out
    assert f"default redirect: {LOCAL}; make {DEPLOYED} the default (dashboard)" in out
    assert not any(r.method == "POST" and "redirect_uris" in r.url.path for r in api.requests)


async def test_a_missing_redirect_is_created_once_and_the_rerun_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeWorkOS({LOCAL})
    assert await run(tmp_path, api, apply=True) == 1
    first = capsys.readouterr().out
    assert f"redirect {DEPLOYED}: created" in first and api.created == [DEPLOYED]
    assert "default redirect: none" in first
    # The person makes the deployed callback the default on the tab.
    api.default = DEPLOYED
    assert await run(tmp_path, api, apply=True) == 0
    second = capsys.readouterr().out
    assert api.created == [DEPLOYED]
    assert f"default redirect: {DEPLOYED}" in second
    assert "nothing to change" in second and "created" not in second


async def test_a_redirect_the_list_holds_and_authkit_refuses_is_a_dashboard_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeWorkOS({LOCAL}, listed={LOCAL, DEPLOYED}, default=DEPLOYED)
    assert await run(tmp_path, api, apply=True) == 1
    out = capsys.readouterr().out
    assert f"redirect {DEPLOYED}: missing (dashboard)" in out
    assert "1 change(s) need the dashboard" in out
    assert api.created == []


async def test_a_write_authkit_does_not_take_fails_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeWorkOS({LOCAL}, default=LOCAL, mirrored=False)
    api.default = None
    assert await run(tmp_path, api, apply=True) == 1
    out = capsys.readouterr().out
    assert f"redirect {DEPLOYED}: missing (dashboard)" in out and api.created == [DEPLOYED]


async def test_the_tabs_other_fields_are_printed_as_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeWorkOS({LOCAL, DEPLOYED}, default=DEPLOYED)
    assert await run(tmp_path, api, apply=False) == 0
    out = capsys.readouterr().out
    assert "login initiation URI: https://app.staging.example.test/login" in out
    assert "app homepage URL: https://app.staging.example.test" in out
    assert (
        "sign-out URIs: http://localhost:55173/signed-out, "
        "https://app.staging.example.test/signed-out, default "
        "https://app.staging.example.test/signed-out (check them on the same tab" in out
    )
    for field in ("sign-up URL", "user invitation URL", "password reset URL"):
        assert f"{field}: not set" in out
    assert "maximum session length: 30 days" in out and "its Sessions tab" in out
    assert "inactivity timeout: 14 days" in out
    assert "webhooks: none" in out and "nothing to change" in out


def test_the_sessions_tab_matches_the_session_lifetimes() -> None:
    """The Sessions tab's values are the lifetimes `.env.example` gives a
    Acme session, so the two never drift apart."""
    root = repository_root()
    assert root is not None
    values = dict(
        line.split("=", 1)
        for line in (root / ".env.example").read_text().splitlines()
        if line.startswith("ACME_SESSION_")
    )
    assert int(values["ACME_SESSION_LIFETIME_SECONDS"]) == SESSION_LIFETIME.total_seconds()
    assert (
        int(values["ACME_SESSION_IDLE_LIFETIME_SECONDS"]) == SESSION_IDLE_LIFETIME.total_seconds()
    )


async def test_a_credential_check_workos_cannot_answer_is_an_error(tmp_path: Path) -> None:
    api = httpx.MockTransport(lambda r: httpx.Response(503, json={"message": "down"}))
    with pytest.raises(ValueError, match="the credential check answered 503"):
        await run(tmp_path, api, apply=False)


async def test_the_key_never_appears_in_what_it_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await run(tmp_path, FakeWorkOS(set(), mirrored=False), apply=True)
    await run(tmp_path, FakeWorkOS(set(), key="another"), apply=False)
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err


async def test_no_key_is_a_usage_error_that_names_the_variable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeWorkOS(set())
    assert await run(tmp_path, api, apply=False, key=None) == 2
    err = capsys.readouterr().err
    assert "WORKOS_API_KEY is not set" in err and f"API key of application {APP}" in err
    assert api.requests == []
