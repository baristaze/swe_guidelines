from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from acme.om.base import Identifiable, Platform, SoftDeletable, Trackable


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class TaskScope(StrEnum):
    """Which tasks a list shows. The org is the team."""

    MINE = "mine"  # assigned to the caller, or unassigned and created by the caller
    TEAM = "team"  # every task in the org


class Task(Identifiable, Trackable, SoftDeletable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "rank",
        "version",
        "reminded_at",
        "archived_at",
    )
    """A place in the open list, the rank, is set by the create, the move,
    the reopen, the import, and the sweep's respace; the version by every
    write; when the reminder went out by the reminder itself; the archive by
    the cleanup, the restore, and the reopen. An update the caller shaped
    keeps all of them as stored."""

    title: str
    notes: str = ""
    status: TaskStatus = TaskStatus.OPEN
    assignee_id: UUID | None = None
    # Manual order of the open list, ascending; the top is the smallest. An
    # exact decimal, so there is always a rank between two others and a move
    # writes one row (tasks.rules.rank_between). Done tasks keep theirs but
    # sort by updated_at instead.
    rank: Decimal = Decimal(0)
    # Optimistic concurrency: the manager's copy increments it on every write,
    # and the write lands only when the stored row still has the version the
    # caller names beside the entity. A caller that names another is stale.
    version: int = 1
    # The day the task is due: a date, never a time. Setting it schedules one
    # reminder on the morning of that day, in the time zone of the person the
    # task is for (tasks.rules.reminder_time); moving or clearing it leaves
    # the scheduled one stale, and a stale reminder never goes out.
    due_on: date | None = None
    # When the reminder for the current `due_on` went out; None while it has
    # not. A new `due_on` clears it.
    reminded_at: datetime | None = None
    # When the daily cleanup archived the task: a done task left unchanged
    # for the archive age (90 days by default). It leaves the done list and
    # every list and count shown by default, and it can still be read and
    # restored. None for a task that is not archived.
    archived_at: datetime | None = None


class DueReminder(Platform):
    """The reminder a task waits for: the due date it is for, and the moment
    it goes out."""

    due_on: date
    at: datetime
