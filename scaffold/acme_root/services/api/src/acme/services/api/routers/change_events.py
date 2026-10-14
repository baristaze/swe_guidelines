"""Change events: the org's diary after a sequence number. A client that
reconnects, or sees a gap on the channel, asks for everything after the
last sequence number it saw (`/v1/change-events`). The live channel is a
websocket opened with a single-use ticket (`/v1/realtime/tickets`, then
`/v1/realtime`). On the operator plane (`routers/tenancy.py`), an operator
reads an org's change events the same way, under the org named in the
path."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from acme.services.api.gateway.admin import OperatorCtx
from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.resolve import AdminService, ChangeEventsService
from acme.services.api.types.change_events import ChangeEventView, OperatorChangeEventView
from acme.services.api.types.common import LIMIT_DEFAULT

router = APIRouter(prefix="/change-events", tags=["change_events"])
operator = APIRouter(prefix="/admin", tags=["change_events"])


@router.get("", response_model=list[ChangeEventView])
async def list_change_events(
    ctx: Ctx,
    change_events: ChangeEventsService,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: int = LIMIT_DEFAULT,
) -> list[ChangeEventView]:
    return await change_events.get_change_events(ctx, after_seq, limit)


@operator.get("/orgs/{org_id}/change-events", response_model=list[OperatorChangeEventView])
async def operator_list_change_events(
    admin: OperatorCtx,
    service: AdminService,
    org_id: UUID,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: int = LIMIT_DEFAULT,
) -> list[OperatorChangeEventView]:
    return await service.get_change_events(admin, org_id, after_seq, limit)
