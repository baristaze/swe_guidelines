"""A lease kept by the worker that runs its holder's job, and a resource with
as many labels as a real one offers: the cases the memory suite and the
Postgres suite both run, each over its own root, so the job's claim fences
the lease the same way in either. The grant writes the job as a work item in
its own commit; the worker that claims the item starts the lease, renews it,
and ends it, under the item's claim token and the lease's token."""

from datetime import datetime, timedelta
from typing import Protocol

import pytest

from acme.om.base import new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.exceptions import LeaseEnded, NotAuthorized
from acme.om.leases.hooks import ResourceKindInterface
from acme.om.leases.impl.manager import LeasesManagerImpl
from acme.om.leases.types.lease import JobClaim, Lease, LeaseStatus
from acme.om.leases.types.request import LeaseRequest
from acme.om.leases.types.resource import (
    MAX_LABELS,
    MAX_TERM_SECONDS,
    Resource,
    ResourceKind,
)
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.root import Managers
from acme.om.work.types.work_item import WorkItem, WorkKind, relayed_lane, work_row_kind

WORKER = AppContext(type=AppType.WORKER, version="worker@test")
CLAIM = timedelta(minutes=5)


class StartsAJob(ResourceKindInterface):
    """A kind whose grant starts a job: one work item, which names the lease
    it runs for."""

    async def may_grant(
        self, ctx: TenantContext, resource: Resource, request: LeaseRequest
    ) -> bool:
        return True

    def grant_rows(
        self, ctx: TenantContext, resource: Resource, request: LeaseRequest, lease: Lease
    ) -> tuple[OutboxRow, ...]:
        return (outbox_row(ctx, work_row_kind(WorkKind.NOOP), lease.id, {}),)


class JobWorld(Protocol):
    """A root whose leases register `StartsAJob` for the `noop` kind, on a
    clock the case moves; the queue keeps its own."""

    managers: Managers
    leases: LeasesManagerImpl
    margin: timedelta

    async def owner(self) -> TenantContext: ...

    async def member(self, name: str) -> TenantContext: ...

    def later(self, by: timedelta) -> datetime: ...

    def clock(self) -> datetime: ...


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
    start_seconds: int | None = None,
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
        start_seconds=start_seconds,
    )


async def claim_job(world: JobWorld, lease: Lease) -> tuple[TenantContext, WorkItem]:
    """The worker's claim on the lease's job, as a worker of its lane claims.
    Items other cases left on the lane are handed back as they were."""
    passed: list[tuple[TenantContext, WorkItem]] = []
    try:
        for _ in range(50):
            claimed = await world.managers.work.claim(
                RequestContext(request_id=new_id(), app=WORKER),
                relayed_lane(WorkKind.NOOP),
                [WorkKind.NOOP],
                "w1",
                CLAIM,
            )
            if claimed is None:
                break
            if claimed[1].idempotency_key == lease.job_key:
                return claimed
            passed.append(claimed)
    finally:
        for ctx, item in passed:
            await world.managers.work.release(ctx, item)
    raise AssertionError(f"no job queued for lease {lease.id}")


def held_by(item: WorkItem, lease: Lease) -> JobClaim:
    assert item.claim_token is not None
    return JobClaim(token=lease.token, claim_token=item.claim_token)


async def the_jobs_worker_keeps_the_lease_and_no_one_else_does(world: JobWorld) -> None:
    """Bob's release grants Ann's request in Bob's commit, so the job's item is
    Bob's write and its worker runs as Bob. The worker starts, renews for a
    length it names within the bound, and ends Ann's lease under the item's
    claim. Bob alone, Cat, a claim token that is not the item's, a token that
    is not the lease's, and a claim the queue took back are each refused."""
    owner = await world.owner()
    ann, bob, cat = await world.member("ann"), await world.member("bob"), await world.member("cat")
    dock = await world.leases.register(owner, a_dock(owner, max_term_seconds=600))
    first = await world.leases.ask(bob, an_ask(dock))
    waits = await world.leases.ask(ann, an_ask(dock, term_seconds=60))
    assert first.lease is not None and waits.lease is None
    await world.leases.release(bob, first.lease.id)
    lease = (await world.leases.get_request(ann, waits.request.id)).lease
    assert lease is not None and lease.holder_id == ann.user_id and lease.job_key is not None

    run, item = await claim_job(world, lease)
    assert run.user_id == bob.user_id, "the worker runs as whoever freed the resource"
    claim = held_by(item, lease)
    with pytest.raises(NotAuthorized):
        await world.leases.renew(run, lease.id)
    with pytest.raises(NotAuthorized):
        await world.leases.renew(cat, lease.id, 120)
    with pytest.raises(NotAuthorized):
        await world.leases.start(cat, lease.id, JobClaim(token=lease.token, claim_token=new_id()))
    with pytest.raises(NotAuthorized):
        await world.leases.release(run, lease.id, claim.model_copy(update={"token": 1}))

    started = await world.leases.start(run, lease.id, claim)
    assert started.started_at == world.clock()
    assert started.expires_at == world.clock() + timedelta(seconds=60)
    world.later(timedelta(seconds=10))
    named = await world.leases.renew(run, lease.id, 500, claim)
    assert named.expires_at == world.clock() + timedelta(seconds=500)
    bounded = await world.leases.renew(run, lease.id, 10_000, claim)
    assert bounded.expires_at == world.clock() + timedelta(seconds=600), "the resource's bound"
    kept = await world.leases.renew(ann, lease.id)
    assert kept.expires_at == world.clock() + timedelta(seconds=60), "the holder keeps its right"

    await world.managers.work.release(run, item)
    with pytest.raises(NotAuthorized):
        await world.leases.renew(run, lease.id, 120, claim)
    again, retried = await claim_job(world, lease)
    fresh = held_by(retried, lease)
    assert (await world.leases.start(again, lease.id, fresh)).started_at == started.started_at
    ended = await world.leases.release(again, lease.id, fresh)
    assert ended.status is LeaseStatus.RELEASED
    with pytest.raises(LeaseEnded):
        await world.leases.renew(again, lease.id, 120, fresh)
    await world.managers.work.complete(again, retried)
    await world.managers.work.complete(*await claim_job(world, first.lease))


