"""The sweep's relay: a poison row stops nothing behind it, and a row whose
attempts are spent is a dead letter, failed for good, counted, and named by
an audit event."""

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.outbox_storage import claim_all, make_row
from contracts.task_storage import make_task
from opentelemetry.sdk.trace import TracerProvider

from acme.infra.impl.local import InfraLocalImpl
from acme.infra.observability import OUTCOMES, current_traceparent
from acme.infra.topics import EntityChangedPayload, TopicPayload, Topics
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.events.types.event import Event
from acme.om.opcontext import AppContext, AppType, OpContext, RequestContext
from acme.om.outbox.impl.relay import DEAD_LETTER_KIND, OutboxOptions, OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow, outbox_row, snapshot, versioned_row
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.work.types.work_item import WorkKind, work_row_kind

NO_GRACE = OutboxOptions(grace=timedelta(0), backoff_base=timedelta(0), max_attempts=2)


class PoisonedEvents(EventStorageMemoryImpl):
    """The append refuses one row's event, as a bad payload downstream would."""

    def __init__(self, poison_id: UUID) -> None:
        super().__init__()
        self.poison_id = poison_id

    async def append_events(self, org_id: UUID, events: Sequence[Event]) -> tuple[Event, ...]:
        if any(event.id == self.poison_id for event in events):
            raise RuntimeError("cannot append this one")
        return await super().append_events(org_id, events)


@pytest.fixture
def infra(tmp_path: Path) -> InfraLocalImpl:
    return InfraLocalImpl(tmp_path)


async def test_the_row_carries_the_trace_context_its_stage_carries(
    infra: InfraLocalImpl,
) -> None:
    """The row names the request that made the write and the trace context of
    that request, as the header the far side links to, both read off the
    stage. A span open when the row is written is not where it looks: the
    stage was minted with the trace context of its request, or with none when
    no tracer was configured, and the far side then starts its own trace."""
    managers = build_managers(StorageMemoryImpl(), infra, TenancyOptions(dev_sign_in=True))
    ctx = await sign_in(managers)
    task = make_task(created_by=ctx.user_id)
    tracer = TracerProvider().get_tracer("acme.om.tests")
    with tracer.start_as_current_span("POST /tasks") as span:
        assert outbox_row(ctx, "tasks.task.created", task.id, snapshot(task)).traceparent is None
        traced = ctx.model_copy(update={"traceparent": current_traceparent()})
    row = outbox_row(traced, "tasks.task.created", task.id, snapshot(task))
    assert row.request_id == ctx.request_id
    assert row.traceparent is not None and row.traceparent == traced.traceparent
    assert f"{span.get_span_context().trace_id:032x}" in row.traceparent


def dead_letters() -> float:
    return OUTCOMES.labels(subsystem="outbox", outcome="dead_letter")._value.get()


async def test_a_poison_row_does_not_block_the_rows_behind_it_and_dies_after_max_attempts(
    infra: InfraLocalImpl,
) -> None:
    outbox = OutboxStorageMemoryImpl()
    tasks = TasksStorageMemoryImpl(outbox)
    org = new_id()
    poison, fine = make_task(), make_task()
    poison_row, fine_row = make_row(org, poison.id), make_row(org, fine.id)
    await tasks.create_task(org, poison, (poison_row,))
    await tasks.create_task(org, fine, (fine_row,))
    events = PoisonedEvents(poison_row.id)
    relay = OutboxRelayImpl(outbox, events, infra.get_topics(), options=NO_GRACE)
    counted = dead_letters()

    # First sweep: the fine row is relayed although the poison row is older.
    assert await relay.relay_pending(10) == 1
    stored = {r.id: r for _, r in outbox._rows.values()}
    assert stored[fine_row.id].done_at is not None
    failed = stored[poison_row.id]
    assert failed.done_at is None and failed.failed_at is None and failed.attempts == 1
    assert failed.last_error == "RuntimeError: cannot append this one"
    assert dead_letters() == counted
    assert await relay.failed_within(timedelta(minutes=15)) == 0, "a retry is no dead letter"
    assert [e.kind for e in await events.read_after(org, 0, 10)] == ["tasks.task.created"]

    # Second sweep: the last attempt is spent; the row is a dead letter with
    # an audit event under the row's own provenance, and is never claimed again.
    assert await relay.relay_pending(10) == 0
    failed = {r.id: r for _, r in outbox._rows.values()}[poison_row.id]
    assert failed.failed_at is not None and failed.attempts == 2
    assert dead_letters() == counted + 1
    # The dead-letter gauge counts it for its window, where the lag no longer can.
    assert await relay.failed_within(timedelta(minutes=15)) == 1
    assert await relay.oldest_pending_age() == timedelta(0)
    audit = [e for e in await events.read_after(org, 0, 10) if e.kind == DEAD_LETTER_KIND]
    assert len(audit) == 1
    assert audit[0].target_id == poison_row.id and audit[0].actor_id == poison_row.actor_id
    assert audit[0].payload["attempts"] == 2 and audit[0].payload["kind"] == poison_row.kind
    assert await claim_all(outbox) == []
    assert await relay.relay_pending(10) == 0
    assert dead_letters() == counted + 1


