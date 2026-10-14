from acme.om.change_events import ChangeEventsManagerInterface
from acme.om.context import TenantContext
from acme.services.api.services.change_events import ChangeEventsServiceInterface
from acme.services.api.types.change_events import ChangeEventView
from acme.services.api.types.common import clamp_limit


class ChangeEventsServiceImpl(ChangeEventsServiceInterface):
    def __init__(self, change_events: ChangeEventsManagerInterface) -> None:
        self._change_events = change_events

    async def get_change_events(
        self, ctx: TenantContext, after_seq: int, limit: int
    ) -> list[ChangeEventView]:
        events = await self._change_events.get_change_events(ctx, after_seq, clamp_limit(limit))
        return [ChangeEventView.model_validate(e) for e in events]
