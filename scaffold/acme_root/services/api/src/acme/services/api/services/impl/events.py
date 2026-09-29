from acme.om.events import EventsManagerInterface
from acme.om.opcontext import OpContext
from acme.services.api.services.events import EventsServiceInterface
from acme.services.api.types.common import clamp_limit
from acme.services.api.types.events import EventView


class EventsServiceImpl(EventsServiceInterface):
    def __init__(self, events: EventsManagerInterface) -> None:
        self._events = events

    async def get_events(self, ctx: OpContext, after_seq: int, limit: int) -> list[EventView]:
        events = await self._events.get_events(ctx, after_seq, clamp_limit(limit))
        return [EventView.model_validate(e) for e in events]
