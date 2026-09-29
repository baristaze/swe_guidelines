"""The bulk change over Postgres: every manager over the relational root. A
whole list of more than two batches goes in one commit a batch; a reopen on
Free opens up to the bound and names it; a change under one tenant leaves
another's tasks alone; and every changed task lands its outbox row beside
it, so the relay announces each one. The per-task fence is the storage
contract's case."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from contracts.outbox_storage import claim_all
from unit.test_task_import import World

from acme.om.base import new_id, utcnow
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import OpContext
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.settings import MigrationSettings
from acme.om.tasks.rules import BULK_BATCH
from acme.om.tasks.types.bulk import BulkAction, SkippedTask, SkipReason
from acme.om.tasks.types.filter import TaskFilter
from acme.om.tasks.types.task import Task, TaskScope, TaskStatus

pytestmark = pytest.mark.integration


@pytest.fixture
async def storage(
    migration_settings: MigrationSettings, migrated: object
) -> AsyncIterator[StoragePostgresImpl]:
    root = StoragePostgresImpl(
        migration_settings.role_urls(),
        migration_settings.role_pools(),
        system_urls=migration_settings.system_role_urls(),
    )
    yield root
    await root.close()


def team(ctx: OpContext) -> TaskFilter:
    return TaskFilter(scope=TaskScope.TEAM, user_id=ctx.user_id)


async def add(world: World, ctx: OpContext, title: str) -> Task:
    now = utcnow()
    task = Task(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        title=title,
    )
    return await world.managers.tasks.create_task(ctx, task)


async def test_mark_all_goes_a_batch_a_commit_and_undo_restores_the_list(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    world = World(tmp_path, storage)
    ctx = await world.org(Plan.TEAM)
    elsewhere = await world.org(Plan.TEAM)
    theirs = await add(world, elsewhere, "theirs")
    total = 2 * BULK_BATCH + 7
    for index in range(total):
        await add(world, ctx, f"task {index:03}")
    before = await world.open_titles(ctx)
    tasks = world.managers.tasks
    assert await tasks.count_tasks(ctx, team(ctx), TaskStatus.OPEN) == total

    marked = await tasks.change_list(ctx, BulkAction.COMPLETE, team(ctx), TaskStatus.OPEN)

    assert marked.changed_count == total and marked.skipped_count == 0
    assert await tasks.count_tasks(ctx, team(ctx), TaskStatus.OPEN) == 0
    assert await tasks.count_tasks(ctx, team(ctx), TaskStatus.DONE) == total
    assert (await tasks.get_task(elsewhere, theirs.id)).status is TaskStatus.OPEN
    # Every changed task landed its outbox row in its batch's commit, and the
    # relay took each one: nothing is left for the sweep.
    assert await claim_all(storage.get_outbox_storage(), limit=1000) == []

    undone = await tasks.change_tasks(ctx, BulkAction.REOPEN, list(reversed(marked.changed)))
    assert undone.changed_count == total
    assert await world.open_titles(ctx) == before


async def test_a_reopen_on_free_opens_up_to_the_bound(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    world = World(tmp_path, storage)
    ctx = await world.org(Plan.FREE)
    tasks = world.managers.tasks
    finished = [await add(world, ctx, f"done {i}") for i in range(6)]
    await tasks.change_tasks(ctx, BulkAction.COMPLETE, [t.id for t in finished])
    for index in range(7):
        await add(world, ctx, f"open {index}")

    reopened = await tasks.change_list(ctx, BulkAction.REOPEN, team(ctx), TaskStatus.DONE)

    assert reopened.changed_count == 3 and reopened.skipped_count == 3
    assert {s.reason for s in reopened.skipped} == {SkipReason.PLAN_LIMIT}
    assert reopened.plan_bound is not None and reopened.plan_bound.limit == 10
    assert await tasks.count_active_tasks(ctx) == 10


async def test_a_reopen_by_ids_lands_in_order_skips_a_moved_task_and_announces_each(
    storage: StoragePostgresImpl, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch is one statement over Postgres: each task fenced on its own
    version, the last named on top, and one event per task that changed, in
    the order named. A task edited between the read and the write is left
    alone, and announces nothing for the change."""
    world = World(tmp_path, storage)
    ctx = await world.org(Plan.TEAM)
    tasks = world.managers.tasks
    kept = await add(world, ctx, "kept open")
    finished = [await add(world, ctx, f"done {index}") for index in range(5)]
    await tasks.change_tasks(ctx, BulkAction.COMPLETE, [t.id for t in finished])
    moved = finished[2]
    task_storage = storage.get_tasks_storage()
    write = task_storage.update_tasks_if_current

    async def edited_first(*args: object) -> tuple[bool, ...]:
        # Another writer lands between the change's read and its write.
        current = await tasks.get_task(ctx, moved.id)
        await tasks.update_task(
            ctx, current.model_copy(update={"title": "renamed"}), current.version
        )
        monkeypatch.setattr(task_storage, "update_tasks_if_current", write)
        return await write(*args)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(task_storage, "update_tasks_if_current", edited_first)
    head = await storage.get_event_storage().read_head(ctx.org_id)
    named = [t.id for t in reversed(finished)]

    outcome = await tasks.change_tasks(ctx, BulkAction.REOPEN, named)

    assert outcome.changed == tuple(i for i in named if i != moved.id)
    assert outcome.skipped == (SkippedTask(id=moved.id, reason=SkipReason.CHANGED),)
    assert await world.open_titles(ctx) == ["done 0", "done 1", "done 3", "done 4", "kept open"]
    stored = await tasks.get_task(ctx, moved.id)
    assert stored.status is TaskStatus.DONE and stored.title == "renamed"
    announced = [
        (e.kind, e.target_id)
        for e in await storage.get_event_storage().read_after(ctx.org_id, head, 100)
        if e.target_id != moved.id
    ]
    assert announced == [("tasks.task.updated", i) for i in outcome.changed]
    assert await claim_all(storage.get_outbox_storage(), limit=1000) == []
    assert kept.id not in outcome.changed
