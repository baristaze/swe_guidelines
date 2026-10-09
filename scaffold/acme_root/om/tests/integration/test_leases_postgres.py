"""The leases over Postgres: the manager over the relational root, on a clock
the case moves. A lease past its expiry holds its resource until the skew
margin has passed too; then the sweep, which reads the due orgs across
tenants, ends it and grants the head of the line a greater token. A record
parked in line is woken with its lease when the resource frees, through the
work item the grant lands in its own commit. The race of two grants is the
storage contract's case."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id, utcnow
from acme.om.context import (
    AppContext,
    AppType,
    CredentialKind,
    RequestContext,
    Role,
    TenantContext,
    build_context,
)
from acme.om.leases.impl.kinds import NoopResourceKindImpl, OrchestrationWaiterImpl
from acme.om.leases.impl.manager import LeasesManagerImpl, LeasesOptions
from acme.om.leases.types.lease import LeaseStatus
from acme.om.leases.types.request import LeaseRequest, WaiterKind
from acme.om.leases.types.resource import Resource, ResourceKind
from acme.om.orchestrations.rules import advanced
from acme.om.orchestrations.types.orchestration import (
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    ParkReason,
    Step,
)
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.settings import MigrationSettings
from acme.om.tenancy.rules import ROLE_PERMISSIONS
from acme.om.work.types.work_item import WakeParkedPayload, WorkKind

pytestmark = pytest.mark.integration

APP = AppContext(type=AppType.PORTAL, version="portal@test")
MARGIN = timedelta(seconds=30)
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


class World:
    """The managers over Postgres, and a leases manager on the case's clock.
    The sweep visits every due org, since other cases leave theirs."""

    def __init__(self, storage: StoragePostgresImpl, tmp_path: Path) -> None:
        self.storage = storage
        self.managers: Managers = build_managers(storage, InfraLocalImpl(tmp_path))
        self.now = utcnow()
        self.leases = LeasesManagerImpl(
            storage.get_lease_storage(),
            self.managers.tenancy,
            self.managers.outbox,
            LeasesOptions(margin=MARGIN, sweep_orgs=100_000),
            kinds={ResourceKind.NOOP: NoopResourceKindImpl()},
            waiters={
                WaiterKind.ORCHESTRATION: OrchestrationWaiterImpl(self.managers.orchestrations)
            },
            clock=lambda: self.now,
        )
        self.slug = f"ajax-{new_id().hex[-8:]}"

    def rctx(self) -> RequestContext:
        return RequestContext(request_id=new_id(), app=APP)

    async def owner(self) -> TenantContext:
        owner, _ = await self.managers.tenancy.bootstrap(
            self.rctx(), "Ajax", self.slug, f"a-{self.slug}@x.test", "Ann"
        )
        return owner

    async def member(self, name: str) -> TenantContext:
        creator, user, _ = await self.managers.tenancy.add_member(
            self.rctx(), self.slug, f"{name}-{self.slug}@x.test", name, Role.MEMBER
        )
        return build_context(
            self.rctx(),
            user_id=user.id,
            org_id=creator.org_id,
            role=Role.MEMBER,
            permissions=ROLE_PERMISSIONS[Role.MEMBER],
            credential_kind=CredentialKind.SESSION_TOKEN,
        )

    async def dock(self, ctx: TenantContext) -> Resource:
        now = utcnow()
        return await self.leases.register(
            ctx,
            Resource(
                id=new_id(),
                created_at=now,
                updated_at=now,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
                kind=ResourceKind.NOOP,
                ref_id=new_id(),
            ),
        )

    def later(self, by: timedelta) -> datetime:
        self.now += by
        return self.now

    async def drain(self, kind: WorkKind) -> list[tuple[UUID, UUID, dict[str, object]]]:
        """Claims every queued item of the kind, so no later case meets one:
        each one's tenant, its target, and its payload."""
        claimed: list[tuple[UUID, UUID, dict[str, object]]] = []
        for _ in range(50):
            item = await self.storage.get_work_storage().claim_next("default", [kind], "it", LEASE)
            if item is None:
                return claimed
            org_id, work = item
            claimed.append((org_id, work.target_id, dict(work.payload)))
        raise AssertionError(f"more than 50 {kind.value} items queued")


