"""The sweep's pass: the requeue of expired leases and the outbox relay, once
a pass across tenants and again while a batch comes back full, its budget and
where the next pass resumes, a purge called again while its batch comes back
full, each namespace's purge past its retention once a pass across tenants,
every row past its retention gone after one pass of the worker's own loop, a
living tenant that costs the purges nothing, a deleted tenant's every row
gone, the tenant marked purged once nothing of it is left and left out
after, the tenant's expiry read once per pass, the count of the platform's
size once an interval, and the pass's duration and the four gauges of the
queue and the outbox on its own line."""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from prometheus_client import REGISTRY
from worker_support import (
    build_container,
    fast_options,
    make_item,
    request,
    sign_in,
    start_noop,
    upload,
)

from acme.infra.buckets import Buckets
from acme.infra.cache import CacheScope
from acme.infra.observability import (
    OUTBOX_FAILED_RECENTLY,
    WORK_OLDEST_READY_SECONDS,
    JsonFormatter,
)
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import CredentialKind, RequestContext, Role, TenantContext, build_context
from acme.om.events.manager import audit_event
from acme.om.media.types.file import File
from acme.om.orchestrations.types.orchestration import OrchestrationKind, OrchestrationStatus
from acme.om.outbox import OutboxRelayInterface
from acme.om.tenancy.rules import hash_token, permissions_of
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.org import Org
from acme.om.work import WorkManagerInterface
from acme.om.work.types.work_item import WorkStatus
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.loop import (
    AcrossStep,
    LoopOptions,
    PurgeStep,
    TallyStep,
    WorkerLoop,
)
from acme.workers.maintenance.main import build_loop


class Tenants(WorkManagerInterface):
    """The part of the work manager the sweep asks: a fixed list of tenants,
    the counts the requeue returns in turn (nothing stale unless given),
    nothing to purge across tenants, an empty queue unless told otherwise,
    and a record of the calls in order and of the tenants it was asked to
    mark purged. A partial double."""

    def __init__(self, contexts: Sequence[TenantContext], requeued: Sequence[int] = (0,)) -> None:
        self.contexts = list(contexts)
        self.marked: list[UUID] = []
        self.requeued = list(requeued)
        self.calls: list[str] = []
        self.oldest_ready: timedelta | Exception = timedelta(0)
        self.failed = 0
        self.windows: list[timedelta] = []

    async def maintenance_contexts(self, rctx: RequestContext) -> list[TenantContext]:
        self.calls.append("contexts")
        return list(self.contexts)

    async def requeue_stale(self, rctx: RequestContext, limit: int) -> int:
        self.calls.append("requeue")
        return self.requeued.pop(0) if len(self.requeued) > 1 else self.requeued[0]

    async def purge_items(self) -> int:
        return 0

    async def oldest_ready_age(self) -> timedelta:
        if isinstance(self.oldest_ready, Exception):
            raise self.oldest_ready
        return self.oldest_ready

    async def failed_within(self, window: timedelta) -> int:
        self.windows.append(window)
        return self.failed

    async def mark_purged(self, ctx: TenantContext) -> bool:
        self.marked.append(ctx.org_id)
        return False


Tenants.__abstractmethods__ = frozenset()


def listed(contexts: Sequence[TenantContext], requeued: Sequence[int] = (0,)) -> Tenants:
    return Tenants(contexts, requeued)  # pyright: ignore[reportAbstractUsage] (a partial double)


class Outbox(OutboxRelayInterface):
    """The part of the relay the sweep asks: the counts the relay returns in
    turn (nothing pending unless given), counting its relays and its purges.
    A partial double."""

    def __init__(self, relayed: Sequence[int] = (0,)) -> None:
        self.purges = 0
        self.relays = 0
        self.relayed = list(relayed)
        self.oldest_pending = timedelta(0)
        self.failed: int | Exception = 0
        self.windows: list[timedelta] = []

    async def oldest_pending_age(self) -> timedelta:
        return self.oldest_pending

    async def failed_within(self, window: timedelta) -> int:
        self.windows.append(window)
        if isinstance(self.failed, Exception):
            raise self.failed
        return self.failed

    async def relay_pending(self, limit: int) -> int:
        self.relays += 1
        return self.relayed.pop(0) if len(self.relayed) > 1 else self.relayed[0]

    async def purge_done(self, retention: timedelta, limit: int) -> int:
        self.purges += 1
        return 0


