"""The daily cleanup of old done tasks, over the memory roots: the day's
record opened once, the day's cut that holds all day so the org has no chore
due once its record ran, the steps that archive a batch each in one
conditional write, the task reopened mid-run left alone, the task done 89
days ago kept, a step that errors retried from its cursor, and the archived
task read and restored."""

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id, utcnow
from acme.om.billing.types.plan import Plan
from acme.om.exceptions import ValidationFailed
from acme.om.opcontext import AppContext, AppType, OpContext, RequestContext
from acme.om.orchestrations.types.orchestration import (
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
)
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.storage.root import StorageInterface
from acme.om.tasks.impl.manager import TasksOptions
from acme.om.tasks.rules import CLEANUP_BATCH
from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl
from acme.om.tasks.types.filter import TaskFilter
from acme.om.tasks.types.task import Task, TaskScope, TaskStatus

APP = AppContext(type=AppType.PORTAL, version="portal@test")


def request() -> RequestContext:
    return RequestContext(request_id=new_id(), app=APP)


class World:
    def __init__(self, tmp_path: Path, storage: StorageInterface | None = None) -> None:
        self.storage = storage or StorageMemoryImpl()
        self.managers: Managers = build_managers(
            self.storage, InfraLocalImpl(tmp_path), tasks_options=TasksOptions()
        )

    async def org(self) -> OpContext:
        slug = f"ajax-{new_id().hex[-8:]}"
        owner, _ = await self.managers.tenancy.bootstrap(
            request(), "Ajax", slug, f"ann-{slug}@example.test", "Ann"
        )
        await self.managers.billing.grant_seeded_plan(owner, Plan.MAX)
        return owner

    async def done(self, ctx: OpContext, title: str, days_ago: float) -> Task:
        """A task marked done `days_ago` days ago and not changed since."""
        return await self.done_at(ctx, title, utcnow() - timedelta(days=days_ago))

    async def done_at(self, ctx: OpContext, title: str, at: datetime) -> Task:
        """A task marked done at `at` and not changed since."""
        now = utcnow()
        task = await self.managers.tasks.create_task(
            ctx,
            Task(
                id=new_id(),
                created_at=now,
                updated_at=now,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
                title=title,
            ),
        )
        aged = task.model_copy(
            update={
                "status": TaskStatus.DONE,
                "updated_at": at,
                "version": task.version + 1,
            }
        )
        await self.storage.get_tasks_storage().update_task(ctx.org_id, aged, task.version, ())
        return aged

    async def run(self, ctx: OpContext, record: Orchestration) -> Orchestration:
        while record.status is OrchestrationStatus.RUNNING:
            record = await self.managers.tasks.step_cleanup(ctx, record)
        return await self.managers.orchestrations.get(ctx, record.id)

    async def done_titles(self, ctx: OpContext) -> list[str]:
        page = await self.managers.tasks.get_done_tasks(ctx, team(ctx), None, 200)
        return sorted(t.title for t in page.items)

    async def archived_titles(self, ctx: OpContext) -> list[str]:
        page = await self.managers.tasks.get_archived_tasks(ctx, team(ctx), None, 200)
        return sorted(t.title for t in page.items)


def team(ctx: OpContext) -> TaskFilter:
    return TaskFilter(scope=TaskScope.TEAM, user_id=ctx.user_id)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


async def test_the_cleanup_archives_done_tasks_past_ninety_days_and_keeps_the_rest(
    world: World,
) -> None:
    ctx = await world.org()
    await world.done(ctx, "a year ago", 365)
    await world.done(ctx, "91 days ago", 91)
    kept = await world.done(ctx, "89 days ago", 89)
    record = await world.managers.tasks.open_cleanup(ctx)
    assert record is not None and record.kind is OrchestrationKind.TASK_CLEANUP
    assert record.input["older_than_days"] == 90
    finished = await world.run(ctx, record)
    assert finished.status is OrchestrationStatus.SUCCEEDED
    assert finished.applied == 2
    assert await world.done_titles(ctx) == ["89 days ago"]
    assert await world.archived_titles(ctx) == ["91 days ago", "a year ago"]
    # An archived task leaves no count behind it: it was done, not active.
    assert await world.managers.tasks.count_active_tasks(ctx) == 0
    assert (await world.managers.tasks.get_task(ctx, kept.id)).archived_at is None


async def test_the_days_record_opens_once_and_only_with_something_to_archive(
    world: World,
) -> None:
    ctx = await world.org()
    assert await world.managers.tasks.open_cleanup(ctx) is None
    await world.done(ctx, "old", 120)
    first = await world.managers.tasks.open_cleanup(ctx)
    second = await world.managers.tasks.open_cleanup(ctx)
    assert first is not None and second is not None and first.id == second.id
    assert first.period == utcnow().date().isoformat()
    page = await world.managers.orchestrations.get_recent(ctx, OrchestrationKind.TASK_CLEANUP, 10)
    assert [r.id for r in page.items] == [first.id]


