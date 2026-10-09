"""A request: a principal's place in the line in front of a resource. It
names one resource, or a selector (its kind and the labels it needs), and
then it stands in the line of every resource the selector matches, with the
one place its rank gives it. One rank order serves every line of an org."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.base import FrozenMapping, Identifiable, Platform, Trackable
from acme.om.leases.types.lease import Lease
from acme.om.leases.types.resource import (
    MAX_LABELS,
    MAX_TERM_SECONDS,
    Label,
    Resource,
    ResourceKind,
)


class RequestStatus(StrEnum):
    WAITING = "waiting"  # in line
    GRANTED = "granted"  # answered by a lease
    CANCELLED = "cancelled"  # out of line without a lease; `end_reason` says why
    EXPIRED = "expired"  # past its wait


SETTLED_REQUESTS = frozenset(
    {RequestStatus.GRANTED, RequestStatus.CANCELLED, RequestStatus.EXPIRED}
)
"""The statuses a request never leaves."""


class EndReason(StrEnum):
    """Why a request left its line without a lease."""

    ASKED = "asked"  # the one who asked, or a manager, cancelled it
    WAITER_GONE = "waiter_gone"  # its waiter no longer waits
    REFUSED = "refused"  # its kind refused it at the grant
    RETIRED = "retired"  # the resource it named was retired


class WaiterKind(StrEnum):
    """What waits on a request, so a grant wakes it. A product adds its own,
    each with a `WaiterInterface`."""

    ORCHESTRATION = "orchestration"  # a long-running record parked on `resource`


class LeaseRequest(Identifiable, Trackable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "rank",
        "wait_until",
        "status",
        "end_reason",
        "lease_id",
    )
    """What the ask names is the asker's; its place and its outcome are the
    manager's."""

    # The ask's own key: an ask asked again by it answers this request.
    idempotency_key: UUID
    kind: ResourceKind
    # One resource, or a selector: the labels a resource of the kind offers.
    resource_id: UUID | None = None
    labels: tuple[Label, ...] | None = Field(default=None, max_length=MAX_LABELS)
    # The dump of ASK_PAYLOADS[kind]: what the grant starts, in the kind's shape.
    payload: FrozenMapping = Field(default_factory=dict, validate_default=True)
    # What waits on it, by a registered kind and an id; none for a caller
    # that follows its request itself.
    waiter_kind: WaiterKind | None = None
    waiter_id: UUID | None = None
    term_seconds: int = Field(default=60, ge=1, le=MAX_TERM_SECONDS)
    # For a grant that starts a job: the window the job has to start in, from
    # the grant, within the resource's bound. None gives it the term.
    start_seconds: int | None = Field(default=None, ge=1, le=MAX_TERM_SECONDS)
    # How long it may wait, from its ask; past it the sweep expires it.
    wait_seconds: int = Field(default=3600, ge=1, le=604_800)
    wait_until: datetime | None = None
    # Its place in the org's one rank order: lower goes first.
    rank: float = 0.0
    status: RequestStatus = RequestStatus.WAITING
    end_reason: EndReason | None = None
    lease_id: UUID | None = None

    @model_validator(mode="after")
    def _names_one_target(self) -> Self:
        if (self.resource_id is None) == (self.labels is None):
            raise ValueError("a request names one resource or a selector's labels, not both")
        if (self.waiter_kind is None) != (self.waiter_id is None):
            raise ValueError("a waiter is a kind and an id together")
        return self


class NoopAsk(Platform):
    """The `noop` kind's ask carries nothing."""


ASK_PAYLOADS: dict[ResourceKind, type[Platform]] = {
    ResourceKind.NOOP: NoopAsk,
}
"""The shape of what each kind's ask carries; the ask validates it."""


class Standing(Platform):
    """Where a request stands: its lease once granted, or, while it waits, its
    place (1 is next in some line it stands in) and an estimate of its wait,
    replayed from the measured holds of the resources in front of it."""

    request: LeaseRequest
    lease: Lease | None = None
    place: int | None = None
    estimate_seconds: float | None = None


class Place(Platform):
    """A waiting request's place and estimate, as its `Standing` answers
    them."""

    request_id: UUID
    place: int | None = None
    estimate_seconds: float | None = None


class Line(Platform):
    """A resource and the requests in its line, first first, with each one's
    place and estimate in the same order, replayed from the one read the
    line makes."""

    resource: Resource
    requests: tuple[LeaseRequest, ...]
    places: tuple[Place, ...] = ()


class LeaseEntry(Platform):
    """One lease of a history, with the request it answered: who asked, for
    what, and with which payload. None when the purge took the request
    first, which only a lease renewed past the retention outlives."""

    lease: Lease
    request: LeaseRequest | None = None


class HistoryMark(Platform):
    """Where a page of a history ended: its last lease's grant and id. The
    next page holds the leases that sort before it, newest first, so a page
    stays where it is however many leases are granted after it."""

    created_at: datetime
    lease_id: UUID

    @classmethod
    def of(cls, lease: Lease) -> Self:
        return cls(created_at=lease.created_at, lease_id=lease.id)


class LeasePage(Platform):
    """One page of a history, and whether another follows. The manager asks
    storage for one entry more than the page and keeps it out, so `has_more`
    is a fact about the rows."""

    items: tuple[LeaseEntry, ...]
    has_more: bool
