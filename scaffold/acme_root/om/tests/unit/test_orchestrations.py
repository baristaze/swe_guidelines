"""The orchestration mechanism on its own: its pure rules, and the manager's
start, step, wake, resume, failure, and purge."""

from datetime import timedelta
from pathlib import Path

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.exceptions import NotFound, PreconditionFailed, ValidationFailed
from acme.om.orchestrations.rules import (
    ROW_ERRORS_KEPT,
    advanced,
    failed,
    resumed,
    stagger,
    with_row_errors,
)
from acme.om.orchestrations.types.orchestration import (
    FailReason,
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    ParkReason,
    RowError,
)
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl

APP = AppContext(type=AppType.PORTAL, version="portal@test")


def a_record(**fields: object) -> Orchestration:
    now = utcnow()
    actor = new_id()
    return Orchestration.model_validate(
        {
            "id": new_id(),
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "updated_by": actor,
            "kind": OrchestrationKind.NOOP,
            "input": {"steps": 3},
            **fields,
        }
    )


# The pure rules.


def test_a_step_moves_the_cursor_counts_its_skips_and_ends_the_record_when_last() -> None:
    record = a_record(skipped=1, row_errors=[{"row": 1, "reason": "malformed"}])
    now = utcnow()
    after = advanced(
        record, now, record.created_by, cursor=5, total=5,
        skipped=[RowError(row=4, reason="malformed")], finished=True,
    )  # fmt: skip
    assert (after.cursor, after.total, after.skipped) == (5, 5, 2)
    assert [e.row for e in after.row_errors] == [1, 4]
    assert after.status is OrchestrationStatus.SUCCEEDED and after.finished_at == now
    assert after.version == record.version + 1
    assert after.applied == record.applied  # the step's commit counts what it wrote


def test_a_guard_parks_the_record_at_its_cursor_keeping_what_it_made() -> None:
    record = a_record(applied=10, cursor=10)
    after = advanced(
        record,
        utcnow(),
        record.created_by,
        cursor=10,
        total=50,
        park=ParkReason.PROVIDER_UNAVAILABLE,
    )
    assert after.status is OrchestrationStatus.PARKED
    assert after.park_reason is ParkReason.PROVIDER_UNAVAILABLE
    assert (after.cursor, after.applied, after.finished_at) == (10, 10, None)
    running = resumed(after, utcnow(), record.created_by)
    assert running.status is OrchestrationStatus.RUNNING and running.park_reason is None
    assert running.cursor == 10 and running.version == after.version + 1


def test_a_bound_fails_the_record_and_names_why() -> None:
    record = a_record()
    now = utcnow()
    ended = failed(record, now, record.created_by, FailReason.DEFECT)
    assert ended.status is OrchestrationStatus.FAILED
    assert ended.fail_reason is FailReason.DEFECT and ended.finished_at == now


def test_the_skipped_rows_named_are_bounded() -> None:
    kept = [RowError(row=n, reason="x") for n in range(ROW_ERRORS_KEPT - 1)]
    more = [RowError(row=100 + n, reason="y") for n in range(5)]
    bounded = with_row_errors(kept, more)
    assert len(bounded) == ROW_ERRORS_KEPT and bounded[-1].row == 100


def test_the_resumes_of_one_wake_are_staggered() -> None:
    assert [stagger(n, timedelta(seconds=2)) for n in range(3)] == [
        timedelta(0),
        timedelta(seconds=2),
        timedelta(seconds=4),
    ]


# The manager.


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.managers: Managers = build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path))

    async def org(self) -> TenantContext:
        slug = f"ajax-{new_id().hex[-8:]}"
        owner, _ = await self.managers.tenancy.bootstrap(
            RequestContext(request_id=new_id(), app=APP), "Ajax", slug, f"a-{slug}@x.test", "Ann"
        )
        return owner


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


async def test_start_holds_the_input_to_its_kind_and_answers_a_retry_as_stored(
    world: World,
) -> None:
    ctx = await world.org()
    orchestrations = world.managers.orchestrations
    for refused in ({"steps": 0}, {"steps": 101}, {"file": "nope"}):
        with pytest.raises(ValidationFailed):
            await orchestrations.start(ctx, a_record(input=refused))
    record = a_record(cursor=99, status=OrchestrationStatus.FAILED)
    started = await orchestrations.start(ctx, record)
    # The steps' fields are the manager's: a start is running at its first cursor.
    assert (started.status, started.cursor, started.version) == (
        OrchestrationStatus.RUNNING,
        0,
        1,
    )
    assert await orchestrations.start(ctx, record) == started


