import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from acme.infra.observability import OUTCOMES, described, failure_level
from acme.infra.topics import EntityChangedPayload, Topics, TopicsInterface
from acme.om.base import Platform, new_id, utcnow
from acme.om.events.storage import EventStorageInterface
from acme.om.events.types.event import Event
from acme.om.outbox.relay import OutboxRelayInterface
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.work import WorkManagerInterface
from acme.om.work.types.work_item import asks_for_work

log = logging.getLogger(__name__)

DEAD_LETTER_KIND = "outbox.row.failed"
"""The audit event a row whose attempts are spent leaves in the tenant's stream."""

UNPUBLISHED = "the bus dropped the publish"
"""The error a row keeps when its event is in the stream and its message was dropped."""


class OutboxOptions(Platform):
    grace: timedelta = timedelta(seconds=10)
    """A row younger than this is the request path's to relay; the sweep leaves it."""
    backoff_base: timedelta = timedelta(seconds=30)
    backoff_cap: timedelta = timedelta(minutes=15)
    max_attempts: int = 10
    """Past this many sweep attempts the row is a dead letter."""


@dataclass
class _Hold:
    """One request's hold: how many holders it has (a caller may send one
    request id twice) and the rows handed over while it was open, with the
    org each was relayed under."""

    holders: int = 0
    rows: list[tuple[UUID, OutboxRow]] = field(default_factory=list)