async def test_once_the_days_cleanup_ran_the_org_has_no_chore_due_until_the_next_day(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day's cleanup takes what was past the archive age when the day
    began. A task that crosses the age during the day waits for the next
    day's record, so once today's has run, the sweep's read across tenants
    finds nothing due in the org until the day turns. An org whose done
    tasks are young is never found."""
    clock = {"now": datetime.combine(utcnow().date(), time(13), tzinfo=UTC)}
    monkeypatch.setattr("acme.om.tasks.impl.manager.utcnow", lambda: clock["now"])
    noon = clock["now"]
    tasks = world.managers.tasks
    ctx, young = await world.org(), await world.org()
    await world.done_at(ctx, "old", noon - timedelta(days=200))
    await world.done_at(ctx, "crossed this morning", noon - timedelta(days=90, hours=5))
    await world.done_at(young, "young", noon - timedelta(days=10))
    assert await tasks.tenants_with_chores(None, 10) == [ctx.org_id]
    record = await tasks.open_cleanup(ctx)
    assert record is not None
    await world.run(ctx, record)
    assert await world.archived_titles(ctx) == ["old"]
    assert await tasks.tenants_with_chores(None, 10) == []
    clock["now"] = noon + timedelta(days=1)
    assert await tasks.tenants_with_chores(None, 10) == [ctx.org_id]


async def test_the_cleanup_steps_a_batch_at_a_time(tmp_path: Path) -> None:
    world = World(tmp_path)
    ctx = await world.org()
    for n in range(CLEANUP_BATCH + 3):
        await world.done(ctx, f"old {n}", 100)
    record = await world.managers.tasks.open_cleanup(ctx)
    assert record is not None
    first = await world.managers.tasks.step_cleanup(ctx, record)
    assert first.status is OrchestrationStatus.RUNNING
    assert (first.cursor, first.applied) == (CLEANUP_BATCH, CLEANUP_BATCH)
    finished = await world.run(ctx, first)
    assert finished.status is OrchestrationStatus.SUCCEEDED
    assert finished.applied == CLEANUP_BATCH + 3


async def test_a_task_reopened_mid_run_is_left_alone(tmp_path: Path) -> None:
    class ReopensOne(TasksStorageMemoryImpl):
        """A person reopens a task between the step's read of the candidates
        and its write."""

        target: UUID | None = None
        manager: Managers | None = None
        ctx: OpContext | None = None

        async def read_archivable(self, org_id: UUID, before: datetime, limit: int) -> list[UUID]:
            found = await super().read_archivable(org_id, before, limit)
            if self.target is not None and self.manager is not None and self.ctx is not None:
                task = await self.manager.tasks.get_task(self.ctx, self.target)
                reopened = task.model_copy(update={"status": TaskStatus.OPEN})
                await self.manager.tasks.update_task(self.ctx, reopened, task.version)
                self.target = None
            return found

    storage = StorageMemoryImpl()
    racing = ReopensOne(storage._outbox, storage._orchestrations)  # type: ignore[attr-defined]
    storage._tasks = racing  # type: ignore[attr-defined]
    world = World(tmp_path, storage)
    ctx = await world.org()
    await world.done(ctx, "archived", 100)
    reopened = await world.done(ctx, "reopened", 100)
    record = await world.managers.tasks.open_cleanup(ctx)
    assert record is not None
    racing.target, racing.manager, racing.ctx = reopened.id, world.managers, ctx
    finished = await world.run(ctx, record)
    assert finished.status is OrchestrationStatus.SUCCEEDED
    assert finished.cursor == 2 and finished.applied == 1
    back = await world.managers.tasks.get_task(ctx, reopened.id)
    assert back.status is TaskStatus.OPEN and back.archived_at is None
    assert await world.archived_titles(ctx) == ["archived"]


async def test_a_cleanup_step_that_errors_leaves_the_record_at_its_cursor(tmp_path: Path) -> None:
    class FailsOnce(TasksStorageMemoryImpl):
        failed = False

        async def update_archived_in_step(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if not FailsOnce.failed:
                FailsOnce.failed = True
                raise TimeoutError("the statement ran past its deadline")
            return await super().update_archived_in_step(*args, **kwargs)

    storage = StorageMemoryImpl()
    storage._tasks = FailsOnce(storage._outbox, storage._orchestrations)  # type: ignore[attr-defined]
    world = World(tmp_path, storage)
    ctx = await world.org()
    await world.done(ctx, "old", 100)
    record = await world.managers.tasks.open_cleanup(ctx)
    assert record is not None
    with pytest.raises(TimeoutError):
        await world.managers.tasks.step_cleanup(ctx, record)
    # Nothing moved: the work queue retries the item, and the retry starts
    # at the same cursor.
    same = await world.managers.orchestrations.get(ctx, record.id)
    assert same == record
    finished = await world.run(ctx, same)
    assert (finished.status, finished.applied) == (OrchestrationStatus.SUCCEEDED, 1)


async def test_an_archived_task_is_read_restored_and_reopened(world: World) -> None:
    ctx = await world.org()
    old = await world.done(ctx, "old", 100)
    other = await world.done(ctx, "other", 100)
    record = await world.managers.tasks.open_cleanup(ctx)
    assert record is not None
    await world.run(ctx, record)
    archived = await world.managers.tasks.get_task(ctx, old.id)
    assert archived.archived_at is not None and archived.status is TaskStatus.DONE
    restored = await world.managers.tasks.restore_task(ctx, old.id, archived.version)
    assert restored.archived_at is None and restored.status is TaskStatus.DONE
    assert await world.done_titles(ctx) == ["old"]
    with pytest.raises(ValidationFailed):
        await world.managers.tasks.restore_task(ctx, old.id, restored.version)
    # Reopening an archived task takes it out of the archive too.
    stored = await world.managers.tasks.get_task(ctx, other.id)
    reopened = await world.managers.tasks.update_task(
        ctx, stored.model_copy(update={"status": TaskStatus.OPEN}), stored.version
    )
    assert reopened.archived_at is None and reopened.status is TaskStatus.OPEN
    assert await world.archived_titles(ctx) == []
