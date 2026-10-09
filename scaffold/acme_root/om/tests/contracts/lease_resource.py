"""A resource its owner updates, and the history of its leases: the cases the
memory suite and the Postgres suite both run, each over its own root. The
owner lands its update in the commit of its own row, through the companion
statement or its memory twin, and offers the resource to its line after it;
or it updates through the manager, which offers at once. A lease the
resource holds keeps its term, and the line is the one its new labels make.
The history lists newest first, ended leases too, each with the request it
answered, and no tenant reads another's."""

from datetime import datetime, timedelta
from typing import Protocol

from acme.om.base import new_id, utcnow
from acme.om.context import RequestContext, TenantContext
from acme.om.leases.impl.manager import LeasesManagerImpl
from acme.om.leases.types.lease import LeaseStatus
from acme.om.leases.types.request import LeaseRequest, RequestStatus
from acme.om.leases.types.resource import Resource, ResourceKind, ResourceUpdate


class OwnerWorld(Protocol):
    """A root whose leases register the `noop` kind, on a clock the case
    moves, and an owner that lands an update in the commit of its own row."""

    leases: LeasesManagerImpl

    async def owner(self, slug: str | None = None) -> TenantContext: ...

    async def member(self, name: str) -> TenantContext: ...

    async def land_update(
        self, ctx: TenantContext, resource: Resource, change: ResourceUpdate, *, commits: bool
    ) -> None:
        """The owner's commit of its row with the resource's update; with
        `commits` False, the commit fails and neither lands."""
        ...

    def later(self, by: timedelta) -> datetime: ...

    def clock(self) -> datetime: ...

    def rctx(self) -> RequestContext: ...


def a_dock(
    owner: TenantContext, labels: tuple[str, ...] = (), max_term_seconds: int = 600
) -> Resource:
    now = utcnow()
    return Resource(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=owner.user_id,
        updated_by=owner.user_id,
        kind=ResourceKind.NOOP,
        ref_id=new_id(),
        labels=labels,
        max_term_seconds=max_term_seconds,
    )


def an_ask(
    resource: Resource | None = None,
    *,
    labels: tuple[str, ...] | None = None,
    term_seconds: int = 60,
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
        resource_id=None if resource is None else resource.id,
        labels=labels,
        term_seconds=term_seconds,
    )


async def an_owners_update_grants_the_requests_its_labels_now_match(world: OwnerWorld) -> None:
    """The dock offered `cold`, and offers `frozen` once its owner updates it,
    with a bound of two minutes. Ann's lease keeps its term and its expiry,
    and her next renewal runs within the new bound. When she releases, Cat,
    who asked for `frozen`, is granted for the new bound; Bob, who asked for
    `cold`, waits on and stands in no line. A free resource updated in its
    owner's commit is offered after it, and the request it now matches is
    granted at once."""
    owner = await world.owner()
    ann, bob, cat = [await world.member(name) for name in ("ann", "bob", "cat")]
    dock = await world.leases.register(owner, a_dock(owner, ("cold",), max_term_seconds=600))
    held = await world.leases.ask(ann, an_ask(dock, term_seconds=600))
    assert held.lease is not None
    cold = await world.leases.ask(bob, an_ask(labels=("cold",)))
    frozen = await world.leases.ask(cat, an_ask(labels=("frozen",), term_seconds=600))
    assert cold.place == 1 and frozen.place is None

    change = ResourceUpdate(labels=("frozen",), max_term_seconds=120)
    updated = await world.leases.update(owner, dock.id, change)
    assert (updated.labels, updated.max_term_seconds) == (("frozen",), 120)
    kept = await world.leases.get_lease(ann, held.lease.id)
    assert (kept.status, kept.term_seconds, kept.expires_at) == (
        LeaseStatus.ACTIVE,
        600,
        held.lease.expires_at,
    )
    assert (await world.leases.get_request(cat, frozen.request.id)).place == 1
    assert (await world.leases.get_request(bob, cold.request.id)).place is None
    renewed = await world.leases.renew(ann, held.lease.id)
    assert renewed.expires_at == world.clock() + timedelta(seconds=120)

    await world.leases.release(ann, held.lease.id)
    granted = await world.leases.get_request(cat, frozen.request.id)
    assert granted.lease is not None and granted.lease.resource_id == dock.id
    assert granted.lease.term_seconds == 120, "the new bound"
    waits = await world.leases.get_request(bob, cold.request.id)
    assert waits.lease is None and waits.request.status is RequestStatus.WAITING

    dry = await world.leases.register(owner, a_dock(owner, ("dry",)))
    wet = await world.leases.ask(bob, an_ask(labels=("wet",)))
    assert wet.lease is None
    await world.land_update(owner, dry, ResourceUpdate(labels=("wet",)), commits=True)
    offered = await world.leases.offer(owner, dry.id)
    assert offered is not None and offered.request_id == wet.request.id
    relabelled = await world.leases.get_resource(owner, dry.id)
    assert (relabelled.labels, relabelled.max_term_seconds) == (("wet",), 600), "the bound stays"


