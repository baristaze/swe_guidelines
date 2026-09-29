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
from acme.integrations.root import IntegrationsInterface
from acme.integrations.settings import IntegrationsSettings


def refuse_unsafe(settings: IntegrationsSettings, environment: str, deployed: bool) -> None:
    """A twin in a deployed environment is refused at boot, naming the setting."""
    if deployed and settings.identity_provider == "twin":
        raise UnsafeIntegration(
            f"ACME_IDENTITY_PROVIDER=twin is refused when ACME_ENVIRONMENT={environment}"
        )


def identity_provider_for(settings: IntegrationsSettings) -> IdentityProviderInterface:
    if settings.identity_provider == "twin":
        return IdentityProviderTwinImpl()
    if settings.identity_provider == "workos":
        if not settings.workos_client_id:
            return IdentityProviderAbsentImpl("ACME_WORKOS_CLIENT_ID is not set")
        if settings.workos_api_key is None:
            return IdentityProviderAbsentImpl("ACME_WORKOS_API_KEY is not set")
        secret = settings.workos_webhook_secret
        return IdentityProviderWorkOSImpl(
            client_id=settings.workos_client_id,
            api_key=settings.workos_api_key.get_secret_value(),
            base_url=settings.workos_base_url,
            timeout=timedelta(seconds=settings.workos_timeout_seconds),
            webhook_secret=None if secret is None else secret.get_secret_value(),
        )
    return IdentityProviderAbsentImpl()


class IntegrationsOverImpl(IntegrationsInterface):
    """The root over providers already built: a test's twin, or the absent
    provider of a process that signs nobody in."""

    def __init__(self, identity: IdentityProviderInterface) -> None:
        self._identity = identity

    def get_identity_provider(self) -> IdentityProviderInterface:
        return self._identity

    def describe(self) -> list[str]:
        return [self._identity.describe()]

    async def start(self) -> None:
        await self._identity.start()

    async def close(self) -> None:
        await self._identity.close()


def absent_integrations() -> IntegrationsInterface:
    """The root of a process that signs nobody in."""
    return IntegrationsOverImpl(IdentityProviderAbsentImpl())


class IntegrationsConfiguredImpl(IntegrationsOverImpl):
    def __init__(self, settings: IntegrationsSettings, environment: str, deployed: bool) -> None:
        refuse_unsafe(settings, environment, deployed)
        super().__init__(identity_provider_for(settings))
