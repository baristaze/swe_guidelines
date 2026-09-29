"""The import of tasks from a CSV file, over the memory roots: the steps a
worker takes, called here one after another. Every outcome: done, rows
skipped, a park on the plan's bound and its wake by a plan that rose or by a
person, a failure on each bound, and a step that died before its commit or
ran twice, neither of which makes a task twice."""

from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id, utcnow
from acme.om.billing.types.plan import Plan
from acme.om.exceptions import PreconditionFailed, ValidationFailed
from acme.om.media.types.file import File, FilePurpose
from acme.om.opcontext import AppContext, AppType, OpContext, RequestContext
from acme.om.orchestrations.rules import ROW_ERRORS_KEPT
from acme.om.orchestrations.types.orchestration import (
    FailReason,
    Orchestration,
    OrchestrationStatus,
    ParkReason,
    Step,
)
from acme.om.outbox.types.row import OutboxRow
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.storage.root import StorageInterface
from acme.om.tasks.rules import IMPORT_BATCH, IMPORT_MAX_ROWS
from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl
from acme.om.tasks.types.filter import OpenTaskCursor, TaskFilter
from acme.om.tasks.types.task import Task, TaskScope
from acme.om.work.types.work_item import WorkKind, WorkStatus

APP = AppContext(type=AppType.PORTAL, version="portal@test")


def request() -> RequestContext:
    return RequestContext(request_id=new_id(), app=APP)


class World:
    def __init__(self, tmp_path: Path, storage: StorageInterface | None = None) -> None:
        self.storage = storage or StorageMemoryImpl()
        self.managers: Managers = build_managers(self.storage, InfraLocalImpl(tmp_path))

    async def org(self, plan: Plan = Plan.FREE) -> OpContext:
        slug = f"ajax-{new_id().hex[-8:]}"
        owner, _ = await self.managers.tenancy.bootstrap(
            request(), "Ajax", slug, f"ann-{slug}@example.test", "Ann"
        )
        if plan is not Plan.FREE:
            await self.managers.billing.grant_seeded_plan(owner, plan)
        return owner

    async def upload(self, ctx: OpContext, data: bytes, name: str = "tasks.csv") -> UUID:
        now = utcnow()
        file = File(
            id=new_id(),
            name=name,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            content_type="text/csv",
            size_bytes=len(data),
            purpose=FilePurpose.TASK_IMPORT,
        )
        started = await self.managers.tasks.create_import_file(ctx, file)
        await self.managers.media.put_content(ctx, started.id, data)
        await self.managers.media.confirm_file(ctx, started.id)
        return started.id

    async def start(self, ctx: OpContext, data: bytes) -> Orchestration:
        file_id = await self.upload(ctx, data)
        return await self.managers.tasks.start_import(ctx, new_id(), file_id)

    async def step(self, ctx: OpContext, record_id: UUID) -> Orchestration:
        record = await self.managers.orchestrations.get(ctx, record_id)
        return await self.managers.tasks.step_import(ctx, record)

    async def run(self, ctx: OpContext, record_id: UUID) -> Orchestration:
        """Steps the record until it stops running, as the worker's items do."""
        record = await self.managers.orchestrations.get(ctx, record_id)
        while record.status is OrchestrationStatus.RUNNING:
            record = await self.managers.tasks.step_import(ctx, record)
        return await self.managers.orchestrations.get(ctx, record_id)

    async def open_titles(self, ctx: OpContext) -> list[str]:
        """Every open task's title, top first, a page at a time."""
        team = TaskFilter(scope=TaskScope.TEAM, user_id=ctx.user_id)
        titles: list[str] = []
        after: OpenTaskCursor | None = None
        while True:
            page = await self.managers.tasks.get_open_tasks(ctx, team, after, 200)
            titles.extend(t.title for t in page.items)
            if not page.has_more:
                return titles
            last = page.items[-1]
            after = OpenTaskCursor(rank=last.rank, id=last.id)

    async def queued(self, ctx: OpContext, kind: WorkKind) -> int:
        """The work items of a kind waiting in the org's queue."""
        items = self.storage.get_work_storage()._items  # type: ignore[attr-defined]
        return sum(
            1
            for org_id, item in items.values()
            if org_id == ctx.org_id and item.kind is kind and item.status is WorkStatus.QUEUED
        )


