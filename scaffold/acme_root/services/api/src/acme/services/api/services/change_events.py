"""The change events service: the tenant's append-only stream, paged by `after_seq`."""

from abc import ABC, abstractmethod

from acme.om.context import TenantContext
from acme.services.api.types.change_events import ChangeEventView


class ChangeEventsServiceInterface(ABC):
    @abstractmethod
    async def get_change_events(
        self, ctx: TenantContext, after_seq: int, limit: int
    ) -> list[ChangeEventView]: ...
