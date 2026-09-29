"""The value objects of a change to many tasks at once: what it does, why a
task was left alone, and what the change did. They travel from the manager
to the service unchanged."""

from enum import StrEnum
from uuid import UUID

from acme.om.base import Platform


class BulkAction(StrEnum):
    """What a bulk change does to each task: the status a single edit would
    write."""

    COMPLETE = "complete"  # an open task becomes done
    REOPEN = "reopen"  # a done task becomes open, on top of the open list


class SkipReason(StrEnum):
    """Why a bulk change left one task alone. Every reason is a fact about the
    task as the change found it, never a failure of the change."""

    NOT_FOUND = "not_found"  # not a live task of this org: never was, deleted, or another's
    ALREADY_DONE = "already_done"  # completing a task someone finished first
    ALREADY_OPEN = "already_open"  # reopening a task someone reopened first
    CHANGED = "changed"  # another write landed between the change's read and its write
    PLAN_LIMIT = "plan_limit"  # reopening it would take the org past its plan's active tasks


class SkippedTask(Platform):
    id: UUID
    reason: SkipReason


class PlanBound(Platform):
    """The plan's bound a reopen met: the lever, the org's plan, the bound,
    and the first plan above it that lifts it. The fields of the refusal a
    single reopen raises (`PlanLimitReached`), kept as a value, since a bulk
    change answers with what it did and names the bound beside it."""

    lever: str
    plan: str
    limit: int | None
    suggested_plan: str | None


class BulkOutcome(Platform):
    """What a bulk change did. `changed` names the tasks it wrote, in the
    order it wrote them, and `skipped` the ones it left alone with the reason;
    each list names at most the report's cap (`tasks.rules.BULK_REPORT_CAP`),
    and the counts beside them are whole. `plan_bound` is set when a reopen
    met the plan's bound on active tasks."""

    action: BulkAction
    changed: tuple[UUID, ...]
    changed_count: int
    skipped: tuple[SkippedTask, ...]
    skipped_count: int
    plan_bound: PlanBound | None = None
