"""A publish the bus drops leaves its row pending. The event is in the stream,
but no socket was told, so the row is not done: the sweep publishes it again,
with the delay and the dead letter any failed row gets. The rows beside it
that were published are marked, and a work row is done once it is enqueued,
whatever became of its wake-up."""

from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from contracts.outbox_storage import a_user, claim_all, make_row

from acme.infra.breaker import Breaker
from acme.infra.observability import OUTCOMES
from acme.infra.topics import EntityChangedPayload, TopicPayload, Topics
from acme.infra.topics.breaker import TopicsBreakerImpl
from acme.infra.topics.memory import TopicsMemoryImpl
from acme.om.base import new_id
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.outbox.impl.relay import (
    DEAD_LETTER_KIND,
    UNPUBLISHED,
    OutboxOptions,
    OutboxRelayImpl,
)
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl

NO_GRACE = OutboxOptions(grace=timedelta(0), backoff_base=timedelta(0), max_attempts=3)


class Bus(TopicsMemoryImpl):
    """A bus that is up, down, or drops the messages of chosen targets, and
    records the changes it took."""

    def __init__(self) -> None:
        super().__init__()
        self.down = False
        self.drops: set[UUID] = set()
        self.took: list[UUID] = []

    async def publish(self, topic: Topics, payload: TopicPayload) -> bool:
        target = payload.target_id if isinstance(payload, EntityChangedPayload) else None
        if self.down or target in self.drops:
            return False
        if target is not None:
            self.took.append(target)
        return await super().publish(topic, payload)


class CountedMarks(OutboxStorageMemoryImpl):
    """Records the rows each call that marks rows done named."""

    def __init__(self) -> None:
        super().__init__()
        self.marks: list[set[UUID]] = []

    async def mark_done(self, org_id: UUID, row_ids: Sequence[UUID]) -> None:
        self.marks.append(set(row_ids))
        await super().mark_done(org_id, row_ids)


def publish_failures() -> float:
    return OUTCOMES.labels(subsystem="outbox", outcome="publish_failed")._value.get()


def dead_letters() -> float:
    return OUTCOMES.labels(subsystem="outbox", outcome="dead_letter")._value.get()


async def landed(outbox: OutboxStorageMemoryImpl, org: UUID, count: int) -> list[OutboxRow]:
    tenancy = TenancyStorageMemoryImpl(outbox)
    rows: list[OutboxRow] = []
    for _ in range(count):
        user = a_user()
        rows.append(make_row(org, user.id))
        await tenancy.write_user(org, user, (rows[-1],))
    return rows


def stored(outbox: OutboxStorageMemoryImpl) -> dict[UUID, OutboxRow]:
    return {row.id: row for _, row in outbox._rows.values()}


async def test_a_dropped_publish_leaves_the_row_pending_until_the_bus_takes_it() -> None:
    outbox, bus, org = CountedMarks(), Bus(), new_id()
    (row,) = await landed(outbox, org, 1)
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), bus, options=NO_GRACE)
    counted = publish_failures()
    bus.down = True

    assert not await relay.relay(org, row), "the request path says the relay failed"
    assert outbox.marks == [], "nothing was delivered, so nothing is marked"
    assert stored(outbox)[row.id].done_at is None
    assert publish_failures() == counted + 1

    # The sweep while the bus is still down: the row spends an attempt and
    # keeps the reason, as any failed row does.
    assert await relay.relay_pending(10) == 0
    waiting = stored(outbox)[row.id]
    assert waiting.done_at is None and waiting.failed_at is None and waiting.attempts == 1
    assert waiting.last_error == UNPUBLISHED
    assert publish_failures() == counted + 2

    # The bus is back: the next pass publishes the change and marks the row.
    bus.down = False
    assert await relay.relay_pending(10) == 1
    assert stored(outbox)[row.id].done_at is not None
    assert bus.took == [row.target_id]


