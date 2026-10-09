"""The work swimlane: the table-backed queue that durable background work
rides, and the claim that produces the context the work runs under."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from acme.om.context import OperatorContext, RequestContext, TenantContext
from acme.om.outbox.types.row import OutboxRow
from acme.om.work.types.tenant_cap import TenantCap
from acme.om.work.types.work_item import WorkItem, WorkKind


class WorkManagerInterface(ABC):
    @abstractmethod
    async def enqueue(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        """A create: the copy stamps the actor, the timestamps, status QUEUED, and
        zero attempts, and clears every claim field, whatever the caller sent.
        The request that caused the work and its trace context are the item's,
        constructed by the caller from its own context, and the copy leaves
        them alone. An id already written returns the row as stored, so a
        retried enqueue never resets a claim, and so does a reused idempotency
        key: the insert reports which key collided and the manager reads the
        row back by it. Raises NotAuthorized when the context lacks the
        permission WORK_ENQUEUE_PERMISSIONS names for the kind, or the table
        names none, and ValidationFailed when the payload is not the shape
        WORK_PAYLOADS fixes for the kind."""
        ...

    @abstractmethod
    async def enqueue_relayed(self, org_id: UUID, row: OutboxRow) -> WorkItem:
        """Platform-internal: the enqueue of a work item that follows a core write,
        which the relay makes from the row of kind `work.<kind>` that rode that
        write. It takes no context, because the relay runs without a principal,
        and stamps the actor from the row, along with the request that made the
        write and that request's trace context, which the run names as its
        cause and links its spans to. The row's id is the item's
        `idempotency_key`, the same on every run of the relay, so a relay that
        runs twice and a caller that retries meet one row under one key. Raises
        ValidationFailed when the row names a kind this build does not know or
        carries a payload outside the shape WORK_PAYLOADS fixes for it."""
        ...

    @abstractmethod
    async def claim(
        self,
        rctx: RequestContext,
        lane: str,
        kinds: Sequence[WorkKind],
        worker_id: str,
        lease: timedelta,
        tenant_cap: int | None = None,
    ) -> tuple[TenantContext, WorkItem] | None:
        """Platform-internal: claims the oldest available item on the lane and rebuilds the
        enqueuer's principal under the service role, refining the request stage the
        worker minted for this claim; returns the context with the item. The
        stage it returns names the item's `request_id` as its
        `caused_by_request_id`, so the run names both the request it is and the
        request that caused it. An item
        whose tenant is gone cannot be run and cannot be retried into existence:
        it is failed in the same call, with the reason, and the claim moves on
        to the next item, so no row stays claimed with nobody to settle it.

        `tenant_cap` is the lane's cap on the items one tenant holds claimed
        on it under a live lease; None sets none. A tenant's own cap on the
        lane, which an operator sets, holds for that tenant in its place,
        on a lane with a cap or without one. The claim passes over a
        tenant at its cap and takes the oldest item of a tenant under it,
        so one tenant cannot hold every worker of a lane it shares. A tenant
        with neither cap is never passed over. A passed-over item is not
        written: it waits where it is, with its attempts, and the next claim
        after a slot frees takes it."""
        ...

    @abstractmethod
    async def complete(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        """Every transition of a claimed item (complete, fail, defer, release,
        extend_lease) raises NotFound when the item is gone and LeaseLost when it
        is no longer claimed by `item.claimed_by`; the write is conditional on
        the claim, so a lost lease is never written over."""
        ...

    @abstractmethod
    async def fail(self, ctx: TenantContext, item: WorkItem, error: str) -> WorkItem:
        """Requeues with a growing delay, or fails the item at max_attempts. A failed
        item is a dead letter: an audit event names it and a metric counts it."""
        ...

    @abstractmethod
    async def fail_for_good(self, ctx: TenantContext, item: WorkItem, error: str) -> WorkItem:
        """Fails the item at once, whatever attempts it has left: the handler
        said no retry changes the outcome. The same dead letter as an item
        whose attempts are spent: an audit event names it and a metric counts
        it. The attempt the claim spent stays spent."""
        ...

    @abstractmethod
    async def defer(self, ctx: TenantContext, item: WorkItem, delay: timedelta) -> WorkItem:
        """Hands the item back for later without spending an attempt."""
        ...

    @abstractmethod
    async def release(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        """Hands the item back now without spending an attempt."""
        ...

    @abstractmethod
    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        """Renews the lease; raises LeaseLost when the item is no longer this worker's."""
        ...

    @abstractmethod
    async def holds(self, ctx: TenantContext, idempotency_key: UUID, claim_token: UUID) -> bool:
        """Whether the tenant's item under this key is claimed under this
        token now: the fence every write to the item conditions on, read for
        a record that lets the worker running its item act on it, such as a
        lease whose job the item is. Reads only."""
        ...

    @abstractmethod
    async def requeue_stale(self, rctx: RequestContext, limit: int) -> int:
        """Platform-internal: the sweep, across tenants, like the claim: returns
        up to `limit` items whose lease expired to the queue, or fails them
        when their attempts are spent; returns how many it moved. `limit` is
        the batch; the sweep calls again while a batch comes back full. A
        failed item is a dead letter, as `fail` makes one, and its audit event
        is written under the service context of its tenant, minted from
        `rctx`; a tenant that is gone gets the log line and the counter
        only, as the claim's orphan does."""
        ...

    @abstractmethod
    async def purge_items(self) -> int:
        """Platform-internal: the sweep, across tenants, like the outbox's
        purge: deletes items done or failed past the retention, a batch at a
        time; returns how many. The one hard delete of an item. It takes no context,
        because it runs for no tenant and no principal."""
        ...

    @abstractmethod
    async def oldest_ready_age(self) -> timedelta:
        """Platform-internal: the sweep's backlog gauge, across tenants like
        the purge: how long the item ready longest has waited for a worker, on
        any lane; zero when nothing is ready. An item parked until later has
        not waited yet. Takes no context, because it reads for no tenant and
        no principal."""
        ...

    @abstractmethod
    async def failed_within(self, window: timedelta) -> int:
        """Platform-internal: the sweep's dead-letter gauge, across tenants
        like the purge: how many items failed in the last `window` and are
        still failed. Takes no context, for the same reason."""
        ...

    @abstractmethod
    async def maintenance_contexts(self, rctx: RequestContext) -> list[TenantContext]:
        """Platform-internal: the tenancy manager's service contexts (the system
        scope first, then every tenant), for the sweep, each refining the request
        stage the worker minted for this pass."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant deleted longer ago than the retention:
        the tenant's own caps, on every lane, a batch at most a call; returns
        how many went. Any other tenant returns 0 and reads nothing. Its
        items go by their own retention, across tenants."""
        ...

    @abstractmethod
    async def mark_purged(self, ctx: TenantContext) -> bool:
        """Platform-internal: the tenancy manager's `mark_purged`, for the sweep,
        once a pass found nothing left of the tenant to trim."""
        ...


