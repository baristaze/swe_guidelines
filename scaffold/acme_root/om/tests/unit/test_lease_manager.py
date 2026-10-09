"""Leases on a resource, over the memory impls: the pure rules, and the
manager's line, grant, renewal, release, revocation, sweep, and waiters.
The races of two connections are the Postgres suite's; here each guard is
the conditional write a caller that arrives second is refused by."""

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
from acme.om.exceptions import LeaseEnded, NotAuthorized, NotFound, ValidationFailed
from acme.om.leases.hooks import ResourceKindInterface
from acme.om.leases.impl.kinds import NoopResourceKindImpl, OrchestrationWaiterImpl
from acme.om.leases.impl.manager import LeasesManagerImpl, LeasesOptions
from acme.om.leases.rules import measured_hold, place_of, rank_at, replay, stands_in
from acme.om.leases.types.lease import Lease, LeaseStatus
from acme.om.leases.types.request import (
    EndReason,
    LeaseRequest,
    RequestStatus,
    WaiterKind,
)
from acme.om.leases.types.resource import Resource, ResourceKind
from acme.om.orchestrations.rules import advanced
from acme.om.orchestrations.types.orchestration import (
    FailReason,
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    ParkReason,
    Step,
)
from acme.om.outbox.types.row import OutboxRow
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tenancy.rules import ROLE_PERMISSIONS
from acme.om.work.types.work_item import WakeParkedPayload, WorkKind

APP = AppContext(type=AppType.PORTAL, version="portal@test")
MARGIN = timedelta(seconds=30)


def a_resource(labels: tuple[str, ...] = (), max_term_seconds: int = 300) -> Resource:
    now = utcnow()
    return Resource(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=new_id(),
        updated_by=new_id(),
        kind=ResourceKind.NOOP,
        ref_id=new_id(),
        labels=labels,
        max_term_seconds=max_term_seconds,
    )


def an_ask(
    resource: Resource | None = None,
    *,
    labels: tuple[str, ...] | None = None,
    key: UUID | None = None,
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
        idempotency_key=key or new_id(),
        kind=ResourceKind.NOOP,
        resource_id=None if resource is None else resource.id,
        labels=labels if resource is None else None,
        waiter_kind=None if waiter is None else WaiterKind.ORCHESTRATION,
        waiter_id=waiter,
        term_seconds=term_seconds,
        wait_seconds=wait_seconds,
    )


# The pure rules.


def test_a_selector_stands_in_the_line_of_every_resource_with_its_labels() -> None:
    cold, dry = a_resource(("cold", "north")), a_resource(("dry",))
    selector = an_ask(labels=("cold",)).model_copy(update={"rank": 1.0})
    assert stands_in(selector, cold) and not stands_in(selector, dry)
    named = an_ask(dry)
    assert stands_in(named, dry) and not stands_in(named, cold)
    assert not stands_in(named.model_copy(update={"status": RequestStatus.GRANTED}), dry)


def test_a_reorder_takes_the_rank_between_its_new_neighbours() -> None:
    assert rank_at([], 0) == 1.0
    assert rank_at([1.0, 2.0, 3.0], 0) == 0.0
    assert rank_at([1.0, 2.0, 3.0], 1) == 1.5
    assert rank_at([1.0, 2.0, 3.0], 3) == 4.0


def test_the_measured_hold_moves_a_fifth_of_the_way_to_each_new_one() -> None:
    assert measured_hold(None, timedelta(seconds=50)) == 50.0
    assert measured_hold(50.0, timedelta(seconds=100)) == 60.0


