import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import Span, TracerProvider
from worker_support import (
    RecordingHandler,
    build_container,
    ended,
    fast_options,
    make_item,
    on_the_test_clock,
    request,
    sign_in,
    until,
)

from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.observability import (
    OUTCOMES,
    caused_by_request_id_var,
    current_traceparent,
    request_id_var,
)
from acme.infra.topics import TopicPayload, Topics
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import RequestContext, TenantContext
from acme.om.events.impl.manager import EventsOptions
from acme.om.events.types.event import Event
from acme.om.exceptions import LeaseLost, Unavailable
from acme.om.idempotency.impl.manager import IdempotencyOptions
from acme.om.orchestrations.types.orchestration import Orchestration, OrchestrationKind
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.work import WorkManagerInterface
from acme.om.work.impl.manager import DEAD_LETTER_KIND, WorkOptions
from acme.om.work.types.handler import WorkHandlerInterface, WorkParked, WorkRefused
from acme.om.work.types.work_item import WorkItem, WorkKind, WorkStatus
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.loop import LoopOptions, WorkerLoop
from acme.workers.maintenance.main import unstaged
from acme.workers.maintenance.settings import MaintenanceSettings


class SlowHandler(WorkHandlerInterface):
    """Holds each item for `hold` seconds, and records on the loop's clock
    when each run started and when a cancel reached it."""

    def __init__(self, hold: float) -> None:
        self.hold = hold
        self.started: list[UUID] = []
        self.finished: list[UUID] = []
        self.cancelled: list[UUID] = []
        self.started_at: list[float] = []
        self.cancelled_at: list[float] = []
        self.request_ids: dict[UUID, str | None] = {}

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        clock = asyncio.get_running_loop().time
        self.started.append(item.id)
        self.started_at.append(clock())
        self.request_ids[item.id] = request_id_var.get()
        try:
            await asyncio.sleep(self.hold)
        except asyncio.CancelledError:
            self.cancelled.append(item.id)
            self.cancelled_at.append(clock())
            raise
        self.finished.append(item.id)