async def test_the_sweep_leaves_a_row_younger_than_the_grace(infra: InfraLocalImpl) -> None:
    outbox = OutboxStorageMemoryImpl()
    tasks = TasksStorageMemoryImpl(outbox)
    org = new_id()
    task = make_task()
    row = make_row(org, task.id, age=timedelta(0))
    await tasks.create_task(org, task, (row,))
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics())
    assert await relay.relay_pending(10) == 0, "the request path relays a fresh row"
    assert await relay.relay(org, row)
    assert await claim_all(outbox) == []


async def test_purge_takes_done_and_failed_rows_past_the_retention(infra: InfraLocalImpl) -> None:
    outbox = OutboxStorageMemoryImpl()
    tasks = TasksStorageMemoryImpl(outbox)
    org = new_id()
    done, failed = make_task(), make_task()
    done_row, failed_row = make_row(org, done.id), make_row(org, failed.id)
    await tasks.create_task(org, done, (done_row,))
    await tasks.create_task(org, failed, (failed_row,))
    await outbox.mark_done(org, [done_row.id])
    await outbox.record_failure(org, failed_row.id, "for good", utcnow())
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics())
    assert await relay.purge_done(timedelta(hours=1), 1000) == 0
    assert await relay.purge_done(timedelta(seconds=-1), 1000) == 2


async def sign_in(managers: Managers) -> OpContext:
    """A tenant with a member, so a relayed work item has a principal to run
    under: the row names the actor and the claim rebuilds it."""
    tenancy = managers.tenancy
    app = AppContext(type=AppType.PORTAL, version="portal@test")

    def request() -> RequestContext:
        return RequestContext(request_id=new_id(), app=app)

    _, org = await tenancy.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await tenancy.dev_sign_in(request(), "ann@example.test")
    identity = await tenancy.authenticate_login(request(), login.token)
    issued = await tenancy.exchange_login(identity, org.id)
    return await tenancy.authenticate(request(), issued.token)


async def test_a_write_that_also_starts_work_rides_a_second_row_the_relay_enqueues(
    infra: InfraLocalImpl,
) -> None:
    """The write that also starts work, end to end. The entity change is one
    row; the work item rides a second row of kind `work.<kind>`, landed by the
    same statement, because the queue is a role of its own and no statement
    reaches both. The relay is what enqueues it, with no context and the actor
    from the row, and it presents the row's id as the item's idempotency key,
    which is the same on every run: a relay that runs twice leaves one item."""
    storage = StorageMemoryImpl()
    managers = build_managers(storage, infra, TenancyOptions(dev_sign_in=True))
    ctx = await sign_in(managers)
    woken: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        woken.append(payload)

    infra.get_topics().subscribe(Topics.WORK_AVAILABLE, "test", record)

    task = make_task(created_by=ctx.user_id)
    tracer = TracerProvider().get_tracer("acme.om.tests")
    with tracer.start_as_current_span("POST /tasks"):
        ctx = ctx.model_copy(update={"traceparent": current_traceparent()})
    change = outbox_row(ctx, "tasks.task.created", task.id, snapshot(task))
    asked = outbox_row(ctx, work_row_kind(WorkKind.NOOP), task.id, {})
    assert await storage.get_tasks_storage().create_task(ctx.org_id, task, (change, asked))
    # One statement, two rows: the entity's change and the work it starts.
    landed = await claim_all(storage.get_outbox_storage())
    assert sorted(row.id for row in landed) == sorted([change.id, asked.id])

    assert await managers.outbox.relay(ctx.org_id, change)
    assert await managers.outbox.relay(ctx.org_id, asked)
    enqueued = await storage.get_work_storage().read_item_by_key(ctx.org_id, asked.id)
    assert enqueued is not None
    assert enqueued.kind is WorkKind.NOOP and enqueued.target_id == task.id
    assert enqueued.created_by == ctx.user_id, "the actor of the write that asked"
    assert enqueued.updated_by == EMPTY_UUID
    # The handoff carries the request that made the write and its trace
    # context; the relay mints neither, it reads both off the row.
    assert enqueued.request_id == asked.request_id == ctx.request_id
    assert enqueued.traceparent == asked.traceparent is not None
    assert len(woken) == 1

    # The sweep relays what it finds, and it finds this row again after a
    # crash between the enqueue and the mark. The key is the row's id on
    # every run, so the second enqueue meets the item already there.
    assert await managers.outbox.relay(ctx.org_id, asked)
    assert await storage.get_work_storage().read_item_by_key(ctx.org_id, asked.id) == enqueued
    assert len(woken) == 1, "the queue is woken once"
    claimed = await managers.work.claim(
        RequestContext(request_id=new_id(), app=AppContext(type=AppType.WORKER, version="w@test")),
        "default",
        [WorkKind.NOOP],
        "w1",
        timedelta(seconds=30),
    )
    assert claimed is not None and claimed[1].id == enqueued.id
    assert claimed[0].user_id == ctx.user_id, "the work runs under the person who asked"
    assert claimed[0].request_id != ctx.request_id, "the run is a request of its own"
    assert claimed[0].caused_by_request_id == ctx.request_id, "and it names its cause"
    assert (
        await managers.work.claim(
            RequestContext(
                request_id=new_id(), app=AppContext(type=AppType.WORKER, version="w@test")
            ),
            "default",
            [WorkKind.NOOP],
            "w2",
            timedelta(seconds=30),
        )
        is None
    ), "one row, not two"


