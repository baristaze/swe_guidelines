"""The handlers of the work kinds: NOOP, which keeps the loop honest, and
SYNC_SEATS, which holds a per-seat subscription's quantity to the org's
active members.

Each handler names the permissions its calls take (`REQUIRES`), and the
tests hold every role that may ask for the kind (`WORK_ENQUEUE_PERMISSIONS`)
to them: nobody reaches through the queue what they could not do directly."""

import logging
from typing import ClassVar

from acme.om.billing import BillingManagerInterface
from acme.om.opcontext import OpContext, Permission
from acme.om.tenancy import TenancyManagerInterface
from acme.om.work.types.handler import WorkHandlerInterface
from acme.om.work.types.work_item import WorkItem
from acme.workers.maintenance.providers import provider_calls

log = logging.getLogger(__name__)


class NoopHandlerImpl(WorkHandlerInterface):
    """Idempotent by construction: handling the same item twice changes nothing.
    It holds nothing per item: it runs for the life of the worker."""

    REQUIRES: ClassVar[tuple[Permission, ...]] = ()

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        log.info(
            "noop %s for %s in org %s (attempt %d)",
            item.id,
            item.target_id,
            ctx.org_id,
            item.attempts,
        )


class SyncSeatsHandlerImpl(WorkHandlerInterface):
    """Reads the org's active members when it runs, never when it was asked
    for, and holds the subscription to that count. Items that run late, twice,
    or out of order converge on the members the org has then; the processor
    call carries a key made of the item and the count, so a retried run is
    one change. A processor out of reach, or refusing the process's own
    key, parks the item; one that refuses the request fails it at once."""

    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.READ, Permission.MANAGE_MEMBERS)
    """`count_members` reads; `sync_seats` manages members."""

    def __init__(self, tenancy: TenancyManagerInterface, billing: BillingManagerInterface) -> None:
        self._tenancy = tenancy
        self._billing = billing

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        seats = await self._tenancy.count_members(ctx)
        async with provider_calls():
            await self._billing.sync_seats(ctx, seats, f"acme-seats-{item.id}-{seats}")
