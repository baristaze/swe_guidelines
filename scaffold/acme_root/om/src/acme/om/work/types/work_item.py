"""Durable background work is a row: what to do, for which record, under
which producer key, on which lane, and its own claim. Payload shapes are
fixed per kind by `WORK_PAYLOADS`, as `TOPIC_PAYLOADS` fixes them per topic;
the row stores the dump."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from acme.om.base import FrozenMapping, Identifiable, Platform, Trackable
from acme.om.context import Permission
from acme.om.orchestrations.types.orchestration import ParkReason


class WorkKind(StrEnum):
    NOOP = "NOOP"  # the maintenance worker's kind: no work beyond the sweep
    ORCHESTRATION = "ORCHESTRATION"  # one step of a long-running record
    WAKE_PARKED = "WAKE_PARKED"  # the reason an org's records parked for is gone
    DELETE_ACCOUNT = "DELETE_ACCOUNT"  # a deleted account's providers, then its personal org
    DELETE_ORG = "DELETE_ORG"  # a closed team org: its providers, then the org


WORK_ROW_PREFIX = "work."
"""The kind of the outbox row that asks for a work item: `work.<kind>`. A write
that also starts work lands such a row beside the one that announces the entity
change, in the same statement, and the relay enqueues the item it names: the
queue is a database role of its own, so no statement reaches both."""


def work_row_kind(kind: WorkKind) -> str:
    """The outbox row kind that asks for work of this kind."""
    return WORK_ROW_PREFIX + kind.value


def asks_for_work(row_kind: str) -> bool:
    """Whether an outbox row asks for a work item rather than announcing a
    change; the row's kind is its destination."""
    return row_kind.startswith(WORK_ROW_PREFIX)


class WorkStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    DONE = "done"
    FAILED = "failed"


class WorkItem(Identifiable, Trackable):
    kind: WorkKind  # what to do
    target_id: UUID  # the record it advances
    idempotency_key: UUID  # unique
    # The request that caused the work and the trace context of that request,
    # the two the run names as its cause and links its spans to. Both are the
    # item's, not the enqueue's: the relay takes them off the outbox row of
    # the write, a direct create off its caller's context, and either enqueue
    # leaves them as constructed. EMPTY_UUID is the platform's marker for no
    # principal and names a producer that knew no request; an empty
    # traceparent is a producer that ran with no tracer configured, and the
    # run then starts a trace of its own.
    request_id: UUID
    traceparent: str | None = None
    payload: FrozenMapping = Field(
        default_factory=dict, validate_default=True
    )  # the dump of WORK_PAYLOADS[kind]
    lane: str = (
        "default"  # routing: "default", "region:<id>", ...; a string, because lanes are dynamic
    )
    status: WorkStatus = WorkStatus.QUEUED
    available_at: datetime  # not before
    claimed_by: str | None = None
    # Minted by the claim and cleared by every hand-back: completion, release,
    # deferral, and renewal condition on it in the statement itself, so a
    # worker that holds one item twice across a requeue cannot settle the
    # first claim's copy over the second's.
    claim_token: UUID | None = None
    lease_expires_at: datetime | None = None
    attempts: int = 0
    max_attempts: int = 3
    last_error: str | None = None


class NoopPayload(Platform):
    """The NOOP kind carries nothing."""


class ScheduledPayload(Platform):
    """A payload that says when its work may run. The relayed enqueue makes
    the item available at `not_before`, or at once when that has passed, so
    work that waits for a time waits in the queue and no timer holds it."""

    not_before: datetime


class OrchestrationPayload(ScheduledPayload):
    """One step of the long-running record the item targets. The step reads
    the record when it runs: its status, its cursor, and its version, which
    the step's write is conditioned on. `not_before` staggers the steps a
    sweep resumes, so a dependency that came back is not met by every parked
    record at once."""


class WakeParkedPayload(Platform):
    """The reason the org's parked records waited for is gone (a provider
    that answers again clears `provider_unavailable`); the item's target is
    the org. Every record parked for it is resumed when the item runs, or
    the one `record_id` names, when the reason was that record's alone (a
    grant, or its request's end without one, clears `resource` for the
    record it was for). A park that knows when its reason may clear (a
    provider marked out until a retry time) asks for the org's wake then:
    the item waits in the queue until `not_before`, and the parks that name
    one time land one item, so the records it wakes resume staggered."""

    reason: ParkReason
    record_id: UUID | None = None
    not_before: datetime | None = None


class DeleteAccountPayload(Platform):
    """What is left of an account once its own rows are gone: the person's
    name at the identity provider, when they signed in through it, since the
    identity that held it is gone. It is an id, never a personal field. The
    item's target is the person's personal org, which it runs in, and the org
    is deleted last."""

    provider_user_id: str | None = None


class DeleteOrgPayload(Platform):
    """What is left of a team org its owner or an operator deleted: its
    organization at the identity provider, when it had one, since the org row
    no longer names it (so no sign-in through it finds the org). It is an id.
    The item's target is the org, which it runs in, and the org is deleted
    last."""

    provider_org_id: str | None = None


WORK_PAYLOADS: dict[WorkKind, type[Platform]] = {
    WorkKind.NOOP: NoopPayload,
    WorkKind.ORCHESTRATION: OrchestrationPayload,
    WorkKind.WAKE_PARKED: WakeParkedPayload,
    WorkKind.DELETE_ACCOUNT: DeleteAccountPayload,
    WorkKind.DELETE_ORG: DeleteOrgPayload,
}
"""The payload shape of every kind; enqueue validates the item's payload against it."""

WORK_ENQUEUE_PERMISSIONS: dict[WorkKind, Permission] = {
    WorkKind.NOOP: Permission.WRITE,
    WorkKind.ORCHESTRATION: Permission.WRITE,
    WorkKind.WAKE_PARKED: Permission.WRITE,
    # Only an account's deletion asks for this one, relayed from its own
    # commit: leaving is every person's right whatever their role, so no
    # route enqueues it, and the permission is the width of the handler.
    WorkKind.DELETE_ACCOUNT: Permission.MANAGE_MEMBERS,
    # Only the deletion of a team org, an owner's or an operator's, asks for
    # this one, relayed from its own commit; no route enqueues it.
    WorkKind.DELETE_ORG: Permission.MANAGE_MEMBERS,
}
"""The permission that asks for each kind. The person who asks authorizes
the whole run once, so the permission has to be as wide as the run: every
role that holds it holds every permission the kind's handler calls with,
which the worker's tests hold each handler to."""

WORK_LANES: dict[WorkKind, str] = {}
"""The lane of each kind that runs on a lane of its own, such as a long-held
kind kept off the lane of short items and that lane's tenant cap. A kind it
does not name runs on the default lane. A layer above adds its own kinds
here and leaves the relay as it is."""


def relayed_lane(kind: WorkKind) -> str:
    """The lane the relay lands an item of this kind on: its own in
    `WORK_LANES`, else the default one. A worker of the kind's own reads its
    lane here too, so the two cannot disagree; a replica of a worker that
    serves many kinds is deployed on the lane this returns."""
    return WORK_LANES.get(kind, "default")
