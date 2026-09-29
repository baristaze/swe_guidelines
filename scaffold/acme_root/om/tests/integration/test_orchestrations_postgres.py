"""The long-running record over Postgres: every manager over the relational
root. A `noop` record steps through its count, each running write asking
for the next step as a work item in the same commit, and succeeds; a record
parked for a provider is woken by the event that clears its reason, and
runs again from its cursor. A step that read a version another writer moved
is the storage contract's case."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.orchestrations.types.orchestration import (
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    ParkReason,
)
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.settings import MigrationSettings
from acme.om.work.types.work_item import WorkKind

pytestmark = pytest.mark.integration

APP = AppContext(type=AppType.PORTAL, version="portal@test")
LEASE = timedelta(seconds=30)


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


@pytest.fixture
def managers(storage: StoragePostgresImpl, tmp_path: Path) -> Managers:
    return build_managers(storage, InfraLocalImpl(tmp_path))


async def an_org(managers: Managers) -> TenantContext:
    slug = f"ajax-{new_id().hex[-8:]}"
    owner, _ = await managers.tenancy.bootstrap(
        RequestContext(request_id=new_id(), app=APP),
        "Ajax",
        slug,
        f"ann-{slug}@example.test",
        "Ann",
    )
    return owner


def a_record(steps: int) -> Orchestration:
    now = utcnow()
    actor = new_id()
    return Orchestration(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        kind=OrchestrationKind.NOOP,
        input={"steps": steps},
    )


async def next_step(storage: StoragePostgresImpl) -> tuple[UUID, UUID] | None:
    """The tenant and the record of the step a worker would claim next."""
    claimed = await storage.get_work_storage().claim_next(
        "default", [WorkKind.ORCHESTRATION], "it", LEASE
    )
    return None if claimed is None else (claimed[0], claimed[1].target_id)


async def test_a_noop_record_steps_through_its_count_one_work_item_a_step(
    storage: StoragePostgresImpl, managers: Managers
) -> None:
    ctx = await an_org(managers)
    orchestrations = managers.orchestrations
    record = await orchestrations.start(ctx, a_record(steps=3))
    steps = 0
    while record.status is OrchestrationStatus.RUNNING:
        assert await next_step(storage) == (ctx.org_id, record.id)
        record = await orchestrations.step_noop(ctx, await orchestrations.get(ctx, record.id))
        steps += 1
    assert steps == 3
    assert (record.status, record.cursor, record.total) == (OrchestrationStatus.SUCCEEDED, 3, 3)
    assert await orchestrations.get(ctx, record.id) == record
    assert await next_step(storage) is None, "a settled record asks for no step"


async def test_a_parked_record_is_woken_by_its_reason_and_runs_on_from_its_cursor(
    storage: StoragePostgresImpl, managers: Managers
) -> None:
    ctx = await an_org(managers)
    orchestrations = managers.orchestrations
    started = await orchestrations.start(ctx, a_record(steps=2))
    assert await next_step(storage) == (ctx.org_id, started.id)
    first = await orchestrations.step_noop(ctx, started)
    assert await next_step(storage) == (ctx.org_id, started.id)
    # A step that found its provider away parks the record at its cursor.
    parked = first.model_copy(
        update={
            "status": OrchestrationStatus.PARKED,
            "park_reason": ParkReason.PROVIDER_UNAVAILABLE,
            "version": first.version + 1,
        }
    )
    await storage.get_orchestrations_storage().write_orchestration(
        ctx.org_id, parked, first.version, ()
    )
    assert await next_step(storage) is None, "a parked record asks for no step"
    assert await orchestrations.wake(ctx, ParkReason.PROVIDER_UNAVAILABLE) == 1
    assert await next_step(storage) == (ctx.org_id, started.id)
    woken = await orchestrations.get(ctx, started.id)
    assert (woken.status, woken.park_reason, woken.cursor) == (
        OrchestrationStatus.RUNNING,
        None,
        1,
    )
    done = await orchestrations.step_noop(ctx, woken)
    assert (done.status, done.cursor) == (OrchestrationStatus.SUCCEEDED, 2)
    assert await orchestrations.wake(ctx, ParkReason.PROVIDER_UNAVAILABLE) == 0
