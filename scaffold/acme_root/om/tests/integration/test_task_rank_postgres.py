"""Moves and the respace over Postgres: every manager over the relational
root. A hundred and fifty moves into one gap each write the moved task
alone; two moves sent at once both land; an edit of a task nobody moved
lands on the version its author read; and the sweep's respace gives a run
of long ranks short ones in the order it had, in one write."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from unit.test_task_import import World

from acme.om.base import new_id, utcnow
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import OpContext
from acme.om.storage.impl.pg_base import set_scope
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings
from acme.om.tasks.rules import needs_respace
from acme.om.tasks.types.filter import TaskFilter
from acme.om.tasks.types.task import Task, TaskScope

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


async def versions(world: World, ctx: OpContext) -> dict[str, int]:
    tasks = world.managers.tasks
    return {
        task.title: task.version
        for task in (await tasks.get_open_tasks(ctx, team(ctx), None, 200)).items
    }


async def split_one_gap(world: World, ctx: OpContext, moves: int) -> None:
    """a and c take turns right after b: every move halves one gap."""
    tasks = world.managers.tasks
    c, a, b = [await add(world, ctx, title) for title in ("c", "a", "b")]
    for index in range(moves):
        moved = a if index % 2 == 0 else c
        current = await tasks.get_task(ctx, moved.id)
        await tasks.move_task(ctx, moved.id, b.id, current.version)


async def test_every_move_into_one_gap_writes_the_moved_task_alone(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    world = World(tmp_path, storage)
    ctx = await world.org(Plan.TEAM)
    tasks = world.managers.tasks
    held = await add(world, ctx, "held")
    c, a, b = [await add(world, ctx, title) for title in ("c", "a", "b")]
    for index in range(150):
        moved, other = (a, c) if index % 2 == 0 else (c, a)
        before = await versions(world, ctx)
        current = await tasks.get_task(ctx, moved.id)
        await tasks.move_task(ctx, moved.id, b.id, current.version)
        after = await versions(world, ctx)
        assert [title for title in after if after[title] != before[title]] == [moved.title]
        assert (await world.open_titles(ctx))[:3] == ["b", moved.title, other.title]
    # The task read before every one of those moves is written on the
    # version its author read.
    edited = await tasks.update_task(
        ctx, held.model_copy(update={"title": "held, edited"}), held.version
    )
    assert edited.version == held.version + 1


async def test_two_moves_at_once_both_land(storage: StoragePostgresImpl, tmp_path: Path) -> None:
    world = World(tmp_path, storage)
    ctx = await world.org(Plan.TEAM)
    tasks = world.managers.tasks
    last, x, y, anchor = [await add(world, ctx, t) for t in ("last", "x", "y", "anchor")]
    for _ in range(20):
        x, y = await asyncio.gather(
            tasks.move_task(ctx, x.id, anchor.id, x.version),
            tasks.move_task(ctx, y.id, anchor.id, y.version),
        )
        titles = await world.open_titles(ctx)
        assert titles[0] == "anchor" and titles[-1] == "last"
        assert sorted(titles[1:3]) == ["x", "y"]
    assert (await tasks.get_task(ctx, last.id)).version == last.version


async def test_the_sweep_respaces_a_long_run_in_one_write(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    world = World(tmp_path, storage)
    ctx = await world.org(Plan.TEAM)
    elsewhere = await world.org(Plan.TEAM)
    tasks = world.managers.tasks
    below = await add(world, ctx, "below")
    await split_one_gap(world, ctx, 90)
    await split_one_gap(world, elsewhere, 90)
    top = await add(world, ctx, "top")
    order = await world.open_titles(ctx)
    before = await versions(world, ctx)
    task_storage = storage.get_tasks_storage()
    assert await task_storage.read_long_place(ctx.org_id) is not None

    respaced = await tasks.respace_ranks(ctx)

    assert respaced > 0
    assert await world.open_titles(ctx) == order
    after = await versions(world, ctx)
    changed = [title for title in after if after[title] != before[title]]
    assert len(changed) == respaced and top.title not in changed and below.title not in changed
    page = await tasks.get_open_tasks(ctx, team(ctx), None, 200)
    assert not any(needs_respace(task.rank) for task in page.items)
    assert await task_storage.read_long_place(ctx.org_id) is None
    # The release before reads the position this release never writes: every
    # row created, moved ninety times, and respaced holds its rank's float.
    sessions = storage._sessions  # type: ignore[attr-defined]
    async with sessions[DatabaseRole.CORE]() as session:
        await set_scope(session, ctx.org_id, None, None)
        rows = (await session.execute(text("SELECT rank, position FROM core.tasks"))).all()
    assert len(rows) == len(order)
    assert all(position == float(rank) for rank, position in rows)
    assert await task_storage.read_long_place(elsewhere.org_id) is not None, "per tenant"
    assert await tasks.respace_ranks(ctx) == 0


async def test_the_long_rank_is_read_through_its_index(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    """The respace's read runs on every sweep pass for every tenant, so it
    must cost nothing when the tenant has no long rank: the plan reads the
    partial index, which holds only such ranks."""
    world = World(tmp_path, storage)
    ctx = await world.org(Plan.TEAM)
    for index in range(50):
        await add(world, ctx, f"t{index}")
    sessions = storage._sessions  # type: ignore[attr-defined]
    async with sessions[DatabaseRole.CORE]() as session:
        await set_scope(session, ctx.org_id, None, None)
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = (
            await session.execute(
                text(
                    "EXPLAIN SELECT rank, id FROM core.tasks WHERE org_id = :org"
                    " AND scale(rank) > 24 AND deleted_at IS NULL AND status = :status"
                    " ORDER BY rank, id LIMIT 1"
                ),
                {"org": ctx.org_id, "status": "open"},
            )
        ).scalars()
        assert "ix_tasks_org_id_rank_long" in "\n".join(plan)
