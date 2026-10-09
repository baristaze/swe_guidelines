"""The worker loop: wake on WORK_AVAILABLE with a short poll fallback, claim
on the lane while a slot is free, run each item as a task that names the
request that caused the work, raises a span linked to that request's trace,
renews its lease and cancels itself when the lease is lost or renewal keeps
failing, beat liveness in memory and publish it to the cache as best
effort, sweep on a timer (the expired leases and the outbox relay across
tenants, then the standing chores of the tenants one read across tenants
names as due, a page a pass, then the purge of a tenant past its retention
per tenant, then
every namespace's purge of its rows past their retention across tenants,
then the purges of done outbox rows and settled work items, within a time
budget, then the tally of the platform's size when it is due, then the four
gauges of the queue and the outbox), and drain first on stop."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from uuid import UUID

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from acme.infra.cache import CacheInterface
from acme.infra.observability import (
    OUTBOX_FAILED_RECENTLY,
    OUTBOX_OLDEST_PENDING_SECONDS,
    OUTCOMES,
    SWEEP_SECONDS,
    WORK_FAILED_RECENTLY,
    WORK_OLDEST_READY_SECONDS,
    caused_by_request_id_var,
    described,
    failure_level,
    links_to,
    request_id_var,
    traceparent_of,
)
from acme.infra.topics import TopicPayload, Topics, TopicsInterface, WorkAvailablePayload
from acme.om.base import EMPTY_UUID, Platform, new_id
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.exceptions import LeaseLost, NotFound
from acme.om.outbox import OutboxRelayInterface
from acme.om.work import WorkManagerInterface
from acme.om.work.types.handler import WorkHandlerInterface, WorkParked, WorkRefused
from acme.om.work.types.work_item import WorkItem, WorkKind

log = logging.getLogger(__name__)

tracer = trace.get_tracer("acme.workers.maintenance")
"""Resolved against whatever provider boot configured; the no-op one otherwise."""

REQUEST_ID_ATTRIBUTE = "acme.request_id"
CAUSED_BY_ATTRIBUTE = "acme.caused_by_request_id"
WORK_ITEM_ATTRIBUTE = "acme.work_item_id"

PurgeStep = Callable[[TenantContext], Awaitable[int]]
"""A manager's `purge_tenant(ctx)`: the hard delete of every row of a tenant
deleted longer ago than the retention, a batch per statement, and nothing
for any other tenant; it returns how many rows went, and a count of a whole
batch or more says there may be more."""

AcrossStep = Callable[[RequestContext], Awaitable[int]]
"""A namespace's purge of its rows past their retention, across tenants in
the system scope, a batch per statement, under the pass's request stage; it
returns how many rows went, and a whole batch or more says there may be
more. A namespace's sweep of what is due across tenants takes the same
shape, and counts the rows it moved."""

ChoreStep = Callable[[TenantContext], Awaitable[object]]
"""A standing chore: a step per tenant, for housekeeping that needs the
tenant's own context rather than a statement across tenants, such as
opening the next period of a record kept per period (an orchestration
started with its `period`, which answers a period already open as stored).
It runs under the service context the pass listed for the tenant, only in
the tenants `ChoreTenants` names, and is idempotent. A namespace's sweep of
what is due across tenants that visits each tenant itself, as the leases'
does, is an `AcrossStep`, not a chore."""

ChoreTenants = Callable[[RequestContext, UUID | None, int], Awaitable[Sequence[UUID]]]
"""The one read a pass, across tenants in the system scope, under the pass's
request stage, of the live tenants where a chore is due: at most `limit` of
them, in id order, after `after` when one is given. It is one read for every
chore, never a read per chore, and a tenant it does not name costs the pass
nothing."""

TallyStep = Callable[[], Awaitable[object]]
"""The count of the platform's size across every tenant, kept as the tally the
operator plane reads (`TenancyOperatorManagerInterface.tally_size`); it runs
once every `LoopOptions.tally_interval`, not every pass."""


class LoopOptions(Platform):
    worker_id: str
    lane: str = "default"
    capacity: int = 4
    lease: timedelta = timedelta(seconds=60)
    heartbeat_interval: timedelta = timedelta(seconds=10)
    sweep_interval: timedelta = timedelta(seconds=30)
    poll_interval: timedelta = timedelta(seconds=5)
    # Pending rows one relay call claims, and expired leases one requeue
    # statement moves, across tenants. Each is called again while its batch
    # comes back full and the budget lasts.
    outbox_batch: int = 100
    requeue_batch: int = 100
    # Done rows are purged after this. It outlives the database backup retention
    # (`backup_retention_days` in the database module), so a role restored to an
    # earlier point than its siblings is reconciled by relaying the outbox again.
    outbox_retention: timedelta = timedelta(days=8)
    # Rows one purge statement deletes at most: the managers' batch, which the
    # loop reads to know a full one. A purge that returns this many or more is
    # called again while the budget lasts; one that returns fewer is drained.
    purge_batch: int = 1000
    # Tenants with a chore due one pass reads at most. The next pass reads on
    # after the last one this pass ran, so a backlog of them is a page a pass.
    chore_batch: int = 1000
    # A pass takes no new tenant past this, and the next pass resumes at the
    # tenant it stopped at, so a pass stays shorter than the interval.
    sweep_budget: timedelta = timedelta(seconds=20)
    # The two dead-letter gauges, the queue's and the outbox's, count what
    # failed for good this long ago or less, so one failure reads as one for
    # this long and then goes. The alarms on them say the same window (the
    # alarms module).
    failed_window: timedelta = timedelta(minutes=15)
    # The platform's size is counted this often, not every pass: its counts
    # cover a day, and a tally five minutes old answers what the operator
    # plane asks of it (ADR 0074). Each worker counts on its own clock.
    tally_interval: timedelta = timedelta(minutes=5)


class WorkerLoop:
    def __init__(
        self,
        *,
        work: WorkManagerInterface,
        outbox: OutboxRelayInterface,
        purges: Mapping[str, PurgeStep],
        handlers: Mapping[WorkKind, WorkHandlerInterface],
        across: Mapping[str, AcrossStep] | None = None,
        across_batches: Mapping[str, int] | None = None,
        chores: Mapping[str, ChoreStep] | None = None,
        chore_tenants: ChoreTenants | None = None,
        tally: TallyStep | None = None,
        topics: TopicsInterface,
        liveness: CacheInterface,
        options: LoopOptions,
    ) -> None:
        self._work = work
        self._outbox = outbox
        self._purges = purges
        self._across = dict(across or {})
        # A purge across tenants whose batch is not the loop's `purge_batch`,
        # by name: what it returns is held against its own batch.
        self._across_batches = dict(across_batches or {})
        self._chores = dict(chores or {})
        if self._chores and chore_tenants is None:
            raise ValueError("a chore runs in the tenants one read names as due; name the read")
        self._chore_tenants = chore_tenants
        self._tally_step = tally
        self._handlers = handlers
        self._topics = topics
        self._liveness = liveness
        self._options = options
        self._app = AppContext(type=AppType.WORKER, version=f"worker@{options.worker_id}")
        self._wake = asyncio.Event()
        self._stopping = asyncio.Event()
        self._drained = asyncio.Event()
        self._running: dict[asyncio.Task[None], tuple[TenantContext, WorkItem]] = {}
        self._last_beat: float | None = None
        self.online = False
        self.sweeps = 0
        # Where the next pass starts: the tenant the last one stopped at, or
        # None when it reached the end.
        self._resume_at: UUID | None = None
        # Where the next pass reads the tenants with a chore due from: after
        # the last one this pass ran, or None to read from the first.
        self._chores_after: UUID | None = None
        # When this worker last counted the platform's size, on the event
        # loop's clock; None until it has, so its first pass counts.
        self._tallied_at: float | None = None

    @property
    def kinds(self) -> list[WorkKind]:
        return list(self._handlers)

    @property
    def running(self) -> int:
        return len(self._running)

    async def run(self) -> None:
        """Runs until `stop()`; returns once every item is back in the queue and the
        worker is marked offline."""
        unsubscribe = self._topics.subscribe(
            Topics.WORK_AVAILABLE, f"worker:{self._options.worker_id}", self._on_work_available
        )
        heartbeat = asyncio.create_task(self._heartbeat_forever(), name="heartbeat")
        sweep = asyncio.create_task(self._sweep_forever(), name="sweep")
        try:
            await self._claim_until_stopped()
        finally:
            # Every step of the shutdown runs whatever the step before it met, and
            # `wait_drained()` returns whatever happened: a step that fails is logged.
            unsubscribe()
            try:
                await self._drain()
                heartbeat.cancel()
                sweep.cancel()
                for timer, ended in zip(
                    (heartbeat, sweep),
                    await asyncio.gather(heartbeat, sweep, return_exceptions=True),
                    strict=True,
                ):
                    if isinstance(ended, Exception):
                        log.error("%s ended with %r", timer.get_name(), ended, exc_info=ended)
                await self._mark_offline()
            finally:
                self._drained.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    async def wait_drained(self) -> None:
        await self._drained.wait()

    # Claiming.

    async def _on_work_available(self, payload: TopicPayload) -> None:
        if isinstance(payload, WorkAvailablePayload) and payload.lane == self._options.lane:
            self._wake.set()

    async def _claim_until_stopped(self) -> None:
        # The wake is cleared before the claim, never after it: a producer that
        # inserts and publishes while the claim is still finishing sets the
        # event, and clearing it afterwards would throw that wake away and
        # leave a runnable item waiting out the whole poll interval.
        while not self._stopping.is_set():
            self._wake.clear()
            if len(self._running) < self._options.capacity:
                claimed = await self._try_claim()
                if claimed:
                    continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._options.poll_interval.total_seconds()
                )

    def _request(self, traceparent: str | None = None) -> RequestContext:
        """The request stage the worker mints at its edge: one per claim, with the
        trace context of the run's span, and one per sweep pass, which raises
        none, the way the gateway mints one per request."""
        return RequestContext(request_id=new_id(), app=self._app, traceparent=traceparent)

    async def _try_claim(self) -> bool:
        # The run's span starts here, at the run's edge, as a request's starts
        # at the gateway: the stage minted for the claim carries its trace
        # context, and every row the run lands takes it from the stage. It is
        # named, linked, and made current once an item is in hand. A claim
        # that finds nothing never ends it, so nothing is exported for it.
        span = tracer.start_span("work", kind=SpanKind.CONSUMER)
        try:
            claimed = await self._work.claim(
                self._request(traceparent_of(span)),
                self._options.lane,
                self.kinds,
                self._options.worker_id,
                self._options.lease,
            )
        except Exception as error:
            # A queue that did not answer in time is a warning: the next poll
            # claims again, and the storage outcome counts it.
            log.log(failure_level(error), "claim failed", exc_info=error)
            OUTCOMES.labels(subsystem="worker", outcome="claim_error").inc()
            return False
        if claimed is None:
            return False
        ctx, item = claimed
        # Linked to the trace of the request that filled the queue, and not a
        # child of it: the item waited in a durable queue, which holds it well
        # past the end of that request, so the causal edge joins two traces
        # instead of stretching one over both. An item with no trace context
        # on it starts a trace here.
        span.update_name(f"work {item.kind.value}")
        for link in links_to(item.traceparent):
            span.add_link(link.context, link.attributes)
        span.set_attributes(self._span_attributes(ctx, item))
        task = asyncio.create_task(self._run_item(ctx, item, span), name=f"work-{item.id}")
        self._running[task] = (ctx, item)
        task.add_done_callback(self._on_item_done)
        return True

    def _on_item_done(self, task: asyncio.Task[None]) -> None:
        """A finished item frees a slot, so the claimer is woken: the poll interval
        bounds how long a queue waits for a worker, not how fast one drains it."""
        self._running.pop(task, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error("work task %s ended with %r", task.get_name(), error, exc_info=error)
        self._wake.set()

    # Running one item.

    async def _run_item(self, ctx: TenantContext, item: WorkItem, span: Span) -> None:
        # The claim refined the request stage minted for it; every log line of
        # the run carries its request id, the way the API's middleware does,
        # and the request that caused the work beside it.
        token = request_id_var.set(str(ctx.request_id))
        cause = caused_by_request_id_var.set(
            str(ctx.caused_by_request_id) if ctx.caused_by_request_id is not None else None
        )
        try:
            # The run's span, started at the claim: current for the run, and
            # ended with it. Like the API's server span, it holds no text of
            # an exception that ends the run, which may quote the item's
            # payload: the span is marked failed with what `described` says.
            with trace.use_span(
                span, end_on_exit=True, record_exception=False, set_status_on_exception=False
            ):
                try:
                    await self._handle(ctx, item)
                except Exception as error:
                    span.set_status(Status(StatusCode.ERROR, described(error)))
                    raise
        finally:
            caused_by_request_id_var.reset(cause)
            request_id_var.reset(token)

    @staticmethod
    def _span_attributes(ctx: TenantContext, item: WorkItem) -> dict[str, str]:
        """The run's two requests and the item it advances. The cause is there
        only where the handoff named one; an attribute with nothing in it says
        less than no attribute."""
        attributes = {
            REQUEST_ID_ATTRIBUTE: str(ctx.request_id),
            WORK_ITEM_ATTRIBUTE: str(item.id),
        }
        if ctx.caused_by_request_id is not None:
            attributes[CAUSED_BY_ATTRIBUTE] = str(ctx.caused_by_request_id)
        return attributes

    async def _handle(self, ctx: TenantContext, item: WorkItem) -> None:
        handler = self._handlers.get(item.kind)
        if handler is None:
            await self._settle(item, self._work.release(ctx, item), "released")
            return
        renewal = asyncio.create_task(
            self._renew_lease(ctx, item, asyncio.current_task()), name=f"lease-{item.id}"
        )
        try:
            await handler.handle(ctx, item)
        except asyncio.CancelledError:
            if self._stopping.is_set():
                note = item.model_copy(update={"last_error": "returned: worker stopping"})
                await self._settle(item, self._work.release(ctx, note), "returned")
            else:
                log.warning("lease lost on %s; task cancelled before the lease expired", item.id)
                OUTCOMES.labels(subsystem="worker", outcome="lease_lost").inc()
        except WorkParked as parked:
            # A guard, not a failure: back to the queue for the time the
            # handler named, with its reason as the note, no attempt spent.
            log.info("parked %s for %s: %s", item.id, parked.resume_after, parked.reason)
            note = item.model_copy(update={"last_error": f"parked: {parked.reason}"})
            await self._settle(item, self._work.defer(ctx, note, parked.resume_after), "parked")
        except WorkRefused as refused:
            # No retry changes the answer: failed at once, a dead letter.
            log.warning("refused %s: %s", item.id, refused.reason)
            reason = f"refused: {refused.reason}"[:500]
            await self._settle(item, self._work.fail_for_good(ctx, item, reason), "refused")
        except Exception as error:
            log.exception("handler failed on %s", item.id)
            failure = self._work.fail(ctx, item, described(error)[:500])
            await self._settle(item, failure, "failed")
        else:
            await self._settle(item, self._work.complete(ctx, item), "done")
        finally:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal

    @staticmethod
    async def _settle(item: WorkItem, transition: Awaitable[WorkItem], outcome: str) -> None:
        """Writes the item's transition; a lease that was lost in the meantime is
        logged and counted, never written over."""
        try:
            await transition
        except (LeaseLost, NotFound) as error:
            log.warning("%s was not settled as %s: %s", item.id, outcome, error)
            OUTCOMES.labels(subsystem="worker", outcome="lease_lost").inc()
            return
        except asyncio.CancelledError:
            # The fence cancelled the item while its transition was in flight.
            # Whether the write landed is the database's answer and not ours,
            # and the sweep requeues the item if it did not; the run is said
            # here either way, since the cancellation stops every line after it.
            log.warning("%s was cancelled while it was settled as %s", item.id, outcome)
            OUTCOMES.labels(subsystem="worker", outcome="lease_lost").inc()
            raise
        OUTCOMES.labels(subsystem="worker", outcome=outcome).inc()

    async def _renew_lease(
        self, ctx: TenantContext, item: WorkItem, owner: asyncio.Task[None] | None
    ) -> None:
        """Renews a third of the lease after the last renewal that succeeded. The
        fence is half the lease after it: every attempt is bounded by the time
        left to the fence, a failed one is retried halfway between the first
        attempt and the fence, and at the fence the owner is cancelled, half
        the lease before it expires, so two workers never advance the same
        record. A renewal refused with LeaseLost is definitive: another worker
        holds the item now, so the owner is cancelled at once."""
        lease = self._options.lease
        interval = (lease / 3).total_seconds()
        fence = (lease / 2).total_seconds()
        retry = (fence - interval) / 2
        clock = asyncio.get_running_loop().time
        renewed_at = clock()
        pause = interval
        while True:
            await asyncio.sleep(pause)
            left = renewed_at + fence - clock()
            if left <= 0:
                if owner is not None:
                    owner.cancel()
                return
            try:
                await asyncio.wait_for(self._work.extend_lease(ctx, item, lease), timeout=left)
                renewed_at = clock()
                pause = interval
            except LeaseLost as error:
                log.warning("lease on %s is held elsewhere: %s", item.id, error)
                if owner is not None:
                    owner.cancel()
                return
            except Exception as error:
                log.warning("lease renewal failed on %s: %r", item.id, error, exc_info=error)
                pause = min(retry, max(renewed_at + fence - clock(), 0))

    # Liveness.

    def alive(self) -> bool:
        """True while the heartbeat has beaten within three intervals. The beat is
        the loop's own, held in memory: it stops when the event loop blocks or
        the heartbeat task dies, and a cache that is down does not stop it. The
        queue and its leases live in the database, so a cache outage is no
        reason to stop claiming or to be restarted."""
        if self._last_beat is None:
            return False
        since = asyncio.get_running_loop().time() - self._last_beat
        return since < (self._options.heartbeat_interval * 3).total_seconds()

    async def _heartbeat_forever(self) -> None:
        while True:
            await self._heartbeat_once()
            await asyncio.sleep(self._options.heartbeat_interval.total_seconds())

    async def _heartbeat_once(self) -> None:
        """One beat: recorded in memory first, then published to the cache as best
        effort, bounded by the interval, so other replicas and operators can see
        the worker. A store that stalls, raises, or reads back empty leaves the
        worker unpublished and nothing else."""
        self._last_beat = asyncio.get_running_loop().time()
        key = f"worker:{self._options.worker_id}"
        ttl = self._options.heartbeat_interval * 3
        try:
            async with asyncio.timeout(self._options.heartbeat_interval.total_seconds()):
                await self._liveness.put(EMPTY_UUID, key, b"online", ttl)
                stored = await self._liveness.get(EMPTY_UUID, key) is not None
        except Exception as error:
            log.warning("heartbeat not published: %r", error, exc_info=error)
            stored = False
        if stored and not self.online:
            log.info("heartbeat published")
        elif not stored and self.online:
            log.warning("heartbeat no longer published; claiming continues")
        self.online = stored

    async def _mark_offline(self) -> None:
        """Bounded like a beat: a key that cannot be removed expires on its own."""
        key = f"worker:{self._options.worker_id}"
        try:
            async with asyncio.timeout(self._options.heartbeat_interval.total_seconds()):
                await self._liveness.invalidate(EMPTY_UUID, key)
        except Exception as error:
            log.warning(
                "could not mark offline; the liveness key expires on its own: %r",
                error,
                exc_info=error,
            )
        self.online = False

    # Maintenance.

    async def _sweep_forever(self) -> None:
        while True:
            await self._sweep_once()
            await asyncio.sleep(self._options.sweep_interval.total_seconds())

    async def _sweep_once(self) -> None:
        """First the cross-tenant steps that bound recovery: the expired
        leases go back to the queue, and the outbox rows a crash or an outage
        left are relayed. Then the standing chores, in the tenants one read
        across tenants names as due, each under its service context. Then
        one service context per tenant, the system scope first and deleted
        tenants included, and under each the purge of a tenant past its
        retention. Then each namespace's purge of its
        rows past their retention, once across every tenant, and the
        cross-tenant purges of the outbox and the queue. Every step is
        idempotent and wrapped, so a failing tenant or step never stops the
        rest.

        The pass has a budget. The requeue and the relay run on every pass,
        each again while its batch comes back full and the budget lasts, so
        a crashed worker's item waits one pass at most and a backlog drains
        at the pace the budget allows. The chores take a page of the tenants
        with one due a pass, and the next pass reads on after the last
        tenant this one ran (`_run_chores`). The ring of tenants takes no new
        tenant once the budget is spent, but always takes one, and the next
        pass starts at the tenant this one stopped at, so every tenant is
        reached in turn however many there are. A tenant it takes runs every
        purge at least once; a purge whose batch came back full runs again,
        in turn with the others, while the budget lasts. The purges across
        tenants run on every pass, each at least once and again while its
        batch comes back full and the budget lasts. A living tenant with no
        chore due costs a pass nothing. The count of the platform's size
        runs once an interval, whatever the budget, as the
        gauges do: it is two counts and a write, and the operator plane
        reads it. The four reads of the queue and the outbox end every pass,
        whatever the budget, since the alarms read them."""
        clock = asyncio.get_running_loop().time
        started = clock()
        deadline = started + self._options.sweep_budget.total_seconds()
        rctx = self._request()
        try:
            await self._while_full(
                lambda: self._work.requeue_stale(rctx, self._options.requeue_batch),
                self._options.requeue_batch,
                deadline,
            )
        except Exception:
            log.exception("sweep: requeue_stale failed")
        try:
            # Whatever a crash left between the core write and its push. A
            # batch with a row that failed is not full, so a destination that
            # is down is not asked again in this pass.
            await self._while_full(
                lambda: self._outbox.relay_pending(self._options.outbox_batch),
                self._options.outbox_batch,
                deadline,
            )
        except Exception:
            log.exception("sweep: outbox relay failed")
        try:
            contexts = await self._work.maintenance_contexts(rctx)
        except Exception:
            log.exception("sweep: maintenance_contexts failed")
            contexts = []
        chored = await self._run_chores(rctx, contexts, deadline)
        ring = self._from_resume_point(contexts)
        swept = 0
        self._resume_at = None
        for ctx in ring:
            if swept and clock() >= deadline:
                self._resume_at = ctx.org_id
                break
            await self._sweep_tenant(ctx, deadline)
            swept += 1
        await self._purge_across(rctx, deadline)
        try:
            await self._while_full(
                lambda: self._outbox.purge_done(
                    self._options.outbox_retention, self._options.purge_batch
                ),
                self._options.purge_batch,
                deadline,
            )
        except Exception:
            log.exception("sweep: outbox purge failed")
        try:
            purged = await self._while_full(
                self._work.purge_items, self._options.purge_batch, deadline
            )
            if purged:
                log.info("sweep: purged %d settled work items", purged)
        except Exception:
            log.exception("sweep: work item purge failed")
        await self._tally()
        gauges = await self._gauges()
        self.sweeps += 1
        seconds = clock() - started
        SWEEP_SECONDS.observe(seconds)
        # One line per pass, its numbers as fields: the alarms module's log
        # filters read the duration and the four gauges off it.
        log.info(
            "sweep: pass took %.3fs over %d of %d tenants, chores in %d%s",
            seconds,
            swept,
            len(ring),
            chored,
            "" if self._resume_at is None else f"; the next resumes at {self._resume_at}",
            extra={
                "sweep": {
                    "duration_ms": round(seconds * 1000),
                    "tenants": swept,
                    "of": len(ring),
                    "chores": chored,
                    "finished": self._resume_at is None,
                    **gauges,
                }
            },
        )

    async def _tally(self) -> None:
        """The count of the platform's size, when the interval has passed
        since this worker last made one. One that fails is logged and tried
        again on the next pass; the tally the operator plane reads stays as
        it was, and says how old it is."""
        if self._tally_step is None:
            return
        clock = asyncio.get_running_loop().time
        due = self._options.tally_interval.total_seconds()
        if self._tallied_at is not None and clock() - self._tallied_at < due:
            return
        started = clock()
        try:
            await self._tally_step()
        except Exception:
            log.exception("sweep: the tally of the platform's size failed")
            return
        self._tallied_at = clock()
        log.info("sweep: counted the platform's size in %.3fs", self._tallied_at - started)

    async def _gauges(self) -> dict[str, int]:
        """The four numbers the queue and outbox alarms read, one read each
        across every tenant: how long the item ready longest has waited, how
        many items failed within the window, how long ago the oldest pending
        outbox row landed, and how many outbox rows failed for good within
        the window. A dead letter is no longer pending, so the lag cannot see
        it; the last count does. A read that fails leaves its field off the
        line and its gauge as it was, so the alarm sees no data, which keeps
        its state, and never a zero that would clear it."""
        found: dict[str, int] = {}
        reads = (
            ("work_oldest_ready_seconds", WORK_OLDEST_READY_SECONDS, self._oldest_ready),
            ("work_failed_recently", WORK_FAILED_RECENTLY, self._failed_recently),
            ("outbox_oldest_pending_seconds", OUTBOX_OLDEST_PENDING_SECONDS, self._oldest_pending),
            ("outbox_failed_recently", OUTBOX_FAILED_RECENTLY, self._outbox_failed_recently),
        )
        for name, gauge, read in reads:
            try:
                value = await read()
            except Exception:
                log.exception("sweep: the %s read failed", name)
                continue
            gauge.set(value)
            found[name] = value
        return found

    async def _oldest_ready(self) -> int:
        return int((await self._work.oldest_ready_age()).total_seconds())

    async def _failed_recently(self) -> int:
        return await self._work.failed_within(self._options.failed_window)

    async def _oldest_pending(self) -> int:
        return int((await self._outbox.oldest_pending_age()).total_seconds())

    async def _outbox_failed_recently(self) -> int:
        return await self._outbox.failed_within(self._options.failed_window)

    def _from_resume_point(self, contexts: list[TenantContext]) -> list[TenantContext]:
        """The pass's tenants in id order, the system scope first, turned to
        start at the tenant the last pass stopped at. A tenant that went from
        the list since starts the pass at the next one."""
        ordered = sorted(contexts, key=lambda ctx: ctx.org_id)
        if self._resume_at is None:
            return ordered
        at = next((i for i, ctx in enumerate(ordered) if ctx.org_id >= self._resume_at), 0)
        return ordered[at:] + ordered[:at]

    async def _run_chores(
        self, rctx: RequestContext, contexts: list[TenantContext], deadline: float
    ) -> int:
        """The standing chores of the tenants with one due, in id order, a page
        a pass: one read across tenants names them, and each runs every chore
        once, under the service context the pass listed for it. A tenant the
        read does not name is never visited. No new tenant is taken once the
        budget is spent, but always one, so no backlog elsewhere stops the
        chores. The next pass reads on after the last tenant this one ran,
        and after a page that came back short and ran whole, from the first,
        so every tenant due is reached in turn however many are due. A tenant
        the read names with no context in the pass (made since the list was
        read, or purged) is passed over until the read names it again, and a
        pass whose list of tenants failed runs no chore and moves no cursor.
        A chore that fails is logged and stops no other. Returns how many
        tenants ran."""
        if self._chore_tenants is None or not contexts:
            return 0
        batch = self._options.chore_batch
        try:
            due = list(await self._chore_tenants(rctx, self._chores_after, batch))
        except Exception:
            log.exception("sweep: the read of the tenants with a chore due failed")
            return 0
        listed = {ctx.org_id: ctx for ctx in contexts}
        clock = asyncio.get_running_loop().time
        self._chores_after = due[-1] if len(due) >= batch else None
        ran = 0
        for at, org_id in enumerate(due):
            if ran and clock() >= deadline:
                self._chores_after = due[at - 1]
                break
            ctx = listed.get(org_id)
            if ctx is None:
                continue
            for name, chore in self._chores.items():
                try:
                    await chore(ctx)
                except Exception:
                    log.exception("sweep: the %s chore failed for tenant %s", name, org_id)
            ran += 1
        return ran

    async def _sweep_tenant(self, ctx: TenantContext, deadline: float) -> None:
        """Every purge of one tenant once, then the purges whose batch came back
        full, round after round, while the budget lasts. A deleted tenant past
        its retention for which nothing was left is marked purged, and the
        sweep leaves it out from then on; its rows that have a retention of
        their own go across tenants, visited or not."""
        settled = True
        full: list[tuple[str, PurgeStep]] = []
        for name, purge in self._purges.items():
            purged = await self._purge(ctx, name, purge)
            settled = settled and purged == 0
            if purged is not None and purged >= self._options.purge_batch:
                full.append((name, purge))
        clock = asyncio.get_running_loop().time
        while full and clock() < deadline:
            again, full = full, []
            for name, purge in again:
                purged = await self._purge(ctx, name, purge)
                if purged is not None and purged >= self._options.purge_batch:
                    full.append((name, purge))
        if settled:
            try:
                if await self._work.mark_purged(ctx):
                    log.info("sweep: nothing is left of deleted org %s; marked purged", ctx.org_id)
            except Exception:
                log.exception("sweep: mark_purged failed for tenant %s", ctx.org_id)

    async def _purge_across(self, rctx: RequestContext, deadline: float) -> None:
        """Every namespace's purge across tenants once, then the ones whose
        batch came back full, round after round, while the budget lasts."""
        full: list[tuple[str, AcrossStep]] = []
        for name, purge in self._across.items():
            if self._whole(name, await self._across_once(rctx, name, purge)):
                full.append((name, purge))
        clock = asyncio.get_running_loop().time
        while full and clock() < deadline:
            again, full = full, []
            for name, purge in again:
                if self._whole(name, await self._across_once(rctx, name, purge)):
                    full.append((name, purge))

    def _whole(self, name: str, purged: int | None) -> bool:
        """Whether a purge across tenants took a whole batch, so may have more."""
        batch = self._across_batches.get(name, self._options.purge_batch)
        return purged is not None and purged >= batch

    @staticmethod
    async def _across_once(rctx: RequestContext, name: str, purge: AcrossStep) -> int | None:
        """One call of one purge across tenants; None when it failed, which it logs."""
        try:
            purged = await purge(rctx)
        except Exception:
            log.exception("sweep: %s purge across tenants failed", name)
            return None
        if purged:
            log.info("sweep: purged %d %s rows across tenants", purged, name)
        return purged

    @staticmethod
    async def _purge(ctx: TenantContext, name: str, purge: PurgeStep) -> int | None:
        """One call of one step; None when it failed, which it logs."""
        try:
            purged = await purge(ctx)
        except Exception:
            log.exception("sweep: %s purge failed for tenant %s", name, ctx.org_id)
            return None
        if purged:
            log.info("sweep: purged %d %s rows in org %s", purged, name, ctx.org_id)
        return purged

    @staticmethod
    async def _while_full(step: Callable[[], Awaitable[int]], batch: int, deadline: float) -> int:
        """A cross-tenant step, called again while its batch comes back full
        and the budget lasts; returns how many rows it moved in all."""
        clock = asyncio.get_running_loop().time
        moved = await step()
        total = moved
        while moved >= batch and clock() < deadline:
            moved = await step()
            total += moved
        return total

    # Shutdown.

    async def _drain(self) -> None:
        """Cancels every running item and awaits them all: one whose return is
        refused by a database that is down ends with an error, which
        `_on_item_done` logs, and stops none of the others."""
        # One turn of the loop first, so a task the claim created as `stop()`
        # arrived has taken its first step. Cancelling one that never ran
        # raises at its first instruction, outside the `except CancelledError`
        # that returns the item, and the item would sit CLAIMED by a worker
        # that is gone until its lease expired.
        await asyncio.sleep(0)
        tasks = list(self._running)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
