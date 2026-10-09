"""A lane's cap on one tenant's claimed items, as a worker's claims meet it:
the cases the memory suite and the Postgres suite both run, each over its
own root, so the claim holds the cap the same way in either."""

from datetime import timedelta
from uuid import UUID

from acme.om.base import new_id
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.root import Managers
from acme.om.work.storage import WorkStorageInterface
from acme.om.work.types.work_item import WorkItem, WorkKind
from contracts.work_storage import make_item

LEASE = timedelta(seconds=30)
WORKER = AppContext(type=AppType.WORKER, version="worker@test")


class CappedLane:
    """One lane with a cap, enqueued to and claimed from through the manager,
    as a worker claims."""

    def __init__(self, managers: Managers, lane: str, cap: int) -> None:
        self.managers, self.lane, self.cap = managers, lane, cap

    async def enqueue(self, by: TenantContext, ready_ago: int) -> WorkItem:
        item = make_item(lane=self.lane, available_in=timedelta(seconds=-ready_ago))
        return await self.managers.work.enqueue(by, item)

    async def claim(self) -> tuple[TenantContext, WorkItem] | None:
        rctx = RequestContext(request_id=new_id(), app=WORKER)
        return await self.managers.work.claim(
            rctx, self.lane, [WorkKind.NOOP], "w1", LEASE, self.cap
        )


async def a_second_item_is_passed_over_at_a_cap_of_one(
    managers: Managers, work: WorkStorageInterface, ann: TenantContext, bob: TenantContext
) -> None:
    """With a cap of one, Ann's second item is passed over, unwritten and with
    its attempts as they were, while Bob's item is claimed. It is claimed
    itself as soon as her first ends."""
    lane = CappedLane(managers, f"cap-{new_id().hex[-12:]}", 1)
    first, second = await lane.enqueue(ann, 3), await lane.enqueue(ann, 2)
    bobs = await lane.enqueue(bob, 1)

    running = await lane.claim()
    assert running is not None and running[1].id == first.id
    claimed = await lane.claim()
    assert claimed is not None
    assert (claimed[0].org_id, claimed[1].id, claimed[1].attempts) == (bob.org_id, bobs.id, 1)
    waiting = await work.read_item(ann.org_id, second.id)
    assert waiting == second, "passed over, never written: its attempts as they were"
    assert await lane.claim() is None, "Ann is at her cap and Bob has nothing left"

    await managers.work.complete(*running)
    freed = await lane.claim()
    assert freed is not None and (freed[1].id, freed[1].attempts) == (second.id, 1)


async def a_burst_drains_as_fast_as_the_worker_runs_at_a_cap_of_two(
    managers: Managers, work: WorkStorageInterface, ann: TenantContext, bob: TenantContext
) -> None:
    """Cap 2, a burst of short items from Ann, and one worker that runs three
    at a time and claims again each time one ends, with no clock moved
    between. A claim comes back empty only while Ann holds her cap; Bob's
    item, ready after the whole burst, is claimed while she holds it; and
    no item of hers is written until the claim that takes it."""
    lane = CappedLane(managers, f"cap-{new_id().hex[-12:]}", 2)
    burst = [await lane.enqueue(ann, 100 - n) for n in range(12)]
    bobs = await lane.enqueue(bob, 1)
    capacity = 3
    running: list[tuple[TenantContext, WorkItem]] = []
    order: list[UUID] = []

    async def none_of_the_waiting_is_written() -> None:
        for item in burst:
            if item.id not in order:
                assert await work.read_item(ann.org_id, item.id) == item, "never written"

    total = len(burst) + 1
    while len(order) < total:
        while len(running) < capacity and len(order) < total:
            anns = sum(1 for ctx, _ in running if ctx.org_id == ann.org_id)
            claimed = await lane.claim()
            if claimed is None:
                assert anns == lane.cap, "an empty claim while Ann held fewer than her cap"
            else:
                if claimed[1].id == bobs.id:
                    assert anns == lane.cap, "Bob's item is claimed while Ann is at her cap"
                running.append(claimed)
                order.append(claimed[1].id)
            await none_of_the_waiting_is_written()
            if claimed is None:
                break
        await managers.work.complete(*running.pop(0))

    assert order[2] == bobs.id, "Bob waits for no more than Ann's cap"
    assert [i for i in order if i != bobs.id] == [item.id for item in burst], "in the burst's order"
    for ctx, item in running:
        await managers.work.complete(ctx, item)
