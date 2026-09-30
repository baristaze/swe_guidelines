"""An operator deletes a team org over Postgres, every manager over the
relational root: one commit closes it for everyone in it and queues
`DELETE_ORG` under the operator's identity, a repeat asks for nothing more,
and the item's last step deletes the org under the operator's name."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id
from acme.om.context import AppContext, AppType, OperatorRole, RequestContext, Role
from acme.om.exceptions import NotAuthenticated, NotFound
from acme.om.root import build_managers
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.settings import MigrationSettings
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.work.types.work_item import WorkKind

pytestmark = pytest.mark.integration

APP = AppContext(type=AppType.CLI, version="cli@test")


def request() -> RequestContext:
    return RequestContext(request_id=new_id(), app=APP)


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


async def test_an_operator_closes_a_team_org_at_once_and_its_work_deletes_it(
    storage: StoragePostgresImpl, tmp_path: Path
) -> None:
    managers = build_managers(storage, InfraLocalImpl(tmp_path), TenancyOptions(dev_sign_in=True))
    tenancy = managers.tenancy
    _, org = await tenancy.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await tenancy.add_member(request(), "ajax", "bob@example.test", "Bob", Role.MEMBER)
    login = await tenancy.sign_in.dev_sign_in(request(), "bob@example.test")
    issued = await tenancy.sign_in.exchange_login(
        await tenancy.authenticate_login(request(), login.token), org.id
    )
    await tenancy.bootstrap(
        request(), "Ops", "ops", "root@example.test", "Root", operator_role=OperatorRole.WRITE
    )
    token = await tenancy.grant_operator_token(request(), "root@example.test")
    admin = await tenancy.admit_operator(await tenancy.authenticate_login(request(), token.token))

    closed = await managers.tenancy_operator.delete_org(admin, org.id)

    tenants = storage.get_tenancy_storage()
    assert closed.deleted_at is None and closed.updated_by == admin.identity_id
    assert await tenants.count_members(org.id) == 0
    with pytest.raises(NotAuthenticated):
        await tenancy.authenticate(request(), issued.token)
    # A repeat before the queue has run asks for nothing more.
    assert await managers.tenancy_operator.delete_org(admin, org.id) == closed
    work = storage.get_work_storage()
    claimed = await work.claim_next("default", [WorkKind.DELETE_ORG], "it", timedelta(seconds=30))
    assert claimed is not None
    org_id, item = claimed
    assert (org_id, item.target_id, item.created_by) == (org.id, org.id, admin.identity_id)
    assert (
        await work.claim_next("default", [WorkKind.DELETE_ORG], "it", timedelta(seconds=30)) is None
    )

    # The item's last step, under the operator's name, as the worker runs it.
    ctx = await tenancy.service_context(request(), org.id, item.created_by)
    deleted = await tenancy.org.delete_closed_org(ctx)
    assert deleted is not None and deleted.deleted_by == admin.identity_id
    with pytest.raises(NotFound):
        await managers.tenancy_operator.delete_org(admin, org.id)
