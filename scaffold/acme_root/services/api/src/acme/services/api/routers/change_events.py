"""The change event stream: a client that reconnects, or sees a gap on the channel,
asks for everything after the last sequence number it saw."""

from typing import Annotated

from fastapi import APIRouter, Query

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.resolve import ChangeEventsService
from acme.services.api.types.change_events import ChangeEventView
from acme.services.api.types.common import LIMIT_DEFAULT

router = APIRouter(prefix="/change-events", tags=["change_events"])


@router.get("", response_model=list[ChangeEventView])
async def list_change_events(
    ctx: Ctx,
    change_events: ChangeEventsService,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: int = LIMIT_DEFAULT,
) -> list[ChangeEventView]:
    return await change_events.get_change_events(ctx, after_seq, limit)