async def test_a_batch_with_one_dropped_publish_marks_only_the_published_rows() -> None:
    """One mark for the rows the bus took; the dropped one stays pending,
    and the relay's answer says the batch did not all land."""
    outbox, bus, org = CountedMarks(), Bus(), new_id()
    rows = await landed(outbox, org, 3)
    bus.drops = {rows[1].target_id}
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), bus, options=NO_GRACE)
    counted = publish_failures()

    assert not await relay.relay_all(org, rows)
    assert outbox.marks == [{rows[0].id, rows[2].id}]
    assert publish_failures() == counted + 1
    assert [r.id for r in await claim_all(outbox)] == [rows[1].id]


async def test_the_sweep_marks_the_published_rows_of_a_batch_and_retries_the_dropped_one() -> None:
    outbox, bus, org = CountedMarks(), Bus(), new_id()
    rows = await landed(outbox, org, 3)
    bus.drops = {rows[2].target_id}
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), bus, options=NO_GRACE)

    assert await relay.relay_pending(10) == 2
    assert outbox.marks == [{rows[0].id, rows[1].id}], "one mark, no row-by-row retry"
    dropped = stored(outbox)[rows[2].id]
    assert dropped.done_at is None and dropped.attempts == 1
    assert dropped.last_error == UNPUBLISHED

    bus.drops = set()
    assert await relay.relay_pending(10) == 1
    assert all(stored(outbox)[row.id].done_at is not None for row in rows)


async def test_a_publish_dropped_on_every_attempt_is_a_dead_letter() -> None:
    """The bus that never comes back: the row is failed for good once its
    attempts are spent, counted, and named in the tenant's stream."""
    outbox, bus, org = CountedMarks(), Bus(), new_id()
    (row,) = await landed(outbox, org, 1)
    events = EventStorageMemoryImpl()
    relay = OutboxRelayImpl(outbox, events, bus, options=NO_GRACE)
    counted = dead_letters()
    bus.down = True
    for _ in range(NO_GRACE.max_attempts):
        assert await relay.relay_pending(10) == 0
    failed = stored(outbox)[row.id]
    assert failed.failed_at is not None and failed.last_error == UNPUBLISHED
    assert dead_letters() == counted + 1
    kinds = [e.kind for e in await events.read_after(org, 0, 10)]
    assert kinds == ["tenancy.user.created", DEAD_LETTER_KIND], "one event, appended once"


async def test_an_open_breaker_is_a_dropped_publish_and_not_a_delivered_one() -> None:
    outbox, org = CountedMarks(), new_id()
    (row,) = await landed(outbox, org, 1)
    breaker = Breaker("valkey_breaker", failures=1, cooldown=timedelta(hours=1), slow=timedelta(0))
    bus = TopicsBreakerImpl(TopicsMemoryImpl(), breaker)
    # One publish that spends the timeout opens it.
    await bus.publish(
        Topics.ENTITY_CHANGED,
        EntityChangedPayload(
            idempotency_key=new_id(),
            produced_at=row.created_at,
            org_id=org,
            kind=row.kind,
            target_id=row.target_id,
            seq=1,
        ),
    )
    assert breaker.is_open
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), bus, options=NO_GRACE)
    assert not await relay.relay(org, row)
    assert outbox.marks == []
    assert [r.id for r in await claim_all(outbox)] == [row.id]


async def test_a_held_request_whose_publish_is_dropped_leaves_its_rows_to_the_sweep() -> None:
    """The relay after the answer: the release publishes what the bus takes and
    leaves the rest pending, and the caller has its answer already."""
    outbox, bus, org = CountedMarks(), Bus(), new_id()
    request_id = new_id()
    rows = [
        row.model_copy(update={"request_id": request_id}) for row in await landed(outbox, org, 2)
    ]
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), bus, options=NO_GRACE)
    relay.hold(request_id)
    assert await relay.relay_all(org, rows), "held, not relayed"
    bus.down = True
    assert await relay.release(request_id) == 2
    assert outbox.marks == []
    bus.down = False
    assert await relay.relay_pending(10) == 2
    assert all(stored(outbox)[row.id].done_at is not None for row in rows)