def csv_of(rows: Sequence[str], header: str = "title,notes,due_on,assignee_email") -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


def titled(count: int) -> bytes:
    return csv_of([f"Task {n},,," for n in range(1, count + 1)])


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


async def test_an_import_makes_a_task_of_every_row_in_batches_and_succeeds(world: World) -> None:
    ctx = await world.org(Plan.TEAM)
    await world.managers.tasks.create_task(ctx, _task(ctx, "already here"))
    total = IMPORT_BATCH * 2 + 30
    started = await world.start(ctx, titled(total))
    assert started.status is OrchestrationStatus.RUNNING and started.cursor == 0
    first = await world.step(ctx, started.id)
    assert (first.cursor, first.applied, first.total) == (IMPORT_BATCH, IMPORT_BATCH, total)
    done = await world.run(ctx, started.id)
    assert done.status is OrchestrationStatus.SUCCEEDED
    assert (done.cursor, done.applied, done.skipped) == (total, total, 0)
    assert done.finished_at is not None
    # An import adds to the bottom of the list, in the file's order.
    titles = await world.open_titles(ctx)
    assert titles[0] == "already here"
    assert titles[1:] == [f"Task {n}" for n in range(1, total + 1)]


async def test_rows_that_make_no_task_are_skipped_and_the_first_few_named(world: World) -> None:
    ctx = await world.org(Plan.TEAM)
    me = (await world.managers.tenancy.get_identity(ctx)).email
    rows = [
        f"Assigned,a note,2026-10-01,{me.upper()}",
        ",no title,,",
        "Bad date,,2026-13-40,",
        "Stranger,,,nobody@example.test",
        *[",,2026-01-01," for _ in range(ROW_ERRORS_KEPT + 5)],
    ]
    started = await world.start(ctx, csv_of(rows))
    done = await world.run(ctx, started.id)
    assert done.status is OrchestrationStatus.SUCCEEDED
    assert done.applied == 1
    assert done.skipped == len(rows) - 1
    assert len(done.row_errors) == ROW_ERRORS_KEPT
    assert [(e.row, e.reason) for e in done.row_errors[:3]] == [
        (2, "no title"),
        (3, "due_on '2026-13-40' is not a date (YYYY-MM-DD)"),
        (4, "nobody@example.test is not a member of this org"),
    ]
    page = await world.managers.tasks.get_open_tasks(
        ctx, TaskFilter(scope=TaskScope.TEAM, user_id=ctx.user_id), None, 10
    )
    (assigned,) = page.items
    assert assigned.assignee_id == ctx.user_id and assigned.notes == "a note"
    assert str(assigned.due_on) == "2026-10-01"


async def test_a_free_org_parks_at_its_bound_and_wakes_when_its_plan_rises(world: World) -> None:
    ctx = await world.org(Plan.FREE)
    started = await world.start(ctx, titled(50))
    parked = await world.run(ctx, started.id)
    # The guard: the eleventh row would pass Free's ten. The record keeps
    # what it made and waits, with the cursor on that row.
    assert parked.status is OrchestrationStatus.PARKED
    assert parked.park_reason is ParkReason.PLAN_LIMIT
    assert (parked.cursor, parked.applied, parked.total) == (10, 10, 50)
    assert len(await world.open_titles(ctx)) == 10
    # A step that arrives for a parked record (a stale item) does nothing.
    assert (await world.managers.orchestrations.get(ctx, started.id)).version == parked.version
    # The plan rises: the account's write asks for the wake in its commit.
    await world.managers.billing.grant_seeded_plan(ctx, Plan.PRO)
    assert await world.queued(ctx, WorkKind.WAKE_PARKED) == 1
    assert await world.managers.orchestrations.wake(ctx, ParkReason.PLAN_LIMIT) == 1
    done = await world.run(ctx, started.id)
    assert done.status is OrchestrationStatus.SUCCEEDED
    assert (done.cursor, done.applied) == (50, 50)
    assert len(await world.open_titles(ctx)) == 50


