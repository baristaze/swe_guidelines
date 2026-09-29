"""The events service: the tenant's append-only stream, paged by `after_seq`."""

from abc import ABC, abstractmethod

from acme.om.opcontext import OpContext
from acme.services.api.types.events import EventView


class EventsServiceInterface(ABC):
    @abstractmethod
    async def get_events(self, ctx: OpContext, after_seq: int, limit: int) -> list[EventView]: ...