async def a_job_that_waited_past_its_window_starts_inside_the_margin(world: JobWorld) -> None:
    """The grant runs the lease to the end of its job's window. The job waited
    in its lane past that end, and a renewal there is refused as ever, but
    its start lands inside the skew margin and runs the whole term. The sweep
    then leaves the lease to run."""
    owner, ann = await world.owner(), await world.member("ann")
    dock = await world.leases.register(owner, a_dock(owner, max_term_seconds=600))
    standing = await world.leases.ask(ann, an_ask(dock, term_seconds=300, start_seconds=120))
    lease = standing.lease
    assert lease is not None and lease.expires_at == world.clock() + timedelta(seconds=120)
    run, item = await claim_job(world, lease)
    claim = held_by(item, lease)

    world.later(timedelta(seconds=120) + world.margin - timedelta(seconds=1))
    with pytest.raises(LeaseEnded):
        await world.leases.renew(run, lease.id, 120, claim)
    started = await world.leases.start(run, lease.id, claim)
    assert started.expires_at == world.clock() + timedelta(seconds=300)
    world.later(world.margin + timedelta(seconds=2))
    await world.leases.sweep(RequestContext(request_id=new_id(), app=WORKER))
    assert (await world.leases.get_lease(ann, lease.id)).status is LeaseStatus.ACTIVE
    await world.leases.release(run, lease.id, claim)
    await world.managers.work.complete(run, item)


async def a_job_that_misses_its_window_lets_the_lease_lapse(world: JobWorld) -> None:
    """Past the window and the margin, the start is refused before the sweep
    comes, and the sweep ends the lease as any lapsed one."""
    owner, ann = await world.owner(), await world.member("ann")
    dock = await world.leases.register(owner, a_dock(owner, max_term_seconds=600))
    standing = await world.leases.ask(ann, an_ask(dock, term_seconds=300, start_seconds=120))
    lease = standing.lease
    assert lease is not None
    run, item = await claim_job(world, lease)

    world.later(timedelta(seconds=120) + world.margin + timedelta(seconds=1))
    with pytest.raises(LeaseEnded):
        await world.leases.start(run, lease.id, held_by(item, lease))
    await world.leases.sweep(RequestContext(request_id=new_id(), app=WORKER))
    assert (await world.leases.get_lease(ann, lease.id)).status is LeaseStatus.EXPIRED
    await world.managers.work.complete(run, item)


async def a_resource_with_as_many_labels_as_a_real_one_is_matched(world: JobWorld) -> None:
    """160 labels of 200 characters each, free text, register and match a
    selector that needs every one; and a bound of seven days runs a term of
    seven days."""
    owner, ann = await world.owner(), await world.member("ann")
    labels = tuple(f"Aisle {n:03d}, north quay: cold, door 3 ".ljust(200, "é") for n in range(160))
    assert len(labels) == MAX_LABELS
    assert {len(label) for label in labels} == {200}
    dock = await world.leases.register(
        owner, a_dock(owner, labels=labels, max_term_seconds=MAX_TERM_SECONDS)
    )
    assert (await world.leases.get_resource(owner, dock.id)).labels == labels
    standing = await world.leases.ask(
        ann, an_ask(labels=labels[::-1], term_seconds=MAX_TERM_SECONDS)
    )
    assert standing.lease is not None and standing.lease.resource_id == dock.id
    assert standing.lease.expires_at == world.clock() + timedelta(days=7)
    run, item = await claim_job(world, standing.lease)
    await world.managers.work.complete(run, item)