async def test_a_plan_that_does_not_rise_wakes_nothing(world: World) -> None:
    ctx = await world.org(Plan.PRO)
    await world.managers.billing.grant_seeded_plan(ctx, Plan.PRO)
    assert await world.queued(ctx, WorkKind.WAKE_PARKED) == 1  # the seed's own rise from Free
    await world.managers.billing.grant_seeded_plan(ctx, Plan.FREE)
    assert await world.queued(ctx, WorkKind.WAKE_PARKED) == 1


async def test_a_person_resumes_a_parked_import_and_it_parks_again_while_full(
    world: World,
) -> None:
    ctx = await world.org(Plan.FREE)
    started = await world.start(ctx, titled(15))
    parked = await world.run(ctx, started.id)
    assert parked.applied == 10
    resumed = await world.managers.tasks.resume_import(ctx, started.id)
    assert resumed.status is OrchestrationStatus.RUNNING and resumed.park_reason is None
    again = await world.run(ctx, started.id)
    assert again.status is OrchestrationStatus.PARKED and again.cursor == 10
    assert again.applied == 10
    # Two tasks done make room for two more; Resume goes on from the cursor.
    page = await world.managers.tasks.get_open_tasks(
        ctx, TaskFilter(scope=TaskScope.TEAM, user_id=ctx.user_id), None, 2
    )
    for task in page.items:
        done = task.model_copy(update={"status": "done"})
        await world.managers.tasks.update_task(ctx, Task.model_validate(done), task.version)
    await world.managers.tasks.resume_import(ctx, started.id)
    later = await world.run(ctx, started.id)
    assert later.status is OrchestrationStatus.PARKED
    assert (later.cursor, later.applied) == (12, 12)


async def test_resume_answers_a_running_import_as_it_is_and_refuses_a_settled_one(
    world: World,
) -> None:
    ctx = await world.org(Plan.TEAM)
    started = await world.start(ctx, titled(3))
    assert await world.managers.tasks.resume_import(ctx, started.id) == started
    await world.run(ctx, started.id)
    with pytest.raises(ValidationFailed):
        await world.managers.tasks.resume_import(ctx, started.id)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (titled(IMPORT_MAX_ROWS + 1), FailReason.TOO_MANY_ROWS),
        (b"\x00\x01\x02 binary", FailReason.NOT_CSV),
        (b"\xff\xfe\xfa not text", FailReason.NOT_CSV),
        (csv_of(["a,b"], header="name,notes"), FailReason.NO_TITLE_COLUMN),
        (b"", FailReason.NO_TITLE_COLUMN),
    ],
)
async def test_a_file_past_a_bound_fails_and_creates_nothing(
    world: World, data: bytes, reason: FailReason
) -> None:
    ctx = await world.org(Plan.TEAM)
    file_id = await world.upload(ctx, data or b"\n")
    started = await world.managers.tasks.start_import(ctx, new_id(), file_id)
    failed = await world.run(ctx, started.id)
    assert failed.status is OrchestrationStatus.FAILED
    assert failed.fail_reason is reason
    assert failed.applied == 0 and failed.finished_at is not None
    assert await world.open_titles(ctx) == []


