"""Sign-in through the identity provider, over the whole API in-process with
the provider's twin: the start, the callback, the device sign-in, the local
sign-in, and the second factor."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from api_support import OWNER, bearer, build_container, dev_login, seed_request
from fastapi import FastAPI
from httpx import ASGITransport
from pydantic import ValidationError

from acme.integrations.exceptions import UnsafeIntegration
from acme.integrations.identity.twin import (
    TWIN_AUTHORIZE,
    TWIN_LOGOUT,
    IdentityProviderTwinImpl,
)
from acme.integrations.impl.configured import IntegrationsConfiguredImpl, IntegrationsOverImpl
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.settings import ApiSettings

CALLBACK = "http://localhost:55173/auth/callback"
SIGNED_OUT = "http://localhost:55173/signed-out"
STATE = "a-random-state-of-the-tab"
VERIFIER = "v" * 43
"""A verifier for a code the twin issued with no challenge, which the twin
does not check; the round trip below checks a real one."""


@pytest.fixture
def twin() -> IdentityProviderTwinImpl:
    return IdentityProviderTwinImpl()


@pytest.fixture
def container(tmp_path: Path, twin: IdentityProviderTwinImpl) -> AppContainer:
    return build_container(tmp_path, integrations=IntegrationsOverImpl(twin))


async def test_the_start_answers_where_the_browser_goes(client: httpx.AsyncClient) -> None:
    started = await client.post(
        "/v1/auth/sign-in",
        json={"redirect_uri": CALLBACK, "state": STATE, "invitation_token": "inv", "sign_up": True},
    )
    assert started.status_code == 200, started.text
    url = urlparse(started.json()["authorization_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == TWIN_AUTHORIZE
    query = parse_qs(url.query)
    assert query["redirect_uri"] == [CALLBACK] and query["state"] == [STATE]
    assert query["invitation_token"] == ["inv"] and query["screen_hint"] == ["sign-up"]


async def test_the_round_trip_holds_the_verifier_the_start_answered_with(
    client: httpx.AsyncClient, twin: IdentityProviderTwinImpl
) -> None:
    """PKCE: the start answers a verifier and sends the provider only its
    digest; the code comes back worth something only with that verifier."""
    started = (
        await client.post("/v1/auth/sign-in", json={"redirect_uri": CALLBACK, "state": STATE})
    ).json()
    verifier = started["code_verifier"]
    [challenge] = parse_qs(urlparse(started["authorization_url"]).query)["code_challenge"]
    assert verifier not in started["authorization_url"]
    wrong = await client.post(
        "/v1/auth/callback",
        json={
            "code": twin.issue_code("pia@example.test", code_challenge=challenge),
            "code_verifier": "w" * 43,
        },
    )
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "sign_in_refused"
    right = await client.post(
        "/v1/auth/callback",
        json={
            "code": twin.issue_code("pia@example.test", code_challenge=challenge),
            "code_verifier": verifier,
        },
    )
    assert right.status_code == 200, right.text


@pytest.mark.parametrize(
    "redirect",
    ["https://evil.example/auth/callback", "http://localhost:55173/elsewhere", ""],
)
async def test_a_redirect_that_is_not_this_environments_is_refused(
    client: httpx.AsyncClient, redirect: str
) -> None:
    refused = await client.post("/v1/auth/sign-in", json={"redirect_uri": redirect, "state": STATE})
    assert refused.status_code == 422, refused.text


async def test_a_short_state_is_refused(client: httpx.AsyncClient) -> None:
    refused = await client.post("/v1/auth/sign-in", json={"redirect_uri": CALLBACK, "state": "x"})
    assert refused.status_code == 422, refused.text


async def test_no_provider_answers_503(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    app = create_app(container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post(
                "/v1/auth/sign-in", json={"redirect_uri": CALLBACK, "state": STATE}
            )
            device = await client.post("/v1/auth/device")
    for answer in (started, device):
        assert answer.status_code == 503, answer.text
        assert answer.json()["error"]["code"] == "unavailable"


async def test_a_first_sign_in_signs_the_person_up_with_their_personal_org(
    client: httpx.AsyncClient, twin: IdentityProviderTwinImpl
) -> None:
    code = twin.issue_code("dee@example.test", first_name="Dee", last_name="Baker")
    signed_in = await client.post(
        "/v1/auth/callback", json={"code": code, "code_verifier": VERIFIER}
    )
    assert signed_in.status_code == 200, signed_in.text
    body = signed_in.json()
    assert body["token"].startswith("lgn_")
    [place] = body["memberships"]
    assert place["org"]["kind"] == "personal" and place["org"]["name"] == "Dee Baker"
    assert place["role"] == "owner" and place["user"]["email"] == "dee@example.test"
    session = await client.post(
        "/v1/auth/sessions", json={"org_id": place["org"]["id"]}, headers=bearer(body["token"])
    )
    assert session.status_code == 200, session.text
    # A code works once.
    again = await client.post("/v1/auth/callback", json={"code": code, "code_verifier": VERIFIER})
    assert again.status_code == 401, again.text
    assert again.json()["error"]["code"] == "sign_in_refused"
    # The next sign-in finds the same person, with the same one place.
    later = await client.post(
        "/v1/auth/callback",
        json={"code": twin.issue_code("dee@example.test"), "code_verifier": VERIFIER},
    )
    assert [m["org"]["id"] for m in later.json()["memberships"]] == [place["org"]["id"]]


async def test_a_person_the_seeding_made_is_linked_by_the_verified_email(
    client: httpx.AsyncClient, container: AppContainer, twin: IdentityProviderTwinImpl
) -> None:
    _, org = await container.managers.tenancy.bootstrap(
        seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
    )
    signed_in = await client.post(
        "/v1/auth/callback",
        json={"code": twin.issue_code(OWNER["email"]), "code_verifier": VERIFIER},
    )
    assert signed_in.status_code == 200, signed_in.text
    teams = [m["org"]["id"] for m in signed_in.json()["memberships"] if m["org"]["kind"] == "team"]
    assert teams == [str(org.id)]


async def test_an_address_the_provider_has_not_verified_is_refused(
    client: httpx.AsyncClient, twin: IdentityProviderTwinImpl
) -> None:
    code = twin.issue_code("dee@example.test", email_verified=False)
    refused = await client.post("/v1/auth/callback", json={"code": code, "code_verifier": VERIFIER})
    assert refused.status_code == 401, refused.text
    assert refused.json()["error"]["code"] == "email_not_verified"


async def test_a_device_signs_in_once_the_person_confirms(
    client: httpx.AsyncClient, twin: IdentityProviderTwinImpl
) -> None:
    started = await client.post("/v1/auth/device")
    assert started.status_code == 200, started.text
    device = started.json()
    assert device["interval"] > 0 and device["expires_in"] > 0
    assert device["user_code"] in device["verification_uri_complete"]
    ask = {"device_code": device["device_code"]}
    pending = await client.post("/v1/auth/device/token", json=ask)
    assert pending.status_code == 400, pending.text
    assert pending.json()["error"]["code"] == "sign_in_pending"
    twin.confirm_device(device["user_code"], "dee@example.test")
    login = await client.post("/v1/auth/device/token", json=ask)
    assert login.status_code == 200, login.text
    assert login.json()["token"].startswith("lgn_")
    assert [m["org"]["kind"] for m in login.json()["memberships"]] == ["personal"]
    # The device code is spent.
    spent = await client.post("/v1/auth/device/token", json=ask)
    assert spent.status_code == 401 and spent.json()["error"]["code"] == "sign_in_refused"


async def test_a_declined_device_sign_in_is_refused(
    client: httpx.AsyncClient, twin: IdentityProviderTwinImpl
) -> None:
    device = (await client.post("/v1/auth/device")).json()
    twin.confirm_device(device["user_code"], "dee@example.test", deny=True)
    refused = await client.post(
        "/v1/auth/device/token", json={"device_code": device["device_code"]}
    )
    assert refused.status_code == 401 and refused.json()["error"]["code"] == "sign_in_refused"


async def test_the_local_sign_in_makes_or_finds_the_person(client: httpx.AsyncClient) -> None:
    first = await client.post(
        "/v1/auth/dev-sign-in", json={"email": "dee@example.test", "display_name": "Dee"}
    )
    assert first.status_code == 200, first.text
    [place] = first.json()["memberships"]
    assert place["org"]["kind"] == "personal" and place["org"]["name"] == "Dee"
    again = await client.post("/v1/auth/dev-sign-in", json={"email": "dee@example.test"})
    assert [m["org"]["id"] for m in again.json()["memberships"]] == [place["org"]["id"]]
    platform = await client.post(
        "/v1/auth/dev-sign-in", json={"email": "smoke@platform.acme.invalid"}
    )
    assert platform.status_code == 422, platform.text


async def test_the_local_sign_in_is_no_route_where_it_is_off(tmp_path: Path) -> None:
    container = build_container(tmp_path, dev_sign_in_enabled=False)
    app = create_app(container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            closed = await client.post("/v1/auth/dev-sign-in", json={"email": "d@example.test"})
            malformed = await client.post("/v1/auth/dev-sign-in", json={})
            missing = await client.post("/v1/auth/no-such-route", json={})
    for answer in (closed, malformed, missing):
        assert answer.status_code == 404, answer.text
        error = answer.json()["error"]
        assert (error["code"], error["message"]) == ("not_found", "Not Found")


async def test_the_second_factor_is_verified_on_a_sign_in_only(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """A person with no second factor enrolled is refused: the check is the
    identity's, and a code nobody could have made is wrong either way."""
    token = await dev_login(client, "dee@example.test")
    refused = await client.post(
        "/v1/auth/second-factor", headers=bearer(token), json={"totp_code": "123456"}
    )
    assert refused.status_code == 422, refused.text
    anonymous = await client.post("/v1/auth/second-factor", json={"totp_code": "123456"})
    assert anonymous.status_code == 401, anonymous.text


