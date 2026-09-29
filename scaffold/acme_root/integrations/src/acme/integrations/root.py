"""The integrations are fronted by one root, like infra: the container asks
it for what the managers need, and it has a lifecycle because a real client
holds connections."""

from abc import ABC, abstractmethod

from acme.integrations.identity import IdentityProviderInterface


class IntegrationsInterface(ABC):
    @abstractmethod
    def get_identity_provider(self) -> IdentityProviderInterface: ...

    @abstractmethod
    def describe(self) -> list[str]:
        """One line per chosen backend, logged once at boot."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