@pytest.fixture
def world(storage: StoragePostgresImpl, tmp_path: Path) -> World:
    return World(storage, tmp_path)


def an_ask(
    resource: Resource, *, term_seconds: int = 60, waiter: UUID | None = None
) -> LeaseRequest:
    now = utcnow()
    return LeaseRequest(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=new_id(),
        updated_by=new_id(),
        idempotency_key=new_id(),
        kind=ResourceKind.NOOP,
        resource_id=resource.id,
        waiter_kind=None if waiter is None else WaiterKind.ORCHESTRATION,
        waiter_id=waiter,
        term_seconds=term_seconds,
    )


async def test_a_lapsed_lease_holds_until_the_margin_passes_then_the_line_moves_on(
    world: World,
) -> None:
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.dock(owner)
    held = await world.leases.ask(ann, an_ask(dock, term_seconds=60))
    waits = await world.leases.ask(bob, an_ask(dock))
    assert held.lease is not None and held.lease.token == 1
    assert waits.lease is None and waits.place == 1

    world.later(timedelta(seconds=60) + MARGIN - timedelta(seconds=1))
    await world.leases.sweep(world.rctx())
    still = await world.leases.get_resource(owner, dock.id)
    assert (still.lease_id, still.token) == (held.lease.id, 1)
    assert (await world.leases.get_request(bob, waits.request.id)).lease is None

    world.later(timedelta(seconds=2))
    await world.leases.sweep(world.rctx())
    lapsed = await world.leases.get_lease(ann, held.lease.id)
    assert lapsed.status is LeaseStatus.EXPIRED
    granted = await world.leases.get_request(bob, waits.request.id)
    assert granted.lease is not None and granted.lease.token == 2
    anchor = await world.leases.get_resource(owner, dock.id)
    assert (anchor.lease_id, anchor.token) == (granted.lease.id, 2)


async def test_a_parked_record_is_woken_with_its_lease_when_the_resource_frees(
    world: World,
) -> None:
    owner = await world.owner()
    ann = await world.member("ann")
    dock = await world.dock(owner)
    held = await world.leases.ask(ann, an_ask(dock))
    assert held.lease is not None
    now = utcnow()
    record = await world.managers.orchestrations.start(
        owner,
        Orchestration(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=owner.user_id,
            updated_by=owner.user_id,
            kind=OrchestrationKind.NOOP,
            input={"steps": 2},
        ),
    )
    parked_at = advanced(
        record, utcnow(), owner.user_id, cursor=record.cursor, total=None,
        park=ParkReason.RESOURCE,
    )  # fmt: skip
    ask = an_ask(dock, waiter=record.id)
    waits = await world.leases.ask(
        owner, ask, Step(record=parked_at, expected_version=record.version)
    )
    assert waits.lease is None
    parked = await world.managers.orchestrations.get(owner, record.id)
    assert (parked.status, parked.park_reason) == (OrchestrationStatus.PARKED, ParkReason.RESOURCE)

    await world.leases.release(ann, held.lease.id)
    # The grant landed the record's wake in its own commit, and the relay
    # queued it; the worker's WAKE_PARKED handler runs it so.
    wakes = [
        (org_id, WakeParkedPayload.model_validate(payload))
        for org_id, _, payload in await world.drain(WorkKind.WAKE_PARKED)
    ]
    ours = [payload for org_id, payload in wakes if org_id == owner.org_id]
    assert [(p.reason, p.record_id) for p in ours] == [(ParkReason.RESOURCE, record.id)]
    assert await world.managers.orchestrations.wake(owner, ours[0].reason, ours[0].record_id) == 1
    woken = await world.managers.orchestrations.get(owner, record.id)
    assert woken.status is OrchestrationStatus.RUNNING
    # Its next step asks again by its key and finds its lease.
    again = await world.leases.ask(owner, ask)
    assert again.lease is not None and again.lease.holder_id == owner.user_id
    assert again.lease.token == held.lease.token + 1
    steps = await world.drain(WorkKind.ORCHESTRATION)
    assert {target for org_id, target, _ in steps if org_id == owner.org_id} == {record.id}