class RaisingHandler(WorkHandlerInterface):
    """Raises the one exception it was built with on every run, and counts
    the runs."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.runs = 0

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        self.runs += 1
        raise self.error


def outcome(subsystem: str, name: str) -> float:
    return OUTCOMES.labels(subsystem=subsystem, outcome=name)._value.get()


class CorrelationHandler(WorkHandlerInterface):
    """Records what a run knows about the request that caused it: the two ids on
    its context, the two its log lines carry, and the span it raised."""

    def __init__(self) -> None:
        self.contexts: list[TenantContext] = []
        self.ambient: list[tuple[str | None, str | None]] = []
        self.spans: list[trace.Span] = []

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        self.contexts.append(ctx)
        self.ambient.append((request_id_var.get(), caused_by_request_id_var.get()))
        self.spans.append(trace.get_current_span())


def ensure_tracer_provider() -> None:
    """The global provider can be set once per process; the tests share one
    that exports nowhere."""
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())


class LeaseLosingWork(WorkManagerInterface):
    """Decorates the real manager: every renewal fails as if another worker held the item.
    Each renewal asked for is recorded on the loop's clock."""

    def __init__(self, inner: WorkManagerInterface) -> None:
        self._inner = inner
        self.renewals: list[float] = []

    def _record_renewal(self) -> None:
        self.renewals.append(asyncio.get_running_loop().time())

    async def enqueue(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        return await self._inner.enqueue(ctx, item)

    async def enqueue_relayed(self, org_id: UUID, row: OutboxRow) -> WorkItem:
        return await self._inner.enqueue_relayed(org_id, row)

    async def claim(
        self,
        rctx: RequestContext,
        lane: str,
        kinds: Sequence[WorkKind],
        worker_id: str,
        lease: timedelta,
        tenant_cap: int | None = None,
    ) -> tuple[TenantContext, WorkItem] | None:
        return await self._inner.claim(rctx, lane, kinds, worker_id, lease, tenant_cap)

    async def complete(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        return await self._inner.complete(ctx, item)

    async def fail(self, ctx: TenantContext, item: WorkItem, error: str) -> WorkItem:
        return await self._inner.fail(ctx, item, error)

    async def fail_for_good(self, ctx: TenantContext, item: WorkItem, error: str) -> WorkItem:
        return await self._inner.fail_for_good(ctx, item, error)

    async def defer(self, ctx: TenantContext, item: WorkItem, delay: timedelta) -> WorkItem:
        return await self._inner.defer(ctx, item, delay)

    async def release(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        return await self._inner.release(ctx, item)

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        self._record_renewal()
        raise LeaseLost("held elsewhere")

    async def requeue_stale(self, rctx: RequestContext, limit: int) -> int:
        return await self._inner.requeue_stale(rctx, limit)

    async def purge_items(self) -> int:
        return await self._inner.purge_items()

    async def oldest_ready_age(self) -> timedelta:
        return await self._inner.oldest_ready_age()

    async def failed_within(self, window: timedelta) -> int:
        return await self._inner.failed_within(window)

    async def maintenance_contexts(self, rctx: RequestContext) -> list[TenantContext]:
        return await self._inner.maintenance_contexts(rctx)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        return await self._inner.purge_tenant(ctx)

    async def mark_purged(self, ctx: TenantContext) -> bool:
        return await self._inner.mark_purged(ctx)


class StallingWork(LeaseLosingWork):
    """Decorates the real manager: every renewal hangs, as an unreachable database behaves."""

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        self._record_renewal()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FailingWork(LeaseLosingWork):
    """Decorates the real manager: every renewal fails for a reason that says
    nothing about who holds the item, as an engine out of reach behaves."""

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        self._record_renewal()
        raise RuntimeError("engine out of reach")


class FailThenStallWork(LeaseLosingWork):
    """Decorates the real manager: the first renewal fails fast, every later one
    hangs, as an engine that refuses once and then stops answering behaves."""

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        self._record_renewal()
        if len(self.renewals) == 1:
            raise RuntimeError("engine out of reach")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class RenewingWork(LeaseLosingWork):
    """Decorates the real manager: every renewal goes through."""

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        self._record_renewal()
        return await self._inner.extend_lease(ctx, item, lease)


class FailingReleaseWork(LeaseLosingWork):
    """Decorates the real manager: renewals go through, but every release fails
    for a reason that says nothing about the lease, as a database down at
    shutdown behaves."""

    def __init__(self, inner: WorkManagerInterface) -> None:
        super().__init__(inner)
        self.releases = 0

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        return await self._inner.extend_lease(ctx, item, lease)

    async def release(self, ctx: TenantContext, item: WorkItem) -> WorkItem:
        self.releases += 1
        raise RuntimeError("engine out of reach")


class StopOnClaimWork(LeaseLosingWork):
    """Decorates the real manager: renewals go through, and `stop()` lands on
    the claim's way back, in the window between the row being marked CLAIMED
    and the loop creating the task that runs it."""

    def __init__(self, inner: WorkManagerInterface) -> None:
        super().__init__(inner)
        self.stop: Callable[[], None] = lambda: None

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        return await self._inner.extend_lease(ctx, item, lease)

    async def claim(
        self,
        rctx: RequestContext,
        lane: str,
        kinds: Sequence[WorkKind],
        worker_id: str,
        lease: timedelta,
        tenant_cap: int | None = None,
    ) -> tuple[TenantContext, WorkItem] | None:
        claimed = await self._inner.claim(rctx, lane, kinds, worker_id, lease, tenant_cap)
        if claimed is not None:
            self.stop()
        return claimed


class EnqueueDuringClaimWork(LeaseLosingWork):
    """Decorates the real manager: the claim finds an empty lane, and a producer
    inserts and announces an item while the claim is still on its way back.
    That is the window a Postgres snapshot plus a Valkey publish really open."""

    def __init__(self, inner: WorkManagerInterface, ctx: TenantContext, item: WorkItem) -> None:
        super().__init__(inner)
        self._pending: list[WorkItem] = [item]
        self._ctx = ctx

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        return await self._inner.extend_lease(ctx, item, lease)

    async def claim(
        self,
        rctx: RequestContext,
        lane: str,
        kinds: Sequence[WorkKind],
        worker_id: str,
        lease: timedelta,
        tenant_cap: int | None = None,
    ) -> tuple[TenantContext, WorkItem] | None:
        claimed = await self._inner.claim(rctx, lane, kinds, worker_id, lease, tenant_cap)
        if claimed is None and self._pending:
            await self._inner.enqueue(self._ctx, self._pending.pop())
        return claimed


class EmptyClaimCountingWork(LeaseLosingWork):
    """Decorates the real manager: renewals go through, and every claim that
    found the lane empty is counted, so a test knows the loop has gone back
    to waiting."""

    def __init__(self, inner: WorkManagerInterface) -> None:
        super().__init__(inner)
        self.empty_claims = 0

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        return await self._inner.extend_lease(ctx, item, lease)

    async def claim(
        self,
        rctx: RequestContext,
        lane: str,
        kinds: Sequence[WorkKind],
        worker_id: str,
        lease: timedelta,
        tenant_cap: int | None = None,
    ) -> tuple[TenantContext, WorkItem] | None:
        claimed = await self._inner.claim(rctx, lane, kinds, worker_id, lease, tenant_cap)
        if claimed is None:
            self.empty_claims += 1
        return claimed


class MissingLiveness(CacheInterface):
    """A liveness store whose writes never stick, as an unreachable backend behaves."""

    async def get(self, org_id: UUID, key: str) -> bytes | None:
        return None

    async def put(self, org_id: UUID, key: str, value: bytes, ttl: timedelta) -> None:
        return None

    async def invalidate(self, org_id: UUID, key: str) -> None:
        return None

    async def increment(self, org_id: UUID, key: str, ttl: timedelta) -> tuple[int, timedelta]:
        return 0, ttl

    def describe(self) -> str:
        return "cache[worker_liveness]=missing"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


class StallingLiveness(MissingLiveness):
    """A liveness store whose every call hangs, as an unreachable backend behaves
    before its socket times out."""

    async def get(self, org_id: UUID, key: str) -> bytes | None:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def put(self, org_id: UUID, key: str, value: bytes, ttl: timedelta) -> None:
        await asyncio.Event().wait()

    async def invalidate(self, org_id: UUID, key: str) -> None:
        await asyncio.Event().wait()


class RaisingLiveness(MissingLiveness):
    """A liveness store whose every call raises, as a backend that refuses connections behaves."""

    async def get(self, org_id: UUID, key: str) -> bytes | None:
        raise RuntimeError("connection refused")

    async def put(self, org_id: UUID, key: str, value: bytes, ttl: timedelta) -> None:
        raise RuntimeError("connection refused")

    async def invalidate(self, org_id: UUID, key: str) -> None:
        raise RuntimeError("connection refused")


def start_loop(
    container: WorkerContainer,
    handler: WorkHandlerInterface,
    options: LoopOptions,
    *,
    work: WorkManagerInterface | None = None,
    liveness: CacheInterface | None = None,
) -> tuple[WorkerLoop, asyncio.Task[None]]:
    loop = WorkerLoop(
        work=work or container.managers.work,
        outbox=container.managers.outbox,
        purges={
            "media": container.managers.media.purge_tenant,
            "tenancy": container.managers.tenancy.purge_tenant,
            "events": container.managers.events.purge_tenant,
        },
        across={
            "media": unstaged(container.managers.media.purge_across_tenants),
            "tenancy": unstaged(container.managers.tenancy.purge_across_tenants),
            "idempotency": unstaged(container.managers.idempotency.purge_across_tenants),
            "events": unstaged(container.managers.events.purge_across_tenants),
        },
        handlers={WorkKind.NOOP: handler},
        topics=container.infra.get_topics(),
        liveness=liveness or container.infra.get_cache(CacheScope.WORKER_LIVENESS),
        options=options,
    )
    return loop, asyncio.create_task(loop.run())


async def claim_all(outbox: OutboxStorageInterface) -> list[OutboxRow]:
    """What the sweep would claim now, with no grace and no delay after it."""
    zero = timedelta(0)
    return await outbox.claim_pending(100, utcnow(), zero, zero, zero)


# On the test clock the loop runs on its own defaults: a lease of a minute,
# renewed every twenty seconds and fenced at thirty. A minute passes there in
# no wall time, and the lease the storage stamps from the wall clock outlives
# any stall of the process. So a test that holds a claimed item until it stops
# the loop runs there: a stall during the drain neither expires the item nor
# moves the bound on the stop.
DEFAULTS = LoopOptions(worker_id="maintenance-test")


def after(start: float, times: Sequence[float]) -> list[float]:
    """Each of `times` on the loop's clock, in seconds after `start`, to the
    millisecond."""
    return [round(at - start, 3) for at in times]


@pytest.mark.parametrize(
    ("error", "level"),
    [
        (
            Unavailable("the database did not answer in time: a statement passed its deadline"),
            logging.WARNING,
        ),
        (RuntimeError("the claim is broken"), logging.ERROR),
    ],
    ids=["unavailable", "any-other"],
)
async def test_a_claim_that_fails_is_counted_and_logged_at_its_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    level: int,
) -> None:
    """The next poll claims again, so a claim that fails is counted and
    logged, and the loop goes on. A queue that did not answer in time is "not
    right now", a warning; any other failure is an error the tracker reports."""
    container = build_container(tmp_path)

    async def refused(*args: object) -> None:
        raise error

    monkeypatch.setattr(container.managers.work, "claim", refused)
    before = outcome("worker", "claim_error")
    with caplog.at_level(logging.WARNING, logger="acme.workers.maintenance.loop"):
        loop, task = start_loop(container, RecordingHandler(), fast_options())
        await until(lambda: outcome("worker", "claim_error") >= before + 2)
        loop.stop()
        await task
    claims = [r for r in caplog.records if r.getMessage() == "claim failed"]
    assert len(claims) >= 2
    assert {r.levelno for r in claims} == {level}


@on_the_test_clock
async def test_claims_within_capacity_and_completes(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    handler = SlowHandler(hold=0.15)
    loop, task = start_loop(container, handler, DEFAULTS.model_copy(update={"capacity": 1}))
    items = [make_item(ctx) for _ in range(3)]
    for item in items:
        await container.managers.work.enqueue(ctx, item)
    await until(lambda: len(handler.started) == 1)
    assert loop.running == 1
    await asyncio.sleep(0.05)
    assert len(handler.started) == 1, "capacity 1 admits one item at a time"
    await until(lambda: len(handler.finished) == 3)
    loop.stop()
    await task
    storage = container.storage.get_work_storage()
    for item in items:
        stored = await storage.read_item(ctx.org_id, item.id)
        assert stored is not None and stored.status is WorkStatus.DONE
    # Each run carried its own request id, the one the claim minted, in the
    # context variable the log filter reads.
    assert len(set(handler.request_ids.values())) == 3
    assert all(rid and rid != str(ctx.request_id) for rid in handler.request_ids.values())


async def test_a_run_names_the_request_that_caused_it_and_links_to_its_trace(
    tmp_path: Path,
) -> None:
    """The run is a request of its own that names the one that caused it, on its
    context and on every line it writes. The span it raises links to the
    causing trace instead of becoming its child: a durable queue holds an item
    well past the end of the request that filled it."""
    ensure_tracer_provider()
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    with trace.get_tracer("acme.workers.tests").start_as_current_span("POST /files") as causing:
        traceparent = current_traceparent()
        causing_trace_id = causing.get_span_context().trace_id
    item = make_item(ctx, traceparent=traceparent)
    await container.managers.work.enqueue(ctx, item)

    handler = CorrelationHandler()
    loop, task = start_loop(container, handler, fast_options())
    await until(lambda: len(handler.contexts) == 1)
    loop.stop()
    await task

    run = handler.contexts[0]
    assert run.request_id != ctx.request_id, "the run has a request of its own"
    assert run.caused_by_request_id == ctx.request_id, "and it names the one that caused it"
    assert handler.ambient[0] == (str(run.request_id), str(ctx.request_id)), "both reach the lines"
    span = handler.spans[0]
    assert isinstance(span, Span)
    assert span.parent is None, "one trace is not stretched over the queue"
    assert [link.context.trace_id for link in span.links or ()] == [causing_trace_id]
    assert span.get_span_context().trace_id != causing_trace_id, "a trace of its own, linked"
    # The run's stage carries the run's own trace context, so a row the run
    # lands hands its far side a trace to link to in turn.
    assert run.traceparent is not None
    assert run.traceparent.split("-")[1:3] == [
        f"{span.get_span_context().trace_id:032x}",
        f"{span.get_span_context().span_id:016x}",
    ]


async def test_a_run_of_work_nothing_asked_for_starts_its_own_trace(tmp_path: Path) -> None:
    """An item with no trace context on it, an enqueue that ran with no tracer
    configured: the run links to nothing and starts a trace of its own."""
    ensure_tracer_provider()
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    await container.managers.work.enqueue(ctx, make_item(ctx))
    handler = CorrelationHandler()
    loop, task = start_loop(container, handler, fast_options())
    await until(lambda: len(handler.contexts) == 1)
    loop.stop()
    await task
    span = handler.spans[0]
    assert isinstance(span, Span)
    assert not span.links and span.parent is None
    assert span.get_span_context().is_valid


async def test_a_finished_item_wakes_the_claimer(tmp_path: Path) -> None:
    # Every item is queued before the loop starts, so no announcement wakes it:
    # with one slot, the second and third claims happen only because a finished
    # item wakes the claimer, never because the poll interval (an hour) passed.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    items = [make_item(ctx) for _ in range(3)]
    for item in items:
        await container.managers.work.enqueue(ctx, item)
    handler = SlowHandler(hold=0.05)
    loop, task = start_loop(
        container, handler, fast_options(capacity=1, poll_interval=timedelta(hours=1))
    )
    await until(lambda: len(handler.finished) == 3)
    loop.stop()
    await task


async def run_once(
    tmp_path: Path, error: Exception
) -> tuple[WorkerContainer, TenantContext, WorkItem]:
    """One item, run once by a loop whose handler raises `error`, and read
    back once it has settled."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    item = await container.managers.work.enqueue(ctx, make_item(ctx))
    handler = RaisingHandler(error)
    loop, task = start_loop(container, handler, fast_options(capacity=1))
    storage = container.storage.get_work_storage()

    async def settled() -> WorkItem:
        stored = await storage.read_item(ctx.org_id, item.id)
        assert stored is not None
        return stored

    # Polled as `until` polls: a count of turns, never the wall clock.
    for _ in range(300):
        if handler.runs and (await settled()).status is not WorkStatus.CLAIMED:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("the item did not settle")
    loop.stop()
    await task
    assert handler.runs == 1
    return container, ctx, await settled()


async def test_a_refusal_fails_the_item_at_once_as_a_dead_letter(tmp_path: Path) -> None:
    """No retry changes a refusal: the one attempt the claim spent is the
    last, and the item is a dead letter with its audit event and its count."""
    dead, refused = outcome("work", "dead_letter"), outcome("worker", "refused")
    container, ctx, stored = await run_once(tmp_path, WorkRefused("the provider said no"))
    assert stored.status is WorkStatus.FAILED
    assert (stored.attempts, stored.max_attempts) == (1, 3)
    assert stored.last_error == "refused: the provider said no"
    events = await container.managers.events.get_events(ctx, after_seq=0, limit=10)
    assert [(e.kind, e.target_id) for e in events] == [(DEAD_LETTER_KIND, stored.id)]
    assert outcome("work", "dead_letter") == dead + 1
    assert outcome("worker", "refused") == refused + 1


async def test_a_park_hands_the_item_back_and_spends_no_attempt(tmp_path: Path) -> None:
    container, ctx, stored = await run_once(tmp_path, WorkParked("not yet", timedelta(minutes=5)))
    assert stored.status is WorkStatus.QUEUED and stored.attempts == 0
    assert stored.last_error == "parked: not yet"
    assert stored.available_at > utcnow() + timedelta(minutes=4)
    assert await container.managers.events.get_events(ctx, after_seq=0, limit=10) == []


async def test_any_other_error_requeues_the_item_with_a_delay(tmp_path: Path) -> None:
    container, ctx, stored = await run_once(tmp_path, RuntimeError("boom"))
    assert stored.status is WorkStatus.QUEUED and stored.attempts == 1
    assert stored.last_error == "RuntimeError", "the type alone; its text is not the platform's"
    assert stored.available_at > utcnow()
    assert await container.managers.events.get_events(ctx, after_seq=0, limit=10) == []


@on_the_test_clock
async def test_lease_is_renewed_while_an_item_runs(tmp_path: Path) -> None:
    # An item that runs for five sixths of the lease is renewed a third of the
    # lease in and a third after that, and is still this worker's to complete.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    lease = DEFAULTS.lease.total_seconds()
    handler = SlowHandler(hold=lease * 5 / 6)
    work = RenewingWork(container.managers.work)
    loop, task = start_loop(container, handler, DEFAULTS, work=work)
    item = make_item(ctx)
    await container.managers.work.enqueue(ctx, item)
    await until(lambda: len(handler.started) == 1)
    storage = container.storage.get_work_storage()
    first = await storage.read_item(ctx.org_id, item.id)
    assert first is not None and first.lease_expires_at is not None
    await asyncio.sleep(lease / 2)
    renewed = await storage.read_item(ctx.org_id, item.id)
    assert renewed is not None and renewed.lease_expires_at is not None
    assert renewed.lease_expires_at > first.lease_expires_at
    assert renewed.status is WorkStatus.CLAIMED
    await asyncio.sleep(lease / 2)
    assert handler.finished == [item.id]
    assert after(handler.started_at[0], work.renewals) == [lease / 3, lease * 2 / 3]
    done = await storage.read_item(ctx.org_id, item.id)
    assert done is not None and done.status is WorkStatus.DONE
    loop.stop()
    await task


@on_the_test_clock
async def test_a_lost_lease_cancels_the_task_at_once(tmp_path: Path) -> None:
    # A renewal refused with LeaseLost is definitive: another worker holds the
    # item, so the task is cancelled on the first refusal, a third of the lease
    # in, and not once half the lease has passed.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    lease = DEFAULTS.lease.total_seconds()
    handler = SlowHandler(hold=lease * 2)
    losing = LeaseLosingWork(container.managers.work)
    loop, task = start_loop(container, handler, DEFAULTS, work=losing)
    await container.managers.work.enqueue(ctx, make_item(ctx))
    await asyncio.sleep(lease)
    (started,) = handler.started_at
    assert after(started, losing.renewals) == [lease / 3], "the first refusal is the answer"
    assert handler.cancelled_at == losing.renewals, "at once: the clock did not move"
    assert handler.finished == []
    loop.stop()
    await task


@on_the_test_clock
async def test_any_other_renewal_failure_cancels_after_half_the_lease(tmp_path: Path) -> None:
    # A renewal that fails for any other reason says nothing about who holds
    # the item, so it is retried, halfway between the first attempt and the
    # fence; the task is cancelled once half the lease has passed without a
    # renewal, half the lease before it expires.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    lease = DEFAULTS.lease.total_seconds()
    handler = SlowHandler(hold=lease * 2)
    failing = FailingWork(container.managers.work)
    loop, task = start_loop(container, handler, DEFAULTS, work=failing)
    await container.managers.work.enqueue(ctx, make_item(ctx))
    await asyncio.sleep(lease)
    (started,) = handler.started_at
    third, half = lease / 3, lease / 2
    assert after(started, failing.renewals) == [third, (third + half) / 2], (
        "the first failure is retried once; the fence comes before a third"
    )
    assert after(started, handler.cancelled_at) == [half]
    assert handler.finished == []
    loop.stop()
    await task


@on_the_test_clock
async def test_a_stalled_renewal_counts_as_failed_and_cancels_in_time(tmp_path: Path) -> None:
    # The renewal a third of the lease in never answers. Its wait is bounded
    # by the time left to the fence, so the task is cancelled at half the
    # lease, well before the lease expires.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    lease = DEFAULTS.lease.total_seconds()
    handler = SlowHandler(hold=lease * 2)
    stalling = StallingWork(container.managers.work)
    loop, task = start_loop(container, handler, DEFAULTS, work=stalling)
    await container.managers.work.enqueue(ctx, make_item(ctx))
    await asyncio.sleep(lease)
    (started,) = handler.started_at
    assert after(started, stalling.renewals) == [lease / 3]
    assert after(started, handler.cancelled_at) == [lease / 2], "cancelled at the fence"
    assert handler.finished == []
    loop.stop()
    await task


@on_the_test_clock
async def test_a_renewal_that_stalls_after_a_failure_still_cancels_in_time(tmp_path: Path) -> None:
    # A fast failure a third of the lease in is retried; the retry stalls. Each
    # attempt is bounded by the time left to the fence at half the lease, so
    # the task is cancelled there, not when the lease has already expired.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    lease = DEFAULTS.lease.total_seconds()
    handler = SlowHandler(hold=lease * 2)
    work = FailThenStallWork(container.managers.work)
    loop, task = start_loop(container, handler, DEFAULTS, work=work)
    await container.managers.work.enqueue(ctx, make_item(ctx))
    await asyncio.sleep(lease)
    (started,) = handler.started_at
    third, half = lease / 3, lease / 2
    assert after(started, work.renewals) == [third, (third + half) / 2], (
        "the fast failure is retried once, the retry stalls"
    )
    assert after(started, handler.cancelled_at) == [half], "fenced at half the lease"
    assert handler.finished == []
    loop.stop()
    await task


async def test_sweep_requeues_stale_items_per_tenant(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    item = make_item(ctx)
    await container.managers.work.enqueue(ctx, item)
    lost = await container.managers.work.claim(
        request(), "default", [WorkKind.NOOP], "gone-worker", timedelta(seconds=-1)
    )
    assert lost is not None
    handler = RecordingHandler()
    loop, task = start_loop(container, handler, fast_options())
    await until(lambda: [h.id for h in handler.handled] == [item.id])
    loop.stop()
    await task
    stored = await container.storage.get_work_storage().read_item(ctx.org_id, item.id)
    assert stored is not None and stored.status is WorkStatus.DONE
    assert stored.attempts == 2, "the sweep kept the lost attempt; the rerun spent one more"


class RequeueRecordingWork(LeaseLosingWork):
    """Decorates the real manager: records the batch size every requeue
    passes and how many items each call moved."""

    def __init__(self, inner: WorkManagerInterface) -> None:
        super().__init__(inner)
        self.requeue_limits: list[int] = []
        self.requeued: list[int] = []

    async def extend_lease(self, ctx: TenantContext, item: WorkItem, lease: timedelta) -> WorkItem:
        return await self._inner.extend_lease(ctx, item, lease)

    async def requeue_stale(self, rctx: RequestContext, limit: int) -> int:
        self.requeue_limits.append(limit)
        moved = await self._inner.requeue_stale(rctx, limit)
        self.requeued.append(moved)
        return moved


async def test_one_pass_requeues_every_tenants_expired_leases_a_batch_at_a_time(
    tmp_path: Path,
) -> None:
    # Two tenants' items whose worker is gone. The requeue is one call across
    # tenants, made again while its batch comes back full, so the first pass
    # moves all three, a batch of one at a time, and the loop runs them.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    other = await sign_in(container, "beta")
    items = [make_item(ctx), make_item(other), make_item(ctx)]
    for owner, item in zip((ctx, other, ctx), items, strict=True):
        await container.managers.work.enqueue(owner, item)
        lost = await container.managers.work.claim(
            request(), "default", [WorkKind.NOOP], "gone-worker", timedelta(seconds=-1)
        )
        assert lost is not None
    work = RequeueRecordingWork(container.managers.work)
    handler = RecordingHandler()
    loop, task = start_loop(container, handler, fast_options(requeue_batch=1), work=work)
    await until(lambda: sorted(h.id for h in handler.handled) == sorted(i.id for i in items))
    loop.stop()
    await task
    assert work.requeued[:4] == [1, 1, 1, 0], "the first pass took all three, one by one"
    assert set(work.requeue_limits) == {1}


async def test_a_departed_members_queued_item_still_runs(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    item = make_item(ctx)
    await container.managers.work.enqueue(ctx, item)
    tenancy_storage = container.storage.get_tenancy_storage()
    ann = await tenancy_storage.read_user(ctx.org_id, ctx.user_id)
    assert ann is not None
    now = utcnow()
    await tenancy_storage.write_user(
        ctx.org_id,
        ann.model_copy(update={"deleted_at": now, "deleted_by": ann.id, "updated_at": now}),
    )

    handler = SlowHandler(hold=0)
    loop, task = start_loop(container, handler, fast_options())
    await until(lambda: handler.finished == [item.id])
    loop.stop()
    await task
    stored = await container.storage.get_work_storage().read_item(ctx.org_id, item.id)
    assert stored is not None and stored.status is WorkStatus.DONE
    assert stored.created_by == ctx.user_id, "the attribution survived the hop"


async def test_sweep_reaches_a_tenant_whose_members_have_all_left(tmp_path: Path) -> None:
    # The stale item sits on a lane this loop never claims from, so what moves
    # it is the sweep alone; the tenant's only member is gone by then.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    item = make_item(ctx).model_copy(update={"lane": "other"})
    await container.managers.work.enqueue(ctx, item)
    lost = await container.managers.work.claim(
        request(), "other", [WorkKind.NOOP], "gone-worker", timedelta(seconds=-1)
    )
    assert lost is not None
    tenancy_storage = container.storage.get_tenancy_storage()
    ann = await tenancy_storage.read_user(ctx.org_id, ctx.user_id)
    assert ann is not None
    now = utcnow()
    await tenancy_storage.write_user(
        ctx.org_id,
        ann.model_copy(update={"deleted_at": now, "deleted_by": ann.id, "updated_at": now}),
    )

    loop, task = start_loop(container, RecordingHandler(), fast_options())
    await until(lambda: loop.sweeps >= 1)
    loop.stop()
    await task
    stored = await container.storage.get_work_storage().read_item(ctx.org_id, item.id)
    assert stored is not None and stored.status is WorkStatus.QUEUED
    assert stored.claimed_by is None and stored.updated_by == EMPTY_UUID


async def test_sweep_relays_the_outbox_and_purges_done_rows(tmp_path: Path) -> None:
    # A row a crash left behind: written with its core row, never relayed.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    now = utcnow()
    record = Orchestration(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        kind=OrchestrationKind.NOOP,
    )
    row = outbox_row(ctx, "orchestrations.orchestration.created", record.id, {}).model_copy(
        update={"created_at": now - timedelta(minutes=1)}  # older than the relay's grace
    )
    await container.storage.get_orchestrations_storage().create_orchestration(
        ctx.org_id, record, (row,)
    )
    outbox = container.storage.get_outbox_storage()
    assert [r.id for r in await claim_all(outbox)] == [row.id]
    loop, task = start_loop(
        container, RecordingHandler(), fast_options(outbox_retention=timedelta(0))
    )
    await until(lambda: loop.sweeps >= 2)
    loop.stop()
    await task
    assert await claim_all(outbox) == [], "the sweep relayed the row"
    events = await container.managers.events.get_events(ctx, after_seq=0, limit=10)
    assert [(e.id, e.kind, e.target_id) for e in events] == [(row.id, row.kind, record.id)]
    # With no retention the second sweep purged the done row: nothing pending,
    # nothing done, and the relay of a purged row is never asked for.
    assert await outbox.purge_done(utcnow(), 1000) == 0


async def test_a_lost_lease_is_never_written_over(tmp_path: Path) -> None:
    # The second fence: the handler finishes before any renewal runs (the
    # lease is long), and the completion itself is refused because the claim
    # is no longer this worker's.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    handler = SlowHandler(hold=0.3)
    loop, task = start_loop(
        container,
        handler,
        fast_options(sweep_interval=timedelta(hours=1), lease=timedelta(seconds=3)),
    )
    item = make_item(ctx)
    await container.managers.work.enqueue(ctx, item)
    await until(lambda: len(handler.started) == 1)
    storage = container.storage.get_work_storage()
    held = await storage.read_item(ctx.org_id, item.id)
    assert held is not None and held.status is WorkStatus.CLAIMED
    # The sweep deems the lease expired (its clock runs an hour ahead) and
    # hands the item back before this worker's handler finishes.
    requeued = await storage.requeue_stale(utcnow() + timedelta(hours=1), timedelta(0), limit=10)
    assert [(org_id, r.id) for org_id, r in requeued] == [(ctx.org_id, item.id)]
    await until(lambda: len(handler.finished) == 1)
    await until(lambda: loop.running == 0)
    stored = await storage.read_item(ctx.org_id, item.id)
    assert stored == requeued[0][1], "complete() saw the lease was lost and wrote nothing"
    loop.stop()
    await task


@pytest.mark.parametrize(
    "liveness",
    [MissingLiveness(), StallingLiveness(), RaisingLiveness()],
    ids=["missing", "stalling", "raising"],
)
@on_the_test_clock
async def test_a_cache_outage_neither_stops_claiming_nor_fails_liveness(
    tmp_path: Path, liveness: CacheInterface
) -> None:
    # The queue and its leases live in the database, so a liveness store that
    # drops writes, hangs, or refuses connections leaves the worker unpublished
    # and nothing else: it claims, and its probe stays healthy. Each beat is
    # bounded by its interval, so the stop still returns although marking
    # offline hangs too.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    handler = RecordingHandler()
    options = fast_options()
    loop, task = start_loop(container, handler, options, liveness=liveness)
    await container.managers.work.enqueue(ctx, make_item(ctx))
    await until(lambda: len(handler.handled) == 1)
    await asyncio.sleep((options.heartbeat_interval * 4).total_seconds())
    assert loop.alive(), "the probe follows the loop's own beat, not the cache"
    assert loop.online is False, "nothing was published"
    loop.stop()
    await asyncio.wait_for(task, 2.0)
    await asyncio.wait_for(loop.wait_drained(), 1.0)


async def test_liveness_fails_once_the_heartbeat_stops(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    await sign_in(container)
    options = fast_options()
    loop, task = start_loop(container, RecordingHandler(), options)
    await until(loop.alive)
    heartbeat = next(t for t in asyncio.all_tasks() if t.get_name() == "heartbeat")
    heartbeat.cancel()
    await until(lambda: not loop.alive())
    loop.stop()
    await ended(task)


@on_the_test_clock
async def test_stop_drains_first_and_goes_offline_last(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    handler = SlowHandler(hold=5.0)
    loop, task = start_loop(container, handler, DEFAULTS)
    liveness = container.infra.get_cache(CacheScope.WORKER_LIVENESS)
    item = make_item(ctx)
    await container.managers.work.enqueue(ctx, item)
    await until(lambda: len(handler.started) == 1)
    await until(lambda: loop.online)
    assert await liveness.get(EMPTY_UUID, "worker:maintenance-test") == b"online"
    loop.stop()
    await task
    assert handler.cancelled == [item.id]
    stored = await container.storage.get_work_storage().read_item(ctx.org_id, item.id)
    assert stored is not None
    assert stored.status is WorkStatus.QUEUED
    assert stored.claimed_by is None
    assert stored.attempts == 0
    assert stored.last_error == "returned: worker stopping"
    assert await liveness.get(EMPTY_UUID, "worker:maintenance-test") is None
    assert loop.sweeps >= 1


@on_the_test_clock
async def test_stop_goes_offline_even_when_a_release_fails(tmp_path: Path) -> None:
    # The database is down at shutdown: returning the item fails with an error
    # that says nothing about the lease. The drain still awaits every item,
    # the heartbeat and the sweep still end, the worker still goes offline,
    # and `run()` still returns.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    handler = SlowHandler(hold=5.0)
    work = FailingReleaseWork(container.managers.work)
    loop, task = start_loop(container, handler, DEFAULTS, work=work)
    liveness = container.infra.get_cache(CacheScope.WORKER_LIVENESS)
    items = [make_item(ctx), make_item(ctx)]
    for item in items:
        await container.managers.work.enqueue(ctx, item)
    await until(lambda: len(handler.started) == 2)
    await until(lambda: loop.online)
    loop.stop()
    await asyncio.wait_for(task, 2.0)
    await asyncio.wait_for(loop.wait_drained(), 1.0)
    assert sorted(handler.cancelled) == sorted(item.id for item in items)
    assert work.releases == 2, "every item was awaited, the first failure stopped nothing"
    assert loop.running == 0
    assert await liveness.get(EMPTY_UUID, "worker:maintenance-test") is None
    assert loop.online is False
    names = {t.get_name() for t in asyncio.all_tasks()}
    assert not names & {"heartbeat", "sweep"}, "the timers were cancelled"


def test_outbox_retention_outlives_the_database_backup_retention() -> None:
    """A role restored to an earlier point than its siblings is reconciled by
    relaying the outbox again, so done rows must survive as long as a backup can
    be old. The bound is the database module's `backup_retention_days`."""
    variables = (
        Path(__file__).resolve().parents[3] / "deployment/terraform/modules/database/variables.tf"
    ).read_text()
    block = variables.split('variable "backup_retention_days"', 1)[1].split("}", 1)[0]
    match = re.search(r"default\s*=\s*(\d+)", block)
    assert match is not None
    backup_days = int(match.group(1))
    assert LoopOptions(worker_id="w").outbox_retention > timedelta(days=backup_days)
    setting = MaintenanceSettings.model_fields["outbox_retention_days"].default
    assert timedelta(days=setting) > timedelta(days=backup_days)


async def test_sweep_purges_settled_work_items_and_finished_idempotency_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    monkeypatch.setattr(container.managers.work, "_options", WorkOptions(retention=timedelta(0)))
    monkeypatch.setattr(
        container.managers.idempotency, "_options", IdempotencyOptions(retention=timedelta(0))
    )
    item = make_item(ctx)
    await container.managers.work.enqueue(ctx, item)
    begun = await container.managers.idempotency.begin(ctx, "k", "d", new_id())
    assert begun.attempt_id is not None
    await container.managers.idempotency.finish(ctx, "k", begun.attempt_id, 201, "{}")
    handler = RecordingHandler()
    loop, task = start_loop(container, handler, fast_options())
    await until(lambda: [h.id for h in handler.handled] == [item.id])
    sweeps = loop.sweeps
    await until(lambda: loop.sweeps >= sweeps + 2)
    loop.stop()
    await task
    assert await container.storage.get_work_storage().read_item(ctx.org_id, item.id) is None
    idempotency = container.storage.get_idempotency_storage()
    assert await idempotency.read_record(ctx.org_id, ctx.user_id, "k") is None


async def test_the_sweep_trims_a_living_stream_a_bounded_batch_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pass trims a batch at a time from the bottom of every org's stream,
    again while the batch comes back full, and moves each floor; once
    nothing is past the retention, a pass trims nothing and the floor stays
    where it is."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    storage = container.storage.get_event_storage()
    aged = utcnow() - timedelta(days=100)
    for _ in range(5):
        await storage.append_events(ctx.org_id, [aged_event(ctx, aged)])
    options = EventsOptions(retention=timedelta(days=90), purge_batch=2)
    monkeypatch.setattr(container.managers.events, "_options", options)
    trims: list[int] = []
    trim = storage.trim

    async def counted(before: datetime, limit: int) -> int:
        trimmed = await trim(before, limit)
        trims.append(trimmed)
        return trimmed

    monkeypatch.setattr(storage, "trim", counted)
    loop, task = start_loop(container, RecordingHandler(), fast_options())
    await until(lambda: len(trims) >= 5)
    loop.stop()
    await task
    assert trims[:3] == [2, 2, 1], "a batch a call, from the bottom, while one comes back full"
    assert set(trims[3:]) == {0}, "nothing left past the retention: the pass is a no-op"
    assert await storage.read_floor(ctx.org_id) == 5
    head = await storage.read_head(ctx.org_id)
    kept = await storage.read_after(ctx.org_id, 5, 100)
    assert [e.seq for e in kept] == list(range(6, head + 1)), "whole above the floor"


def aged_event(ctx: TenantContext, produced_at: datetime) -> Event:
    return Event(
        id=new_id(),
        org_id=ctx.org_id,
        kind="tenancy.test.noted",
        target_id=new_id(),
        produced_at=produced_at,
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
        app="portal",
    )


@on_the_test_clock
async def test_stop_during_a_claim_still_returns_the_item(tmp_path: Path) -> None:
    """`stop()` lands while the claim is on its way back: the loop exits before
    the task it created has taken a step. Cancelling a task that never ran
    raises at its first instruction, so the handler's `except CancelledError`
    never returns the item, and it would sit CLAIMED by a worker that is gone
    until the lease expired and the sweep requeued it as a stale lease."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    handler = SlowHandler(hold=5.0)
    work = StopOnClaimWork(container.managers.work)
    loop, task = start_loop(container, handler, DEFAULTS, work=work)
    work.stop = loop.stop
    item = make_item(ctx)
    await container.managers.work.enqueue(ctx, item)
    await asyncio.wait_for(task, 3.0)
    stored = await container.storage.get_work_storage().read_item(ctx.org_id, item.id)
    assert stored is not None
    assert stored.status is WorkStatus.QUEUED, "the item was left claimed by a stopped worker"
    assert stored.claimed_by is None
    assert stored.last_error == "returned: worker stopping"
    assert loop.running == 0


async def test_an_announcement_during_a_claim_is_not_lost(tmp_path: Path) -> None:
    """The claim snapshots an empty lane, a producer inserts and announces, and
    the claim then returns nothing. The wake the announcement set belongs to
    the item the claim never saw: clearing it after the claim threw it away
    and the item waited out the whole poll interval, an hour here."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    handler = SlowHandler(hold=0.05)
    item = make_item(ctx)
    work = EnqueueDuringClaimWork(container.managers.work, ctx, item)
    loop, task = start_loop(
        container, handler, fast_options(poll_interval=timedelta(hours=1)), work=work
    )
    await until(lambda: handler.started == [item.id], polls=200)
    loop.stop()
    await ended(task)


async def test_an_item_whose_wake_up_the_bus_dropped_is_claimed_on_the_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wake-up is a hint. The loop has found the lane empty and waits, and
    the bus drops the announcement of the next item: nothing wakes the loop,
    and its poll claims the item. That is why a work row is done once its
    item is queued."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    handler = SlowHandler(hold=0.0)
    work = EmptyClaimCountingWork(container.managers.work)
    loop, task = start_loop(
        container, handler, fast_options(poll_interval=timedelta(seconds=0.2)), work=work
    )
    await until(lambda: work.empty_claims >= 1)
    dropped: list[Topics] = []

    async def drop(topic: Topics, payload: TopicPayload) -> bool:
        dropped.append(topic)
        return False

    monkeypatch.setattr(container.infra.get_topics(), "publish", drop)
    item = make_item(ctx)
    await container.managers.work.enqueue(ctx, item)
    assert Topics.WORK_AVAILABLE in dropped, "the enqueue announced the item and the bus dropped it"
    await until(lambda: handler.started == [item.id])
    loop.stop()
    await ended(task)
