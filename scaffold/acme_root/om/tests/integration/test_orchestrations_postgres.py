"""The two long-running records over Postgres: every manager over the
relational root, the file in the local store. An import on Free parks at
the plan's bound in the step's own commit, a plan that rises queues its wake
in the account's commit, and the woken import finishes; the day's cleanup
opens once, archives the old done task, and leaves the one reopened. A
candidate reopened between a step's read and its write is the storage
contract's case."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from unit.test_task_cleanup import World as CleanupWorld
from unit.test_task_import import World as ImportWorld
from unit.test_task_import import titled

from acme.om.billing.types.plan import Plan
from acme.om.orchestrations.types.orchestration import OrchestrationStatus, ParkReason
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.settings import MigrationSettings
from acme.om.tasks.types.task import TaskStatus
from acme.om.work.types.work_item import WorkKind

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


async def test_an_import_parks_on_the_plan_and_the_rising_plan_wakes_it(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    world = ImportWorld(tmp_path, storage)
    ctx = await world.org(Plan.FREE)
    started = await world.start(ctx, titled(25))
    parked = await world.run(ctx, started.id)
    assert (parked.status, parked.park_reason) == (
        OrchestrationStatus.PARKED,
        ParkReason.PLAN_LIMIT,
    )
    assert (parked.cursor, parked.applied) == (10, 10)
    await world.managers.billing.grant_seeded_plan(ctx, Plan.PRO)
    claimed = await storage.get_work_storage().claim_next(
        "default", [WorkKind.WAKE_PARKED], "it", timedelta(seconds=30)
    )
    assert claimed is not None and claimed[0] == ctx.org_id
    assert await world.managers.orchestrations.wake(ctx, ParkReason.PLAN_LIMIT) == 1
    done = await world.run(ctx, started.id)
    assert (done.status, done.applied, done.cursor) == (OrchestrationStatus.SUCCEEDED, 25, 25)
    assert len(await world.open_titles(ctx)) == 25


async def test_the_days_cleanup_archives_the_old_done_task_and_leaves_the_rest(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    world = CleanupWorld(tmp_path, storage)
    ctx = await world.org()
    await world.done(ctx, "old", 120)
    reopened = await world.done(ctx, "reopened", 120)
    await world.done(ctx, "recent", 89)
    record = await world.managers.tasks.open_cleanup(ctx)
    again = await world.managers.tasks.open_cleanup(ctx)
    assert record is not None and again is not None and again.id == record.id
    current = await world.managers.tasks.get_task(ctx, reopened.id)
    await world.managers.tasks.update_task(
        ctx, current.model_copy(update={"status": TaskStatus.OPEN}), current.version
    )
    finished = await world.run(ctx, record)
    assert (finished.status, finished.applied) == (OrchestrationStatus.SUCCEEDED, 1)
    assert await world.archived_titles(ctx) == ["old"]
    assert await world.done_titles(ctx) == ["recent"]
