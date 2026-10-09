"""Lease routes: an ask, where a request stands, its cancel, a manager's
reorder, a resource's line, the history of leases, a lease, and its
renewal, its release, and a manager's revocation. Each function is one call into the leases service;
the ask runs under the idempotency record, whose id is the request's key."""

from uuid import UUID

from fastapi import APIRouter, Response

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.resolve import LeasesService
from acme.services.api.types.common import LIMIT_DEFAULT, ErrorResponse
from acme.services.api.types.leases import (
    AskRequest,
    LeaseEntryView,
    LeaseRequestView,
    LeaseView,
    LineView,
    RenewRequest,
    ReorderRequest,
    StandingView,
)

router = APIRouter(prefix="/leases", tags=["leases"])

ENDED: dict[int | str, dict[str, object]] = {
    409: {"model": ErrorResponse, "description": "lease_ended: the lease no longer runs"}
}
ASK_REFUSED: dict[int | str, dict[str, object]] = {
    403: {"model": ErrorResponse, "description": "not_authorized: the kind refuses this asker"}
}


@router.post("/requests", response_model=StandingView, status_code=201, responses=ASK_REFUSED)
async def ask(ctx: Ctx, leases: LeasesService, body: AskRequest, idem: Idem) -> Response:
    """Joins the line, last; granted at once only when no one waits in front.
    The resource's kind may refuse the ask first: 403 for who asks, 422 for
    what it asks. A replay answers what the first call saw: read the
    request for where it stands now."""
    return await idem.run(201, lambda attempt: leases.ask(ctx, body, attempt.target_id))


@router.get("/requests/{request_id}", response_model=StandingView)
async def get_request(ctx: Ctx, leases: LeasesService, request_id: UUID) -> StandingView:
    return await leases.get_request(ctx, request_id)


@router.post("/requests/{request_id}/cancel", response_model=LeaseRequestView)
async def cancel(ctx: Ctx, leases: LeasesService, request_id: UUID) -> LeaseRequestView:
    """Its asker's, or a manager's."""
    return await leases.cancel(ctx, request_id)


@router.post("/requests/{request_id}/reorder", response_model=LeaseRequestView)
async def reorder(
    ctx: Ctx, leases: LeasesService, request_id: UUID, body: ReorderRequest
) -> LeaseRequestView:
    """A manager's: the request moves in front of `before_id`, or to the end."""
    return await leases.reorder(ctx, request_id, body)


@router.get("/resources/{resource_id}/line", response_model=LineView)
async def line(ctx: Ctx, leases: LeasesService, resource_id: UUID) -> LineView:
    return await leases.line(ctx, resource_id)


@router.get("", response_model=list[LeaseEntryView])
async def list_leases(
    ctx: Ctx, leases: LeasesService, resource_id: UUID | None = None, limit: int = LIMIT_DEFAULT
) -> list[LeaseEntryView]:
    """The history: the org's leases, or one resource's, newest first, ended
    ones included, each with the request it answered; at most `limit`."""
    return await leases.list_leases(ctx, resource_id, limit)


@router.get("/{lease_id}", response_model=LeaseView)
async def get_lease(ctx: Ctx, leases: LeasesService, lease_id: UUID) -> LeaseView:
    return await leases.get_lease(ctx, lease_id)


@router.post("/{lease_id}/renew", response_model=LeaseView, responses=ENDED)
async def renew(
    ctx: Ctx, leases: LeasesService, lease_id: UUID, body: RenewRequest | None = None
) -> LeaseView:
    """Its holder's: the lease runs the seconds the body names from now, or
    its term again, within the resource's bound."""
    return await leases.renew(ctx, lease_id, body)


@router.post("/{lease_id}/release", response_model=LeaseView, responses=ENDED)
async def release(ctx: Ctx, leases: LeasesService, lease_id: UUID) -> LeaseView:
    """Its holder's: the resource goes to its line."""
    return await leases.release(ctx, lease_id)


@router.post("/{lease_id}/revoke", response_model=LeaseView, responses=ENDED)
async def revoke(ctx: Ctx, leases: LeasesService, lease_id: UUID) -> LeaseView:
    """A manager's: the lease ends and the resource goes to its line."""
    return await leases.revoke(ctx, lease_id)
