"""The wire's leases: a resource and its anchor, a lease with the seconds it
has left, a request with its place and estimate, and a resource's line. A
lease's time travels as a duration as well as an instant: the holder's
clock is not the server's, so it counts the seconds from when it asked."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from acme.om.leases.types.lease import LeaseStatus
from acme.om.leases.types.request import EndReason, RequestStatus, WaiterKind
from acme.om.leases.types.resource import MAX_LABELS, Label, ResourceKind
from acme.services.api.types.common import RequestBody, View


class ResourceView(View):
    """A resource and its anchor: the highest token granted on it, and the
    lease that holds it until when."""

    id: UUID
    kind: ResourceKind
    ref_id: UUID
    labels: list[str]
    max_term_seconds: int
    available: bool
    retired_at: datetime | None
    fencing_token: int  # the highest granted
    lease_id: UUID | None
    held_until: datetime | None
    created_at: datetime


class LeaseView(View):
    """One grant: the holder acts on the resource under `fencing_token` until
    it has used `expires_in_seconds`, counted from when it asked. The token is
    no secret: it is the number the resource's own side refuses to go below."""

    id: UUID
    resource_id: UUID
    request_id: UUID
    holder_id: UUID
    fencing_token: int
    term_seconds: int
    expires_at: datetime
    expires_in_seconds: float
    status: LeaseStatus
    ended_at: datetime | None
    created_at: datetime


class LeaseRequestView(View):
    id: UUID
    kind: ResourceKind
    resource_id: UUID | None
    labels: list[str] | None
    waiter_kind: WaiterKind | None
    waiter_id: UUID | None
    term_seconds: int
    wait_until: datetime | None
    rank: float
    status: RequestStatus
    end_reason: EndReason | None
    lease_id: UUID | None
    created_at: datetime
    created_by: UUID


class StandingView(View):
    """Where a request stands: its lease once granted, or its place (1 is
    next in some line it stands in) and an estimate of its wait in seconds,
    while it waits."""

    request: LeaseRequestView
    lease: LeaseView | None
    place: int | None
    estimate_seconds: float | None


class LineView(View):
    """A resource and the requests in its line, first first."""

    resource: ResourceView
    requests: list[LeaseRequestView]


class AskRequest(RequestBody):
    """An ask for a lease: one resource by its id, or a selector, the labels
    a resource of `kind` must offer. `payload` is in the shape the kind
    fixes; the term is bounded by the resource's, and the ask expires in line
    after `wait_seconds`."""

    kind: ResourceKind
    resource_id: UUID | None = None
    labels: list[Label] | None = Field(default=None, max_length=MAX_LABELS)
    payload: dict[str, Any] = Field(default_factory=dict)
    term_seconds: int = Field(default=60, ge=1, le=86_400)
    wait_seconds: int = Field(default=3600, ge=1, le=604_800)


class ReorderRequest(RequestBody):
    """Moves a waiting request in front of `before_id`, or to the end."""

    before_id: UUID | None = None
