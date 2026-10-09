"""The hooks of the kinds the mechanism ships: the `noop` resource kind, and
the orchestration as a waiter. A product registers its own beside them in
the root."""

from uuid import UUID

from acme.om.context import TenantContext
from acme.om.exceptions import NotFound
from acme.om.leases.hooks import ResourceKindInterface, WaiterInterface
from acme.om.leases.types.lease import Lease
from acme.om.leases.types.request import LeaseRequest
from acme.om.leases.types.resource import Resource
from acme.om.orchestrations import OrchestrationsManagerInterface
from acme.om.orchestrations.rules import is_settled
from acme.om.orchestrations.types.orchestration import ParkReason
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.work.types.work_item import WakeParkedPayload, WorkKind, work_row_kind


class NoopResourceKindImpl(ResourceKindInterface):
    """A grant starts nothing, and every request may be granted."""

    async def may_grant(
        self, ctx: TenantContext, resource: Resource, request: LeaseRequest
    ) -> bool:
        return True

    def grant_rows(
        self, ctx: TenantContext, resource: Resource, request: LeaseRequest, lease: Lease
    ) -> tuple[OutboxRow, ...]:
        return ()


class OrchestrationWaiterImpl(WaiterInterface):
    """A long-running record waits on its request parked on `resource`. It
    waits while it has not settled; the grant wakes it, and its next step
    asks again by its key and finds its lease. A revocation tells it
    nothing: its next renewal is refused, and its step stops there."""

    def __init__(self, orchestrations: OrchestrationsManagerInterface) -> None:
        self._orchestrations = orchestrations

    async def still_waits(self, ctx: TenantContext, waiter_id: UUID) -> bool:
        try:
            record = await self._orchestrations.get(ctx, waiter_id)
        except NotFound:
            return False
        return not is_settled(record)

    def wake_rows(self, ctx: TenantContext, waiter_id: UUID, lease: Lease) -> tuple[OutboxRow, ...]:
        payload = WakeParkedPayload(reason=ParkReason.RESOURCE, record_id=waiter_id)
        return (
            outbox_row(
                ctx,
                work_row_kind(WorkKind.WAKE_PARKED),
                ctx.org_id,
                payload.model_dump(mode="json"),
            ),
        )

    def revoke_rows(
        self, ctx: TenantContext, waiter_id: UUID, lease: Lease
    ) -> tuple[OutboxRow, ...]:
        return ()
