import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from acme.infra.observability import OUTCOMES
from acme.infra.topics import EntityChangedPayload, Topics, TopicsInterface, WorkAvailablePayload
from acme.om.base import EMPTY_UUID, Platform, derived_id, new_id, utcnow
from acme.om.context import Permission, RequestContext, TenantContext
from acme.om.events import EventsManagerInterface
from acme.om.events.manager import audit_event
from acme.om.exceptions import (
    InvalidCredential,
    LeaseLost,
    NotAuthorized,
    NotFound,
    UniqueKeyTaken,
    ValidationFailed,
)
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy import TenancyManagerInterface
from acme.om.work.manager import WorkManagerInterface
from acme.om.work.rules import attempts_after_hand_back, is_exhausted, retry_delay
from acme.om.work.storage import InsertOutcome, WorkStorageInterface
from acme.om.work.types.work_item import (
    WORK_ENQUEUE_PERMISSIONS,
    WORK_PAYLOADS,
    WORK_ROW_PREFIX,
    ScheduledPayload,
    WakeParkedPayload,
    WorkItem,
    WorkKind,
    WorkStatus,
    relayed_lane,
)

log = logging.getLogger(__name__)

DEAD_LETTER_KIND = "work.item.failed"
"""The audit event a dead letter leaves in the tenant's stream."""


class WorkOptions(Platform):
    base_retry_delay: timedelta = timedelta(seconds=30)
    max_retry_delay: timedelta = timedelta(minutes=15)
    stale_stagger: timedelta = timedelta(seconds=5)
    retention: timedelta = timedelta(days=30)  # a done or failed item is purged after this
    purge_batch: int = 1000  # items one purge statement deletes at most


def caused_by(rctx: RequestContext, item: WorkItem) -> RequestContext:
    """The request stage a claim runs under: the one the worker minted for this
    claim, now naming the request that caused the work, read off the item. The
    run keeps its own `request_id`, because it has its own lifetime and its own
    failures, and names the cause in a field of its own; neither is written
    over the other. An item that names no causing request leaves the field
    empty, the way a request that arrived at the edge does."""
    cause = item.request_id if item.request_id != EMPTY_UUID else None
    return rctx.model_copy(update={"caused_by_request_id": cause})


def not_before(kind: WorkKind, payload: object) -> datetime | None:
    """When work of this kind may run, read off its payload: the payload's
    `not_before` for a scheduled kind and for a wake that names a time, None
    for any other. A payload that does not parse answers None here; `_land`
    refuses it with the reason."""
    shape = WORK_PAYLOADS[kind]
    if not issubclass(shape, ScheduledPayload | WakeParkedPayload):
        return None
    try:
        return shape.model_validate(payload).not_before
    except ValidationError:
        return None


def relayed_key(kind: WorkKind, row: OutboxRow, now: datetime) -> UUID:
    """The idempotency key of the item a relayed row lands: the row's id, the
    same on every run of the relay, so a retry creates nothing twice. A wake
    of the org's records parked for one reason, at a time still to come, is
    keyed by the org, the reason, and the time instead: the parks that read
    one mark land one item, which resumes them staggered. A row whose time
    has passed keeps its own key and runs at once, since the item its time
    named may have run before its record parked."""
    if kind is not WorkKind.WAKE_PARKED:
        return row.id
    try:
        wake = WakeParkedPayload.model_validate(row.payload)
    except ValidationError:
        return row.id  # `_land` refuses it with the reason
    if wake.record_id is not None or wake.not_before is None or wake.not_before <= now:
        return row.id
    return derived_id(row.org_id, wake.not_before, f"{kind.value}:{wake.reason.value}")


