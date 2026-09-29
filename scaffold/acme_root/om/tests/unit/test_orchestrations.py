"""The orchestration mechanism on its own: its pure rules, the parse of an
import file, and the manager's start, wake, resume, failure, and purge."""

from datetime import timedelta
from pathlib import Path

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id, utcnow
from acme.om.exceptions import NotFound, PreconditionFailed, ValidationFailed
from acme.om.opcontext import AppContext, AppType, OpContext, RequestContext
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
from acme.om.tasks.rules import (
    ImportFileRefused,
    ImportRow,
    import_refusal,
    imported,
    parse_import,
    room_for,
)

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
            "kind": OrchestrationKind.TASK_IMPORT,
            "input": {"file_id": str(new_id())},
            **fields,
        }
    )


# The pure rules.


def test_a_step_moves_the_cursor_counts_its_skips_and_ends_the_record_when_last() -> None:
    record = a_record(skipped=1, row_errors=[{"row": 1, "reason": "no title"}])
    now = utcnow()
    after = advanced(
        record, now, record.created_by, cursor=5, total=5,
        skipped=[RowError(row=4, reason="no title")], finished=True,
    )  # fmt: skip
    assert (after.cursor, after.total, after.skipped) == (5, 5, 2)
    assert [e.row for e in after.row_errors] == [1, 4]
    assert after.status is OrchestrationStatus.SUCCEEDED and after.finished_at == now
    assert after.version == record.version + 1
    assert after.applied == record.applied  # the step's commit counts what it wrote


def test_a_guard_parks_the_record_at_its_cursor_keeping_what_it_made() -> None:
    record = a_record(applied=10, cursor=10)
    after = advanced(
        record, utcnow(), record.created_by, cursor=10, total=50, park=ParkReason.PLAN_LIMIT
    )
    assert after.status is OrchestrationStatus.PARKED
    assert after.park_reason is ParkReason.PLAN_LIMIT
    assert (after.cursor, after.applied, after.finished_at) == (10, 10, None)
    running = resumed(after, utcnow(), record.created_by)
    assert running.status is OrchestrationStatus.RUNNING and running.park_reason is None
    assert running.cursor == 10 and running.version == after.version + 1


def test_a_bound_fails_the_record_and_names_why() -> None:
    record = a_record()
    now = utcnow()
    ended = failed(record, now, record.created_by, FailReason.TOO_MANY_ROWS)
    assert ended.status is OrchestrationStatus.FAILED
    assert ended.fail_reason is FailReason.TOO_MANY_ROWS and ended.finished_at == now


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


def test_room_is_what_the_plan_leaves_and_never_below_zero() -> None:
    assert room_for(None, 500) is None
    assert room_for(10, 7) == 3
    assert room_for(10, 12) == 0


def test_an_import_file_reads_its_known_columns_in_any_order() -> None:
    data = b"\xef\xbb\xbfNotes, Title ,extra,Due_On\nn1,First,x,2026-01-02\n,,,\n,Second,,\n"
    rows = parse_import(data, 1024)
    assert rows == [
        ImportRow(number=1, title="First", notes="n1", due_on="2026-01-02"),
        ImportRow(number=2, title="Second"),
    ]


@pytest.mark.parametrize(
    ("data", "bound", "rows", "reason"),
    [
        (b"title\n" + b"x\n" * 20, 10, 100, "file_too_large"),
        (b"title\n" + b"x\n" * 4, 1024, 3, "too_many_rows"),
        (b"title\x00\nx\n", 1024, 100, "not_csv"),
        (b"\xc3\x28 title\n", 1024, 100, "not_csv"),
        (b'title\n"unclosed\n', 1024, 100, "not_csv"),
        (b"name,notes\na,b\n", 1024, 100, "no_title_column"),
        (b"", 1024, 100, "no_title_column"),
    ],
)
def test_an_import_file_past_a_bound_is_refused(
    data: bytes, bound: int, rows: int, reason: str
) -> None:
    with pytest.raises(ImportFileRefused) as refused:
        parse_import(data, bound, rows)
    assert refused.value.reason == reason
    assert FailReason(reason)  # every refusal is a reason a record fails with


def test_a_row_is_refused_for_a_title_a_date_or_an_assignee() -> None:
    ann = new_id()
    members = {"ann@example.test": ann}
    assert import_refusal(ImportRow(number=1), members) == "no title"
    assert import_refusal(ImportRow(number=1, title="x" * 501), members) is not None
    assert import_refusal(ImportRow(number=1, title="t", due_on="2026-02-30"), members)
    assert import_refusal(ImportRow(number=1, title="t", due_on="26-02-01"), members)
    assert import_refusal(ImportRow(number=1, title="t", assignee_email="bob@x.test"), members)
    fine = ImportRow(number=3, title="t", due_on="2026-02-01", assignee_email="ANN@example.test")
    assert import_refusal(fine, members) is None
    made = imported(fine, members)
    assert made.assignee_id == ann and str(made.due_on) == "2026-02-01"


# The manager.


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.managers: Managers = build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path))

    async def org(self) -> OpContext:
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
    with pytest.raises(ValidationFailed):
        await orchestrations.start(ctx, a_record(input={"file": "nope"}))
    record = a_record(cursor=99, status=OrchestrationStatus.FAILED)
    started = await orchestrations.start(ctx, record)
    # The steps' fields are the manager's: a start is running at its first cursor.
    assert (started.status, started.cursor, started.version) == (
        OrchestrationStatus.RUNNING,
        0,
        1,
    )
    assert await orchestrations.start(ctx, record) == started


async def test_wake_resumes_only_the_records_parked_for_the_reason(world: World) -> None:
    ctx = await world.org()
    orchestrations = world.managers.orchestrations
    storage = orchestrations._storage  # type: ignore[attr-defined]
    parked = await orchestrations.start(ctx, a_record())
    waiting = parked.model_copy(
        update={"status": OrchestrationStatus.PARKED, "park_reason": ParkReason.PLAN_LIMIT,
                "version": 2}
    )  # fmt: skip
    await storage.write_orchestration(ctx.org_id, waiting, 1, ())
    running = await orchestrations.start(ctx, a_record())
    assert await orchestrations.wake(ctx, ParkReason.PLAN_LIMIT) == 1
    assert (await orchestrations.get(ctx, parked.id)).status is OrchestrationStatus.RUNNING
    assert (await orchestrations.get(ctx, running.id)).version == running.version
    assert await orchestrations.wake(ctx, ParkReason.PLAN_LIMIT) == 0


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
    with pytest.raises(NotFound):
        await world.managers.tasks.get_import(other, record.id)


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
