"""The integrations are fronted by one root, like infra: the container asks
it for what the managers need, and it has a lifecycle because a real client
holds connections."""

from abc import ABC, abstractmethod

from acme.integrations.identity import IdentityProviderInterface
from acme.integrations.payments import PaymentsInterface
from acme.integrations.slack import SlackInterface


class IntegrationsInterface(ABC):
    @abstractmethod
    def get_identity_provider(self) -> IdentityProviderInterface: ...

    @abstractmethod
    def get_payments(self) -> PaymentsInterface: ...

    @abstractmethod
    def get_slack(self) -> SlackInterface: ...

    @abstractmethod
    def describe(self) -> list[str]:
        """One line per chosen backend, logged once at boot."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
