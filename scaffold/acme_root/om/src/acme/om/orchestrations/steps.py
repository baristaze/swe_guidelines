"""The rows a record's write lands beside it: the hint that announces the
record changed, and, while it runs, the work row that asks for its next
step, or, when its park names a time, the work row of its wake. A step
writes them in the commit of its own effect, whichever namespace's storage
makes it; the manager writes them with every transition it makes itself (a
start, a resume, a park, a failure)."""

from datetime import datetime

from acme.om.context import ProvenanceScope
from acme.om.orchestrations.types.orchestration import Orchestration, OrchestrationStatus
from acme.om.outbox.types.row import OutboxRow, outbox_row, versioned_row
from acme.om.work.types.work_item import (
    OrchestrationPayload,
    WakeParkedPayload,
    WorkKind,
    work_row_kind,
)

CREATED = "orchestrations.orchestration.created"
UPDATED = "orchestrations.orchestration.updated"


def step_rows(
    ctx: ProvenanceScope,
    record: Orchestration,
    *,
    created: bool = False,
    not_before: datetime | None = None,
    wake_at: datetime | None = None,
) -> tuple[OutboxRow, ...]:
    """The hint (ids, and the version the write left, as the row of a
    versioned record carries: ADR 0061), then the next step's work row while
    the record runs. The work waits in the queue until `not_before`, which is
    how a wake staggers the steps it resumes.

    A park that knows when its reason may clear passes `wake_at`: a step that
    reads a provider's outage parks on `provider_unavailable` until the
    outage's retry time. The park lands the `WAKE_PARKED` row of its own
    record, waiting in the queue until then, and the woken record asks its
    guard again.

    The work row asks as the person who started the record, whoever wrote
    this step: a step woken by the platform or an operator still runs as the
    person who asked for the work, so what it makes is theirs, and their
    principal is the one the claim rebuilds."""
    hint = versioned_row(ctx, CREATED if created else UPDATED, record.id, record.version)
    if record.status is OrchestrationStatus.PARKED and record.park_reason and wake_at:
        wake = WakeParkedPayload(reason=record.park_reason, record_id=record.id, not_before=wake_at)
        row = outbox_row(
            ctx, work_row_kind(WorkKind.WAKE_PARKED), ctx.org_id, wake.model_dump(mode="json")
        )
        return (hint, row)
    if record.status is not OrchestrationStatus.RUNNING:
        return (hint,)
    payload = OrchestrationPayload(not_before=not_before or record.updated_at)
    work = outbox_row(
        ctx,
        work_row_kind(WorkKind.ORCHESTRATION),
        record.id,
        payload.model_dump(mode="json"),
    ).model_copy(update={"actor_id": record.created_by})
    return (hint, work)
