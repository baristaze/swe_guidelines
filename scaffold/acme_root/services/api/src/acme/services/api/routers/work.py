"""Work: an org's work items and its own caps, on the operator plane.

An operator sends one of an org's failed work items back to the queue, and
sets, reads, or clears the org's own cap on a lane: the most items it holds
claimed there at once, in place of the lane's cap, `404` where it has none.
Each route is the operator plane's (`routers/tenancy.py`): an operator token
whose entry may read for a read and may write for a write, and one call
into the operator service."""

from uuid import UUID

from fastapi import APIRouter

from acme.services.api.gateway.admin import OperatorCtx
from acme.services.api.gateway.resolve import AdminService
from acme.services.api.types.work import OperatorWorkItemView, SetTenantCapRequest, TenantCapView

operator = APIRouter(prefix="/admin", tags=["work"])


@operator.post("/orgs/{org_id}/work/{item_id}/requeue", response_model=OperatorWorkItemView)
async def requeue_work(
    admin: OperatorCtx, service: AdminService, org_id: UUID, item_id: UUID
) -> OperatorWorkItemView:
    """Sends one failed work item back to the queue, available now, with its
    attempts reset; the org's diary names the operator who did. Refused
    with `409 work_not_failed` for an item that is not failed, so a second
    call finds the first one's work done and says so."""
    return await service.requeue_work(admin, org_id, item_id)


# A cap is set whole by a PUT, so a retry after a lost answer sets the same
# cap again and the route takes no Idempotency-Key.
@operator.put("/orgs/{org_id}/work/lanes/{lane}/cap", response_model=TenantCapView)
async def set_tenant_cap(
    admin: OperatorCtx,
    service: AdminService,
    org_id: UUID,
    lane: str,
    body: SetTenantCapRequest,
) -> TenantCapView:
    """Sets the org's own cap on the lane: the most items it holds claimed
    there at once, in place of the lane's cap, from the next claim on."""
    return await service.set_tenant_cap(admin, org_id, lane, body)


@operator.get("/orgs/{org_id}/work/lanes/{lane}/cap", response_model=TenantCapView)
async def get_tenant_cap(
    admin: OperatorCtx, service: AdminService, org_id: UUID, lane: str
) -> TenantCapView:
    """The org's own cap on the lane; `404` when it has none, and the lane's
    cap holds for it."""
    return await service.get_tenant_cap(admin, org_id, lane)


@operator.delete("/orgs/{org_id}/work/lanes/{lane}/cap", response_model=TenantCapView)
async def clear_tenant_cap(
    admin: OperatorCtx, service: AdminService, org_id: UUID, lane: str
) -> TenantCapView:
    """Removes the org's own cap on the lane and answers the cap it removed;
    the lane's cap holds for it from the next claim on. `404` when it has
    none."""
    return await service.clear_tenant_cap(admin, org_id, lane)
