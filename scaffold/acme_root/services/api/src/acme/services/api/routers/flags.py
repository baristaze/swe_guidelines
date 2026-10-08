"""The session's flags: what a client reads to decide what it shows. The
answer carries an entity tag, so a client that asks again with it is
answered `304` until a value changes."""

from fastapi import APIRouter, Response

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.resolve import FlagsService
from acme.services.api.gateway.snapshot import NOT_MODIFIED, Snap
from acme.services.api.types.flags import FlagsView

router = APIRouter(prefix="/flags", tags=["flags"])


@router.get("", response_model=FlagsView, responses=NOT_MODIFIED)
async def get_flags(ctx: Ctx, flags: FlagsService, snapshot: Snap) -> Response:
    """The flags marked for clients, for the session's org and user. A
    client never decides what the server allows: an operation a flag gates
    is refused on the server whatever a client shows."""
    return await snapshot.answer(flags.get_flags(ctx))