class OutboxRelayImpl(OutboxRelayInterface):
    def __init__(
        self,
        storage: OutboxStorageInterface,
        events: EventStorageInterface,
        topics: TopicsInterface,
        work: Callable[[], WorkManagerInterface] | None = None,
        options: OutboxOptions | None = None,
    ) -> None:
        """`work` is a provider and not the manager itself: the work manager needs
        the tenancy manager, which needs this relay, so the root binds the edge
        at call time and hands back one whole graph. A relay built without one
        relays entity changes and refuses a row that asks for work."""
        self._storage = storage
        self._events = events
        self._topics = topics
        self._work = work
        self._options = options or OutboxOptions()
        self._holds: dict[UUID, _Hold] = {}

    async def relay(self, org_id: UUID, row: OutboxRow) -> bool:
        return await self.relay_all(org_id, (row,))

    async def relay_all(self, org_id: UUID, rows: Sequence[OutboxRow]) -> bool:
        if self._holds:
            for row in rows:
                if (hold := self._holds.get(row.request_id)) is not None:
                    hold.rows.append((org_id, row))
            rows = [row for row in rows if row.request_id not in self._holds]
        return await self._relay_now(org_id, rows)

    def hold(self, request_id: UUID) -> None:
        self._holds.setdefault(request_id, _Hold()).holders += 1

    def held(self, request_id: UUID) -> int:
        hold = self._holds.get(request_id)
        return 0 if hold is None else len(hold.rows)

    async def release(self, request_id: UUID) -> int:
        hold = self._holds.get(request_id)
        if hold is None:
            return 0
        held, hold.rows = hold.rows, []
        hold.holders -= 1
        if hold.holders == 0:
            del self._holds[request_id]
        by_org: dict[UUID, list[OutboxRow]] = {}
        for org_id, row in held:
            by_org.setdefault(org_id, []).append(row)
        left = len(held)
        try:
            for org_id, rows in by_org.items():
                await self._relay_now(org_id, rows)
                left -= len(rows)
        except asyncio.CancelledError:
            log.warning("%d outbox rows held for after the answer are left to the sweep", left)
            raise
        return len(held)

    def abandon(self, request_id: UUID) -> int:
        hold = self._holds.get(request_id)
        if hold is None:
            return 0
        hold.holders -= 1
        if hold.holders > 0:
            return 0  # the other holder relays them
        del self._holds[request_id]
        if hold.rows:
            log.warning(
                "%d outbox rows held for after the answer are left to the sweep", len(hold.rows)
            )
        return len(hold.rows)

    async def _relay_now(self, org_id: UUID, rows: Sequence[OutboxRow]) -> bool:
        if not rows:
            return True
        try:
            unpublished = await self._deliver_all(org_id, rows)
        except Exception as error:
            # The rows are durable; the sweep relays what is left. The request
            # that wrote them has already succeeded and is not failed for a bus
            # hiccup. A dependency that did not answer in time is a warning.
            log.log(
                failure_level(error),
                "outbox relay of %d rows from %s (%s) failed; the sweep retries",
                len(rows),
                rows[0].id,
                rows[0].kind,
                exc_info=error,
            )
            OUTCOMES.labels(subsystem="outbox", outcome="relay_failed").inc()
            return False
        OUTCOMES.labels(subsystem="outbox", outcome="relayed").inc(len(rows) - len(unpublished))
        return not unpublished

    async def relay_pending(self, limit: int) -> int:
        now = utcnow()
        options = self._options
        claimed = await self._storage.claim_pending(
            limit, now, options.grace, options.backoff_base, options.backoff_cap
        )
        by_tenant: dict[UUID, list[OutboxRow]] = {}
        for row in claimed:
            by_tenant.setdefault(row.org_id, []).append(row)
        relayed = 0
        for org_id, rows in by_tenant.items():
            relayed += await self._relay_claimed(org_id, rows, now)
        if relayed:
            log.info("outbox sweep relayed %d rows", relayed)
        return relayed

    async def _relay_claimed(self, org_id: UUID, rows: Sequence[OutboxRow], now: datetime) -> int:
        """One tenant's claimed rows, relayed together: one append, one mark.
        When that fails, each row is relayed alone, so the row that fails
        keeps its own error and delay and the rows beside it are relayed.
        Delivering again what the first try delivered is harmless: every step
        is idempotent on the row's id."""
        if len(rows) > 1:
            try:
                unpublished = await self._deliver_all(org_id, rows)
            except Exception:
                log.warning("outbox relay of %d rows of %s failed; one by one", len(rows), org_id)
            else:
                return await self._settled(org_id, rows, unpublished, now)
        relayed = 0
        for row in rows:
            try:
                unpublished = await self._deliver_all(org_id, (row,))
            except Exception as error:
                await self._failed(org_id, row, described(error)[:500], now)
                continue
            relayed += await self._settled(org_id, (row,), unpublished, now)
        return relayed

    async def _settled(
        self,
        org_id: UUID,
        rows: Sequence[OutboxRow],
        unpublished: Sequence[OutboxRow],
        now: datetime,
    ) -> int:
        """A delivery that came back: the rows it published are done, and each
        one whose message the bus dropped spends its attempt as any failure
        does. Returns how many were relayed."""
        for row in unpublished:
            await self._failed(org_id, row, UNPUBLISHED, now)
        relayed = len(rows) - len(unpublished)
        OUTCOMES.labels(subsystem="outbox", outcome="relayed").inc(relayed)
        return relayed

    async def oldest_pending_age(self) -> timedelta:
        oldest = await self._storage.oldest_pending_at()
        return timedelta(0) if oldest is None else max(utcnow() - oldest, timedelta(0))

    async def failed_within(self, window: timedelta) -> int:
        return await self._storage.count_failed_since(utcnow() - window)

    async def purge_done(self, retention: timedelta, limit: int) -> int:
        purged = await self._storage.purge_done(utcnow() - retention, limit)
        if purged:
            OUTCOMES.labels(subsystem="outbox", outcome="purged").inc(purged)
        return purged

    async def _deliver_all(self, org_id: UUID, rows: Sequence[OutboxRow]) -> list[OutboxRow]:
        """Each row's kind is its destination: an entity change becomes an event
        and an ENTITY_CHANGED publish, and a row of kind `work.<kind>`, the one
        a write that also starts work landed beside its entity's row, becomes a
        row in the queue and a WORK_AVAILABLE publish. The entity changes are
        appended in one call and published one by one, the work rows enqueued
        one by one, and then every row delivered is marked done in one
        statement.

        An entity change is delivered once the bus took its message. One the
        bus dropped (a refusal, or an open breaker) is not marked: it is
        returned, counted as `publish_failed`, and stays pending for the
        sweep, since its event is in the stream but no one was told. A work
        row is delivered once it is enqueued: the queue is its truth and its
        wake-up is a hint the workers' poll stands in for (ADR 0062).

        Raises on any other step, and every step is safe to run again: an
        event is idempotent on its row's id and so is the enqueue, which
        presents that id as the item's key. A raise leaves the whole batch
        for the sweep, never half of it marked."""
        changes = [row for row in rows if not asks_for_work(row.kind)]
        unpublished: list[OutboxRow] = []
        if changes:
            # An event's id is its row's id: the append is idempotent on it, so
            # a second relay of the same rows gets the same events back, same seqs.
            appended = await self._events.append_events(
                org_id, [self._event_of(row) for row in changes]
            )
            by_id = {row.id: row for row in changes}
            for event in appended:
                if not await self._publish(org_id, event):
                    unpublished.append(by_id[event.id])
        for row in rows:
            if asks_for_work(row.kind):
                await self._enqueue(org_id, row)
        if unpublished:
            log.warning(
                "the bus dropped %d of %d outbox rows of %s (first %s, %s); they stay pending",
                len(unpublished),
                len(rows),
                org_id,
                unpublished[0].id,
                unpublished[0].kind,
            )
            OUTCOMES.labels(subsystem="outbox", outcome="publish_failed").inc(len(unpublished))
        left = {row.id for row in unpublished}
        if delivered := [row.id for row in rows if row.id not in left]:
            await self._storage.mark_done(org_id, delivered)
        return unpublished

    @staticmethod
    def _event_of(row: OutboxRow) -> Event:
        return Event(
            id=row.id,
            org_id=row.org_id,
            kind=row.kind,
            target_id=row.target_id,
            payload=row.payload,
            produced_at=row.created_at,
            actor_id=row.actor_id,
            request_id=row.request_id,
            app=row.app,
        )

    async def _enqueue(self, org_id: UUID, row: OutboxRow) -> None:
        """The work item the row asks for, enqueued with no context: the relay has
        no principal, so the manager stamps the actor from the row."""
        if self._work is None:
            raise RuntimeError("this relay was built without a work manager to enqueue into")
        await self._work().enqueue_relayed(org_id, row)

    async def _failed(self, org_id: UUID, row: OutboxRow, error: str, now: datetime) -> None:
        """One sweep attempt failed: the claim already set the next attempt, so
        the row keeps its error and waits, and nothing behind it waits with it.
        Once the attempts are spent the row is a dead letter: failed for good,
        counted, and named by an audit event in the tenant's stream, best
        effort, since the stream may be what is failing."""
        OUTCOMES.labels(subsystem="outbox", outcome="relay_failed").inc()
        if row.attempts < self._options.max_attempts:
            log.warning(
                "outbox relay of %s (%s) failed on attempt %d: %s",
                row.id,
                row.kind,
                row.attempts,
                error,
            )
            await self._storage.record_failure(org_id, row.id, error, None)
            return
        log.error(
            "outbox row %s (%s) failed for good after %d attempts: %s",
            row.id,
            row.kind,
            row.attempts,
            error,
        )
        await self._storage.record_failure(org_id, row.id, error, now)
        OUTCOMES.labels(subsystem="outbox", outcome="dead_letter").inc()
        audit = Event(
            id=new_id(),
            org_id=row.org_id,
            kind=DEAD_LETTER_KIND,
            target_id=row.id,
            payload={
                "kind": row.kind,
                "row_target_id": str(row.target_id),
                "attempts": row.attempts,
                "last_error": error,
            },
            produced_at=now,
            actor_id=row.actor_id,
            request_id=row.request_id,
            app=row.app,
        )
        try:
            (event,) = await self._events.append_events(org_id, [audit])
            await self._publish(org_id, event)
        except Exception:
            log.exception("the dead letter audit event for %s could not be appended", row.id)

    async def _publish(self, org_id: UUID, appended: Event) -> bool:
        return await self._topics.publish(
            Topics.ENTITY_CHANGED,
            EntityChangedPayload(
                idempotency_key=appended.id,
                produced_at=appended.produced_at,
                org_id=org_id,
                kind=appended.kind,
                target_id=appended.target_id,
                seq=appended.seq,
                actor_id=appended.actor_id,
                version=version_of(appended),
            ),
        )


def version_of(event: Event) -> int | None:
    """The version the change wrote, which a versioned record's row names in
    its payload (`versioned_row`); None for every other row."""
    version = event.payload.get("version")
    return version if isinstance(version, int) and not isinstance(version, bool) else None
