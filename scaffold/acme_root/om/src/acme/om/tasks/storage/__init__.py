"""Storage of the tasks swimlane. Every operation takes org_id first but the
sweep's read and purge of deleted tasks, which reach across tenants; deleted
tasks never appear in a list. The filter and the cursor arrive as the value
objects the manager received, unchanged."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from acme.om.orchestrations.types.orchestration import Step
from acme.om.outbox.types.row import OutboxRow
from acme.om.tasks.rules import Place
from acme.om.tasks.types.filter import OpenTaskCursor, TaskCursor, TaskFilter
from acme.om.tasks.types.task import Task


class TasksStorageInterface(ABC):
    @abstractmethod
    async def read_open_tasks(
        self, org_id: UUID, criterion: TaskFilter, after: OpenTaskCursor | None, limit: int
    ) -> list[Task]:
        """Open tasks the filter shows (tasks.rules.is_visible), by rank, then
        id, strictly after the cursor (tasks.rules.is_after). `limit` is the
        caller's: the manager asks for one row more than its page."""
        ...

    @abstractmethod
    async def read_recent_open_tasks(
        self, org_id: UUID, criterion: TaskFilter, limit: int
    ) -> list[Task]:
        """Open tasks the filter shows (tasks.rules.is_visible), newest first
        by id, whose time is when the task was made. `limit` is the
        caller's: the manager asks for one row more than its page."""
        ...

    @abstractmethod
    async def count_open_tasks(self, org_id: UUID, criterion: TaskFilter) -> int:
        """How many open tasks the filter shows (tasks.rules.is_visible): a
        number, never the rows, for a caller that shows a page and says how
        many more there are."""
        ...

    @abstractmethod
    async def count_done_tasks(self, org_id: UUID, criterion: TaskFilter) -> int:
        """How many done tasks the filter shows and the cleanup has not
        archived: the done list's length, as `count_open_tasks` is the open
        list's."""
        ...

    @abstractmethod
    async def read_tasks(self, org_id: UUID, task_ids: Sequence[UUID]) -> dict[UUID, Task]:
        """The tenant's tasks among `task_ids`, deleted ones too, by id; an id
        that is not the tenant's is absent. The ids are the bound, which the
        caller picks: a bulk change reads one batch at a time before it
        decides."""
        ...

    @abstractmethod
    async def read_done_tasks(
        self, org_id: UUID, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> list[Task]:
        """Done tasks the filter shows and the cleanup has not archived, newest
        first by (updated_at, id), strictly before the cursor
        (tasks.rules.is_before)."""
        ...

    @abstractmethod
    async def read_archived_tasks(
        self, org_id: UUID, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> list[Task]:
        """Archived tasks the filter shows, newest first by (updated_at, id),
        strictly before the cursor, as the done list pages."""
        ...

    @abstractmethod
    async def read_last_place(self, org_id: UUID) -> Place | None:
        """The place at the bottom of the open list, or None when it is empty:
        where an import puts its tasks after."""
        ...

    @abstractmethod
    async def read_archivable(self, org_id: UUID, before: datetime, limit: int) -> list[UUID]:
        """The ids of the tasks the cleanup archives (tasks.rules.is_archivable),
        oldest change first, at most `limit`."""
        ...

    @abstractmethod
    async def read_open_places(
        self, org_id: UUID, exclude: UUID | None, after: Place | None, limit: int
    ) -> list[Place]:
        """Open tasks' places in the org, their (rank, id), ascending in the
        order the open list reads, strictly after `after` when one is given,
        at most `limit` of them: the neighbours a placement needs, whatever
        list the caller was looking at. The id rides along because a rank is
        not unique (tasks.rules.Place). The caller picks the bound: a
        placement asks for the one place it reads."""
        ...

    @abstractmethod
    async def count_open_and_read_places(
        self, org_id: UUID, criterion: TaskFilter, exclude: UUID | None, limit: int
    ) -> tuple[int, list[Place]]:
        """One transaction: what `count_open_tasks` counts, then what
        `read_open_places` reads from the top. What one more open task under
        a plan's bound needs: the count the bound is held to, and the top
        place it goes above."""
        ...

    @abstractmethod
    async def read_open_places_before(self, org_id: UUID, before: Place, limit: int) -> list[Place]:
        """The open places strictly before `before`, nearest first, at most
        `limit`: the side of a respaced run above its long rank."""
        ...

    @abstractmethod
    async def read_long_place(self, org_id: UUID) -> Place | None:
        """The open place nearest the top whose rank grew past
        `tasks.rules.RANK_SCALE_BOUND` (tasks.rules.needs_respace), or None:
        what the sweep's respace starts from. It reads an index that holds
        only such ranks, so a tenant with none costs one empty read."""
        ...

    @abstractmethod
    async def read_task(self, org_id: UUID, task_id: UUID) -> Task | None: ...

    @abstractmethod
    async def count_created_since(self, since: datetime) -> int:
        """Global: how many tasks were created at or after `since`, across every
        tenant and whatever became of them since; the traffic the operator
        plane's size reads."""
        ...

    @abstractmethod
    async def read_deleted(self, before: datetime, limit: int) -> list[tuple[UUID, UUID]]:
        """Cross-tenant, for the sweep, in the system scope: at most `limit`
        tasks soft-deleted before `before`, the longest deleted first, across
        every tenant, each as its tenant and its id: what the purge takes
        next, read first so the attachments of each go before it does. One
        read a pass, however many tenants there are, so a tenant with nothing
        deleted costs nothing."""
        ...

    @abstractmethod
    async def read_tenants_with_chores(
        self, archivable_before: datetime, after: UUID | None, limit: int
    ) -> list[UUID]:
        """Cross-tenant, for the sweep, in the system scope: the tenants with a
        chore due, each once, in id order, strictly after `after` when one is
        given, at most `limit` of them. A tenant has a chore due when it has a
        task the cleanup archives before `archivable_before`
        (tasks.rules.is_archivable), or an open task whose rank grew past
        `tasks.rules.RANK_SCALE_BOUND` (tasks.rules.needs_respace). One read a
        pass, however many tenants there are, so a tenant with no chore due
        costs the sweep nothing."""
        ...

    @abstractmethod
    async def purge_deleted(self, before: datetime, task_ids: list[UUID]) -> int:
        """Cross-tenant, for the sweep, in the system scope: the one hard
        delete removes those of `task_ids` still soft-deleted before
        `before`, whatever their tenant, skipping a row another transaction
        holds; returns how many. The ids are the ones `read_deleted` gave."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """The hard delete of a deleted tenant's tasks once the retention has
        passed: every task of the tenant, whatever its state, since an open or
        a done task is never soft-deleted and `purge_deleted` would leave it
        behind forever; at most `limit` per call; returns how many rows went."""
        ...

    @abstractmethod
    async def create_task(
        self, org_id: UUID, task: Task, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create: lands the task and the rows that announce it together, or
        neither when the id is already written, which it reports as False. An
        entity change is one row; a create that also starts work passes a second
        row of kind `work.<kind>` in the same tuple, because the queue is a role
        of its own and no statement reaches both."""
        ...

    @abstractmethod
    async def update_task(
        self, org_id: UUID, task: Task, expected_version: int, outbox_rows: tuple[OutboxRow, ...]
    ) -> None:
        """The update, a compare-and-set: lands the task and its outbox rows
        together (the named atomic write) when the stored row is at
        `expected_version`, and raises `PreconditionFailed` when it is at another
        version or is gone, landing nothing. It never inserts: a missing row is
        a row that moved."""
        ...

    @abstractmethod
    async def update_tasks(
        self, org_id: UUID, updates: Sequence[tuple[Task, int, tuple[OutboxRow, ...]]]
    ) -> None:
        """The same compare-and-set over many tasks: every task lands with its
        outbox rows, each against its own expected version, in one commit, or
        none does and `PreconditionFailed` is raised. The sweep's respace of a
        run is this write: a run respaced halfway is out of order."""
        ...

    @abstractmethod
    async def update_tasks_if_current(
        self, org_id: UUID, updates: Sequence[tuple[Task, int, tuple[OutboxRow, ...]]]
    ) -> tuple[bool, ...]:
        """One batch of a bulk change, one commit: each task lands with its
        outbox rows when the stored row is still at its expected version and is
        the tenant's, and is left alone otherwise, without refusing the rest.
        Answers, per update and in order, whether it landed. `update_tasks` is
        the all-or-nothing twin a respace needs; a bulk change is many edits,
        each fenced on its own."""
        ...

    @abstractmethod
    async def create_tasks_in_step(
        self,
        org_id: UUID,
        tasks: Sequence[tuple[Task, tuple[OutboxRow, ...]]],
        step: Step,
        step_rows: tuple[OutboxRow, ...],
    ) -> tuple[bool, ...]:
        """One step of an import, one commit: every task an id not written yet
        (a row stepped twice meets its task already there), each with the rows
        that announce it, and the record as the step left it with the rows
        that announce it and ask for its next step. The record is a
        compare-and-set on the version the step read (`Step`), and its
        `applied` grows by the tasks this commit wrote. A record that moved
        raises `PreconditionFailed` and lands nothing. Answers, per task and in
        order, whether this commit wrote it (and landed its rows) or found it
        there already."""
        ...

    @abstractmethod
    async def update_archived_in_step(
        self,
        org_id: UUID,
        candidates: Sequence[tuple[UUID, tuple[OutboxRow, ...]]],
        before: datetime,
        archived_at: datetime,
        actor: UUID,
        step: Step,
        step_rows: tuple[OutboxRow, ...],
    ) -> tuple[bool, ...]:
        """One step of a cleanup, one commit: one conditional write archives
        the candidates that are still archivable before `before`
        (tasks.rules.is_archivable), moving each one's version on, with the
        rows that announce the ones it archived; a candidate reopened, edited,
        or deleted since it was read is left alone. The record lands beside it
        as on `create_tasks_in_step`. Answers, per candidate and in order,
        whether this commit archived it (and landed its rows)."""
        ...

    @abstractmethod
    async def mark_reminded(
        self,
        org_id: UUID,
        task_id: UUID,
        due_on: date,
        reminded_at: datetime,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Task | None:
        """The reminder's compare-and-set, one conditional write: stamps
        `reminded_at` and moves the version on while the task is open, not
        deleted, still due on `due_on`, and not yet reminded, and lands the
        rows that announce it in the same commit. Returns the task as written,
        or None when any of the four no longer holds, landing nothing: a
        reminder for a moved, cleared, or finished due date, and a second run
        of one that went out, write nothing."""
        ...
