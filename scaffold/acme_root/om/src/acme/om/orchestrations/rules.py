"""Pure rules of the orchestrations namespace: the transitions a record makes
(a step, a park, a failure, a resume), the stagger of the resumes one wake
makes, and the bounded list of the rows a step skipped. Values in, values
out; no clock, no storage, no settings: the caller passes the time."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from acme.om.orchestrations.types.orchestration import (
    SETTLED,
    FailReason,
    Orchestration,
    OrchestrationStatus,
    ParkReason,
    RowError,
)

ROW_ERRORS_KEPT = 20
"""How many skipped rows a record names with their reason; the rest are
counted. A file of five thousand bad rows is a record of twenty lines."""


def _written(record: Orchestration, now: datetime, actor: UUID, **fields: object) -> Orchestration:
    """Every transition's copy: the fields it changes, the clock, the writer,
    and the next version. It carries no caller input, so it is not validated
    again."""
    return record.model_copy(
        update={**fields, "updated_at": now, "updated_by": actor, "version": record.version + 1}
    )


def is_settled(record: Orchestration) -> bool:
    return record.status in SETTLED


def with_row_errors(
    kept: Sequence[RowError], new: Sequence[RowError], bound: int = ROW_ERRORS_KEPT
) -> tuple[RowError, ...]:
    """The first `bound` skipped rows of a record: those it kept, then the new
    ones, until the bound. The rows past it are counted, never named."""
    return (*kept, *new)[:bound]


def advanced(
    record: Orchestration,
    now: datetime,
    actor: UUID,
    *,
    cursor: int,
    total: int | None,
    skipped: Sequence[RowError] = (),
    finished: bool = False,
    park: ParkReason | None = None,
) -> Orchestration:
    """A step that moved the cursor: the rows it skipped counted and the first
    few named; the record succeeded when the step was the last, or parked
    when a guard stopped it at the cursor. `applied` is not set here: the
    step's own commit adds the rows its effect wrote (`Step`)."""
    fields: dict[str, object] = {
        "cursor": cursor,
        "total": total,
        "skipped": record.skipped + len(skipped),
        "row_errors": with_row_errors(record.row_errors, skipped),
        "park_reason": park,
    }
    if park is not None:
        fields["status"] = OrchestrationStatus.PARKED
    elif finished:
        fields["status"] = OrchestrationStatus.SUCCEEDED
        fields["finished_at"] = now
    return _written(record, now, actor, **fields)


def failed(
    record: Orchestration,
    now: datetime,
    actor: UUID,
    reason: FailReason,
    detail: str | None = None,
) -> Orchestration:
    """A bound ended the record. What it achieved stays achieved; nothing
    more is done."""
    return _written(
        record,
        now,
        actor,
        status=OrchestrationStatus.FAILED,
        fail_reason=reason,
        fail_detail=detail,
        park_reason=None,
        finished_at=now,
    )


def resumed(record: Orchestration, now: datetime, actor: UUID) -> Orchestration:
    """A parked record running again, from its cursor. The reason goes: the
    next step asks its guard again, and parks again if it still holds."""
    return _written(record, now, actor, status=OrchestrationStatus.RUNNING, park_reason=None)


def stagger(position: int, step: timedelta) -> timedelta:
    """The delay before the record at `position` in one wake's resumes takes
    its next step, so the records an event wakes do not all start at once."""
    return step * max(0, position)


def outcome(record: Orchestration) -> str:
    """The label an operator reads a record's transition by: its kind and
    where it stands now, `noop_succeeded`, `noop_parked`."""
    return f"{record.kind.value}_{record.status.value}"