async def test_the_gauge_reads_the_oldest_row_still_pending(infra: InfraLocalImpl) -> None:
    """How long ago the oldest row neither done nor failed landed, a row
    waiting out its delay included; zero once every row is settled."""
    outbox = OutboxStorageMemoryImpl()
    tasks = TasksStorageMemoryImpl(outbox)
    org = new_id()
    poison, fine = make_task(), make_task()
    poison_row = make_row(org, poison.id, age=timedelta(minutes=7))
    fine_row = make_row(org, fine.id, age=timedelta(minutes=1))
    await tasks.create_task(org, poison, (poison_row,))
    await tasks.create_task(org, fine, (fine_row,))
    relay = OutboxRelayImpl(
        outbox, PoisonedEvents(poison_row.id), infra.get_topics(), options=NO_GRACE
    )
    assert timedelta(minutes=7) <= await relay.oldest_pending_age() < timedelta(minutes=8)
    assert await relay.relay_pending(10) == 1
    assert timedelta(minutes=7) <= await relay.oldest_pending_age() < timedelta(minutes=8)
    assert await relay.relay_pending(10) == 0  # the poison row's last attempt
    assert await relay.oldest_pending_age() == timedelta(0)


class CountedMarks(OutboxStorageMemoryImpl):
    """Counts the calls that mark rows done, and how many rows each named."""

    def __init__(self) -> None:
        super().__init__()
        self.marks: list[int] = []

    async def mark_done(self, org_id: UUID, row_ids: Sequence[UUID]) -> None:
        self.marks.append(len(row_ids))
        await super().mark_done(org_id, row_ids)


async def landed_rows(outbox: OutboxStorageMemoryImpl, org: UUID, count: int) -> list[OutboxRow]:
    tasks = TasksStorageMemoryImpl(outbox)
    rows: list[OutboxRow] = []
    for _ in range(count):
        task = make_task()
        rows.append(make_row(org, task.id))
        await tasks.create_task(org, task, (rows[-1],))
    return rows


async def test_a_batch_is_marked_done_in_one_call_and_relaying_it_again_changes_nothing(
    infra: InfraLocalImpl,
) -> None:
    outbox = CountedMarks()
    events = EventStorageMemoryImpl()
    org = new_id()
    rows = await landed_rows(outbox, org, 5)
    relay = OutboxRelayImpl(outbox, events, infra.get_topics())
    assert await relay.relay_all(org, rows)
    assert outbox.marks == [5], "one mark for the batch, not one a row"
    first = {r.id: r.done_at for _, r in outbox._rows.values()}
    assert all(first[row.id] is not None for row in rows)
    appended = await events.read_after(org, 0, 10)
    assert [e.id for e in appended] == [row.id for row in rows]

    # A second relay of the same rows, as the sweep makes after a crash
    # between the append and the mark: the same events, and the marks stand.
    assert await relay.relay_all(org, rows)
    assert await events.read_after(org, 0, 10) == appended
    assert {r.id: r.done_at for _, r in outbox._rows.values()} == first


async def test_a_row_settled_meanwhile_keeps_its_mark_and_the_batch_lands(
    infra: InfraLocalImpl,
) -> None:
    """The mark is conditional per row: a row the sweep relayed while this
    batch was on its way keeps the time it was marked, and a row failed for
    good stays failed and out of every claim."""
    outbox = CountedMarks()
    org = new_id()
    rows = await landed_rows(outbox, org, 3)
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics())
    await outbox.mark_done(org, [rows[0].id])
    marked = {r.id: r for _, r in outbox._rows.values()}[rows[0].id].done_at
    assert await relay.relay_all(org, rows)
    stored = {r.id: r for _, r in outbox._rows.values()}
    assert stored[rows[0].id].done_at == marked
    assert all(stored[row.id].done_at is not None for row in rows[1:])
    assert await claim_all(outbox) == []