Outbox.__abstractmethods__ = frozenset()


def quiet_outbox(relayed: Sequence[int] = (0,)) -> Outbox:
    return Outbox(relayed)  # pyright: ignore[reportAbstractUsage] (a partial double)


def service_contexts(count: int) -> list[TenantContext]:
    """The system scope and `count` tenants, as the sweep receives them."""
    rctx = request()
    return [
        build_context(
            rctx,
            user_id=EMPTY_UUID,
            org_id=org_id,
            role=Role.SERVICE,
            permissions=permissions_of(Role.SERVICE),
            credential_kind=CredentialKind.INTERNAL,
        )
        for org_id in [EMPTY_UUID, *(new_id() for _ in range(count))]
    ]


def sweeping(
    container: WorkerContainer,
    work: WorkManagerInterface,
    purges: dict[str, PurgeStep],
    options: LoopOptions,
    outbox: OutboxRelayInterface | None = None,
    across: dict[str, AcrossStep] | None = None,
    tally: TallyStep | None = None,
) -> WorkerLoop:
    return WorkerLoop(
        work=work,
        outbox=outbox or quiet_outbox(),
        purges=purges,
        across=across,
        tally=tally,
        handlers={},
        topics=container.infra.get_topics(),
        liveness=container.infra.get_cache(CacheScope.WORKER_LIVENESS),
        options=options,
    )


def recording(
    calls: list[tuple[str, UUID]], name: str, counts: Sequence[int] = (0,)
) -> Callable[[TenantContext], Awaitable[int]]:
    """A purge step that records each call and returns `counts` in turn, the
    last of them for ever after."""
    left = list(counts)

    async def step(ctx: TenantContext) -> int:
        calls.append((name, ctx.org_id))
        return left.pop(0) if len(left) > 1 else left[0]

    return step


async def test_a_spent_budget_stops_the_pass_and_the_next_resumes_where_it_stopped(
    tmp_path: Path,
) -> None:
    """With no budget at all a pass takes one tenant, never none, and runs
    every step of it; the next pass takes the next tenant. Every tenant is
    reached in turn, the system scope among them, and no step is skipped for
    any of them. The cross-tenant steps run on every pass."""
    container = build_container(tmp_path)
    contexts = service_contexts(3)
    calls: list[tuple[str, UUID]] = []
    outbox = quiet_outbox()
    loop = sweeping(
        container,
        listed(contexts),
        {"one": recording(calls, "one"), "two": recording(calls, "two")},
        fast_options(sweep_budget=timedelta(0)),
        outbox,
    )
    for _ in range(len(contexts) + 1):
        await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    ids = [ctx.org_id for ctx in sorted(contexts, key=lambda ctx: ctx.org_id)]
    expected = [(name, org_id) for org_id in [*ids, ids[0]] for name in ("one", "two")]
    assert calls == expected, "a tenant a pass, in turn, round to the first again"
    assert outbox.purges == len(contexts) + 1, "the outbox purge ran on every pass"


