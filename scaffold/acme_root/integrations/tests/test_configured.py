"""The integrations root picks the provider from settings, and refuses the
twin anywhere but a local environment."""

import json
from typing import Any

import httpx
import pytest

from acme.integrations.exceptions import ProviderUnavailable, UnsafeIntegration
from acme.integrations.identity import workos
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.identity.workos import IdentityProviderWorkOSImpl
from acme.integrations.impl.configured import (
    IntegrationsConfiguredImpl,
    absent_integrations,
    slack_for,
)
from acme.integrations.settings import IntegrationsSettings
from acme.integrations.slack.off import SlackOffImpl
from acme.integrations.slack.web import SlackWebImpl

DEPLOYED = frozenset({"dev", "staging", "production"})


def settings(**values: object) -> IntegrationsSettings:
    return IntegrationsSettings.model_validate({"_env_file": None, **values})


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_the_twin_is_refused_in_a_deployed_environment(environment: str) -> None:
    with pytest.raises(UnsafeIntegration, match="ACME_IDENTITY_PROVIDER=twin"):
        IntegrationsConfiguredImpl(
            settings(identity_provider="twin"), environment, environment in DEPLOYED
        )


@pytest.mark.parametrize("environment", ["local", "test"])
def test_the_twin_runs_locally(environment: str) -> None:
    root = IntegrationsConfiguredImpl(
        settings(identity_provider="twin"), environment, environment in DEPLOYED
    )
    assert isinstance(root.get_identity_provider(), IdentityProviderTwinImpl)


@pytest.mark.parametrize("key", [None, "", "off", " OFF "])
def test_workos_without_its_key_is_absent(key: str | None) -> None:
    root = IntegrationsConfiguredImpl(
        settings(identity_provider="workos", workos_client_id="client_x", workos_api_key=key),
        "staging",
        True,
    )
    provider = root.get_identity_provider()
    assert isinstance(provider, IdentityProviderAbsentImpl) and not provider.configured
    assert "ACME_WORKOS_API_KEY" in provider.describe()


def test_workos_without_its_client_id_is_absent() -> None:
    root = IntegrationsConfiguredImpl(
        settings(identity_provider="workos", workos_api_key="sk_test"), "local", False
    )
    assert "ACME_WORKOS_CLIENT_ID" in root.get_identity_provider().describe()


def test_workos_with_both_is_the_real_client_and_says_so_without_the_key() -> None:
    root = IntegrationsConfiguredImpl(
        settings(
            identity_provider="workos", workos_client_id="client_x", workos_api_key="sk_secret"
        ),
        "staging",
        True,
    )
    assert isinstance(root.get_identity_provider(), IdentityProviderWorkOSImpl)
    described = " ".join(root.describe())
    assert "client_x" in described and "sk_secret" not in described
    assert "sk_secret" not in repr(settings(identity_provider="workos", workos_api_key="sk_secret"))


async def test_the_settings_key_is_the_exchanges_secret_and_the_management_calls_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path == "/user_management/authenticate":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(201, json={"link": "https://setup.example/portal"})

    class OverTheFake(httpx.AsyncClient):
        def __init__(self, **options: Any) -> None:
            super().__init__(**{**options, "transport": httpx.MockTransport(answer)})

    monkeypatch.setattr(workos.httpx, "AsyncClient", OverTheFake)
    root = IntegrationsConfiguredImpl(
        settings(
            identity_provider="workos", workos_client_id="client_app", workos_api_key="sk_app"
        ),
        "staging",
        True,
    )
    await root.start()
    provider = root.get_identity_provider()
    await provider.portal_link(organization_id="org_1", intent="sso", return_url="https://x")
    await root.close()
    check, link = sent
    assert json.loads(check.content)["client_secret"] == "sk_app"
    assert json.loads(check.content)["client_id"] == "client_app"
    assert link.headers["authorization"] == "Bearer sk_app"


def test_no_provider_is_the_default_and_refuses_every_call() -> None:
    root = IntegrationsConfiguredImpl(settings(), "production", True)
    provider = root.get_identity_provider()
    assert not provider.configured
    with pytest.raises(ProviderUnavailable):
        provider.authorization_url(redirect_uri="https://x/cb", state="s", code_challenge="c")


async def test_the_absent_provider_refuses_every_call() -> None:
    absent = IdentityProviderAbsentImpl("closed")
    for call in (
        absent.authenticate_code("c", code_verifier="v"),
        absent.start_device(),
        absent.authenticate_device("d"),
        absent.ensure_organization(external_id="e", name="n"),
        absent.get_organization("o"),
        absent.send_invitation(email="e@x.test", organization_id="o", expires_in_days=1),
        absent.find_pending_invitation(email="e@x.test", organization_id="o"),
        absent.resend_invitation("i"),
        absent.revoke_invitation("i"),
        absent.accepted_invitation(organization_id="o", user_id="u", email="e@x.test"),
        absent.portal_link(organization_id="o", intent="sso", return_url="https://x"),
    ):
        with pytest.raises(ProviderUnavailable, match="closed"):
            await call
    root = absent_integrations()
    await root.start()
    assert root.describe() == [
        root.get_identity_provider().describe(),
        root.get_payments().describe(),
        root.get_slack().describe(),
    ]
    await root.close()


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_slacks_twin_is_refused_in_a_deployed_environment(environment: str) -> None:
    with pytest.raises(UnsafeIntegration, match="ACME_SLACK_BACKEND=twin"):
        slack_for(settings(slack_backend="twin"), environment)


@pytest.mark.parametrize(
    "missing", ["slack_client_id", "slack_client_secret", "slack_signing_secret"]
)
def test_slack_without_any_of_its_three_credentials_is_off(missing: str) -> None:
    given = {
        "slack_client_id": "111.222",
        "slack_client_secret": "not-a-secret",
        "slack_signing_secret": "not-a-secret",
    }
    given[missing] = "off" if missing != "slack_client_id" else ""
    assert isinstance(slack_for(settings(**given), "staging"), SlackOffImpl)


def test_slack_with_all_three_is_the_real_client_and_says_so_without_a_secret() -> None:
    chosen = slack_for(
        settings(
            slack_client_id="111.222",
            slack_client_secret="client-secret-value",
            slack_signing_secret="signing-secret-value",
        ),
        "staging",
    )
    assert isinstance(chosen, SlackWebImpl) and chosen.describe() == "slack=web"
    assert "secret-value" not in repr(settings(slack_client_secret="client-secret-value"))
