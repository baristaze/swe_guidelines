"""A lease: one grant of one resource to one principal, under a fencing token
greater than every earlier grant of that resource. Whatever acts on the
resource for the holder presents the token, and the resource's own side
refuses one lower than the highest it has seen. A grant that starts a job,
a work item in the grant's own commit, names it, and the worker that claims
it acts for the holder."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from acme.om.base import Identifiable, Platform, Trackable


class LeaseStatus(StrEnum):
    ACTIVE = "active"  # the holder may act on the resource until it expires
    RELEASED = "released"  # its holder gave it back
    EXPIRED = "expired"  # past its expiry and the skew margin, ended by the sweep or a grant
    REVOKED = "revoked"  # a manager took it back


class Lease(Identifiable, Trackable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "token",
        "expires_at",
        "status",
        "ended_at",
        "started_at",
    )
    """A lease is the grant's and its transitions'; no caller writes it."""

    resource_id: UUID
    request_id: UUID  # the request it answered; one lease per request
    holder_id: UUID  # the principal that asked, and the one that renews and releases
    token: int  # one above the anchor's when it was granted
    # How long a grant, a job's start, or a renewal that names no length
    # runs, within the resource's bound.
    term_seconds: int
    # On the server's clock; the holder keeps its own. For a job not started
    # yet, the end of the window the grant gave it to start in.
    expires_at: datetime
    status: LeaseStatus = LeaseStatus.ACTIVE
    ended_at: datetime | None = None
    # The key of the work item the grant wrote in its own commit, when what
    # it starts is a job: the worker that holds that item's claim acts for
    # the holder. None for a lease its holder keeps itself.
    job_key: UUID | None = None
    started_at: datetime | None = None  # when its job started


class JobClaim(Platform):
    """What the worker that runs a lease's job presents to act for its
    holder: the lease's token, and the claim token of the job's work item.
    A claim the queue has since taken back, or given to another worker, no
    longer holds."""

    token: int
    claim_token: UUID


class Grant(Platform):
    """What a grant writes in one commit: the lease, under the request it
    answers, and the token the anchor held when the grant was decided. The
    storage lands it only while the anchor still holds that token and no
    lease, and while the request still waits."""

    lease: Lease
    expected_token: int