async def test_a_failed_batch_marks_nothing_and_the_sweep_relays_it(
    infra: InfraLocalImpl,
) -> None:
    outbox = CountedMarks()
    org = new_id()
    rows = await landed_rows(outbox, org, 3)
    relay = OutboxRelayImpl(outbox, PoisonedEvents(rows[1].id), infra.get_topics())
    assert not await relay.relay_all(org, rows)
    assert outbox.marks == []
    assert {r.id for r in await claim_all(outbox)} == {row.id for row in rows}


async def test_the_sweep_relays_each_tenants_rows_together(infra: InfraLocalImpl) -> None:
    """One mark a tenant when its rows relay; when one of them fails, the
    rest of that tenant's rows relay one by one and the failing one keeps
    its own error and attempt, while the other tenant is untouched by it."""
    outbox = CountedMarks()
    ann, bob = new_id(), new_id()
    ann_rows = await landed_rows(outbox, ann, 3)
    bob_rows = await landed_rows(outbox, bob, 4)
    events = PoisonedEvents(ann_rows[1].id)
    relay = OutboxRelayImpl(outbox, events, infra.get_topics(), options=NO_GRACE)
    assert await relay.relay_pending(100) == 6
    assert sorted(outbox.marks) == [1, 1, 4], "bob's four at once; ann's two one by one"
    stored = {r.id: r for _, r in outbox._rows.values()}
    poison = stored[ann_rows[1].id]
    assert poison.done_at is None and poison.attempts == 1
    assert poison.last_error == "RuntimeError: cannot append this one"
    assert all(stored[row.id].done_at is not None for row in (*bob_rows, ann_rows[0], ann_rows[2]))
    assert len(await events.read_after(bob, 0, 10)) == 4


async def test_a_work_row_is_done_once_enqueued_though_its_wake_up_is_dropped(
    infra: InfraLocalImpl, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The queue is a work row's truth and the workers poll it, so a dropped
    wake-up costs latency and the row is done. The entity change beside it
    told no one, so it stays pending."""
    storage = StorageMemoryImpl()
    managers = build_managers(storage, infra, TenancyOptions(dev_sign_in=True))
    ctx = await sign_in(managers)
    task = make_task(created_by=ctx.user_id)
    change = outbox_row(ctx, "tasks.task.created", task.id, snapshot(task))
    asked = outbox_row(ctx, work_row_kind(WorkKind.NOOP), task.id, {})
    assert await storage.get_tasks_storage().create_task(ctx.org_id, task, (change, asked))

    async def dropped(topic: Topics, payload: TopicPayload) -> bool:
        return False

    monkeypatch.setattr(infra.get_topics(), "publish", dropped)
    assert not await managers.outbox.relay_all(ctx.org_id, (change, asked))
    assert await storage.get_work_storage().read_item_by_key(ctx.org_id, asked.id) is not None
    pending = await claim_all(storage.get_outbox_storage())
    assert [row.id for row in pending] == [change.id]


async def test_the_push_names_the_version_a_versioned_row_carries_and_none_otherwise(
    infra: InfraLocalImpl,
) -> None:
    """A versioned record's row names the version its change wrote, and the
    push carries it, so the tab that made the write reads nothing. Any other
    row, and a payload whose `version` is not a whole number, pushes none."""
    seen: list[EntityChangedPayload] = []

    async def record(payload: TopicPayload) -> None:
        assert isinstance(payload, EntityChangedPayload)
        seen.append(payload)

    infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", record)
    outbox = OutboxStorageMemoryImpl()
    tasks = TasksStorageMemoryImpl(outbox)
    managers = build_managers(StorageMemoryImpl(), infra, TenancyOptions(dev_sign_in=True))
    ctx = await sign_in(managers)
    rows = []
    for payload in ({"version": 4}, {}, {"version": "4"}, {"version": True}):
        task = make_task(created_by=ctx.user_id)
        row = outbox_row(ctx, "tasks.task.updated", task.id, payload)
        await tasks.create_task(ctx.org_id, task, (row,))
        rows.append(row)
    assert versioned_row(ctx, "tasks.task.updated", rows[0].target_id, 4).payload == {"version": 4}
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics())
    assert await relay.relay_all(ctx.org_id, rows)
    assert [(p.target_id, p.version) for p in seen] == [
        (rows[0].target_id, 4),
        (rows[1].target_id, None),
        (rows[2].target_id, None),
        (rows[3].target_id, None),
    ]