class WorkOperatorManagerInterface(ABC):
    """The operator plane of the work queue: one named org's failed item,
    sent back to the queue, and one named org's own cap on a lane. Takes
    `OperatorContext` and nothing else."""

    @abstractmethod
    async def requeue(self, admin: OperatorContext, org_id: UUID, item_id: UUID) -> WorkItem:
        """Moves one failed item back to the queue, available now, with its
        attempts reset, so it runs as a fresh item would: every attempt, and
        the growing delay between them. The write is conditional on the item
        still being failed. An audit event in the org's diary names the item
        and the operator who requeued it. Requires the write permission.
        NotFound when the org or the item is not there; WorkNotFailed when
        the item is not failed."""
        ...

    @abstractmethod
    async def set_tenant_cap(
        self, admin: OperatorContext, org_id: UUID, lane: str, cap: int
    ) -> TenantCap:
        """The org's own cap on the lane: the most items it holds claimed
        there under a live lease, in place of the lane's cap, from the next
        claim on. Sets it, or writes it over the one there. Requires the
        write permission. NotFound when the org is not there or is deleted;
        ValidationFailed when the lane is empty or the cap is outside 1 to
        `MAX_CAP`."""
        ...

    @abstractmethod
    async def read_tenant_cap(self, admin: OperatorContext, org_id: UUID, lane: str) -> TenantCap:
        """The org's own cap on the lane. Requires the read permission.
        NotFound when the org is not there or has no cap of its own there."""
        ...

    @abstractmethod
    async def clear_tenant_cap(self, admin: OperatorContext, org_id: UUID, lane: str) -> TenantCap:
        """Removes the org's own cap on the lane, so the lane's cap holds for
        it from the next claim on, or none; returns the cap it removed.
        Requires the write permission. NotFound when the org is not there or
        has no cap of its own there."""
        ...