async def test_a_pass_within_its_budget_reaches_every_tenant(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    contexts = service_contexts(3)
    calls: list[tuple[str, UUID]] = []
    loop = sweeping(container, listed(contexts), {"one": recording(calls, "one")}, fast_options())
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert sorted(org for _, org in calls) == sorted(ctx.org_id for ctx in contexts)


async def test_a_full_batch_is_purged_again_while_the_budget_lasts(tmp_path: Path) -> None:
    """A purge that returns a whole batch may have more; it is called again,
    in turn with the other full ones, until it returns less. A purge that
    returned less is drained and not called again."""
    container = build_container(tmp_path)
    (system,) = service_contexts(0)
    calls: list[tuple[str, UUID]] = []
    loop = sweeping(
        container,
        listed([system]),
        {
            "backlog": recording(calls, "backlog", (2, 2, 1)),
            "drained": recording(calls, "drained", (1,)),
            "other backlog": recording(calls, "other backlog", (3, 0)),
        },
        fast_options(purge_batch=2),
    )
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert [name for name, _ in calls] == [
        "backlog",
        "drained",
        "other backlog",
        "backlog",
        "other backlog",
        "backlog",
    ]


async def test_a_spent_budget_leaves_a_full_batch_for_the_next_pass(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    (system,) = service_contexts(0)
    calls: list[tuple[str, UUID]] = []
    loop = sweeping(
        container,
        listed([system]),
        {"backlog": recording(calls, "backlog", (2,))},
        fast_options(purge_batch=2, sweep_budget=timedelta(0)),
    )
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert len(calls) == 2, "one batch a pass"


async def test_only_a_tenant_with_nothing_left_is_offered_to_be_marked_purged(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path)
    busy, idle, failing = service_contexts(3)[1:]
    work = listed([busy, idle, failing])
    calls: list[tuple[str, UUID]] = []

    async def step(ctx: TenantContext) -> int:
        calls.append(("step", ctx.org_id))
        if ctx.org_id == failing.org_id:
            raise RuntimeError("the database is down")
        return 1 if ctx.org_id == busy.org_id else 0

    loop = sweeping(container, work, {"step": step}, fast_options())
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert work.marked == [idle.org_id]


async def deleted_org(container: WorkerContainer, days_ago: int) -> tuple[UUID, File]:
    """A team org deleted `days_ago`, with rows of every namespace left: its
    owner's user and membership, an api key, a stored file and its object, a
    running record, and the stream of events they made."""
    tail = new_id().hex[-8:]
    owner, org = await container.managers.tenancy.bootstrap(
        request(), "Gone", f"gone-{tail}", f"gone-{tail}@example.test", "Gone"
    )
    file = await upload(container, owner)
    await start_noop(container, owner, 3)
    await container.managers.tenancy.create_api_key(owner, "ci", Role.MEMBER)
    storage = container.storage.get_tenancy_storage()
    stored = await storage.read_org(org.id)
    assert stored is not None
    when = utcnow() - timedelta(days=days_ago)
    await storage.write_org(org.id, stored.model_copy(update={"deleted_at": when}))
    return org.id, file


async def rows_of(container: WorkerContainer, org_id: UUID) -> dict[str, int]:
    """How many rows of the tenant each namespace holds."""
    storage = container.storage
    orchestrations = storage.get_orchestrations_storage()
    return {
        "users": len(await storage.get_tenancy_storage().read_users(org_id, None, limit=10)),
        "files": len(await storage.get_media_storage().read_every_file(org_id, None, 10)),
        "records": len(await orchestrations.read_recent(org_id, OrchestrationKind.NOOP, 10)),
        "events": len(await storage.get_event_storage().read_after(org_id, 0, 100)),
    }


async def test_a_deleted_tenant_is_marked_purged_once_nothing_is_left_and_skipped_after(
    tmp_path: Path,
) -> None:
    """The first pass takes every row of a tenant past its retention, each
    namespace through its purge of the tenant, and keeps the org row as the
    record; the next finds nothing left and marks it, and from then on the
    sweep leaves it out. A tenant within its retention is swept and never
    marked, and keeps its rows."""
    container = build_container(tmp_path)
    expired, file = await deleted_org(container, days_ago=40)
    recent, _ = await deleted_org(container, days_ago=1)
    loop = build_loop(container)
    tenancy = container.storage.get_tenancy_storage()
    buckets = container.infra.get_buckets()
    assert all((await rows_of(container, expired)).values())
    assert await buckets.exists(expired, Buckets.USER_FILE_UPLOADS, file.key)

    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    first = await tenancy.read_org(expired)
    assert first is not None and first.purged_at is None, "this pass took its rows"
    assert await rows_of(container, expired) == {
        "users": 0,
        "files": 0,
        "records": 0,
        "events": 0,
    }
    assert not await buckets.exists(expired, Buckets.USER_FILE_UPLOADS, file.key)
    assert await container.storage.get_event_storage().read_head(expired) == 0

    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    marked = await tenancy.read_org(expired)
    assert marked is not None and marked.purged_at is not None, "nothing was left"
    kept = await tenancy.read_org(recent)
    assert kept is not None and kept.purged_at is None, "within its retention"
    assert all((await rows_of(container, recent)).values())

    swept = {ctx.org_id for ctx in await container.managers.work.maintenance_contexts(request())}
    assert expired not in swept, "a purged tenant is left out"
    assert recent in swept and EMPTY_UUID in swept


async def test_a_pass_reads_no_org_row_to_ask_whether_a_tenant_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every namespace's purge asks whether its tenant is past the retention;
    the pass answers from the org rows it read to list the tenants, so no
    purge reads the org row again."""
    container = build_container(tmp_path)
    await deleted_org(container, days_ago=40)
    await deleted_org(container, days_ago=1)
    storage = container.storage.get_tenancy_storage()
    reads: list[UUID] = []
    read_org = storage.read_org

    async def counted(org_id: UUID) -> Org | None:
        reads.append(org_id)
        return await read_org(org_id)

    monkeypatch.setattr(storage, "read_org", counted)
    await build_loop(container)._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert reads == []


async def test_a_pass_says_how_long_it_took_on_one_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    container = build_container(tmp_path)
    contexts = service_contexts(2)
    loop = sweeping(
        container,
        listed(contexts),
        {"one": recording([], "one")},
        fast_options(sweep_budget=timedelta(0)),
    )
    with caplog.at_level(logging.INFO, logger="acme.workers.maintenance.loop"):
        await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    (record,) = [r for r in caplog.records if hasattr(r, "sweep")]
    line = json.loads(JsonFormatter().format(record))
    assert line["sweep"]["tenants"] == 1 and line["sweep"]["of"] == 3
    assert line["sweep"]["finished"] is False
    assert isinstance(line["sweep"]["duration_ms"], int)


def pass_line(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    (record,) = [r for r in caplog.records if hasattr(r, "sweep")]
    return json.loads(JsonFormatter().format(record))["sweep"]


async def test_a_pass_reads_the_queue_and_the_outbox_onto_its_line_and_its_gauges(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The four numbers the queue and outbox alarms read, in whole seconds
    and counts, as fields of the pass's line (the cloud's metric filters) and
    as gauges (what Grafana draws). The dead letters of the queue and of the
    outbox are counted over the loop's window, fifteen minutes by default."""
    container = build_container(tmp_path)
    work = listed(service_contexts(1))
    work.oldest_ready = timedelta(minutes=11, seconds=0.6)
    work.failed = 2
    outbox = quiet_outbox()
    outbox.oldest_pending = timedelta(minutes=6)
    outbox.failed = 3
    loop = sweeping(container, work, {}, fast_options(), outbox)
    with caplog.at_level(logging.INFO, logger="acme.workers.maintenance.loop"):
        await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    line = pass_line(caplog)
    assert line["work_oldest_ready_seconds"] == 660
    assert line["work_failed_recently"] == 2
    assert line["outbox_oldest_pending_seconds"] == 360
    assert line["outbox_failed_recently"] == 3
    assert work.windows == outbox.windows == [timedelta(minutes=15)]
    assert REGISTRY.get_sample_value("acme_work_oldest_ready_seconds") == 660
    assert REGISTRY.get_sample_value("acme_work_failed_recently") == 2
    assert REGISTRY.get_sample_value("acme_outbox_oldest_pending_seconds") == 360
    assert REGISTRY.get_sample_value("acme_outbox_failed_recently") == 3


async def test_a_gauge_that_cannot_be_read_is_left_off_the_line_and_as_it_was(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No field is no data point, which keeps the alarm's state; a zero
    would clear an alarm on a queue nobody could read. The others are read
    and written as ever."""
    container = build_container(tmp_path)
    work = listed(service_contexts(1))
    work.oldest_ready = RuntimeError("the database is down")
    work.failed = 1
    outbox = quiet_outbox()
    outbox.failed = RuntimeError("the database is down")
    WORK_OLDEST_READY_SECONDS.set(900)
    OUTBOX_FAILED_RECENTLY.set(4)
    loop = sweeping(container, work, {}, fast_options(), outbox)
    with caplog.at_level(logging.INFO, logger="acme.workers.maintenance.loop"):
        await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    line = pass_line(caplog)
    assert "work_oldest_ready_seconds" not in line
    assert "outbox_failed_recently" not in line
    assert line["work_failed_recently"] == 1
    assert line["outbox_oldest_pending_seconds"] == 0
    assert REGISTRY.get_sample_value("acme_work_oldest_ready_seconds") == 900
    assert REGISTRY.get_sample_value("acme_outbox_failed_recently") == 4


class Tally:
    """The count of the platform's size: how many times it ran, and the
    failures it raises first, one a call."""

    def __init__(self, failures: int = 0) -> None:
        self.calls = 0
        self.failures = failures

    async def __call__(self) -> object:
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("the database is down")
        return None


async def test_the_platforms_size_is_counted_on_the_first_pass_then_once_an_interval(
    tmp_path: Path,
) -> None:
    """The first pass counts, whatever its budget, and the passes within the
    interval after it do not; with no interval, every pass counts."""
    container = build_container(tmp_path)
    hourly, every = Tally(), Tally()
    for tally, interval in ((hourly, timedelta(hours=1)), (every, timedelta(0))):
        loop = sweeping(
            container,
            listed(service_contexts(1)),
            {},
            fast_options(sweep_budget=timedelta(0), tally_interval=interval),
            tally=tally,
        )
        for _ in range(3):
            await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert (hourly.calls, every.calls) == (1, 3)


async def test_a_failed_count_is_tried_again_on_the_next_pass_and_stops_no_other_step(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    container = build_container(tmp_path)
    tally = Tally(failures=1)
    loop = sweeping(
        container,
        listed(service_contexts(1)),
        {},
        fast_options(tally_interval=timedelta(hours=1)),
        tally=tally,
    )
    with caplog.at_level(logging.INFO, logger="acme.workers.maintenance.loop"):
        await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert pass_line(caplog)["work_failed_recently"] == 0, "the gauges are read after it"
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert tally.calls == 2


async def test_the_worker_keeps_the_tally_the_operator_plane_reads(tmp_path: Path) -> None:
    """The loop the worker runs counts the platform's size into the tally
    row, and the next pass within the interval leaves it as it was."""
    container = build_container(tmp_path)
    await sign_in(container)
    storage = container.storage.get_tenancy_storage()
    assert await storage.read_platform_size() is None
    loop = build_loop(container)
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    tally = await storage.read_platform_size()
    assert tally is not None
    # The org and its owner's personal org, and the owner in each.
    assert (tally.tenants, tally.users) == (2, 2)
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert await storage.read_platform_size() == tally


async def test_the_requeue_runs_once_a_pass_across_tenants_before_any_tenant(
    tmp_path: Path,
) -> None:
    """However many tenants there are, and with no budget at all, a pass
    requeues expired leases in one call, before it lists the tenants, so a
    crashed worker's item waits one pass and not its tenant's turn."""
    container = build_container(tmp_path)
    work = listed(service_contexts(3))
    calls: list[tuple[str, UUID]] = []
    loop = sweeping(
        container,
        work,
        {"one": recording(calls, "one")},
        fast_options(sweep_budget=timedelta(0)),
    )
    for _ in range(2):
        await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert work.calls == ["requeue", "contexts", "requeue", "contexts"]
    assert len(calls) == 2, "a tenant a pass, and the requeue in none of them"


async def test_a_full_requeue_batch_is_requeued_again_while_the_budget_lasts(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path)
    work = listed(service_contexts(0), requeued=(2, 2, 1))
    loop = sweeping(container, work, {}, fast_options(requeue_batch=2))
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert work.calls == ["requeue", "requeue", "requeue", "contexts"]

    spent = listed(service_contexts(0), requeued=(2,))
    loop = sweeping(container, spent, {}, fast_options(requeue_batch=2, sweep_budget=timedelta(0)))
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert spent.calls == ["requeue", "contexts"], "past the budget, one batch a pass"


async def test_the_relay_runs_again_while_its_batch_comes_back_full(tmp_path: Path) -> None:
    """A backlog of pending rows, after a crash or an outage of the bus,
    drains at the pace of the budget and not of one batch a pass. A batch
    that came back short, a row failing in it among the reasons, ends it."""
    container = build_container(tmp_path)
    outbox = quiet_outbox(relayed=(2, 2, 1, 2))
    loop = sweeping(
        container, listed(service_contexts(0)), {}, fast_options(outbox_batch=2), outbox
    )
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert outbox.relays == 3

    spent = quiet_outbox(relayed=(2,))
    loop = sweeping(
        container,
        listed(service_contexts(0)),
        {},
        fast_options(outbox_batch=2, sweep_budget=timedelta(0)),
        spent,
    )
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert spent.relays == 2, "past the budget, one batch a pass"


def across_recording(
    calls: list[str], name: str, counts: Sequence[int] = (0,)
) -> Callable[[RequestContext], Awaitable[int]]:
    """A purge across tenants that records each call and returns `counts` in
    turn, the last of them for ever after."""
    left = list(counts)

    async def step(rctx: RequestContext) -> int:
        calls.append(name)
        return left.pop(0) if len(left) > 1 else left[0]

    return step


async def test_each_purge_across_tenants_runs_once_a_pass_after_the_tenants(
    tmp_path: Path,
) -> None:
    """However many tenants there are, and with no budget at all, a pass runs
    each namespace's purge past its retention once, after the tenants it
    takes, under the pass's one request stage."""
    container = build_container(tmp_path)
    calls: list[str] = []
    stages: set[UUID] = set()

    async def events(rctx: RequestContext) -> int:
        calls.append("events")
        stages.add(rctx.request_id)
        return 0

    async def tenant(ctx: TenantContext) -> int:
        calls.append("tenant")
        stages.add(ctx.request_id)
        return 0

    work = listed(service_contexts(3))
    loop = sweeping(
        container,
        work,
        {"tenant": tenant},
        fast_options(sweep_budget=timedelta(0)),
        across={"media": across_recording(calls, "media"), "events": events},
    )
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert calls == ["tenant", "media", "events"], "one tenant, then each purge once"
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert calls[3:] == ["tenant", "media", "events"]


async def test_a_full_purge_across_tenants_runs_again_while_the_budget_lasts(
    tmp_path: Path,
) -> None:
    """A purge whose batch came back whole may have more: it runs again, in
    turn with the other full ones, until it comes back short. Past the
    budget, each runs once a pass, and the next pass takes the rest."""
    container = build_container(tmp_path)
    calls: list[str] = []
    loop = sweeping(
        container,
        listed(service_contexts(0)),
        {},
        fast_options(purge_batch=2),
        across={
            "backlog": across_recording(calls, "backlog", (2, 2, 1)),
            "drained": across_recording(calls, "drained", (1,)),
            "other backlog": across_recording(calls, "other backlog", (3, 0)),
        },
    )
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert calls == [
        "backlog",
        "drained",
        "other backlog",
        "backlog",
        "other backlog",
        "backlog",
    ]

    spent: list[str] = []
    loop = sweeping(
        container,
        listed(service_contexts(0)),
        {},
        fast_options(purge_batch=2, sweep_budget=timedelta(0)),
        across={"backlog": across_recording(spent, "backlog", (2,))},
    )
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert spent == ["backlog", "backlog"], "past the budget, one batch a pass"


async def test_a_purge_across_tenants_with_a_batch_of_its_own_is_full_at_it(
    tmp_path: Path,
) -> None:
    """The media purge erases an object per row, so its batch is smaller than
    the rows' batch; a whole one of its own is full, and it runs again."""
    container = build_container(tmp_path)
    calls: list[str] = []
    loop = WorkerLoop(
        work=listed(service_contexts(0)),
        outbox=quiet_outbox(),
        purges={},
        across={
            "media": across_recording(calls, "media", (1, 1, 0)),
            "rows": across_recording(calls, "rows", (1, 0)),
        },
        across_batches={"media": 1},
        handlers={},
        topics=container.infra.get_topics(),
        liveness=container.infra.get_cache(CacheScope.WORKER_LIVENESS),
        options=fast_options(purge_batch=2),
    )
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert calls == ["media", "rows", "media", "media"]


async def test_a_failing_purge_across_tenants_stops_no_other_step(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    calls: list[str] = []

    async def failing(rctx: RequestContext) -> int:
        raise RuntimeError("the database is down")

    outbox = quiet_outbox()
    loop = sweeping(
        container,
        listed(service_contexts(1)),
        {"tenant": recording([], "tenant")},
        fast_options(),
        outbox,
        across={"failing": failing, "after": across_recording(calls, "after")},
    )
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert calls == ["after"]
    assert outbox.purges == 1, "the outbox purge still ran"


PURGE_READS = (
    "purge_deleted",
    "read_purgeable",
    "purge_files_across_tenants",
    "purge_records",
    "purge_sign_in_delays",
    "trim",
    "purge_settled",
)
"""The storage methods of the purges past a retention, across tenants."""


async def test_a_living_tenant_costs_the_purges_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the worker's own wiring and three living tenants, a pass sends
    each purge past its retention once for every tenant, and no purge
    statement at all for any one tenant: a living tenant's own purge answers
    from the expiry the pass read with the org rows."""
    container = build_container(tmp_path)
    for slug in ("ajax", "beta", "gamma"):
        await container.managers.tenancy.bootstrap(
            request(), slug.title(), slug, f"ann@{slug}.test", "Ann"
        )
    storage = container.storage
    calls: list[str] = []
    for namespace in (
        storage.get_media_storage(),
        storage.get_tenancy_storage(),
        storage.get_idempotency_storage(),
        storage.get_event_storage(),
        storage.get_orchestrations_storage(),
    ):
        for name in (*PURGE_READS, "purge_tenant", "read_every_file"):
            method = getattr(namespace, name, None)
            if method is None:
                continue

            def counted(
                method: Callable[..., Awaitable[object]] = method,
                label: str = f"{type(namespace).__name__}.{name}",
            ) -> Callable[..., Awaitable[object]]:
                async def call(*args: object, **kwargs: object) -> object:
                    calls.append(label)
                    return await method(*args, **kwargs)

                return call

            monkeypatch.setattr(namespace, name, counted())
    await build_loop(container)._sweep_once()  # pyright: ignore[reportPrivateUsage]
    per_tenant = [c for c in calls if c.endswith((".purge_tenant", ".read_every_file"))]
    assert per_tenant == [], "no living tenant is purged in its own right"
    assert len(calls) == len(set(calls)), f"each purge once a pass: {calls}"
    assert sorted(calls) == [
        "EventStorageMemoryImpl.trim",
        "IdempotencyStorageMemoryImpl.purge_records",
        "MediaStorageMemoryImpl.purge_files_across_tenants",
        "MediaStorageMemoryImpl.read_purgeable",
        "OrchestrationsStorageMemoryImpl.purge_settled",
        "TenancyStorageMemoryImpl.purge_deleted",
        "TenancyStorageMemoryImpl.purge_sign_in_delays",
    ], calls


LATER = timedelta(days=100)
"""Past every retention the sweep keeps: the events' ninety days, and each
shorter one."""

CLOCKS = (
    "acme.om.tenancy.impl.manager",
    "acme.om.media.impl.manager",
    "acme.om.idempotency.impl.manager",
    "acme.om.events.impl.manager",
    "acme.om.work.impl.manager",
    "acme.om.outbox.impl.relay",
)
"""The modules whose clock a purge past a retention reads."""


def move_on(container: WorkerContainer, monkeypatch: pytest.MonkeyPatch, by: timedelta) -> None:
    """Every clock the purges read, `by` ahead of the wall's."""

    def later() -> datetime:
        return utcnow() + by

    for module in CLOCKS:
        monkeypatch.setattr(f"{module}.utcnow", later)
    monkeypatch.setattr(container.managers.orchestrations, "_clock", later)


async def test_a_pass_purges_every_row_past_its_retention_and_keeps_what_lives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the worker's own wiring, a pass a hundred days on takes, across
    tenants, every row whose retention has passed: through the tenancy
    purge, a removed member's user and membership, a revoked api key, an
    expired session, a socket ticket, a closed invitation, and a run of
    failed sign-ins; through the media purge, a deleted file with its object
    and an upload never confirmed; a settled record; a finished idempotency
    marker; a settled work item; every relayed outbox row; and the org's
    events, the floor moving with them. What still lives stays: the owner's
    user and membership, a live key, a stored file, a running record, and a
    queued item."""
    container = build_container(tmp_path)
    ann = await sign_in(container)
    org = ann.org_id
    tenancy, media = container.managers.tenancy, container.managers.media
    tenancy_rows = container.storage.get_tenancy_storage()

    await tenancy.add_member(request(), "ajax", "bob@example.test", "Bob", Role.MEMBER)
    (bob,) = [u for u in await tenancy_rows.read_users(org, None, 10) if u.id != ann.user_id]
    await tenancy.remove_member(ann, bob.id)
    revoked = await tenancy.create_api_key(ann, "old", Role.MEMBER)
    await tenancy.revoke_api_key(ann, revoked.api_key.id)
    live = await tenancy.create_api_key(ann, "ci", Role.MEMBER)
    invitation = await tenancy.invite_member(ann, "carol@example.test", Role.MEMBER)
    await tenancy.revoke_invitation(ann, invitation.id)
    ticket = await tenancy.issue_ticket(ann)
    await tenancy_rows.record_failed_sign_in("a-digest", utcnow())

    deleted = await upload(container, ann, "old.png")
    await media.delete_file(ann, deleted.id)
    abandoned = await upload(container, ann, "never.png", confirm=False)
    stored = await upload(container, ann, "kept.png")

    orchestrations = container.managers.orchestrations
    settled = await orchestrations.step_noop(ann, await start_noop(container, ann, 1))
    assert settled.status is OrchestrationStatus.SUCCEEDED
    running = await start_noop(container, ann, 3)

    begun = await container.managers.idempotency.begin(ann, "k", "d", new_id())
    assert begun.attempt_id is not None
    await container.managers.idempotency.finish(ann, "k", begun.attempt_id, 201, "{}")

    work = container.managers.work
    done = await work.enqueue(ann, make_item(ann))
    claimed = await work.claim(request(), "default", [done.kind], "test", timedelta(minutes=1))
    assert claimed is not None and claimed[1].id == done.id
    await work.complete(*claimed)
    queued = await work.enqueue(ann, make_item(ann))

    events = container.storage.get_event_storage()
    await events.append_events(org, [audit_event(ann, new_id(), "tenancy.test.noted", org, {})])
    head = await events.read_head(org)

    storage = container.storage
    media_rows, buckets = storage.get_media_storage(), container.infra.get_buckets()
    records, items = storage.get_orchestrations_storage(), storage.get_work_storage()
    idempotency = storage.get_idempotency_storage()
    memory = cast(TenancyStorageMemoryImpl, tenancy_rows)
    ticket_hash = hash_token(ticket.ticket)

    async def past_their_retention() -> dict[str, object]:
        """Each row the pass must take, as storage reads it: None once gone."""
        # The memory storage's own tables, for the two rows no read names by
        # id: an ended membership, and a ticket.
        memberships = memory._memberships.values()  # pyright: ignore[reportPrivateUsage]
        tickets = memory._socket_tickets.values()  # pyright: ignore[reportPrivateUsage]
        found = {
            "removed user": await tenancy_rows.read_user(org, bob.id),
            "ended membership": next((m for _, m in memberships if m.user_id == bob.id), None),
            "revoked key": await tenancy_rows.read_api_key(org, revoked.api_key.id),
            "expired session": await tenancy_rows.read_session(org, ann.credential_id),
            "ticket": next((t for _, t in tickets if t.ticket_hash == ticket_hash), None),
            "closed invitation": await tenancy_rows.read_invitation(org, invitation.id),
            "failed sign-ins": await tenancy_rows.read_sign_in_delay("a-digest"),
            "deleted file": await media_rows.read_file(org, deleted.id),
            "abandoned upload": await media_rows.read_file(org, abandoned.id),
            "settled record": await records.read_orchestration(org, settled.id),
            "finished marker": await idempotency.read_record(org, ann.user_id, "k"),
            "settled item": await items.read_item(org, done.id),
        }
        return {name: row for name, row in found.items() if row is not None}

    assert len(await past_their_retention()) == 12, "every row is there before the pass"
    assert await buckets.exists(org, Buckets.USER_FILE_UPLOADS, deleted.key)

    move_on(container, monkeypatch, LATER)
    await build_loop(container)._sweep_once()  # pyright: ignore[reportPrivateUsage]

    assert await past_their_retention() == {}
    assert not await buckets.exists(org, Buckets.USER_FILE_UPLOADS, deleted.key)
    outbox = storage.get_outbox_storage()
    assert await outbox.purge_done(utcnow() + LATER, 1000) == 0, "no relayed row is left"
    assert head > 0 and await events.read_floor(org) == await events.read_head(org) == head
    assert await events.read_after(org, 0, 100) == []

    # What still lives stays.
    assert await tenancy_rows.read_user(org, ann.user_id) is not None
    assert await tenancy_rows.read_membership_for_user(org, ann.user_id) is not None
    assert await tenancy_rows.read_api_key(org, live.api_key.id) is not None
    assert await media_rows.read_file(org, stored.id) is not None
    assert await buckets.exists(org, Buckets.USER_FILE_UPLOADS, stored.key)
    assert await records.read_orchestration(org, running.id) is not None
    kept = await items.read_item(org, queued.id)
    assert kept is not None and kept.status is WorkStatus.QUEUED