def test_the_estimate_replays_the_lines_from_the_measured_holds() -> None:
    now = utcnow()
    held = a_resource(("cold",)).model_copy(
        update={
            "lease_id": new_id(),
            "held_until": now + timedelta(seconds=10),
            "mean_hold_seconds": 20.0,
        }
    )
    first, second, third = (
        an_ask(labels=("cold",)).model_copy(update={"rank": float(rank)}) for rank in (1, 2, 3)
    )
    waiting = [first, second, third]
    # One resource: it frees in ten seconds, then serves each for twenty.
    assert replay(first, [held], waiting, now) == 10.0
    assert replay(third, [held], waiting, now) == 50.0
    assert place_of(third, [held], waiting) == 3
    # A second, free one takes the first at once and halves the line.
    free = a_resource(("cold",)).model_copy(update={"mean_hold_seconds": 20.0})
    assert replay(first, [held, free], waiting, now) == 0.0
    assert replay(third, [held, free], waiting, now) == 20.0
    # No resource it can take: no estimate, and no place.
    lone = an_ask(labels=("dry",))
    assert replay(lone, [held], [lone], now) is None
    assert place_of(lone, [held], [lone]) is None


# The manager.


class Refusing(ResourceKindInterface):
    """A kind that no longer allows any request at the grant."""

    async def may_grant(
        self, ctx: TenantContext, resource: Resource, request: LeaseRequest
    ) -> bool:
        return False

    def grant_rows(
        self, ctx: TenantContext, resource: Resource, request: LeaseRequest, lease: Lease
    ) -> tuple[OutboxRow, ...]:
        return ()


class World:
    def __init__(
        self,
        tmp_path: Path,
        kind: ResourceKindInterface | None = None,
        options: LeasesOptions | None = None,
    ) -> None:
        self.storage = StorageMemoryImpl()
        self.managers: Managers = build_managers(self.storage, InfraLocalImpl(tmp_path))
        self.now = utcnow()
        self.leases = LeasesManagerImpl(
            self.storage.get_lease_storage(),
            self.managers.tenancy,
            self.managers.outbox,
            options or LeasesOptions(margin=MARGIN),
            kinds={ResourceKind.NOOP: kind or NoopResourceKindImpl()},
            waiters={
                WaiterKind.ORCHESTRATION: OrchestrationWaiterImpl(self.managers.orchestrations)
            },
            clock=lambda: self.now,
        )
        self.slug = f"ajax-{new_id().hex[-8:]}"

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
            ctx.org_id, org.model_copy(update={"deleted_at": self.now, "deleted_by": ctx.user_id})
        )

    async def member(self, name: str, role: Role = Role.MEMBER) -> TenantContext:
        """A person of the org, as a session of theirs would act."""
        creator, user, _ = await self.managers.tenancy.add_member(
            self.rctx(), self.slug, f"{name}-{self.slug}@x.test", name, role
        )
        return build_context(
            self.rctx(),
            user_id=user.id,
            org_id=creator.org_id,
            role=role,
            permissions=ROLE_PERMISSIONS[role],
            credential_kind=CredentialKind.SESSION_TOKEN,
        )

    async def resource(self, ctx: TenantContext, *labels: str) -> Resource:
        return await self.leases.register(ctx, a_resource(labels))

    def later(self, by: timedelta) -> datetime:
        self.now += by
        return self.now


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


async def test_a_resource_registers_once_per_kind_and_row(world: World) -> None:
    owner = await world.owner()
    resource = await world.resource(owner)
    again = a_resource().model_copy(update={"ref_id": resource.ref_id, "token": 99})
    assert await world.leases.register(owner, again) == resource
    assert resource.token == 0 and resource.lease_id is None


async def test_a_free_resource_is_granted_at_once_and_the_next_waits_in_line(
    world: World,
) -> None:
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.resource(owner)
    first = await world.leases.ask(ann, an_ask(dock))
    assert first.lease is not None and first.lease.token == 1
    assert first.lease.holder_id == ann.user_id
    second = await world.leases.ask(bob, an_ask(dock))
    assert second.lease is None and second.place == 1
    assert second.estimate_seconds == pytest.approx(60.0)
    released = await world.leases.release(ann, first.lease.id)
    assert released.status is LeaseStatus.RELEASED
    now = await world.leases.get_request(bob, second.request.id)
    assert now.lease is not None and now.lease.token == 2 and now.lease.holder_id == bob.user_id


