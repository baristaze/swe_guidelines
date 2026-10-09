"""The leases storage contract. The cases named in `CROSS_TENANT_CASES` are
the tenant fence's evidence: each one presents another tenant's identifier
and asserts that nothing is found and nothing changes. The grant's cases
are the anchor's lock and the second fence: two grants of one resource, or
of one request, of which exactly one lands."""

from datetime import timedelta
from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.exceptions import Conflict
from acme.om.leases.storage import LeasesStorageInterface
from acme.om.leases.types.lease import Grant, Lease, LeaseStatus
from acme.om.leases.types.request import (
    EndReason,
    LeaseRequest,
    RequestStatus,
    WaiterKind,
)
from acme.om.leases.types.resource import Resource, ResourceKind, ResourceUpdate
from acme.om.orchestrations.rules import advanced
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.types.orchestration import (
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    ParkReason,
    Step,
)
from contracts.racing import race

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "cancel_waiting_of",
        "create_request",
        "create_resource",
        "end_lease",
        "grant",
        "park_waiting",
        "purge_tenant",
        "rank_request",
        "read_free",
        "read_lapsed",
        "read_lease",
        "read_leases",
        "read_overdue",
        "read_request",
        "read_resource",
        "read_resource_by_ref",
        "read_resources",
        "read_stranded",
        "read_waiting",
        "renew_lease",
        "retire_resource",
        "settle_request",
        "start_lease",
        "update_resource",
        "write_availability",
    }
)
"""Every method of `LeasesStorageInterface` that takes a tenant has a case in
this module that presents another tenant's."""

TERM = timedelta(seconds=60)


def make_resource(labels: tuple[str, ...] = (), *, max_term_seconds: int = 300) -> Resource:
    now = utcnow()
    actor = new_id()
    return Resource(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        kind=ResourceKind.NOOP,
        ref_id=new_id(),
        labels=labels,
        max_term_seconds=max_term_seconds,
    )


def make_request(
    resource: Resource | None = None,
    *,
    labels: tuple[str, ...] | None = None,
    waiter: UUID | None = None,
    waited: timedelta = timedelta(0),
    wait: timedelta = timedelta(hours=1),
) -> LeaseRequest:
    now = utcnow() - waited
    actor = new_id()
    return LeaseRequest(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        idempotency_key=new_id(),
        kind=ResourceKind.NOOP,
        resource_id=None if resource is None else resource.id,
        labels=labels if resource is None else None,
        waiter_kind=None if waiter is None else WaiterKind.ORCHESTRATION,
        waiter_id=waiter,
        term_seconds=int(TERM.total_seconds()),
        wait_until=now + wait,
    )


def make_lease(resource: Resource, request: LeaseRequest, *, expires_in: timedelta = TERM) -> Lease:
    now = utcnow()
    return Lease(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=request.created_by,
        updated_by=request.created_by,
        resource_id=resource.id,
        request_id=request.id,
        holder_id=request.created_by,
        token=resource.token + 1,
        term_seconds=int(TERM.total_seconds()),
        expires_at=now + expires_in,
    )


def grant_of(resource: Resource, request: LeaseRequest, **kwargs: timedelta) -> Grant:
    return Grant(lease=make_lease(resource, request, **kwargs), expected_token=resource.token)


async def drained(storage: LeasesStorageInterface) -> timedelta:
    """How far back a purge case stands: a century, so no other case's row is
    past its cut, with whatever an earlier run left behind it purged first."""
    back = timedelta(days=36500)
    while await storage.purge_settled(utcnow() - back - timedelta(days=30), 1000):
        pass
    return back