async def test_a_noop_step_moves_the_cursor_by_one_and_succeeds_at_its_count(
    world: World,
) -> None:
    ctx = await world.org()
    orchestrations = world.managers.orchestrations
    record = await orchestrations.start(ctx, a_record(input={"steps": 2}))
    first = await orchestrations.step_noop(ctx, record)
    assert (first.status, first.cursor, first.total) == (OrchestrationStatus.RUNNING, 1, 2)
    assert first.version == record.version + 1 and first.finished_at is None
    last = await orchestrations.step_noop(ctx, first)
    assert (last.status, last.cursor) == (OrchestrationStatus.SUCCEEDED, 2)
    assert last.finished_at is not None
    assert await orchestrations.get(ctx, record.id) == last


async def test_a_noop_step_is_conditioned_on_the_version_it_read(world: World) -> None:
    ctx = await world.org()
    orchestrations = world.managers.orchestrations
    record = await orchestrations.start(ctx, a_record())
    stepped = await orchestrations.step_noop(ctx, record)
    with pytest.raises(PreconditionFailed):
        await orchestrations.step_noop(ctx, record)
    assert await orchestrations.get(ctx, record.id) == stepped


async def test_wake_resumes_only_the_records_parked_for_the_reason(world: World) -> None:
    ctx = await world.org()
    orchestrations = world.managers.orchestrations
    storage = orchestrations._storage  # type: ignore[attr-defined]
    parked = await orchestrations.start(ctx, a_record())
    waiting = parked.model_copy(
        update={"status": OrchestrationStatus.PARKED,
                "park_reason": ParkReason.PROVIDER_UNAVAILABLE, "version": 2}
    )  # fmt: skip
    await storage.write_orchestration(ctx.org_id, waiting, 1, ())
    running = await orchestrations.start(ctx, a_record())
    assert await orchestrations.wake(ctx, ParkReason.PROVIDER_UNAVAILABLE) == 1
    assert (await orchestrations.get(ctx, parked.id)).status is OrchestrationStatus.RUNNING
    assert (await orchestrations.get(ctx, running.id)).version == running.version
    assert await orchestrations.wake(ctx, ParkReason.PROVIDER_UNAVAILABLE) == 0


async def test_fail_is_conditioned_on_the_version_it_read(world: World) -> None:
    ctx = await world.org()
    orchestrations = world.managers.orchestrations
    record = await orchestrations.start(ctx, a_record())
    ended = await orchestrations.fail(ctx, record, FailReason.DEFECT, "RuntimeError: boom")
    assert ended.fail_detail == "RuntimeError: boom"
    with pytest.raises(PreconditionFailed):
        await orchestrations.fail(ctx, record, FailReason.DEFECT)


async def test_a_record_of_another_org_is_not_found(world: World) -> None:
    ctx, other = await world.org(), await world.org()
    record = await world.managers.orchestrations.start(ctx, a_record())
    with pytest.raises(NotFound):
        await world.managers.orchestrations.get(other, record.id)
    with pytest.raises(PreconditionFailed):
        await world.managers.orchestrations.step_noop(other, record)
    assert await world.managers.orchestrations.get(ctx, record.id) == record


async def test_the_sweep_purges_settled_records_past_the_retention(world: World) -> None:
    ctx = await world.org()
    orchestrations = world.managers.orchestrations
    old = await orchestrations.start(ctx, a_record())
    ended = await orchestrations.fail(ctx, old, FailReason.DEFECT)
    storage = orchestrations._storage  # type: ignore[attr-defined]
    aged = ended.model_copy(
        update={"updated_at": utcnow() - timedelta(days=31), "version": ended.version + 1}
    )
    await storage.write_orchestration(ctx.org_id, aged, ended.version, ())
    live = await orchestrations.start(ctx, a_record())
    assert await orchestrations.purge_tenant(ctx) == 0, "a living tenant keeps its records"
    assert await orchestrations.purge_across_tenants() == 1
    with pytest.raises(NotFound):
        await orchestrations.get(ctx, old.id)
    assert (await orchestrations.get(ctx, live.id)).id == live.id