async def an_update_that_takes_a_resource_out_of_service_lands_in_the_owners_commit(
    world: OwnerWorld,
) -> None:
    """The owner takes the dock out of service in the commit of its own row,
    with an update that names its availability alone, and a commit that
    fails takes it out of nothing. Once it is out, Ann's release and the
    sweep grant Bob nothing; its return in the owner's commit, and the offer
    after it, grant him the dock. It keeps its labels and its bound
    throughout."""
    owner = await world.owner()
    ann, bob = await world.member("ann"), await world.member("bob")
    dock = await world.leases.register(owner, a_dock(owner, ("cold",)))
    held = await world.leases.ask(ann, an_ask(dock))
    assert held.lease is not None
    waits = await world.leases.ask(bob, an_ask(dock))
    out = ResourceUpdate(available=False)

    await world.land_update(owner, dock, out, commits=False)
    assert (await world.leases.get_resource(owner, dock.id)).available
    await world.land_update(owner, dock, out, commits=True)
    paused = await world.leases.get_resource(owner, dock.id)
    assert (paused.available, paused.labels, paused.max_term_seconds) == (False, ("cold",), 600)

    await world.leases.release(ann, held.lease.id)
    await world.leases.sweep(world.rctx())
    still = await world.leases.get_request(bob, waits.request.id)
    assert still.lease is None and still.request.status is RequestStatus.WAITING
    assert (await world.leases.get_resource(owner, dock.id)).lease_id is None

    await world.land_update(owner, dock, ResourceUpdate(available=True), commits=True)
    granted = await world.leases.offer(owner, dock.id)
    assert granted is not None and granted.request_id == waits.request.id
    assert granted.token == held.lease.token + 1
    back = await world.leases.get_resource(owner, dock.id)
    assert (back.available, back.labels, back.max_term_seconds) == (True, ("cold",), 600)


async def the_history_lists_each_lease_with_its_request_and_no_tenant_reads_another(
    world: OwnerWorld,
) -> None:
    """A dock's leases and the org's, newest first, ended ones too, each with
    the request it answered; a limit bounds the page. Another tenant reads
    none of them, by the dock's id or across its own org."""
    owner = await world.owner()
    ann = await world.member("ann")
    dock = await world.leases.register(owner, a_dock(owner))
    spare = await world.leases.register(owner, a_dock(owner))
    leases = []
    for resource in (dock, spare, dock):
        standing = await world.leases.ask(ann, an_ask(resource))
        assert standing.lease is not None
        leases.append(standing.lease)
        world.later(timedelta(seconds=1))
        await world.leases.release(ann, standing.lease.id)
    held = await world.leases.ask(ann, an_ask(dock))
    assert held.lease is not None
    newest_first = [held.lease.id, *(lease.id for lease in reversed(leases))]

    org_wide = await world.leases.list_leases(owner)
    assert [e.lease.id for e in org_wide] == newest_first
    assert [e.lease.status for e in org_wide] == [LeaseStatus.ACTIVE] + 3 * [LeaseStatus.RELEASED]
    assert all(e.request is not None and e.request.lease_id == e.lease.id for e in org_wide)
    assert [e.request.created_by for e in org_wide if e.request] == 4 * [ann.user_id]
    of_dock = await world.leases.list_leases(ann, dock.id, limit=2)
    assert [e.lease.id for e in of_dock] == [held.lease.id, leases[2].id]

    other = await world.owner(f"bolt-{new_id().hex[-8:]}")
    assert await world.leases.list_leases(other) == ()
    assert await world.leases.list_leases(other, dock.id) == ()