async def test_a_direct_ask_never_passes_anyone_waiting(world: World) -> None:
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.resource(owner)
    # Someone waits in front of a free resource: an offer a crash cut short.
    waiting = an_ask(dock).model_copy(
        update={"created_by": ann.user_id, "wait_until": world.now + timedelta(hours=1)}
    )
    await world.storage.get_lease_storage().create_request(owner.org_id, waiting, ())
    late = await world.leases.ask(bob, an_ask(dock))
    assert late.lease is None and late.place == 1
    first = await world.leases.get_request(ann, waiting.id)
    assert first.lease is not None and first.lease.holder_id == ann.user_id


async def test_an_ask_asked_again_by_its_key_joins_no_line_twice(world: World) -> None:
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.resource(owner)
    held = await world.leases.ask(ann, an_ask(dock))
    key = new_id()
    waits = await world.leases.ask(bob, an_ask(dock, key=key))
    again = await world.leases.ask(bob, an_ask(dock, key=key))
    assert again.request.id == waits.request.id and again.place == 1
    assert len((await world.leases.line(owner, dock.id)).requests) == 1
    assert held.lease is not None
    await world.leases.release(ann, held.lease.id)
    answered = await world.leases.ask(bob, an_ask(dock, key=key))
    assert answered.lease is not None and answered.request.id == waits.request.id


async def test_an_ask_is_held_to_its_kind_and_names_a_live_resource(world: World) -> None:
    owner = await world.owner()
    dock = await world.resource(owner)
    with pytest.raises(ValidationFailed):
        await world.leases.ask(owner, an_ask(dock).model_copy(update={"payload": {"x": 1}}))
    with pytest.raises(NotFound):
        await world.leases.ask(owner, an_ask(a_resource()))
    await world.leases.retire(owner, dock.id)
    with pytest.raises(NotFound):
        await world.leases.ask(owner, an_ask(dock))
    with pytest.raises(ValueError):
        an_ask(labels=("Not A Label",))


async def test_only_the_holder_renews_and_releases_and_a_lapsed_lease_is_not_renewed(
    world: World,
) -> None:
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.leases.register(owner, a_resource(max_term_seconds=30))
    held = await world.leases.ask(ann, an_ask(dock, term_seconds=600))
    assert held.lease is not None
    # The term is the resource's bound, whatever the ask named.
    assert held.lease.term_seconds == 30
    with pytest.raises(NotAuthorized):
        await world.leases.renew(bob, held.lease.id)
    with pytest.raises(NotAuthorized):
        await world.leases.release(bob, held.lease.id)
    world.later(timedelta(seconds=20))
    renewed = await world.leases.renew(ann, held.lease.id)
    assert renewed.expires_at == world.now + timedelta(seconds=30)
    world.later(timedelta(seconds=31))
    with pytest.raises(LeaseEnded):
        await world.leases.renew(ann, held.lease.id)


async def test_a_manager_revokes_and_the_line_moves_on(world: World) -> None:
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.resource(owner)
    held = await world.leases.ask(ann, an_ask(dock))
    waits = await world.leases.ask(bob, an_ask(dock))
    assert held.lease is not None
    with pytest.raises(NotAuthorized):
        await world.leases.revoke(bob, held.lease.id)
    revoked = await world.leases.revoke(owner, held.lease.id)
    assert revoked.status is LeaseStatus.REVOKED
    assert await world.leases.revoke(owner, held.lease.id) == revoked
    with pytest.raises(LeaseEnded):
        await world.leases.release(ann, held.lease.id)
    with pytest.raises(LeaseEnded):
        await world.leases.renew(ann, held.lease.id)
    granted = await world.leases.get_request(bob, waits.request.id)
    assert granted.lease is not None and granted.lease.token == 2