async def session_of(client: httpx.AsyncClient, login: httpx.Response) -> dict[str, str]:
    """The tenant session a sign-in's answer exchanges for, in its one place."""
    assert login.status_code == 200, login.text
    body = login.json()
    [place] = body["memberships"]
    session = await client.post(
        "/v1/auth/sessions", json={"org_id": place["org"]["id"]}, headers=bearer(body["token"])
    )
    assert session.status_code == 200, session.text
    return bearer(session.json()["token"])


async def test_a_sign_out_answers_where_the_browser_ends_the_providers_session(
    client: httpx.AsyncClient, twin: IdentityProviderTwinImpl
) -> None:
    code = twin.issue_code("dee@example.test")
    headers = await session_of(
        client,
        await client.post("/v1/auth/callback", json={"code": code, "code_verifier": VERIFIER}),
    )
    out = await client.post("/v1/auth/logout", json={"return_to": SIGNED_OUT}, headers=headers)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["revoked_at"] is not None and body["credential_kind"] == "session_token"
    url = urlparse(body["provider_logout_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == TWIN_LOGOUT
    query = parse_qs(url.query)
    assert query["return_to"] == [SIGNED_OUT]
    assert query["session_id"][0].startswith("twin_session_")
    # Acme's session is over.
    gone = await client.get("/v1/me", headers=headers)
    assert gone.status_code == 401, gone.text


async def test_a_sign_out_with_no_body_still_ends_the_session(
    client: httpx.AsyncClient, twin: IdentityProviderTwinImpl
) -> None:
    headers = await session_of(
        client,
        await client.post(
            "/v1/auth/callback",
            json={"code": twin.issue_code("dee@example.test"), "code_verifier": VERIFIER},
        ),
    )
    out = await client.post("/v1/auth/logout", headers=headers)
    assert out.status_code == 200, out.text
    query = parse_qs(urlparse(out.json()["provider_logout_url"]).query)
    assert "return_to" not in query and query["session_id"]


async def test_a_session_signed_in_another_way_signs_out_of_acme_alone(
    client: httpx.AsyncClient, twin: IdentityProviderTwinImpl
) -> None:
    local = await session_of(
        client, await client.post("/v1/auth/dev-sign-in", json={"email": "eve@example.test"})
    )
    device = (await client.post("/v1/auth/device")).json()
    twin.confirm_device(device["user_code"], "dee@example.test")
    by_device = await session_of(
        client,
        await client.post("/v1/auth/device/token", json={"device_code": device["device_code"]}),
    )
    for headers in (local, by_device):
        out = await client.post("/v1/auth/logout", json={"return_to": SIGNED_OUT}, headers=headers)
        assert out.status_code == 200, out.text
        assert out.json()["provider_logout_url"] is None and out.json()["revoked_at"]


@pytest.mark.parametrize(
    "return_to", ["https://evil.example/signed-out", "http://localhost:55173/elsewhere", ""]
)
async def test_a_sign_out_return_that_is_not_this_environments_is_refused(
    client: httpx.AsyncClient, twin: IdentityProviderTwinImpl, return_to: str
) -> None:
    headers = await session_of(
        client,
        await client.post(
            "/v1/auth/callback",
            json={"code": twin.issue_code("dee@example.test"), "code_verifier": VERIFIER},
        ),
    )
    refused = await client.post("/v1/auth/logout", json={"return_to": return_to}, headers=headers)
    assert refused.status_code == 422, refused.text
    still = await client.get("/v1/me", headers=headers)
    assert still.status_code == 200, still.text


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_a_deployed_environment_refuses_the_local_sign_in(environment: str) -> None:
    with pytest.raises(ValidationError, match="ACME_DEV_SIGN_IN_ENABLED"):
        ApiSettings.model_validate(
            {
                "_env_file": None,
                "environment": environment,
                "dev_sign_in_enabled": True,
                "sign_in_redirect_uris": ["https://app.example.test/auth/callback"],
            }
        )


@pytest.mark.parametrize(
    "redirect",
    [
        "http://app.staging.acme.example/auth/callback",
        "https://localhost:55173/auth/callback",
        "https://127.0.0.1/auth/callback",
    ],
)
def test_a_deployed_environments_sign_in_comes_back_to_its_own_https_address(
    redirect: str,
) -> None:
    with pytest.raises(ValidationError, match="ACME_SIGN_IN_REDIRECT_URIS"):
        ApiSettings.model_validate(
            {
                "_env_file": None,
                "environment": "staging",
                "sign_in_redirect_uris": [redirect],
                "sign_out_return_uris": ["https://app.staging.acme.example/signed-out"],
            }
        )
    allowed = ApiSettings.model_validate(
        {
            "_env_file": None,
            "environment": "staging",
            "sign_in_redirect_uris": ["https://app.staging.acme.example/auth/callback"],
            "sign_out_return_uris": ["https://app.staging.acme.example/signed-out"],
        }
    )
    assert allowed.sign_in_redirect_uris == ["https://app.staging.acme.example/auth/callback"]


@pytest.mark.parametrize(
    "return_to",
    ["http://app.staging.acme.example/signed-out", "https://localhost:55173/signed-out"],
)
def test_a_deployed_environments_sign_out_comes_back_to_its_own_https_address(
    return_to: str,
) -> None:
    deployed = {
        "_env_file": None,
        "environment": "staging",
        "sign_in_redirect_uris": ["https://app.staging.acme.example/auth/callback"],
    }
    with pytest.raises(ValidationError, match="ACME_SIGN_OUT_RETURN_URIS"):
        ApiSettings.model_validate({**deployed, "sign_out_return_uris": [return_to]})
    allowed = ApiSettings.model_validate(
        {**deployed, "sign_out_return_uris": ["https://app.staging.acme.example/signed-out"]}
    )
    assert allowed.sign_out_return_uris == ["https://app.staging.acme.example/signed-out"]


def test_the_settings_choose_the_provider_and_a_deployed_one_refuses_the_twin() -> None:
    settings = ApiSettings.model_validate(
        {"_env_file": None, "environment": "test", "identity_provider": "twin"}
    )
    integrations = IntegrationsConfiguredImpl(
        settings, settings.environment, settings.is_cloud_environment
    )
    assert isinstance(integrations.get_identity_provider(), IdentityProviderTwinImpl)
    assert any("twin" in line for line in integrations.describe())
    keyless = ApiSettings.model_validate(
        {
            "_env_file": None,
            "environment": "test",
            "identity_provider": "workos",
            "workos_client_id": "client_test",
            "workos_api_key": "off",
        }
    )
    absent = IntegrationsConfiguredImpl(
        keyless, keyless.environment, keyless.is_cloud_environment
    ).get_identity_provider()
    assert not absent.configured
    staging = ApiSettings.model_validate(
        {
            "_env_file": None,
            "environment": "staging",
            "identity_provider": "twin",
            "sign_in_redirect_uris": ["https://app.staging.acme.example/auth/callback"],
            "sign_out_return_uris": ["https://app.staging.acme.example/signed-out"],
        }
    )
    with pytest.raises(UnsafeIntegration, match="ACME_IDENTITY_PROVIDER=twin"):
        IntegrationsConfiguredImpl(staging, staging.environment, staging.is_cloud_environment)


async def test_the_container_starts_with_the_provider_it_was_given(
    container: AppContainer, twin: IdentityProviderTwinImpl
) -> None:
    assert container.integrations.get_identity_provider() is twin
    assert container.managers.tenancy is not None
