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

from pydantic import Field

from acme.om.base import FrozenMapping, Identifiable, Platform, Trackable


class OrchestrationKind(StrEnum):
    """A product adds its kinds here, each with its input shape
    (`ORCHESTRATION_INPUTS`) and the step the worker runs for it."""

    NOOP = "noop"  # the mechanism's own: steps through its input's count, and changes nothing else


class OrchestrationStatus(StrEnum):
    RUNNING = "running"  # a work item carries the next step
    PARKED = "parked"  # stopped with a reason; woken by the event that clears it, or a person
    SUCCEEDED = "succeeded"
    FAILED = "failed"


SETTLED = frozenset({OrchestrationStatus.SUCCEEDED, OrchestrationStatus.FAILED})
"""The statuses a record never leaves."""


class ParkReason(StrEnum):
    """Why a record waits, and so what wakes it."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"  # a provider a step calls did not answer
    RESOURCE = "resource"  # in line for a resource; the grant wakes it


class FailReason(StrEnum):
    """Why a record ended without finishing: a bound, never a guard."""

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
    # What the steps did: the rows their effects wrote.
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


class NoopInput(Platform):
    """How many steps the record takes before it succeeds."""

    steps: int = Field(default=1, ge=1, le=100)


ORCHESTRATION_INPUTS: dict[OrchestrationKind, type[Platform]] = {
    OrchestrationKind.NOOP: NoopInput,
}
"""The input shape of every kind; the start validates a record's input against it."""


class Step(Platform):
    """A step's companion write: the record as the step leaves it, landed in
    the same commit as the step's effect (the rows of the namespace it
    changes) and conditioned on the version the step read. The record's `applied` grows,
    in that commit, by the rows the effect wrote, so a row another writer
    got to first is not counted twice."""

    record: Orchestration
    expected_version: int