async def test_a_file_removed_before_its_step_fails_the_import(world: World) -> None:
    ctx = await world.org(Plan.TEAM)
    started = await world.start(ctx, titled(3))
    file_id = UUID(started.input["file_id"])
    await world.managers.media.delete_file(ctx, file_id)
    failed = await world.run(ctx, started.id)
    assert failed.status is OrchestrationStatus.FAILED
    assert failed.fail_reason is FailReason.FILE_GONE


async def test_start_takes_only_a_stored_import_file(world: World) -> None:
    ctx = await world.org(Plan.TEAM)
    now = utcnow()
    pending = await world.managers.tasks.create_import_file(
        ctx,
        File(
            id=new_id(),
            name="later.csv",
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            content_type="text/csv",
            size_bytes=10,
            purpose=FilePurpose.TASK_ATTACHMENT,  # the import's purpose is the manager's
        ),
    )
    assert pending.purpose is FilePurpose.TASK_IMPORT and pending.subject_id is None
    with pytest.raises(ValidationFailed):
        await world.managers.tasks.start_import(ctx, new_id(), pending.id)


async def test_a_step_that_dies_before_its_commit_starts_again_at_the_cursor(
    tmp_path: Path,
) -> None:
    class DiesOnce(TasksStorageMemoryImpl):
        """The second batch's commit never happens, once: a worker that died
        (or a database that went away) mid-step."""

        calls = 0

        async def create_tasks_in_step(
            self,
            org_id: UUID,
            tasks: Sequence[tuple[Task, tuple[OutboxRow, ...]]],
            step: Step,
            step_rows: tuple[OutboxRow, ...],
        ) -> tuple[bool, ...]:
            DiesOnce.calls += 1
            if DiesOnce.calls == 2:
                raise ConnectionResetError("the connection went away")
            return await super().create_tasks_in_step(org_id, tasks, step, step_rows)

    storage = StorageMemoryImpl()
    storage._tasks = DiesOnce(storage._outbox, storage._orchestrations)  # type: ignore[attr-defined]
    world = World(tmp_path, storage)
    ctx = await world.org(Plan.TEAM)
    total = IMPORT_BATCH * 2 + 5
    started = await world.start(ctx, titled(total))
    await world.step(ctx, started.id)
    with pytest.raises(ConnectionResetError):
        await world.step(ctx, started.id)
    halfway = await world.managers.orchestrations.get(ctx, started.id)
    assert (halfway.cursor, halfway.applied) == (IMPORT_BATCH, IMPORT_BATCH)
    assert len(await world.open_titles(ctx)) == IMPORT_BATCH
    done = await world.run(ctx, started.id)
    assert (done.status, done.applied) == (OrchestrationStatus.SUCCEEDED, total)
    assert await world.open_titles(ctx) == [f"Task {n}" for n in range(1, total + 1)]


async def test_a_step_from_a_stale_read_lands_nothing(world: World) -> None:
    """Two workers held the same record (a lease that passed): the one that
    read it first and wrote second is refused, and makes no task."""
    ctx = await world.org(Plan.TEAM)
    started = await world.start(ctx, titled(IMPORT_BATCH + 10))
    stale = await world.managers.orchestrations.get(ctx, started.id)
    await world.managers.tasks.step_import(ctx, stale)
    with pytest.raises(PreconditionFailed):
        await world.managers.tasks.step_import(ctx, stale)
    record = await world.managers.orchestrations.get(ctx, started.id)
    assert record.applied == IMPORT_BATCH
    assert len(await world.open_titles(ctx)) == IMPORT_BATCH


async def test_the_imports_are_listed_newest_first(world: World) -> None:
    ctx = await world.org(Plan.TEAM)
    first = await world.start(ctx, titled(1))
    second = await world.start(ctx, titled(1))
    page = await world.managers.tasks.get_imports(ctx, 10)
    assert [r.id for r in page.items] == [second.id, first.id]
    assert (await world.managers.tasks.get_import(ctx, first.id)).id == first.id


def _task(ctx: OpContext, title: str) -> Task:
    now = utcnow()
    return Task(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        title=title,
    )
