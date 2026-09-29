"""The tasks swimlane: the to-do items a team creates, works, and closes."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from uuid import UUID

from acme.om.media.types.file import File
from acme.om.media.types.page import FilePage
from acme.om.opcontext import OpContext, RequestContext
from acme.om.orchestrations.types.orchestration import Orchestration, OrchestrationPage
from acme.om.tasks.types.bulk import BulkAction, BulkOutcome
from acme.om.tasks.types.filter import OpenTaskCursor, TaskCursor, TaskFilter
from acme.om.tasks.types.page import TaskPage
from acme.om.tasks.types.task import DueReminder, Task, TaskStatus


class TasksManagerInterface(ABC):
    @abstractmethod
    async def get_open_tasks(
        self, ctx: OpContext, criterion: TaskFilter, after: OpenTaskCursor | None, limit: int
    ) -> TaskPage:
        """One page of the open tasks the filter shows, in manual order, top
        first, strictly after the cursor. The filter's user is the caller:
        `mine` is about nobody else. `limit` is clamped; `has_more` says
        whether a page follows, whatever the clamp did."""
        ...

    @abstractmethod
    async def get_recent_open_tasks(
        self, ctx: OpContext, criterion: TaskFilter, limit: int
    ) -> TaskPage:
        """The newest open tasks the filter shows, newest first: one page and
        no cursor, for a caller that shows a few and links to the rest. The
        filter's user is the caller, as on `get_open_tasks`; `limit` is
        clamped, and `has_more` says whether more are open."""
        ...

    @abstractmethod
    async def count_open_tasks(self, ctx: OpContext, criterion: TaskFilter) -> int:
        """How many open tasks the filter shows, the number beside a page of
        `get_open_tasks`; the filter's user is the caller, as there."""
        ...

    @abstractmethod
    async def count_tasks(self, ctx: OpContext, criterion: TaskFilter, status: TaskStatus) -> int:
        """How many tasks the list of `status` shows under the filter: the open
        list, or the done list without the archived tasks. The number a
        "Mark all" asks about before it runs; the filter's user is the caller,
        as on `get_open_tasks`."""
        ...

    @abstractmethod
    async def get_done_tasks(
        self, ctx: OpContext, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> TaskPage:
        """One page of the done tasks the filter shows, newest first, strictly
        before the cursor; `limit` and `has_more` as on `get_open_tasks`."""
        ...

    @abstractmethod
    async def get_archived_tasks(
        self, ctx: OpContext, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> TaskPage:
        """One page of the archived tasks the filter shows, newest first,
        paged as the done list is."""
        ...

    @abstractmethod
    async def get_task(self, ctx: OpContext, task_id: UUID) -> Task:
        """A live task, archived or not: an archived one is still read."""
        ...

    @abstractmethod
    async def create_task(self, ctx: OpContext, task: Task) -> Task:
        """A new task is open and goes to the top of the open list. An org at
        its plan's bound of active tasks is refused (`PlanLimitReached`)."""
        ...

    @abstractmethod
    async def update_task(self, ctx: OpContext, task: Task, expected_version: int) -> Task:
        """A task reopened from done goes back to the top of the open list, and
        is refused like a create when the org is at its bound. The
        entity supplies the fields a caller may change; the provenance and the
        task's `MANAGER_OWNED_FIELDS` stay as stored. `expected_version` is the
        version the caller read, never one read here: the write lands only
        when the stored task is still at it, and is `PreconditionFailed` when
        another writer landed since."""
        ...

    @abstractmethod
    async def move_task(
        self, ctx: OpContext, task_id: UUID, after_id: UUID | None, expected_version: int
    ) -> Task:
        """Places an open task right after `after_id` in the open list, or at
        the top when it is None, by giving it a rank between its new
        neighbours: the one write is the moved task's, and no other task's
        version moves. `expected_version` is the one the caller read, as on
        `update_task`."""
        ...

    @abstractmethod
    async def change_tasks(
        self, ctx: OpContext, action: BulkAction, task_ids: Sequence[UUID]
    ) -> BulkOutcome:
        """Completes or reopens the named tasks, at most `BULK_MAX_IDS` of them
        (more is `ValidationFailed`), a batch at a time (`BULK_BATCH`), one
        commit a batch. Each task is an edit of its own under the rules a
        single edit applies (`tasks.rules.bulk_skip`) and fenced on the
        version this change read: a task that is not the org's, is deleted,
        is already in the status asked for, or moved between the read and the
        write is skipped and counted, never a refusal of the rest. A reopen
        puts each task on top of the open list, the last one named on top,
        and reopens only as many as the plan's bound on active tasks still
        allows; the rest are skipped for it and the answer names the bound.
        Each changed task is announced as a single edit announces it; nothing
        is posted to Slack."""
        ...

    @abstractmethod
    async def change_list(
        self, ctx: OpContext, action: BulkAction, criterion: TaskFilter, status: TaskStatus
    ) -> BulkOutcome:
        """The same change over a whole list rather than named tasks: every
        task the list of `status` shows under the filter, read a batch at a
        time from its top, as `change_tasks` applies it. Completing is for the
        open list and reopening for the done list; the other pairs are
        `ValidationFailed`. The filter's user is the caller."""
        ...

    @abstractmethod
    async def delete_task(self, ctx: OpContext, task_id: UUID, expected_version: int) -> Task:
        """The soft delete. `expected_version` is the one the caller read, as
        on `update_task`: a delete that raced an edit is refused, and an edit
        that raced a delete finds the task gone and cannot bring it back. The
        task's attachments are soft-deleted after it, in their own writes."""
        ...

    @abstractmethod
    async def restore_task(self, ctx: OpContext, task_id: UUID, expected_version: int) -> Task:
        """Takes an archived task back to the done list, at its top. A task
        that is not archived is refused (`ValidationFailed`). `expected_version`
        is the one the caller read, as on `update_task`."""
        ...

    @abstractmethod
    async def attach_file(self, ctx: OpContext, task_id: UUID, file: File) -> File:
        """Starts an upload of a file to a live task: the media namespace lands
        it pending, as a task attachment whose subject is the task, whatever
        purpose and subject the caller's file names. The bytes and the confirm
        go through the media namespace."""
        ...

    @abstractmethod
    async def get_attachments(
        self, ctx: OpContext, task_id: UUID, after: UUID | None, limit: int
    ) -> FilePage:
        """One page of a live task's stored attachments, oldest first."""
        ...

    @abstractmethod
    async def remove_attachment(self, ctx: OpContext, task_id: UUID, file_id: UUID) -> File:
        """Soft-deletes one attachment of a live task. A file that is not this
        task's attachment is `NotFound`, as one that never existed is."""
        ...

    # The import of tasks from a CSV file.

    @abstractmethod
    async def create_import_file(self, ctx: OpContext, file: File) -> File:
        """Starts the upload of a CSV file to import: the media namespace
        lands it pending under the `task_import` purpose and its bounds,
        whatever purpose and subject the caller's file names. The bytes and
        the confirm go through the media namespace."""
        ...

    @abstractmethod
    async def start_import(self, ctx: OpContext, import_id: UUID, file_id: UUID) -> Orchestration:
        """Starts the import of a stored `task_import` file: the record and
        the work row of its first step, in one commit. The rows are read by
        the worker, a batch a step. An id written already answers the import
        as stored."""
        ...

    @abstractmethod
    async def get_import(self, ctx: OpContext, import_id: UUID) -> Orchestration: ...

    @abstractmethod
    async def get_imports(self, ctx: OpContext, limit: int) -> OrchestrationPage:
        """The org's newest imports, newest first."""
        ...

    @abstractmethod
    async def resume_import(self, ctx: OpContext, import_id: UUID) -> Orchestration:
        """A person's wake of a parked import: it runs again from its cursor,
        and parks again at once if the plan still has no room."""
        ...

    @abstractmethod
    async def step_import(self, ctx: OpContext, record: Orchestration) -> Orchestration:
        """One step of an import: reads the file, checks its bounds, and makes
        the tasks of the next batch of rows in one commit with the record's
        next cursor (`TasksStorageInterface.create_tasks_in_step`). A row that
        makes no task is skipped and named; a row that would take the org past
        its plan's bound of active tasks parks the record `plan_limit` at that
        row; a file past a bound fails it. Each task's id is derived from the
        import and the row, so a step run twice makes each task once."""
        ...

    # The daily cleanup of old done tasks.

    @abstractmethod
    async def open_cleanup(self, ctx: OpContext) -> Orchestration | None:
        """The sweep, for one tenant: opens today's cleanup record when the org
        has a done task unchanged since the day began, less the archive age
        (`tasks.rules.archive_cutoff`), and today's record is not open yet;
        the org, the kind, and the day are its unique key, so every sweep
        after the first one of the day opens nothing. None when there is
        nothing to archive."""
        ...

    @abstractmethod
    async def step_cleanup(self, ctx: OpContext, record: Orchestration) -> Orchestration:
        """One step of a cleanup: archives the next batch of done tasks
        unchanged since the record's cutoff, in one conditional write with the
        record's next cursor (`TasksStorageInterface.update_archived_in_step`).
        A task reopened, edited, or deleted meanwhile is left alone."""
        ...

    @abstractmethod
    async def count_active_tasks(self, ctx: OpContext) -> int:
        """How many of the org's tasks are open and not deleted: what the
        plan's active-task bound counts."""
        ...

    @abstractmethod
    async def get_due_reminder(self, ctx: OpContext, task_id: UUID) -> DueReminder | None:
        """The reminder the task is waiting for: its due date and the moment
        the reminder goes out, nine in the morning of that date in the time
        zone of the person the task is for (`tasks.rules.reminder_time`),
        read as the task and the person are now. None when the task waits for
        none: no due date, done, deleted, gone, or reminded already."""
        ...

    @abstractmethod
    async def fire_reminder(self, ctx: OpContext, task_id: UUID, due_on: date) -> Task | None:
        """The reminder of the task's due date, when its moment has come:
        marks the task reminded and announces it (`tasks.task.reminded`), and
        asks for the Slack post when the org has a Slack channel bound, all in
        one write conditioned on the task still being open and due on `due_on`
        and not yet reminded. None when it no longer is, which is a reminder
        gone stale: nothing is written and nothing is announced."""
        ...

    @abstractmethod
    async def respace_ranks(self, ctx: OpContext) -> int:
        """The sweep, for one tenant: when an open task's rank grew past
        `tasks.rules.RANK_SCALE_BOUND`, gives its run (the tasks between the
        nearest short ranks around it, `tasks.rules.respace_run`) ranks that
        are short again, in the order they had, in one write conditioned on
        each task's version. Each task it writes moves its version on and is
        announced as an edit is. A run written meanwhile is left for the next
        pass. Returns how many tasks it respaced; zero when none needed it."""
        ...

    @abstractmethod
    async def tenants_with_chores(self, after: UUID | None, limit: int) -> list[UUID]:
        """Platform-internal: the sweep's one read a pass, across tenants, of
        the tenants whose tasks have a chore due: a done task today's cleanup
        archives (`open_cleanup`), or an open task whose rank grew long
        (`respace_ranks`). At most `limit` of them, in id order, after `after`
        when one is given; the sweep runs the chores in those tenants alone.
        Takes no context, because it reads for no tenant and no principal."""
        ...

    @abstractmethod
    async def purge_across_tenants(self, rctx: RequestContext) -> int:
        """Platform-internal: the sweep, across tenants, once a pass: hard-deletes
        a batch of tasks soft-deleted longer ago than the retention period,
        whatever their tenant; returns how many. The attachments of each go
        first, under the service context of its tenant minted from `rctx`
        (the tenant-shaped part, paid only by a tenant that has such a task);
        a task whose files will not go stays for the next pass. A count of a
        whole batch says there may be more. The one hard delete of a task
        that lives on."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: OpContext) -> int:
        """The sweep, for one tenant deleted longer ago than the retention: every
        task goes, open and done ones too, since the tenant keeps nothing but
        its org row; at most a batch a call; returns how many. Any other
        tenant returns 0 and reads nothing: its deleted tasks go across
        tenants."""
        ...