class WorkManagerImpl(WorkManagerInterface):
    def __init__(
        self,
        storage: WorkStorageInterface,
        tenancy: TenancyManagerInterface,
        events: EventsManagerInterface,
        topics: TopicsInterface,
        options: WorkOptions,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._events = events
        self._topics = topics
        self._options = options

    async def enqueue(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        """The direct create, under a context: work a CLI, a sweep, or an app asks
        for on its own, which no core write announced. The actor is the
        context's, and the key and the lane are the caller's. The caller
        authorizes the whole run, which has the service role, so it holds the
        permission the kind is asked for with; a kind the table does not name
        is asked for by nobody."""
        asking = WORK_ENQUEUE_PERMISSIONS.get(item.kind)
        if asking is None:
            raise NotAuthorized(f"no permission asks for work of kind {item.kind.value}")
        ctx.require(asking)
        now = utcnow()
        queued = item.model_copy(
            update={
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "status": WorkStatus.QUEUED,
                "attempts": 0,
                "claimed_by": None,
                "claim_token": None,
                "lease_expires_at": None,
                "last_error": None,
            }
        )
        return await self._land(ctx.org_id, queued)

    async def enqueue_relayed(self, org_id: UUID, row: OutboxRow) -> WorkItem:
        """The enqueue of work a core write started: the relay makes it from the
        second outbox row of that write, which landed in the same statement as
        the entity's. No context, since the relay runs without a principal: the
        actor comes from the row, and so does the idempotency key, which is the
        same on every run of the relay (`relayed_key`). The request that
        caused the work and its trace context come from the row too, which
        names the request that made the write: the row is the whole handoff,
        so nothing here is minted afresh. A row carries no routing of its
        own, so the lane is the kind's (`relayed_lane`): its own in
        `WORK_LANES`, else the default one. A kind whose payload is a
        `ScheduledPayload`, and a wake that names a time, waits in the queue
        until its `not_before`."""
        kind = row.kind.removeprefix(WORK_ROW_PREFIX)
        if kind not in {k.value for k in WorkKind}:
            raise ValidationFailed(f"outbox row {row.id} asks for unknown work {row.kind}")
        now = utcnow()
        available_at = max(now, not_before(WorkKind(kind), row.payload) or now)
        return await self._land(
            org_id,
            WorkItem(
                id=new_id(),
                created_at=now,
                updated_at=now,
                created_by=row.actor_id,  # the principal of the write that asked
                updated_by=EMPTY_UUID,  # the machinery, from here on
                kind=WorkKind(kind),
                target_id=row.target_id,
                idempotency_key=relayed_key(WorkKind(kind), row, now),
                request_id=row.request_id,  # the request that made the write
                traceparent=row.traceparent,  # its trace context, for the run's link
                payload=row.payload,
                lane=relayed_lane(WorkKind(kind)),
                status=WorkStatus.QUEUED,
                available_at=available_at,
            ),
        )

    async def _land(self, org_id: UUID, queued: WorkItem) -> WorkItem:
        """The insert both enqueues share: the payload against the shape its kind
        fixes, the create, and the wake. Ids are minted above storage, so the
        only way to present one twice is a retry, and a retry must not create
        twice: the insert reports an id, or an idempotency key, already
        written and nothing changes, a claim on the row included, so the row
        as stored is the answer and it was announced when it landed."""
        try:
            WORK_PAYLOADS[queued.kind].model_validate(queued.payload)
        except ValidationError as error:
            raise ValidationFailed(f"payload of {queued.kind.value} work: {error}"[:500]) from None
        outcome = await self._storage.create_item(org_id, queued)
        if outcome is not InsertOutcome.INSERTED:
            return await self._stored(org_id, queued, outcome)
        await self._topics.publish(
            Topics.WORK_AVAILABLE,
            WorkAvailablePayload(
                idempotency_key=queued.idempotency_key,
                produced_at=queued.created_at,
                org_id=org_id,
                lane=queued.lane,
                kind=queued.kind.value,
            ),
        )
        return queued

    async def claim(
        self,
        rctx: RequestContext,
        lane: str,
        kinds: Sequence[WorkKind],
        worker_id: str,
        lease: timedelta,
        tenant_cap: int | None = None,
    ) -> tuple[TenantContext, WorkItem] | None:
        while True:
            found = await self._storage.claim_next(lane, kinds, worker_id, lease, tenant_cap)
            if found is None:
                return None
            org_id, item = found
            try:
                ctx = await self._tenancy.service_context(
                    caused_by(rctx, item), org_id, item.created_by
                )
            except InvalidCredential as error:
                # The claim is written and the tenant is gone: no retry can
                # bring it back, and a row left claimed would stay so, since
                # the worker that holds it has no context to settle it under.
                await self._fail_orphan(org_id, item, str(error))
                continue
            return ctx, item

    async def complete(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        return await self._transition(
            ctx,
            item,
            {
                "status": WorkStatus.DONE,
                "claimed_by": None,
                "claim_token": None,
                "lease_expires_at": None,
                "updated_at": utcnow(),
            },
        )

    async def fail(self, ctx: TenantContext, item: WorkItem, error: str) -> WorkItem:
        return await self._fail(ctx, item, error, is_exhausted(item))

    async def fail_for_good(self, ctx: TenantContext, item: WorkItem, error: str) -> WorkItem:
        return await self._fail(ctx, item, error, True)

    async def _fail(
        self, ctx: TenantContext, item: WorkItem, error: str, exhausted: bool
    ) -> WorkItem:
        """The failed run: a dead letter when `exhausted`, and otherwise back
        to the queue after the retry curve's delay."""
        now = utcnow()
        if exhausted:
            update: dict[str, Any] = {"status": WorkStatus.FAILED}
        else:
            delay = retry_delay(
                item.attempts, self._options.base_retry_delay, self._options.max_retry_delay
            )
            update = {"status": WorkStatus.QUEUED, "available_at": now + delay}
        failed = await self._transition(
            ctx,
            item,
            {
                **update,
                "claimed_by": None,
                "claim_token": None,
                "lease_expires_at": None,
                "last_error": error,
                "updated_at": now,
            },
        )
        if exhausted:
            await self._dead_letter(ctx, failed)
        return failed

    async def defer(self, ctx: TenantContext, item: WorkItem, delay: timedelta) -> WorkItem:
        return await self._hand_back(ctx, item, delay)

    async def release(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        return await self._hand_back(ctx, item, timedelta(0))

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        now = utcnow()
        return await self._transition(
            ctx, item, {"lease_expires_at": now + lease, "updated_at": now}
        )

    async def requeue_stale(self, rctx: RequestContext, limit: int) -> int:
        requeued = await self._storage.requeue_stale(
            utcnow(), self._options.stale_stagger, max(1, limit)
        )
        if requeued:
            log.info(
                "requeued %d stale work items in %d orgs",
                len(requeued),
                len({org_id for org_id, _ in requeued}),
            )
        for org_id, item in requeued:
            if item.status is WorkStatus.FAILED:
                await self._dead_letter_in(rctx, org_id, item)
        return len(requeued)

    async def purge_items(self) -> int:
        return await self._storage.purge_items(
            utcnow() - self._options.retention, self._options.purge_batch
        )

    async def oldest_ready_age(self) -> timedelta:
        now = utcnow()
        oldest = await self._storage.oldest_ready_at(now)
        return timedelta(0) if oldest is None else max(now - oldest, timedelta(0))

    async def failed_within(self, window: timedelta) -> int:
        return await self._storage.count_failed_since(utcnow() - window)

    async def maintenance_contexts(self, rctx: RequestContext) -> list[TenantContext]:
        return await self._tenancy.service_contexts(rctx)

    async def mark_purged(self, ctx: TenantContext) -> bool:
        return await self._tenancy.mark_purged(ctx)

    async def _stored(self, org_id: UUID, queued: WorkItem, outcome: InsertOutcome) -> WorkItem:
        """The row a reported create met, read back by the key that collided:
        by id on `ID_EXISTS`, and by idempotency key on `KEY_EXISTS`, since the
        row that holds the key carries another id. The key is unique per
        tenant, so a key that reads back nowhere went between the insert and
        the read, and the create is refused as a conflict."""
        if outcome is InsertOutcome.ID_EXISTS:
            existing = await self._storage.read_item(org_id, queued.id)
        else:
            existing = await self._storage.read_item_by_key(org_id, queued.idempotency_key)
        if existing is None:
            raise UniqueKeyTaken(f"idempotency key {queued.idempotency_key} was taken and is gone")
        return existing

    async def _hand_back(self, ctx: TenantContext, item: WorkItem, delay: timedelta) -> WorkItem:
        now = utcnow()
        return await self._transition(
            ctx,
            item,
            {
                "status": WorkStatus.QUEUED,
                "available_at": now + delay,
                "claimed_by": None,
                "claim_token": None,
                "lease_expires_at": None,
                "attempts": attempts_after_hand_back(item.attempts),
                "updated_at": now,
            },
        )

    async def _transition(
        self, ctx: TenantContext, item: WorkItem, update: dict[str, Any]
    ) -> WorkItem:
        """Confirms the item exists, is in this tenant, and is still claimed under
        the token the claim minted, then writes the transition conditionally on
        that token (`claim_token` in the statement itself). The token, not the
        worker's name, is the fence: one worker can hold one item twice across a
        requeue, and the first claim's copy must not settle the second. A worker
        whose item the sweep requeued after its lease passed is refused with
        LeaseLost, a Conflict, and spends no attempt; the lease's expiry alone
        is not checked here, the sweep's requeue is what takes the item away.

        The copy starts from the stored row, so what a worker sends back cannot
        rewrite who asked for the work, when it was asked for, or what it is;
        the note a hand-back carries is the one field the caller supplies. And
        every write here is the platform's, so it signs `updated_by` with
        EMPTY_UUID and never with `ctx.user_id`: the context the work runs
        under is the attribution of the work, never of the bookkeeping on its
        row."""
        ctx.require(Permission.WRITE)
        stored = await self._storage.read_item(ctx.org_id, item.id)
        if stored is None:
            raise NotFound(f"work item {item.id} not found")
        if (
            item.claim_token is None
            or stored.status is not WorkStatus.CLAIMED
            or stored.claim_token != item.claim_token
        ):
            raise LeaseLost(f"work item {item.id} is no longer held by {item.claimed_by}")
        moved = WorkItem.model_validate(
            {
                **stored.model_dump(),
                "last_error": item.last_error,
                **update,
                "updated_by": EMPTY_UUID,
            }
        )
        written = await self._storage.write_item_if_held(ctx.org_id, item.claim_token, moved)
        if written is None:
            raise LeaseLost(f"work item {item.id} was taken from {item.claimed_by} mid-write")
        return written

    async def _fail_orphan(self, org_id: UUID, item: WorkItem, reason: str) -> None:
        """Fails a claimed item whose tenant is gone, conditionally on the claim
        just written, as the system user. It is a dead letter without an audit
        event: the tenant's stream is not one to write into any more, so the
        log line and the counter are its record."""
        assert item.claim_token is not None
        now = utcnow()
        failed = item.model_copy(
            update={
                "status": WorkStatus.FAILED,
                "claimed_by": None,
                "claim_token": None,
                "lease_expires_at": None,
                "last_error": reason,
                "updated_at": now,
                "updated_by": EMPTY_UUID,
            }
        )
        if await self._storage.write_item_if_held(org_id, item.claim_token, failed) is None:
            return  # taken from under this claim meanwhile; whoever holds it settles it
        OUTCOMES.labels(subsystem="work", outcome="dead_letter").inc()
        log.error(
            "work item %s (%s) in org %s failed for good: %s",
            item.id,
            item.kind.value,
            org_id,
            reason,
        )

    async def _dead_letter_in(self, rctx: RequestContext, org_id: UUID, item: WorkItem) -> None:
        """A dead letter the sweep made across tenants: under the service
        context of the item's tenant, the system user its actor, as the
        sweep's context per tenant is. A tenant that is gone keeps no audit
        event, as `_fail_orphan` says, so it gets the log line and the
        counter alone."""
        try:
            ctx = await self._tenancy.service_context(rctx, org_id, EMPTY_UUID)
        except InvalidCredential:
            OUTCOMES.labels(subsystem="work", outcome="dead_letter").inc()
            log.error(
                "work item %s (%s) failed for good: %s",
                item.id,
                item.kind.value,
                item.last_error,
            )
            return
        await self._dead_letter(ctx, item)

    async def _dead_letter(self, ctx: TenantContext, item: WorkItem) -> None:
        """A failed item is a dead letter: an audit event names it in the tenant's
        stream and a metric counts it. The queue row is in the `queue` role and the
        outbox in `core`, so this write follows the transition directly; a crash
        between the two loses the audit entry, never the dead letter itself."""
        OUTCOMES.labels(subsystem="work", outcome="dead_letter").inc()
        log.error(
            "work item %s (%s) in org %s failed for good: %s",
            item.id,
            item.kind.value,
            ctx.org_id,
            item.last_error,
        )
        event = await self._events.append_event(
            ctx,
            audit_event(
                ctx,
                new_id(),
                DEAD_LETTER_KIND,
                item.id,
                {
                    "kind": item.kind.value,
                    "work_target_id": str(item.target_id),
                    "attempts": item.attempts,
                    "last_error": item.last_error,
                },
            ),
        )
        await self._topics.publish(
            Topics.ENTITY_CHANGED,
            EntityChangedPayload(
                idempotency_key=event.id,
                produced_at=event.produced_at,
                org_id=ctx.org_id,
                kind=event.kind,
                target_id=event.target_id,
                seq=event.seq,
                actor_id=event.actor_id,
            ),
        )
