"""A lane's cap on one tenant's claimed items, and a tenant's own cap in its
place, over Postgres: every manager over the relational root, so the claim
that passes a tenant over is the statement a worker runs. The cases are the
ones the memory suite runs."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from contracts.work_cap import (
    a_burst_drains_as_fast_as_the_worker_runs_at_a_cap_of_two,
    a_second_item_is_passed_over_at_a_cap_of_one,
    a_tenants_own_cap_holds_in_place_of_the_lanes,
    a_tenants_own_cap_holds_on_a_lane_with_no_cap,
)

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.settings import MigrationSettings

pytestmark = pytest.mark.integration

APP = AppContext(type=AppType.PORTAL, version="portal@test")


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


async def test_a_tenant_at_a_cap_of_one_is_passed_over_while_another_tenants_item_is_claimed(
    managers: Managers, storage: StoragePostgresImpl
) -> None:
    ann, bob = await an_org(managers), await an_org(managers)
    await a_second_item_is_passed_over_at_a_cap_of_one(
        managers, storage.get_work_storage(), ann, bob
    )


async def test_a_burst_at_a_cap_of_two_drains_as_fast_as_the_worker_runs(
    managers: Managers, storage: StoragePostgresImpl
) -> None:
    ann, bob = await an_org(managers), await an_org(managers)
    await a_burst_drains_as_fast_as_the_worker_runs_at_a_cap_of_two(
        managers, storage.get_work_storage(), ann, bob
    )


async def test_a_tenants_own_cap_of_one_holds_in_place_of_a_lane_cap_of_four(
    managers: Managers, storage: StoragePostgresImpl
) -> None:
    ann, bob = await an_org(managers), await an_org(managers)
    await a_tenants_own_cap_holds_in_place_of_the_lanes(
        managers, storage.get_work_storage(), ann, bob
    )


async def test_a_tenants_own_cap_holds_on_a_lane_with_no_cap(
    managers: Managers, storage: StoragePostgresImpl
) -> None:
    ann, bob = await an_org(managers), await an_org(managers)
    await a_tenants_own_cap_holds_on_a_lane_with_no_cap(
        managers, storage.get_work_storage(), ann, bob
    )
