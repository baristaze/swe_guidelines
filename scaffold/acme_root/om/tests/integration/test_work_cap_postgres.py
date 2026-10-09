"""A lane's cap on one tenant's claimed items over Postgres: every manager
over the relational root, so the claim, the count of the items ahead, and
the hand-back are the statements a worker runs. The count's own cases are
the storage contract's."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from contracts.work_storage import make_item

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.settings import MigrationSettings
from acme.om.work.impl.manager import WorkOptions
from acme.om.work.types.work_item import WorkItem, WorkKind, WorkStatus

pytestmark = pytest.mark.integration

APP = AppContext(type=AppType.PORTAL, version="portal@test")
WORKER = AppContext(type=AppType.WORKER, version="worker@test")
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


async def test_a_tenant_over_its_cap_waits_while_another_tenants_item_is_claimed(
    managers: Managers, storage: StoragePostgresImpl
) -> None:
    lane = f"cap-{new_id().hex[-12:]}"  # its own lane, in a database the suite shares
    ann, bob = await an_org(managers), await an_org(managers)

    async def enqueued(by: TenantContext, ready_ago: int) -> WorkItem:
        item = make_item(lane=lane, available_in=timedelta(seconds=-ready_ago))
        return await managers.work.enqueue(by, item)

    first, second, bobs = await enqueued(ann, 3), await enqueued(ann, 2), await enqueued(bob, 1)

    async def claim() -> tuple[TenantContext, WorkItem] | None:
        rctx = RequestContext(request_id=new_id(), app=WORKER)
        return await managers.work.claim(rctx, lane, [WorkKind.NOOP], "w1", LEASE, 1)

    running = await claim()
    assert running is not None and running[1].id == first.id
    before = utcnow()
    claimed = await claim()
    assert claimed is not None
    assert (claimed[0].org_id, claimed[1].id, claimed[1].attempts) == (bob.org_id, bobs.id, 1)

    waiting = await storage.get_work_storage().read_item(ann.org_id, second.id)
    assert waiting is not None
    assert waiting.status is WorkStatus.QUEUED and waiting.attempts == 0, "no attempt spent"
    assert waiting.available_at >= before + WorkOptions().over_cap_delay, "back with a delay"
    assert (waiting.claim_token, waiting.updated_by) == (None, EMPTY_UUID)
    assert await claim() is None
