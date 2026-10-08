"""The flags service: the session's flags that a client may read."""

from abc import ABC, abstractmethod

from acme.om.context import TenantContext
from acme.services.api.types.flags import FlagsView


class FlagsServiceInterface(ABC):
    @abstractmethod
    async def get_flags(self, ctx: TenantContext) -> FlagsView:
        """Every flag marked for clients, for the session's org and user."""
        ...
