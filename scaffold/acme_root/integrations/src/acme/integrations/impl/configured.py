"""The integrations root that picks each provider from settings, and the
root over providers a caller already built. A twin is refused in a
deployed environment, the way the infra root refuses a local backend; the
caller says whether the environment is one, from the settings it booted
with."""

from datetime import timedelta

from acme.integrations.exceptions import UnsafeIntegration
from acme.integrations.identity import IdentityProviderInterface
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.identity.workos import IdentityProviderWorkOSImpl
from acme.integrations.payments import PaymentsInterface
from acme.integrations.payments.stripe import PaymentsStripeImpl
from acme.integrations.payments.twin import TWIN_ENVIRONMENTS, PaymentsTwinImpl
from acme.integrations.root import IntegrationsInterface
from acme.integrations.settings import IntegrationsSettings, key_refusal
from acme.integrations.slack import SlackInterface
from acme.integrations.slack.off import SlackOffImpl
from acme.integrations.slack.twin import SlackTwinImpl
from acme.integrations.slack.web import SlackWebImpl


def refuse_unsafe(settings: IntegrationsSettings, environment: str, deployed: bool) -> None:
    """A twin in a deployed environment is refused at boot, naming the setting,
    and so is a payment processor's key whose mode is not the environment's."""
    if deployed and settings.identity_provider == "twin":
        raise UnsafeIntegration(
            f"ACME_IDENTITY_PROVIDER=twin is refused when ACME_ENVIRONMENT={environment}"
        )
    refuse_unsafe_payments(settings, environment)
    refuse_unsafe_slack(settings, environment)


def refuse_unsafe_slack(settings: IntegrationsSettings, environment: str) -> None:
    """Slack's twin anywhere but local and test."""
    if settings.slack_backend == "twin" and environment not in TWIN_ENVIRONMENTS:
        raise UnsafeIntegration(
            f"ACME_SLACK_BACKEND=twin is refused when ACME_ENVIRONMENT={environment}"
        )


def slack_for(settings: IntegrationsSettings, environment: str) -> SlackInterface:
    """The Slack app the settings name, after the boot's refusal: the twin, the
    real client when all three of the app's credentials are set, and
    otherwise the client that reaches Slack for nothing and says so."""
    refuse_unsafe_slack(settings, environment)
    if settings.slack_backend == "twin":
        return SlackTwinImpl(environment)
    client_secret, signing_secret = settings.slack_client_secret, settings.slack_signing_secret
    if not settings.slack_client_id or client_secret is None or signing_secret is None:
        return SlackOffImpl()
    return SlackWebImpl(
        client_id=settings.slack_client_id,
        client_secret=client_secret.get_secret_value(),
        signing_secret=signing_secret.get_secret_value(),
        timeout=timedelta(seconds=settings.slack_timeout_seconds),
    )


RUNTIME_KEY_VARIABLE = "ACME_STRIPE_RUNTIME_KEY"


def refuse_unsafe_payments(settings: IntegrationsSettings, environment: str) -> None:
    """The payment processor's twin anywhere but local and test; a runtime
    key that is not a restricted key, or whose mode is not the environment's
    (production takes a live key, every other environment a test key)."""
    if settings.billing_backend == "twin":
        if environment not in TWIN_ENVIRONMENTS:
            raise UnsafeIntegration(
                f"ACME_BILLING_BACKEND=twin is refused when ACME_ENVIRONMENT={environment}"
            )
        return
    key = settings.stripe_runtime_key
    if key is None:
        return
    refusal = key_refusal(RUNTIME_KEY_VARIABLE, key.get_secret_value(), environment)
    if refusal is not None:
        raise UnsafeIntegration(refusal)


def payments_for(settings: IntegrationsSettings, environment: str) -> PaymentsInterface:
    """The payment processor the settings name, after the boot's refusals."""
    refuse_unsafe_payments(settings, environment)
    if settings.billing_backend == "twin":
        return PaymentsTwinImpl(environment=environment)
    key, secret = settings.stripe_runtime_key, settings.stripe_webhook_secret
    return PaymentsStripeImpl(
        api_key=None if key is None else key.get_secret_value(),
        account_id=settings.stripe_account_id,
        webhook_secret=None if secret is None else secret.get_secret_value(),
        timeout=timedelta(seconds=settings.stripe_timeout_seconds),
    )


def absent_slack() -> SlackInterface:
    """The Slack app of a process that holds none: every call answers
    `slack_unavailable`."""
    return SlackOffImpl()


def absent_payments() -> PaymentsInterface:
    """The payment processor of a process that holds none: every call answers
    `billing_unavailable`, and every org keeps its plan."""
    return PaymentsStripeImpl(
        api_key=None, account_id=None, webhook_secret=None, timeout=timedelta(seconds=10)
    )


def identity_provider_for(settings: IntegrationsSettings) -> IdentityProviderInterface:
    if settings.identity_provider == "twin":
        return IdentityProviderTwinImpl()
    if settings.identity_provider == "workos":
        if not settings.workos_client_id:
            return IdentityProviderAbsentImpl("ACME_WORKOS_CLIENT_ID is not set")
        if settings.workos_api_key is None:
            return IdentityProviderAbsentImpl("ACME_WORKOS_API_KEY is not set")
        return IdentityProviderWorkOSImpl(
            client_id=settings.workos_client_id,
            api_key=settings.workos_api_key.get_secret_value(),
            base_url=settings.workos_base_url,
            timeout=timedelta(seconds=settings.workos_timeout_seconds),
        )
    return IdentityProviderAbsentImpl()


class IntegrationsOverImpl(IntegrationsInterface):
    """The root over providers already built: a test's twin, or the absent
    provider of a process that signs nobody in."""

    def __init__(
        self,
        identity: IdentityProviderInterface,
        payments: PaymentsInterface | None = None,
        slack: SlackInterface | None = None,
    ) -> None:
        self._identity = identity
        self._payments = payments or absent_payments()
        self._slack = slack or absent_slack()

    def get_identity_provider(self) -> IdentityProviderInterface:
        return self._identity

    def get_payments(self) -> PaymentsInterface:
        return self._payments

    def get_slack(self) -> SlackInterface:
        return self._slack

    def describe(self) -> list[str]:
        return [self._identity.describe(), self._payments.describe(), self._slack.describe()]

    async def start(self) -> None:
        await self._identity.start()
        await self._payments.start()
        await self._slack.start()

    async def close(self) -> None:
        await self._slack.close()
        await self._payments.close()
        await self._identity.close()


def absent_integrations(
    payments: PaymentsInterface | None = None, slack: SlackInterface | None = None
) -> IntegrationsInterface:
    """The root of a process that signs nobody in; it holds no payment
    processor and no Slack app either unless the caller hands them in."""
    return IntegrationsOverImpl(IdentityProviderAbsentImpl(), payments, slack)


class IntegrationsConfiguredImpl(IntegrationsOverImpl):
    def __init__(self, settings: IntegrationsSettings, environment: str, deployed: bool) -> None:
        refuse_unsafe(settings, environment, deployed)
        super().__init__(
            identity_provider_for(settings),
            payments_for(settings, environment),
            slack_for(settings, environment),
        )