async def test_a_holder_cancels_its_own_and_a_manager_any(world: World) -> None:
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.resource(owner)
    await world.leases.ask(owner, an_ask(dock))
    mine = await world.leases.ask(ann, an_ask(dock))
    theirs = await world.leases.ask(bob, an_ask(dock))
    with pytest.raises(NotAuthorized):
        await world.leases.cancel(ann, theirs.request.id)
    cancelled = await world.leases.cancel(ann, mine.request.id)
    assert cancelled.status is RequestStatus.CANCELLED and cancelled.end_reason is EndReason.ASKED
    assert (await world.leases.cancel(owner, theirs.request.id)).status is RequestStatus.CANCELLED
    assert (await world.leases.line(owner, dock.id)).requests == ()


async def test_a_manager_reorders_one_request_and_no_one_else(world: World) -> None:
    owner = await world.owner()
    ann, bob, cy = await world.member("ann"), await world.member("bob"), await world.member("cy")
    dock = await world.resource(owner)
    await world.leases.ask(owner, an_ask(dock))
    a, b, c = [(await world.leases.ask(m, an_ask(dock))).request for m in (ann, bob, cy)]
    with pytest.raises(NotAuthorized):
        await world.leases.reorder(ann, c.id, a.id)
    moved = await world.leases.reorder(owner, c.id, b.id)
    line = (await world.leases.line(owner, dock.id)).requests
    assert [r.id for r in line] == [a.id, c.id, b.id]
    assert [r.rank for r in line if r.id != c.id] == [a.rank, b.rank]
    assert a.rank < moved.rank < b.rank
    with pytest.raises(ValidationFailed):
        await world.leases.reorder(owner, c.id, new_id())


async def test_a_lapsed_lease_holds_until_the_margin_passes_then_the_line_moves_on(
    world: World,
) -> None:
    """A lease past its expiry still holds its resource for the skew margin,
    since its holder's clock may run behind; once the margin has passed too,
    the sweep ends it and the head of the line is granted a greater token."""
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.resource(owner)
    held = await world.leases.ask(ann, an_ask(dock, term_seconds=60))
    waits = await world.leases.ask(bob, an_ask(dock))
    assert held.lease is not None
    world.later(timedelta(seconds=60) + MARGIN - timedelta(seconds=1))
    assert await world.leases.sweep(world.rctx()) == 0
    still = await world.leases.get_resource(owner, dock.id)
    assert still.lease_id == held.lease.id
    assert (await world.leases.get_request(bob, waits.request.id)).lease is None
    world.later(timedelta(seconds=2))
    assert await world.leases.sweep(world.rctx()) == 1
    lapsed = await world.leases.get_lease(ann, held.lease.id)
    assert lapsed.status is LeaseStatus.EXPIRED
    granted = await world.leases.get_request(bob, waits.request.id)
    assert granted.lease is not None and granted.lease.token > held.lease.token


async def test_a_deleted_org_takes_no_place_of_a_live_one_in_the_sweep(tmp_path: Path) -> None:
    """A deleted org's lapsed lease stays due until its purge takes it. The
    sweep reads on past it, so in a batch of one org, the live org's lapsed
    lease still ends, whichever comes first."""
    world = World(tmp_path, options=LeasesOptions(margin=MARGIN, sweep_orgs=1))
    first, second = await world.owner(), await world.owner(f"bolt-{new_id().hex[-8:]}")
    gone, live = sorted((first, second), key=lambda ctx: ctx.org_id)
    held: dict[UUID, Lease] = {}
    for ctx in (gone, live):
        standing = await world.leases.ask(ctx, an_ask(await world.resource(ctx)))
        assert standing.lease is not None
        held[ctx.org_id] = standing.lease
    await world.delete_org(gone)
    world.later(timedelta(seconds=60) + MARGIN + timedelta(seconds=1))
    assert await world.leases.sweep(world.rctx()) == 1
    ended = await world.leases.get_lease(live, held[live.org_id].id)
    assert ended.status is LeaseStatus.EXPIRED
    kept = await world.storage.get_lease_storage().read_lease(gone.org_id, held[gone.org_id].id)
    assert kept is not None and kept.status is LeaseStatus.ACTIVE


