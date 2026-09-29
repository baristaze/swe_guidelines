"""One row per handoff: the record that changed, how, a payload of ids and
values that are not personal, and the principal and request that produced
it, so the event the relay appends carries the same provenance the core
write did. A payload never carries a person's field (an address, a name):
the stream keeps it, and an erasure cannot rewrite the stream."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from acme.om.base import Created, FrozenMapping, Identifiable, new_id, utcnow
from acme.om.opcontext import ProvenanceScope


class OutboxRow(Identifiable, Created):
    org_id: UUID  # carried on the row: the relay and the sweep run with no context
    kind: str  # "<namespace>.<entity>.<created|updated|deleted>"
    target_id: UUID  # the record that changed
    payload: FrozenMapping = Field(
        default_factory=dict, validate_default=True
    )  # ids, and values that are not personal; never a person's field
    actor_id: UUID  # the user whose request produced it
    request_id: UUID  # the request that produced it
    # The trace context of that request, as the W3C header spells it and not
    # as a trace id: an id names a trace, and only the header carries what the
    # span on the far side of the handoff links to. Empty when the write ran
    # with no tracer configured, and the far side then starts its own trace.
    traceparent: str | None = None
    app: str  # the AppType value the request came from
    done_at: datetime | None = None  # set by the relay; the sweep purges done rows
    # The sweep's claim: each claim spends an attempt and sets the next one
    # with a growing delay, so a row that will not relay stops nothing behind
    # it; past the relay's max_attempts the row is failed, a dead letter.
    attempts: int = 0
    next_attempt_at: datetime | None = None  # None: at once
    last_error: str | None = None
    failed_at: datetime | None = None


def outbox_row(
    ctx: ProvenanceScope, kind: str, target_id: UUID, payload: Mapping[str, Any]
) -> OutboxRow:
    """The row a manager writes beside its core row, under the caller's provenance:
    the tenant, the actor, the request, its trace context, and the app are all
    the row reads from the context. With no tracer configured where the stage
    was minted, the trace context is empty."""
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=ctx.org_id,
        kind=kind,
        target_id=target_id,
        payload=payload,
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
        traceparent=ctx.traceparent,
        app=ctx.app.type.value,
    )


def versioned_row(ctx: ProvenanceScope, kind: str, target_id: UUID, version: int) -> OutboxRow:
    """The row of a change to a record that carries a version: its payload is
    the version the change wrote, a counter and never a field's value. The
    push carries it, so a client that already holds the record at that
    version, the one whose own write it is, reads nothing (ADR 0061)."""
    return outbox_row(ctx, kind, target_id, {"version": version})
