"""A long-running orchestration: work that takes minutes, kept as a durable
record with a status and a cursor, and advanced one step at a time by
whichever worker holds its work item. The claim lives on the work item, never
on the record, so a worker that dies leaves the record at its last committed
step and the next claim goes on from there.

A record has three outcomes. It succeeds, it fails, or it parks. A park
stops the work with a reason and keeps everything the record achieved; the
event that clears the reason, or a person, wakes it. A guard parks; only a
bound fails. An error that is neither (a database or a store that did not
answer) is the work queue's to retry: the item comes back with a growing
delay, and the step starts again at the cursor the last commit left."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from pydantic import Field

from acme.om.base import FrozenMapping, Identifiable, Platform, Trackable


class OrchestrationKind(StrEnum):
    TASK_IMPORT = "task_import"  # tasks made from the rows of a CSV file the org uploaded
    TASK_CLEANUP = "task_cleanup"  # done tasks past an age archived; one record per org per day


class OrchestrationStatus(StrEnum):
    RUNNING = "running"  # a work item carries the next step
    PARKED = "parked"  # stopped with a reason; woken by the event that clears it, or a person
    SUCCEEDED = "succeeded"
    FAILED = "failed"


SETTLED = frozenset({OrchestrationStatus.SUCCEEDED, OrchestrationStatus.FAILED})
"""The statuses a record never leaves."""


class ParkReason(StrEnum):
    """Why a record waits, and so what wakes it."""

    PLAN_LIMIT = "plan_limit"  # the plan's bound: a plan that rises, or a person, wakes it


class FailReason(StrEnum):
    """Why a record ended without finishing: a bound, never a guard."""

    FILE_TOO_LARGE = "file_too_large"
    TOO_MANY_ROWS = "too_many_rows"
    NOT_CSV = "not_csv"
    NO_TITLE_COLUMN = "no_title_column"
    FILE_GONE = "file_gone"  # the file was removed, or never arrived in the store
    DEFECT = "defect"  # a step still failed on its work item's last attempt


class RowError(Platform):
    """One input row a step skipped: its number, counting the first data row
    as 1, and why."""

    row: int
    reason: str


class Orchestration(Identifiable, Trackable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "status",
        "cursor",
        "total",
        "applied",
        "skipped",
        "row_errors",
        "park_reason",
        "fail_reason",
        "fail_detail",
        "finished_at",
        "version",
    )
    """Every field but the kind, the input, and the period is the steps'."""

    kind: OrchestrationKind
    # The dump of ORCHESTRATION_INPUTS[kind]: what the record works on.
    input: FrozenMapping = Field(default_factory=dict, validate_default=True)
    # The period a record kept per period is for (a day, `2026-09-25`); the
    # org, the kind, and the period are its unique key. None for a record
    # a person started.
    period: str | None = None
    status: OrchestrationStatus = OrchestrationStatus.RUNNING
    # Where the next step starts: rows read of the input, or rows looked at.
    cursor: int = 0
    # How many there are to read, once a step has counted them.
    total: int | None = None
    # What the steps did: the tasks created, or the tasks archived.
    applied: int = 0
    # Rows a step passed over, and the first few of them with why.
    skipped: int = 0
    row_errors: tuple[RowError, ...] = ()
    park_reason: ParkReason | None = None
    fail_reason: FailReason | None = None
    fail_detail: str | None = None
    finished_at: datetime | None = None
    # Every write is a compare-and-set on it: a stale worker's step, landed
    # after the next holder's, is refused with nothing written.
    version: int = 1


class OrchestrationPage(Platform):
    items: tuple[Orchestration, ...]
    has_more: bool


class TaskImportInput(Platform):
    """The CSV file the import reads, uploaded through the media namespace."""

    file_id: UUID


class TaskCleanupInput(Platform):
    """Done tasks last changed before `before` are archived. The cutoff is
    fixed when the day's record opens (`older_than_days` before then), so
    every step of one record asks the same question."""

    older_than_days: int
    before: datetime


ORCHESTRATION_INPUTS: dict[OrchestrationKind, type[Platform]] = {
    OrchestrationKind.TASK_IMPORT: TaskImportInput,
    OrchestrationKind.TASK_CLEANUP: TaskCleanupInput,
}
"""The input shape of every kind; the start validates a record's input against it."""


class Step(Platform):
    """A step's companion write: the record as the step leaves it, landed in
    the same commit as the step's effect (tasks created, tasks archived) and
    conditioned on the version the step read. The record's `applied` grows,
    in that commit, by the rows the effect wrote, so a row another writer
    got to first is not counted twice."""

    record: Orchestration
    expected_version: int