async def test_the_sweep_expires_a_request_past_its_wait(world: World) -> None:
    owner = await world.owner()
    ann = await world.member("ann")
    dock = await world.resource(owner)
    await world.leases.ask(owner, an_ask(dock, term_seconds=3600))
    waits = await world.leases.ask(ann, an_ask(dock, wait_seconds=60))
    world.later(timedelta(seconds=61))
    assert await world.leases.sweep(world.rctx()) == 1
    expired = await world.leases.get_request(ann, waits.request.id)
    assert expired.request.status is RequestStatus.EXPIRED


async def test_a_kind_that_refuses_at_the_grant_cancels_the_request(tmp_path: Path) -> None:
    world = World(tmp_path, kind=Refusing())
    owner = await world.owner()
    dock = await world.resource(owner)
    refused = await world.leases.ask(owner, an_ask(dock))
    assert refused.lease is None
    assert refused.request.status is RequestStatus.CANCELLED
    assert refused.request.end_reason is EndReason.REFUSED
    assert (await world.leases.get_resource(owner, dock.id)).lease_id is None


async def test_an_unavailable_resource_grants_nothing_until_it_is_back(world: World) -> None:
    owner = await world.owner()
    dock = await world.resource(owner)
    await world.leases.set_available(owner, dock.id, False)
    waits = await world.leases.ask(owner, an_ask(dock))
    assert waits.lease is None
    await world.leases.set_available(owner, dock.id, True)
    assert (await world.leases.get_request(owner, waits.request.id)).lease is not None


# The waiter.


async def a_running_record(world: World, ctx: TenantContext) -> Orchestration:
    now = utcnow()
    return await world.managers.orchestrations.start(
        ctx,
        Orchestration(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            kind=OrchestrationKind.NOOP,
            input={"steps": 2},
        ),
    )


def park_of(record: Orchestration) -> Step:
    after = advanced(
        record, utcnow(), record.created_by, cursor=record.cursor, total=None,
        park=ParkReason.RESOURCE,
    )  # fmt: skip
    return Step(record=after, expected_version=record.version)


async def test_a_parked_record_is_woken_with_its_lease_when_the_resource_frees(
    world: World,
) -> None:
    owner = await world.owner()
    ann = await world.member("ann")
    dock = await world.resource(owner)
    held = await world.leases.ask(ann, an_ask(dock))
    record = await a_running_record(world, owner)
    key = new_id()
    waits = await world.leases.ask(owner, an_ask(dock, key=key, waiter=record.id), park_of(record))
    assert waits.lease is None
    parked = await world.managers.orchestrations.get(owner, record.id)
    assert parked.status is OrchestrationStatus.PARKED and parked.park_reason is ParkReason.RESOURCE
    assert held.lease is not None
    await world.leases.release(ann, held.lease.id)
    # The grant asked for the record's wake in its own commit; the worker
    # runs it as the WAKE_PARKED handler does.
    claimed = await world.managers.work.claim(
        world.rctx(), "default", [WorkKind.WAKE_PARKED], "test", timedelta(seconds=30)
    )
    assert claimed is not None
    ctx, item = claimed
    payload = WakeParkedPayload.model_validate(dict(item.payload))
    assert (payload.reason, payload.record_id) == (ParkReason.RESOURCE, record.id)
    assert await world.managers.orchestrations.wake(ctx, payload.reason, payload.record_id) == 1
    woken = await world.managers.orchestrations.get(owner, record.id)
    assert woken.status is OrchestrationStatus.RUNNING
    # Its next step asks again by its key and finds its lease.
    again = await world.leases.ask(owner, an_ask(dock, key=key, waiter=record.id), park_of(woken))
    assert again.lease is not None and again.lease.holder_id == owner.user_id
    assert (await world.managers.orchestrations.get(owner, record.id)).status is (
        OrchestrationStatus.RUNNING
    )