class LeaseStorageContract:
    @pytest.fixture
    def storage(self) -> LeasesStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    @pytest.fixture
    def records(self) -> OrchestrationsStorageInterface:
        raise NotImplementedError("the concrete test class provides the records a park lands in")

    async def a_resource(
        self, storage: LeasesStorageInterface, org: UUID, labels: tuple[str, ...] = ()
    ) -> Resource:
        resource = make_resource(labels)
        assert await storage.create_resource(org, resource, ())
        return resource

    async def a_request(
        self, storage: LeasesStorageInterface, org: UUID, request: LeaseRequest
    ) -> LeaseRequest:
        stored, created = await storage.create_request(org, request, ())
        assert created
        return stored

    # Resources.

    async def test_a_resource_is_one_per_org_kind_and_row(
        self, storage: LeasesStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        resource = await self.a_resource(storage, org)
        assert await storage.read_resource(org, resource.id) == resource
        twin = make_resource().model_copy(update={"ref_id": resource.ref_id})
        assert await storage.create_resource(org, twin, ()) is False
        assert await storage.read_resource(org, twin.id) is None
        assert await storage.read_resource_by_ref(org, ResourceKind.NOOP, resource.ref_id) == (
            resource
        )
        # The same row in another org is a resource of its own.
        assert await storage.create_resource(other, twin, ())

    async def test_resources_under_another_tenant_are_not_read_or_written_here(
        self, storage: LeasesStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        resource = await self.a_resource(storage, org)
        assert await storage.create_resource(other, resource, ()) is False
        assert await storage.read_resource(other, resource.id) is None
        assert await storage.read_resource_by_ref(other, resource.kind, resource.ref_id) is None
        assert await storage.read_resources(other, ResourceKind.NOOP, 10) == []
        assert await storage.read_free(other, 10) == []
        at = utcnow()
        assert await storage.write_availability(other, resource.id, False, at, new_id(), ()) is None
        moved = ResourceUpdate(labels=("moved",), max_term_seconds=5, available=False)
        assert await storage.update_resource(other, resource.id, moved, at, new_id(), ()) is None
        assert await storage.retire_resource(other, resource.id, at, new_id(), ()) is None
        assert await storage.read_resource(org, resource.id) == resource
        # Another tenant reads none of this one's stranded requests.
        named = await self.a_request(storage, org, make_request(resource))
        assert await storage.retire_resource(org, resource.id, at, new_id(), ())
        assert await storage.read_stranded(org, 10) == [named]
        assert await storage.read_stranded(other, 10) == []

    async def test_availability_and_retirement_leave_the_free_list(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        kept, paused, retired = [await self.a_resource(storage, org) for _ in range(3)]
        named = await self.a_request(storage, org, make_request(retired))
        actor = new_id()
        off = await storage.write_availability(org, paused.id, False, utcnow(), actor, ())
        assert off is not None and not off.available
        gone = await storage.retire_resource(org, retired.id, utcnow(), actor, ())
        assert gone is not None and gone.retired_at is not None
        assert [r.id for r in await storage.read_free(org, 10)] == [kept.id]
        assert {r.id for r in await storage.read_resources(org, ResourceKind.NOOP, 10)} == {
            kept.id,
            paused.id,
        }
        # A request that named the retired resource waits on, stranded, until
        # the manager takes it out of line with its waiter's wake; the org is
        # due for the sweep until then.
        assert await storage.read_stranded(org, 10) == [named]
        now = utcnow()
        assert org in await storage.read_due_orgs(now, now - timedelta(seconds=30), 10_000)
        cancelled = await storage.settle_request(
            org, named.id, RequestStatus.CANCELLED, EndReason.RETIRED, now, actor, ()
        )
        assert cancelled is not None and cancelled.end_reason is EndReason.RETIRED
        assert await storage.read_stranded(org, 10) == []
        assert await storage.retire_resource(org, retired.id, utcnow(), actor, ()) is None

    async def test_an_update_moves_the_labels_and_the_bound_and_keeps_the_lease(
        self, storage: LeasesStorageInterface
    ) -> None:
        """The owner's update, under the anchor's lock: the labels, the bound,
        and the availability when it names one. The lease that holds the
        resource, its token, and its expiry stay as they were."""
        org = new_id()
        resource = await self.a_resource(storage, org, ("cold",))
        request = await self.a_request(storage, org, make_request(resource))
        held = await storage.grant(org, grant_of(resource, request), ())
        assert held is not None
        actor, at = new_id(), utcnow()
        warm = ResourceUpdate(labels=("warm", "north"), max_term_seconds=30)
        updated = await storage.update_resource(org, resource.id, warm, at, actor, ())
        assert updated is not None
        assert (updated.labels, updated.max_term_seconds, updated.available) == (
            ("warm", "north"),
            30,
            True,
        )
        assert (updated.token, updated.lease_id, updated.held_until) == (
            1,
            held.id,
            held.expires_at,
        )
        assert updated.updated_by == actor
        assert await storage.read_lease(org, held.id) == held
        off = warm.model_copy(update={"available": False})
        paused = await storage.update_resource(org, resource.id, off, at, actor, ())
        assert paused is not None and not paused.available
        kept = await storage.update_resource(org, resource.id, warm, at, actor, ())
        assert kept is not None and not kept.available, "None keeps the availability"
        assert await storage.retire_resource(org, resource.id, at, actor, ())
        assert await storage.update_resource(org, resource.id, warm, at, actor, ()) is None

    # Requests.

    async def test_a_request_lands_last_and_an_ask_again_answers_it(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        first = await self.a_request(storage, org, make_request(resource))
        second = await self.a_request(storage, org, make_request(labels=()))
        assert second.rank > first.rank
        again = make_request(resource).model_copy(update={"idempotency_key": first.idempotency_key})
        stored, created = await storage.create_request(org, again, ())
        assert not created and stored == first
        assert await storage.read_request(org, again.id) is None
        waiting = await storage.read_waiting(org, ResourceKind.NOOP, 10)
        assert [r.id for r in waiting] == [first.id, second.id]

    async def test_requests_under_another_tenant_are_not_read_or_written_here(
        self, storage: LeasesStorageInterface, records: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        resource = await self.a_resource(storage, org)
        record = await self.a_record(records, org)
        request = await self.a_request(
            storage, org, make_request(resource, waiter=record.id, waited=timedelta(hours=2))
        )
        at = utcnow()
        with pytest.raises(Conflict):
            await storage.create_request(other, request, ())
        assert await storage.read_request(other, request.id) is None
        assert await storage.read_waiting(other, ResourceKind.NOOP, 10) == []
        assert await storage.read_overdue(other, at, 10) == []
        expired, cancelled = RequestStatus.EXPIRED, EndReason.ASKED
        assert (
            await storage.settle_request(other, request.id, expired, None, at, new_id(), ()) is None
        )
        assert (
            await storage.settle_request(
                other, request.id, RequestStatus.CANCELLED, cancelled, at, new_id(), ()
            )
            is None
        )
        assert await storage.rank_request(other, request.id, -5.0, at, new_id(), ()) is None
        park = parked(record)
        assert await storage.park_waiting(other, request.id, park, ()) is None
        assert (
            await storage.cancel_waiting_of(
                other, WaiterKind.ORCHESTRATION, record.id, at, new_id()
            )
            == 0
        )
        assert await storage.read_request(org, request.id) == request
        assert await records.read_orchestration(org, record.id) == record

    async def test_a_waiting_request_settles_once_and_moves_while_it_waits(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        first = await self.a_request(storage, org, make_request(resource))
        second = await self.a_request(storage, org, make_request(resource))
        actor = new_id()
        moved = await storage.rank_request(org, second.id, first.rank - 1, utcnow(), actor, ())
        assert moved is not None and moved.rank == first.rank - 1
        waiting = await storage.read_waiting(org, ResourceKind.NOOP, 10)
        assert [r.id for r in waiting] == [second.id, first.id]
        settled = await storage.settle_request(
            org, first.id, RequestStatus.CANCELLED, EndReason.ASKED, utcnow(), actor, ()
        )
        assert settled is not None and settled.status is RequestStatus.CANCELLED
        again = await storage.settle_request(
            org, first.id, RequestStatus.EXPIRED, None, utcnow(), actor, ()
        )
        assert again is None
        assert await storage.rank_request(org, first.id, 0.0, utcnow(), actor, ()) is None

    async def test_overdue_requests_are_read_oldest_first(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        late = await self.a_request(storage, org, make_request(resource, waited=timedelta(hours=3)))
        later = await self.a_request(
            storage, org, make_request(resource, waited=timedelta(hours=2))
        )
        await self.a_request(storage, org, make_request(resource))
        assert [r.id for r in await storage.read_overdue(org, utcnow(), 10)] == [late.id, later.id]

    async def test_a_waiter_leaves_every_line(self, storage: LeasesStorageInterface) -> None:
        org = new_id()
        a, b = await self.a_resource(storage, org), await self.a_resource(storage, org)
        waiter = new_id()
        mine = [
            await self.a_request(storage, org, make_request(a, waiter=waiter)),
            await self.a_request(storage, org, make_request(b, waiter=waiter)),
        ]
        theirs = await self.a_request(storage, org, make_request(a, waiter=new_id()))
        left = await storage.cancel_waiting_of(
            org, WaiterKind.ORCHESTRATION, waiter, utcnow(), new_id()
        )
        assert left == 2
        for request in mine:
            stored = await storage.read_request(org, request.id)
            assert stored is not None and stored.end_reason is EndReason.WAITER_GONE
        assert await storage.read_request(org, theirs.id) == theirs

    # The park.

    async def a_record(self, records: OrchestrationsStorageInterface, org: UUID) -> Orchestration:
        now = utcnow()
        actor = new_id()
        record = Orchestration(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=actor,
            updated_by=actor,
            kind=OrchestrationKind.NOOP,
            input={"steps": 1},
        )
        assert await records.create_orchestration(org, record, ())
        return record

    async def test_a_park_lands_only_while_the_request_waits(
        self, storage: LeasesStorageInterface, records: OrchestrationsStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        waiting_record, granted_record = (
            await self.a_record(records, org),
            await self.a_record(records, org),
        )
        waits = await self.a_request(storage, org, make_request(resource, waiter=waiting_record.id))
        stood = await storage.park_waiting(org, waits.id, parked(waiting_record), ())
        assert stood is not None and stood.status is RequestStatus.WAITING
        stored = await records.read_orchestration(org, waiting_record.id)
        assert stored is not None and stored.park_reason is ParkReason.RESOURCE
        # A request a grant answered first leaves its record running.
        other = await self.a_resource(storage, org)
        granted = await self.a_request(storage, org, make_request(other, waiter=granted_record.id))
        assert await storage.grant(org, grant_of(other, granted), ())
        stood = await storage.park_waiting(org, granted.id, parked(granted_record), ())
        assert stood is not None and stood.status is RequestStatus.GRANTED
        assert await records.read_orchestration(org, granted_record.id) == granted_record

    # Leases.

    async def test_a_grant_moves_the_anchor_and_answers_the_request(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        request = await self.a_request(storage, org, make_request(resource))
        lease = await storage.grant(org, grant_of(resource, request), ())
        assert lease is not None and lease.token == 1
        anchor = await storage.read_resource(org, resource.id)
        assert anchor is not None
        assert (anchor.token, anchor.lease_id, anchor.held_until) == (1, lease.id, lease.expires_at)
        answered = await storage.read_request(org, request.id)
        assert answered is not None and answered.status is RequestStatus.GRANTED
        assert answered.lease_id == lease.id
        assert await storage.read_lease(org, lease.id) == lease
        assert [r.id for r in await storage.read_free(org, 10)] == []

    async def test_a_grant_is_refused_on_a_moved_anchor_or_a_settled_request(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        first = await self.a_request(storage, org, make_request(resource))
        second = await self.a_request(storage, org, make_request(resource))
        assert await storage.grant(org, grant_of(resource, first), ())
        # Held: a second grant from the same read of the anchor lands nothing.
        assert await storage.grant(org, grant_of(resource, second), ()) is None
        waiting = await storage.read_request(org, second.id)
        assert waiting is not None and waiting.status is RequestStatus.WAITING
        # Settled: a request that left its line is never granted.
        free = await self.a_resource(storage, org)
        await storage.settle_request(
            org, second.id, RequestStatus.CANCELLED, EndReason.ASKED, utcnow(), new_id(), ()
        )
        moved = second.model_copy(update={"resource_id": free.id})
        assert await storage.grant(org, grant_of(free, moved), ()) is None

    async def test_a_grant_is_refused_once_the_resource_no_longer_fits_the_request(
        self, storage: LeasesStorageInterface
    ) -> None:
        """A grant decided on a read of the resource lands nothing once an
        update moved it under the lock: the labels the request needs are gone,
        or the bound is below the lease's term. The request waits on."""
        org = new_id()
        cold = await self.a_resource(storage, org, ("cold",))
        selector = await self.a_request(storage, org, make_request(labels=("cold",)))
        at, actor = utcnow(), new_id()
        dry = ResourceUpdate(labels=("dry",), max_term_seconds=300)
        assert await storage.update_resource(org, cold.id, dry, at, actor, ())
        assert await storage.grant(org, grant_of(cold, selector), ()) is None
        short = await self.a_resource(storage, org)
        named = await self.a_request(storage, org, make_request(short))
        brief = ResourceUpdate(max_term_seconds=int(TERM.total_seconds()) - 1)
        assert await storage.update_resource(org, short.id, brief, at, actor, ())
        assert await storage.grant(org, grant_of(short, named), ()) is None
        for request in (selector, named):
            waiting = await storage.read_request(org, request.id)
            assert waiting is not None and waiting.status is RequestStatus.WAITING
        assert {r.id for r in await storage.read_free(org, 10)} == {cold.id, short.id}

    async def test_two_grants_of_one_resource_land_one_lease_one_token_up(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        first = await self.a_request(storage, org, make_request(resource))
        second = await self.a_request(storage, org, make_request(resource))
        before = await storage.read_resource(org, resource.id)
        assert before is not None
        run = await race(
            storage.grant(org, grant_of(before, first), ()),
            storage.grant(org, grant_of(before, second), ()),
        )
        assert len(run.admitted) == 1, run.summary()
        winner = run.admitted[0]
        assert winner is not None
        anchor = await storage.read_resource(org, resource.id)
        assert anchor is not None and anchor.token == before.token + 1
        active = [
            lease
            for lease in (await storage.read_lapsed(org, utcnow() + timedelta(days=1), 10))
            if lease.resource_id == resource.id
        ]
        assert [lease.id for lease in active] == [winner.id]
        assert active[0].token == before.token + 1

    async def test_one_request_standing_in_two_lines_is_granted_once(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        a = await self.a_resource(storage, org, ("cold",))
        b = await self.a_resource(storage, org, ("cold",))
        request = await self.a_request(storage, org, make_request(labels=("cold",)))
        run = await race(
            storage.grant(org, grant_of(a, request), ()),
            storage.grant(org, grant_of(b, request), ()),
        )
        assert len(run.admitted) == 1, run.summary()
        free = [r.id for r in await storage.read_free(org, 10)]
        assert len(free) == 1 and free[0] in {a.id, b.id}

    async def test_leases_under_another_tenant_are_not_read_or_written_here(
        self, storage: LeasesStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        resource = await self.a_resource(storage, org)
        request = await self.a_request(storage, org, make_request(resource))
        held = await storage.grant(org, grant_of(resource, request), ())
        assert held is not None
        spare = await self.a_resource(storage, org)
        waiting = await self.a_request(storage, org, make_request(spare))
        assert await storage.grant(other, grant_of(spare, waiting), ()) is None
        at = utcnow()
        assert await storage.read_lease(other, held.id) is None
        assert await storage.read_lapsed(other, at + timedelta(days=1), 10) == []
        assert await storage.renew_lease(other, held.id, at, at + TERM, new_id()) is None
        assert await storage.start_lease(other, held.id, at, at, at + TERM, new_id()) is None
        ended = await storage.end_lease(other, held.id, LeaseStatus.REVOKED, at, new_id(), 1.0, ())
        assert ended is None
        assert await storage.read_lease(org, held.id) == held
        assert await storage.read_leases(other, None, 10) == []
        assert await storage.read_leases(other, resource.id, 10) == []
        assert [e.lease.id for e in await storage.read_leases(org, resource.id, 10)] == [held.id]
        assert await storage.purge_tenant(other, 100) == 0
        assert await storage.read_resource(org, resource.id) is not None

    async def test_a_renewal_moves_the_expiry_until_the_lease_lapses(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        request = await self.a_request(storage, org, make_request(resource))
        lease = await storage.grant(org, grant_of(resource, request), ())
        assert lease is not None
        now = utcnow()
        renewed = await storage.renew_lease(org, lease.id, now, now + TERM * 2, request.created_by)
        assert renewed is not None and renewed.expires_at == now + TERM * 2
        anchor = await storage.read_resource(org, resource.id)
        assert anchor is not None and anchor.held_until == renewed.expires_at
        # Past its expiry by the server's clock, a lease is not renewed.
        late = renewed.expires_at + timedelta(seconds=1)
        assert await storage.renew_lease(org, lease.id, late, late + TERM, new_id()) is None

    async def test_a_start_lands_once_until_the_lease_lapses_past_the_margin(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        request = await self.a_request(storage, org, make_request(resource))
        lease = await storage.grant(org, grant_of(resource, request), ())
        assert lease is not None and lease.started_at is None
        # Past its expiry, but not by the margin: the job still starts.
        late = lease.expires_at + timedelta(seconds=5)
        lapsed_before = late - timedelta(seconds=30)
        started = await storage.start_lease(
            org, lease.id, late, lapsed_before, late + TERM, new_id()
        )
        assert started is not None and started.started_at == late
        assert started.expires_at == late + TERM
        anchor = await storage.read_resource(org, resource.id)
        assert anchor is not None and anchor.held_until == late + TERM
        # Once started, a second start lands nothing.
        again = await storage.start_lease(
            org, lease.id, late, lapsed_before, late + TERM * 2, new_id()
        )
        assert again is None
        # A lease past its expiry and the margin does not start.
        spare = await self.a_resource(storage, org)
        other = await self.a_request(storage, org, make_request(spare))
        lapsing = await storage.grant(org, grant_of(spare, other), ())
        assert lapsing is not None
        past = lapsing.expires_at + timedelta(seconds=31)
        refused = await storage.start_lease(
            org, lapsing.id, past, past - timedelta(seconds=30), past + TERM, new_id()
        )
        assert refused is None
        assert await storage.read_lease(org, lapsing.id) == lapsing

    async def test_an_end_frees_the_anchor_and_keeps_its_token(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        request = await self.a_request(storage, org, make_request(resource))
        lease = await storage.grant(org, grant_of(resource, request), ())
        assert lease is not None
        at = utcnow()
        # Not lapsed yet: an end that asks for a lapsed lease lands nothing.
        early = await storage.end_lease(
            org, lease.id, LeaseStatus.EXPIRED, at, new_id(), 5.0, (), lapsed_before=at
        )
        assert early is None
        ended = await storage.end_lease(org, lease.id, LeaseStatus.RELEASED, at, new_id(), 5.0, ())
        assert ended is not None and ended.status is LeaseStatus.RELEASED and ended.ended_at == at
        anchor = await storage.read_resource(org, resource.id)
        assert anchor is not None
        assert (anchor.token, anchor.lease_id, anchor.mean_hold_seconds) == (1, None, 5.0)
        assert (
            await storage.end_lease(org, lease.id, LeaseStatus.REVOKED, at, new_id(), 1.0, ())
            is None
        )
        assert await storage.renew_lease(org, lease.id, at, at + TERM, new_id()) is None

    async def test_the_leases_list_newest_first_with_their_requests_ended_ones_too(
        self, storage: LeasesStorageInterface
    ) -> None:
        """The org's leases, or one resource's, newest first and bounded,
        whatever their status, each with the request it answered, from one
        read."""
        org = new_id()
        a, b = await self.a_resource(storage, org), await self.a_resource(storage, org)
        granted: list[tuple[Lease, LeaseRequest]] = []
        start = utcnow() - timedelta(minutes=10)
        for minute, resource in enumerate((a, b, a)):
            request = await self.a_request(storage, org, make_request(resource))
            anchor = await storage.read_resource(org, resource.id)
            assert anchor is not None
            grant = grant_of(anchor, request)
            at = start + timedelta(minutes=minute)
            lease = grant.lease.model_copy(
                update={"created_at": at, "updated_at": at, "expires_at": at + TERM}
            )
            landed = await storage.grant(org, grant.model_copy(update={"lease": lease}), ())
            assert landed is not None
            granted.append((landed, request))
            ended = await storage.end_lease(
                org, landed.id, LeaseStatus.RELEASED, at, new_id(), 1.0, ()
            )
            assert ended is not None
        # The newest is still held: an active lease lists beside the ended ones.
        request = await self.a_request(storage, org, make_request(b))
        anchor = await storage.read_resource(org, b.id)
        assert anchor is not None
        held = await storage.grant(org, grant_of(anchor, request), ())
        assert held is not None
        granted.append((held, request))
        newest_first = [lease.id for lease, _ in reversed(granted)]
        org_wide = await storage.read_leases(org, None, 10)
        assert [e.lease.id for e in org_wide] == newest_first
        assert [e.lease.status for e in org_wide] == [LeaseStatus.ACTIVE] + 3 * [
            LeaseStatus.RELEASED
        ]
        for entry, (_, request) in zip(org_wide, reversed(granted), strict=True):
            assert entry.request is not None
            assert (entry.request.id, entry.request.status) == (
                request.id,
                RequestStatus.GRANTED,
            )
            assert entry.request.lease_id == entry.lease.id
        assert [e.lease.id for e in await storage.read_leases(org, None, 2)] == newest_first[:2]
        of_a = await storage.read_leases(org, a.id, 10)
        assert [e.lease.id for e in of_a] == [granted[2][0].id, granted[0][0].id]

    # The sweep.

    async def test_the_due_orgs_are_those_with_a_lapse_an_overdue_ask_or_a_free_line(
        self, storage: LeasesStorageInterface
    ) -> None:
        lapsed_org, overdue_org, free_org, idle_org = new_id(), new_id(), new_id(), new_id()
        held = await self.a_resource(storage, lapsed_org)
        request = await self.a_request(storage, lapsed_org, make_request(held))
        assert await storage.grant(
            lapsed_org, grant_of(held, request, expires_in=timedelta(seconds=-120)), ()
        )
        named = await self.a_resource(storage, overdue_org)
        assert await storage.grant(
            overdue_org,
            grant_of(named, await self.a_request(storage, overdue_org, make_request(named))),
            (),
        )
        await self.a_request(storage, overdue_org, make_request(named, waited=timedelta(hours=2)))
        await self.a_resource(storage, free_org, ("cold", "north"))
        await self.a_request(storage, free_org, make_request(labels=("cold",)))
        await self.a_resource(storage, idle_org)
        # A free resource whose kind has waiters in other lines only: one
        # names a held resource, and one needs a label it lacks.
        elsewhere_org = new_id()
        taken = await self.a_resource(storage, elsewhere_org)
        assert await storage.grant(
            elsewhere_org,
            grant_of(taken, await self.a_request(storage, elsewhere_org, make_request(taken))),
            (),
        )
        await self.a_request(storage, elsewhere_org, make_request(taken))
        await self.a_resource(storage, elsewhere_org, ("cold",))
        await self.a_request(storage, elsewhere_org, make_request(labels=("dry",)))
        now = utcnow()
        due = set(await storage.read_due_orgs(now, now - timedelta(seconds=30), 10_000))
        assert {lapsed_org, overdue_org, free_org} <= due
        assert idle_org not in due and elsewhere_org not in due
        # In the order of their ids, after the one named: the sweep reads on
        # past an org it skips.
        ours = sorted((lapsed_org, overdue_org, free_org))
        after = await storage.read_due_orgs(now, now - timedelta(seconds=30), 10_000, ours[0])
        assert after == sorted(after) and all(org_id > ours[0] for org_id in after)
        assert set(ours[1:]) <= set(after)

    async def test_the_purge_takes_what_settled_and_keeps_what_runs(
        self, storage: LeasesStorageInterface
    ) -> None:
        back = await drained(storage)
        org = new_id()
        resource = await self.a_resource(storage, org)
        request = await self.a_request(storage, org, make_request(resource))
        lease = await storage.grant(org, grant_of(resource, request), ())
        assert lease is not None
        old = utcnow() - back - timedelta(days=40)
        await storage.end_lease(org, lease.id, LeaseStatus.RELEASED, old, new_id(), 1.0, ())
        waiting = await self.a_request(storage, org, make_request(resource))
        purged = await storage.purge_settled(utcnow() - back - timedelta(days=30), 1000)
        assert purged == 1
        assert await storage.read_lease(org, lease.id) is None
        assert await storage.read_request(org, waiting.id) is not None
        assert await storage.read_resource(org, resource.id) is not None

    async def test_purge_tenant_takes_every_row_of_the_tenant(
        self, storage: LeasesStorageInterface
    ) -> None:
        org = new_id()
        resource = await self.a_resource(storage, org)
        request = await self.a_request(storage, org, make_request(resource))
        assert await storage.grant(org, grant_of(resource, request), ())
        assert await storage.purge_tenant(org, 100) == 3
        assert await storage.read_resource(org, resource.id) is None
        assert await storage.purge_tenant(org, 100) == 0


def parked(record: Orchestration) -> Step:
    """The record parked on `resource` at its cursor, as a step would write it."""
    after = advanced(
        record,
        utcnow(),
        record.created_by,
        cursor=record.cursor,
        total=None,
        park=ParkReason.RESOURCE,
    )
    assert after.status is OrchestrationStatus.PARKED
    return Step(record=after, expected_version=record.version)
