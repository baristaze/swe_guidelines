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
from contracts.lease_job import (
    StartsAJob,
    a_job_that_misses_its_window_lets_the_lease_lapse,
    a_job_that_waited_past_its_window_starts_inside_the_margin,
    a_resource_with_as_many_labels_as_a_real_one_is_matched,
    the_jobs_worker_keeps_the_lease_and_no_one_else_does,
)

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
from acme.om.leases.hooks import ResourceKindInterface
from acme.om.leases.impl.kinds import NoopResourceKindImpl, OrchestrationWaiterImpl
from acme.om.leases.impl.manager import LeasesManagerImpl, LeasesOptions
from acme.om.leases.types.lease import Lease, LeaseStatus
from acme.om.leases.types.request import LeaseRequest, RequestStatus, WaiterKind
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

    def __init__(
        self,
        storage: StoragePostgresImpl,
        tmp_path: Path,
        kind: ResourceKindInterface | None = None,
    ) -> None:
        self.storage = storage
        self.managers: Managers = build_managers(storage, InfraLocalImpl(tmp_path))
        self.now = utcnow()
        self.margin = MARGIN
        self.kind = kind or NoopResourceKindImpl()
        self.leases = self.leases_of(sweep_orgs=100_000)
        self.slug = f"ajax-{new_id().hex[-8:]}"

    def leases_of(self, *, sweep_orgs: int) -> LeasesManagerImpl:
        return LeasesManagerImpl(
            self.storage.get_lease_storage(),
            self.managers.tenancy,
            self.managers.outbox,
            LeasesOptions(margin=MARGIN, sweep_orgs=sweep_orgs),
            kinds={ResourceKind.NOOP: self.kind},
            waiters={
                WaiterKind.ORCHESTRATION: OrchestrationWaiterImpl(self.managers.orchestrations)
            },
            work=self.managers.work,
            clock=lambda: self.now,
        )

    def rctx(self) -> RequestContext:
        return RequestContext(request_id=new_id(), app=APP)

    async def owner(self, slug: str | None = None) -> TenantContext:
        slug = slug or self.slug
        owner, _ = await self.managers.tenancy.bootstrap(
            self.rctx(), "Ajax", slug, f"a-{slug}@x.test", "Ann"
        )
        return owner

    async def delete_org(self, ctx: TenantContext) -> None:
        tenancy = self.storage.get_tenancy_storage()
        org = await tenancy.read_org(ctx.org_id)
        assert org is not None
        await tenancy.write_org(
            ctx.org_id, org.model_copy(update={"deleted_at": utcnow(), "deleted_by": ctx.user_id})
        )

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

    def clock(self) -> datetime:
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


@pytest.fixture
def job_world(storage: StoragePostgresImpl, tmp_path: Path) -> World:
    """A world whose grants start a job, which its worker keeps the lease
    through."""
    return World(storage, tmp_path, StartsAJob())


async def test_the_jobs_worker_keeps_the_lease_and_no_one_else_does(job_world: World) -> None:
    await the_jobs_worker_keeps_the_lease_and_no_one_else_does(job_world)


async def test_a_job_that_waited_past_its_window_starts_inside_the_margin(
    job_world: World,
) -> None:
    await a_job_that_waited_past_its_window_starts_inside_the_margin(job_world)


async def test_a_job_that_misses_its_window_lets_the_lease_lapse(job_world: World) -> None:
    await a_job_that_misses_its_window_lets_the_lease_lapse(job_world)


async def test_a_resource_with_as_many_labels_as_a_real_one_is_matched(
    job_world: World,
) -> None:
    await a_resource_with_as_many_labels_as_a_real_one_is_matched(job_world)


def an_ask(
    resource: Resource,
    *,
    term_seconds: int = 60,
    wait_seconds: int = 3600,
    waiter: UUID | None = None,
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
        wait_seconds=wait_seconds,
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


async def test_a_deleted_org_takes_no_place_of_a_live_one_in_the_sweep(world: World) -> None:
    """A deleted org's lapsed lease stays due until its purge takes it, and
    the sweep reads on past it: in a batch of one org, the live org's lapsed
    lease still ends. Other cases leave due orgs, the storage contract's
    tenants among them, which no org row names; so the case runs a year on,
    and a full sweep a second before the two leases lapse leaves only them
    due."""
    world.later(timedelta(days=365))
    first, second = await world.owner(), await world.owner(f"bolt-{new_id().hex[-8:]}")
    gone, live = sorted((first, second), key=lambda ctx: ctx.org_id)
    held: dict[UUID, Lease] = {}
    for ctx in (gone, live):
        standing = await world.leases.ask(ctx, an_ask(await world.dock(ctx), term_seconds=60))
        assert standing.lease is not None
        held[ctx.org_id] = standing.lease
    await world.delete_org(gone)
    world.later(timedelta(seconds=60) + MARGIN - timedelta(seconds=1))
    await world.leases.sweep(world.rctx())
    assert (await world.leases.get_lease(live, held[live.org_id].id)).status is LeaseStatus.ACTIVE

    world.later(timedelta(seconds=2))
    assert await world.leases_of(sweep_orgs=1).sweep(world.rctx()) == 1
    ended = await world.leases.get_lease(live, held[live.org_id].id)
    assert ended.status is LeaseStatus.EXPIRED
    kept = await world.storage.get_lease_storage().read_lease(gone.org_id, held[gone.org_id].id)
    assert kept is not None and kept.status is LeaseStatus.ACTIVE


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


async def test_a_parked_record_whose_request_expires_is_woken_and_reads_its_end(
    world: World,
) -> None:
    owner = await world.owner()
    ann = await world.member("ann")
    dock = await world.dock(owner)
    assert (await world.leases.ask(ann, an_ask(dock))).lease is not None
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
    ask = an_ask(dock, wait_seconds=60, waiter=record.id)
    waits = await world.leases.ask(
        owner, ask, Step(record=parked_at, expected_version=record.version)
    )
    assert waits.lease is None

    world.later(timedelta(seconds=61))
    await world.leases.sweep(world.rctx())
    # The expiry landed the record's wake in its own commit, and the relay
    # queued it.
    wakes = [
        (org_id, WakeParkedPayload.model_validate(payload))
        for org_id, _, payload in await world.drain(WorkKind.WAKE_PARKED)
    ]
    ours = [payload for org_id, payload in wakes if org_id == owner.org_id]
    assert [(p.reason, p.record_id) for p in ours] == [(ParkReason.RESOURCE, record.id)]
    assert await world.managers.orchestrations.wake(owner, ours[0].reason, ours[0].record_id) == 1
    assert (await world.managers.orchestrations.get(owner, record.id)).status is (
        OrchestrationStatus.RUNNING
    )
    # Its next step asks again by its key and reads the end.
    again = await world.leases.ask(owner, ask)
    assert again.lease is None and again.request.status is RequestStatus.EXPIRED
    steps = await world.drain(WorkKind.ORCHESTRATION)
    assert {target for org_id, target, _ in steps if org_id == owner.org_id} == {record.id}