async def test_a_request_whose_waiter_stopped_waiting_is_cancelled_never_granted(
    world: World,
) -> None:
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.resource(owner)
    held = await world.leases.ask(ann, an_ask(dock))
    record = await a_running_record(world, owner)
    gone = await world.leases.ask(owner, an_ask(dock, waiter=record.id), park_of(record))
    after = await world.leases.ask(bob, an_ask(dock))
    parked = await world.managers.orchestrations.get(owner, record.id)
    await world.managers.orchestrations.fail(owner, parked, FailReason.DEFECT)
    assert held.lease is not None
    await world.leases.release(ann, held.lease.id)
    cancelled = await world.leases.get_request(owner, gone.request.id)
    assert cancelled.lease is None
    assert cancelled.request.end_reason is EndReason.WAITER_GONE
    granted = await world.leases.get_request(bob, after.request.id)
    assert granted.lease is not None


async def test_a_waiter_that_ends_leaves_every_line(world: World) -> None:
    owner = await world.owner()
    a, b = await world.resource(owner, "cold"), await world.resource(owner, "cold")
    await world.leases.ask(owner, an_ask(a))
    await world.leases.ask(owner, an_ask(b))
    waiter = new_id()
    for resource in (a, b):
        await world.leases.ask(owner, an_ask(resource).model_copy(
            update={"waiter_kind": WaiterKind.ORCHESTRATION, "waiter_id": waiter}
        ))  # fmt: skip
    assert await world.leases.leave(owner, WaiterKind.ORCHESTRATION, waiter) == 2
    assert (await world.leases.line(owner, a.id)).requests == ()


# The selector.


async def test_a_selector_is_granted_by_the_first_resource_that_frees_and_leaves_the_other_line(
    world: World,
) -> None:
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    north, south = await world.resource(owner, "cold"), await world.resource(owner, "cold")
    on_north = await world.leases.ask(ann, an_ask(north))
    await world.leases.ask(bob, an_ask(south))
    either = await world.leases.ask(owner, an_ask(labels=("cold",)))
    assert either.lease is None
    assert {r.id for r in (await world.leases.line(owner, south.id)).requests} == {
        either.request.id
    }
    assert on_north.lease is not None
    await world.leases.release(ann, on_north.lease.id)
    granted = await world.leases.get_request(owner, either.request.id)
    assert granted.lease is not None and granted.lease.resource_id == north.id
    assert (await world.leases.line(owner, south.id)).requests == ()


async def test_two_resources_freeing_at_once_grant_a_selector_once(world: World) -> None:
    owner = await world.owner()
    ann, bob, cy = await world.member("ann"), await world.member("bob"), await world.member("cy")
    north, south = await world.resource(owner, "cold"), await world.resource(owner, "cold")
    on_north = await world.leases.ask(ann, an_ask(north))
    on_south = await world.leases.ask(bob, an_ask(south))
    either = await world.leases.ask(owner, an_ask(labels=("cold",)))
    behind = await world.leases.ask(cy, an_ask(labels=("cold",)))
    assert on_north.lease is not None and on_south.lease is not None
    await world.leases.release(ann, on_north.lease.id)
    await world.leases.release(bob, on_south.lease.id)
    first = await world.leases.get_request(owner, either.request.id)
    second = await world.leases.get_request(cy, behind.request.id)
    assert first.lease is not None and second.lease is not None
    assert {first.lease.resource_id, second.lease.resource_id} == {north.id, south.id}


async def test_purge_tenant_reads_nothing_of_a_live_tenant(world: World) -> None:
    owner = await world.owner()
    await world.resource(owner)
    assert await world.leases.purge_tenant(owner) == 0
