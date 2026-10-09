"""Pure rules of the leases namespace: who stands in which line, whether a
resource may be granted, the term a grant runs, the rank a reorder gives,
the hold a resource measures, and the replay that estimates a wait. Values
in, values out; no clock, no storage, no settings: the caller passes the
time."""

import heapq
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from acme.om.leases.types.lease import Lease, LeaseStatus
from acme.om.leases.types.request import LeaseRequest, RequestStatus
from acme.om.leases.types.resource import Resource

HOLD_WEIGHT = 0.2
"""How much one ended lease moves a resource's measured hold: the newest
hold counts a fifth, and the measure before it the rest."""


def stands_in(request: LeaseRequest, resource: Resource) -> bool:
    """Whether a waiting request is in the resource's line: it names the
    resource, or its selector's labels are all among the resource's."""
    if request.status is not RequestStatus.WAITING or request.kind is not resource.kind:
        return False
    if request.resource_id is not None:
        return request.resource_id == resource.id
    return set(request.labels or ()) <= set(resource.labels)


def is_held(resource: Resource) -> bool:
    """Whether a lease holds the resource. A lease past its expiry holds it
    until it is ended, which the sweep does once the skew margin has passed
    too, since its holder's clock may still be running."""
    return resource.lease_id is not None


def holds_lapsed(resource: Resource, lapsed_before: datetime) -> bool:
    """Whether the lease that holds the resource is past its expiry and the
    margin: `lapsed_before` is the clock less the margin."""
    return (
        resource.lease_id is not None
        and resource.held_until is not None
        and resource.held_until <= lapsed_before
    )


def is_grantable(resource: Resource) -> bool:
    return resource.retired_at is None and resource.available and not is_held(resource)


def term_of(request: LeaseRequest, resource: Resource) -> timedelta:
    """How long a grant or a renewal runs: what the request asked, within the
    resource's bound on one lease."""
    return timedelta(seconds=min(request.term_seconds, resource.max_term_seconds))


def is_lapsed(lease: Lease, lapsed_before: datetime) -> bool:
    return lease.status is LeaseStatus.ACTIVE and lease.expires_at <= lapsed_before


def measured_hold(previous: float | None, held: timedelta) -> float:
    """The resource's measured hold once a lease of it has ended."""
    seconds = max(0.0, held.total_seconds())
    if previous is None:
        return seconds
    return previous + HOLD_WEIGHT * (seconds - previous)


def rank_at(ranks: Sequence[float], index: int) -> float:
    """The rank that puts a request at `index` of an order whose ranks are
    `ranks`, the request itself left out: halfway between its two new
    neighbours, one before the first, or one after the last. Only the
    request moves."""
    if not ranks:
        return 1.0
    if index <= 0:
        return ranks[0] - 1.0
    if index >= len(ranks):
        return ranks[-1] + 1.0
    return (ranks[index - 1] + ranks[index]) / 2


def line_of(resource: Resource, waiting: Sequence[LeaseRequest]) -> list[LeaseRequest]:
    """The resource's line, first first: `waiting` is in rank order already."""
    return [request for request in waiting if stands_in(request, resource)]


def place_of(
    request: LeaseRequest, resources: Sequence[Resource], waiting: Sequence[LeaseRequest]
) -> int | None:
    """The request's place: the best of its places across the lines it
    stands in, 1 first. None when it stands in no line."""
    places = [
        index + 1
        for resource in resources
        for index, ahead in enumerate(line_of(resource, waiting))
        if ahead.id == request.id
    ]
    return min(places) if places else None


def replay(
    request: LeaseRequest,
    resources: Sequence[Resource],
    waiting: Sequence[LeaseRequest],
    now: datetime,
) -> float | None:
    """The estimate of the request's wait, in seconds: the lines replayed in
    rank order. Each resource that will grant (live and available) frees when
    its lease expires, or now, and then serves the first request still
    waiting in its line for its measured hold, or its bound when it has
    measured none. Only the requests up to this one matter, since a request
    behind it takes a resource before it only where it does not stand. None
    when no resource ever reaches it."""
    pending: list[LeaseRequest] = []
    for ahead in waiting:
        pending.append(ahead)
        if ahead.id == request.id:
            break
    else:
        return None
    serving = [r for r in resources if r.retired_at is None and r.available]
    heap: list[tuple[datetime, UUID]] = []
    by_id: dict[UUID, Resource] = {}
    for resource in serving:
        free_at = now
        if resource.lease_id is not None and resource.held_until is not None:
            free_at = max(now, resource.held_until)
        heapq.heappush(heap, (free_at, resource.id))
        by_id[resource.id] = resource
    while heap and pending:
        free_at, resource_id = heapq.heappop(heap)
        resource = by_id[resource_id]
        taker = next((p for p in pending if stands_in(p, resource)), None)
        if taker is None:
            continue
        if taker.id == request.id:
            return max(0.0, (free_at - now).total_seconds())
        pending.remove(taker)
        hold = resource.mean_hold_seconds
        if hold is None:
            hold = float(resource.max_term_seconds)
        heapq.heappush(heap, (free_at + timedelta(seconds=hold), resource_id))
    return None


def ranked_first(waiting: Sequence[LeaseRequest]) -> list[LeaseRequest]:
    """Waiting requests in the org's one order: by rank, then by id."""
    return sorted(waiting, key=lambda request: (request.rank, request.id))
