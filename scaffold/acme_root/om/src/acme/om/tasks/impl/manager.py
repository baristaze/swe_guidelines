import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from acme.infra.observability import OUTCOMES
from acme.om.base import PROVENANCE_FIELDS, Platform, derived_id, utcnow
from acme.om.billing.manager import EntitlementsInterface
from acme.om.billing.rules import limits_of, refuse_past
from acme.om.billing.types.plan import Lever, Plan
from acme.om.exceptions import (
    NotFound,
    PlanLimitReached,
    PreconditionFailed,
    TenantMismatch,
    ValidationFailed,
)
from acme.om.media import MediaManagerInterface
from acme.om.media.rules import BOUNDS
from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.media.types.page import FilePage
from acme.om.opcontext import OpContext, Permission, RequestContext
from acme.om.orchestrations import OrchestrationsManagerInterface
from acme.om.orchestrations.rules import advanced
from acme.om.orchestrations.steps import step_rows
from acme.om.orchestrations.types.orchestration import (
    FailReason,
    Orchestration,
    OrchestrationKind,
    OrchestrationPage,
    ParkReason,
    RowError,
    Step,
    TaskCleanupInput,
    TaskImportInput,
)
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row, versioned_row
from acme.om.slack import SlackManagerInterface
from acme.om.slack.types.installation import SlackInstallationStatus
from acme.om.tasks.manager import TasksManagerInterface
from acme.om.tasks.rules import (
    BULK_BATCH,
    BULK_MAX_IDS,
    BULK_REPORT_CAP,
    CLEANUP_BATCH,
    IMPORT_BATCH,
    RESPACE_REACH,
    ImportedTask,
    ImportFileRefused,
    Place,
    archive_cutoff,
    bulk_ranks,
    bulk_skip,
    cleanup_part,
    cleanup_period,
    earliest_reminder_time,
    import_refusal,
    import_row_part,
    imported,
    parse_import,
    rank_after,
    reminder_person,
    reminder_time,
    respace_run,
    respaced,
    room_for,
    spread,
    top_rank,
)
from acme.om.tasks.storage import TasksStorageInterface
from acme.om.tasks.types.bulk import (
    BulkAction,
    BulkOutcome,
    PlanBound,
    SkippedTask,
    SkipReason,
)
from acme.om.tasks.types.filter import OpenTaskCursor, TaskCursor, TaskFilter
from acme.om.tasks.types.page import TaskPage
from acme.om.tasks.types.task import DueReminder, Task, TaskScope, TaskStatus
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.rules import fold_email
from acme.om.work.types.work_item import (
    SlackPostEvent,
    SlackPostPayload,
    TaskReminderPayload,
    WorkKind,
    work_row_kind,
)

log = logging.getLogger(__name__)

NEIGHBOURS = 1
"""How many open places a placement reads: the top one for a task placed on
top, the one after the anchor for a move. The rules decide from that place
alone (tasks.rules.top_rank, rank_after), so the read stays one row however
long the open list grows, and the write is the placed task's one row."""


class TasksOptions(Platform):
    max_limit: int = 200
    retention: timedelta = timedelta(days=30)  # a deleted task is purged after this
    purge_batch: int = 1000  # deleted tasks one purge takes at most
    # A done task unchanged this long is archived by the daily cleanup. The
    # worker's setting; 90 days is illustrative, like the plans' numbers.
    archive_after: timedelta = timedelta(days=90)


class _BulkChange:
    """One bulk change as it runs: what it did so far, capped for the answer
    and counted whole, and the room the plan leaves for a reopen. A value the
    manager's loop fills, never shared between two changes."""

    def __init__(self, action: BulkAction, room: int | None, plan: Plan | None) -> None:
        self.action = action
        self.room = room  # None: no bound, or a change that opens nothing
        self.plan = plan
        self.changed: list[UUID] = []
        self.changed_count = 0
        self.skipped: list[SkippedTask] = []
        self.skipped_count = 0
        self.plan_bound: PlanBound | None = None

    def change(self, task_id: UUID) -> None:
        self.changed_count += 1
        if len(self.changed) < BULK_REPORT_CAP:
            self.changed.append(task_id)

    def skip(self, task_id: UUID, reason: SkipReason) -> None:
        self.skipped_count += 1
        if len(self.skipped) < BULK_REPORT_CAP:
            self.skipped.append(SkippedTask(id=task_id, reason=reason))

    def outcome(self) -> BulkOutcome:
        return BulkOutcome(
            action=self.action,
            changed=tuple(self.changed),
            changed_count=self.changed_count,
            skipped=tuple(self.skipped),
            skipped_count=self.skipped_count,
            plan_bound=self.plan_bound,
        )


class TasksManagerImpl(TasksManagerInterface):
    def __init__(
        self,
        storage: TasksStorageInterface,
        tenancy: TenancyManagerInterface,
        media: MediaManagerInterface,
        relay: OutboxRelayInterface,
        slack: SlackManagerInterface,
        options: TasksOptions,
        *,
        entitlements: EntitlementsInterface,
        orchestrations: OrchestrationsManagerInterface,
    ) -> None:
        self._orchestrations = orchestrations
        self._storage = storage
        self._tenancy = tenancy
        self._media = media
        self._relay = relay
        self._slack = slack
        self._options = options
        self._entitlements = entitlements

    async def get_open_tasks(
        self, ctx: OpContext, criterion: TaskFilter, after: OpenTaskCursor | None, limit: int
    ) -> TaskPage:
        ctx.require(Permission.READ)
        self._own(ctx, criterion)
        limit = self._clamp(limit)
        rows = await self._storage.read_open_tasks(ctx.org_id, criterion, after, limit + 1)
        return self._page(rows, limit)

    async def get_recent_open_tasks(
        self, ctx: OpContext, criterion: TaskFilter, limit: int
    ) -> TaskPage:
        ctx.require(Permission.READ)
        self._own(ctx, criterion)
        limit = self._clamp(limit)
        rows = await self._storage.read_recent_open_tasks(ctx.org_id, criterion, limit + 1)
        return self._page(rows, limit)

    async def count_open_tasks(self, ctx: OpContext, criterion: TaskFilter) -> int:
        ctx.require(Permission.READ)
        self._own(ctx, criterion)
        return await self._storage.count_open_tasks(ctx.org_id, criterion)

    async def count_tasks(self, ctx: OpContext, criterion: TaskFilter, status: TaskStatus) -> int:
        ctx.require(Permission.READ)
        self._own(ctx, criterion)
        if status is TaskStatus.OPEN:
            return await self._storage.count_open_tasks(ctx.org_id, criterion)
        return await self._storage.count_done_tasks(ctx.org_id, criterion)

    async def get_done_tasks(
        self, ctx: OpContext, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> TaskPage:
        ctx.require(Permission.READ)
        self._own(ctx, criterion)
        limit = self._clamp(limit)
        rows = await self._storage.read_done_tasks(ctx.org_id, criterion, before, limit + 1)
        return self._page(rows, limit)

    async def get_archived_tasks(
        self, ctx: OpContext, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> TaskPage:
        ctx.require(Permission.READ)
        self._own(ctx, criterion)
        limit = self._clamp(limit)
        rows = await self._storage.read_archived_tasks(ctx.org_id, criterion, before, limit + 1)
        return self._page(rows, limit)

    async def get_task(self, ctx: OpContext, task_id: UUID) -> Task:
        ctx.require(Permission.READ)
        task = await self._storage.read_task(ctx.org_id, task_id)
        if task is None or task.deleted_at is not None:
            raise NotFound(f"task {task_id} not found")
        return task

    async def create_task(self, ctx: OpContext, task: Task) -> Task:
        ctx.require(Permission.WRITE)
        await self._verify(ctx, task)
        rank = await self._rank_for_one_more(ctx, exclude=task.id)
        # The platform stamps the provenance and the clock, as enqueue does:
        # a caller cannot backdate a task or create one already deleted.
        now = utcnow()
        created = task.model_copy(
            update={
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "deleted_at": None,
                "deleted_by": None,
                "status": TaskStatus.OPEN,
                "rank": rank,
                "version": 1,
                "reminded_at": None,
            }
        )
        rows = (
            versioned_row(ctx, "tasks.task.created", created.id, created.version),
            *self._reminder_rows(ctx, created),
            *await self._slack_rows(ctx, created.id, SlackPostEvent.CREATED),
        )
        if not await self._storage.create_task(ctx.org_id, created, rows):
            # Ids are minted above storage, so the only way to present one twice
            # is a retry, and a retry must not create twice: the insert reported
            # the id and nothing changed, so the row as stored is the answer.
            existing = await self._storage.read_task(ctx.org_id, created.id)
            if existing is None:
                # The id is held, and not in this tenant: refused, never a 500.
                raise TenantMismatch(f"task {created.id} is not in {ctx.org_id}")
            return existing
        # The work rows ride the same commit and the same relay.
        await self._relay.relay_all(ctx.org_id, rows)
        return created

    async def update_task(self, ctx: OpContext, task: Task, expected_version: int) -> Task:
        ctx.require(Permission.WRITE)
        current = await self.get_task(ctx, task.id)  # existence and tenancy, or NotFound
        await self._verify(ctx, task, current)
        # The copy starts from the stored row: the caller's entity supplies the
        # fields a caller may change, and the provenance and the fields the
        # manager owns stay as stored. The version is the caller's expected
        # one plus one, and the write is conditioned on the caller's, never on
        # the one read above, which would always match: a snapshot that missed
        # a write is refused, not merged. The copy carries a dump, so it is
        # validated, never model_copy.
        caller_owned = {*PROVENANCE_FIELDS, *Task.MANAGER_OWNED_FIELDS}
        changes: dict[str, object] = {
            **task.model_dump(exclude=caller_owned),
            "updated_at": utcnow(),
            "updated_by": ctx.user_id,
            "version": expected_version + 1,
        }
        if current.status == TaskStatus.DONE and task.status == TaskStatus.OPEN:
            changes["rank"] = await self._rank_for_one_more(ctx, exclude=task.id)
            changes["archived_at"] = None  # an open task is never archived
        rescheduled = task.due_on != current.due_on
        if rescheduled:
            # A new due date has not been reminded of; the reminder the old
            # one scheduled is stale from this write on, whatever it holds.
            changes["reminded_at"] = None
        updated = Task.model_validate({**current.model_dump(), **changes})
        work: list[OutboxRow] = []
        # A reminder already waiting keeps the morning of the person it read
        # when it ran; one for the new assignee asks again. The two converge
        # on the one write that lands (`fire_reminder`).
        reassigned = updated.assignee_id != current.assignee_id and updated.reminded_at is None
        if rescheduled or reassigned:
            work.extend(self._reminder_rows(ctx, updated))
        if current.status == TaskStatus.OPEN and updated.status == TaskStatus.DONE:
            work.extend(await self._slack_rows(ctx, updated.id, SlackPostEvent.COMPLETED))
        await self._write(ctx, updated, expected_version, "updated", tuple(work))
        return updated

    async def move_task(
        self, ctx: OpContext, task_id: UUID, after_id: UUID | None, expected_version: int
    ) -> Task:
        ctx.require(Permission.WRITE)
        task = await self.get_task(ctx, task_id)
        if task.status != TaskStatus.OPEN:
            raise ValidationFailed("only an open task can be moved")
        if after_id == task_id:
            raise ValidationFailed("a task cannot be placed after itself")
        if after_id is None:
            rank = await self._top_rank(ctx, exclude=task_id)
        else:
            anchor = await self.get_task(ctx, after_id)
            if anchor.status != TaskStatus.OPEN:
                raise ValidationFailed("a task can only be placed after an open task")
            following = await self._rank_past(ctx, (anchor.rank, anchor.id), exclude=task_id)
            rank = rank_after(anchor.rank, following)
        # One row: there is always a rank between two others, so no other
        # task is written and no other task's version moves.
        moved = task.model_copy(
            update={
                "rank": rank,
                "updated_at": utcnow(),
                "updated_by": ctx.user_id,
                "version": expected_version + 1,
            }
        )
        await self._write(ctx, moved, expected_version, "updated")
        return moved

    async def change_tasks(
        self, ctx: OpContext, action: BulkAction, task_ids: Sequence[UUID]
    ) -> BulkOutcome:
        ctx.require(Permission.WRITE)
        named = list(dict.fromkeys(task_ids))  # a task named twice is changed once
        if len(named) > BULK_MAX_IDS:
            raise ValidationFailed(f"a bulk change names at most {BULK_MAX_IDS} tasks")
        bulk = await self._bulk_change(ctx, action)
        for start in range(0, len(named), BULK_BATCH):
            batch = named[start : start + BULK_BATCH]
            found = await self._storage.read_tasks(ctx.org_id, batch)
            pairs = [(task_id, found.get(task_id)) for task_id in batch]
            await self._change_batch(ctx, bulk, pairs)
        return self._finished(ctx, bulk)

    async def change_list(
        self, ctx: OpContext, action: BulkAction, criterion: TaskFilter, status: TaskStatus
    ) -> BulkOutcome:
        ctx.require(Permission.WRITE)
        self._own(ctx, criterion)
        starts_from = TaskStatus.OPEN if action is BulkAction.COMPLETE else TaskStatus.DONE
        if status is not starts_from:
            raise ValidationFailed(f"{action.value} applies to the {starts_from.value} list")
        bulk = await self._bulk_change(ctx, action)
        # Each page is read strictly past the last task of the one before, so
        # a task the change left alone is never read twice, and one it
        # changed has left the list already.
        after: OpenTaskCursor | None = None
        before: TaskCursor | None = None
        while True:
            if status is TaskStatus.OPEN:
                page = await self._storage.read_open_tasks(ctx.org_id, criterion, after, BULK_BATCH)
            else:
                page = await self._storage.read_done_tasks(
                    ctx.org_id, criterion, before, BULK_BATCH
                )
            if not page:
                break
            await self._change_batch(ctx, bulk, [(task.id, task) for task in page])
            if len(page) < BULK_BATCH:
                break
            after = OpenTaskCursor(rank=page[-1].rank, id=page[-1].id)
            before = TaskCursor(updated_at=page[-1].updated_at, id=page[-1].id)
        return self._finished(ctx, bulk)

    async def _bulk_change(self, ctx: OpContext, action: BulkAction) -> _BulkChange:
        """A bulk change about to run: for a reopen, the room the plan leaves,
        read once, as the import reads it once a step."""
        if action is not BulkAction.REOPEN:
            return _BulkChange(action, None, None)
        entitlements = await self._entitlements.get_entitlements(ctx)
        return _BulkChange(action, await self._room(ctx), entitlements.plan)

    async def _change_batch(
        self, ctx: OpContext, bulk: _BulkChange, batch: Sequence[tuple[UUID, Task | None]]
    ) -> None:
        """One batch of a bulk change: decides each task by the rules a single
        edit applies, then writes the ones it changes in one commit, each
        fenced on the version read here."""
        chosen: list[Task] = []
        for task_id, task in batch:
            reason = bulk_skip(task, bulk.action)
            if reason is None and bulk.room is not None and bulk.room <= 0:
                reason = SkipReason.PLAN_LIMIT
                bulk.plan_bound = bulk.plan_bound or self._plan_bound(bulk)
            if reason is not None:
                bulk.skip(task_id, reason)
                continue
            assert task is not None, "bulk_skip names a missing task"
            if bulk.room is not None:
                bulk.room -= 1
            chosen.append(task)
        if not chosen:
            return
        now = utcnow()
        changes: dict[str, object] = {"updated_at": now, "updated_by": ctx.user_id}
        ranks: list[Decimal] = []
        if bulk.action is BulkAction.REOPEN:
            top = await self._storage.read_open_places(
                ctx.org_id, exclude=None, after=None, limit=NEIGHBOURS
            )
            ranks = bulk_ranks(top[0][0] if top else None, len(chosen))
        updates: list[tuple[Task, int, tuple[OutboxRow, ...]]] = []
        for index, task in enumerate(chosen):
            if bulk.action is BulkAction.COMPLETE:
                changed = task.model_copy(
                    update={**changes, "status": TaskStatus.DONE, "version": task.version + 1}
                )
            else:
                # A reopened task is never archived, and goes on top.
                changed = task.model_copy(
                    update={
                        **changes,
                        "status": TaskStatus.OPEN,
                        "rank": ranks[index],
                        "archived_at": None,
                        "version": task.version + 1,
                    }
                )
            rows = (versioned_row(ctx, "tasks.task.updated", task.id, changed.version),)
            updates.append((changed, task.version, rows))
        landed = await self._storage.update_tasks_if_current(ctx.org_id, updates)
        landed_rows: list[OutboxRow] = []
        for (changed, _, rows), hit in zip(updates, landed, strict=True):
            if hit:
                bulk.change(changed.id)
                landed_rows.extend(rows)
            else:
                bulk.skip(changed.id, SkipReason.CHANGED)
                if bulk.room is not None:
                    bulk.room += 1  # the room it held goes to the next batch
        # The batch's events take one run of numbers under one hold of the cursor.
        await self._relay_all(ctx, landed_rows)

    def _plan_bound(self, bulk: _BulkChange) -> PlanBound:
        """The bound a reopen met, as the refusal of one more reopen names it."""
        assert bulk.plan is not None, "a change with room has a plan"
        try:
            # The bound is met, so the org holds it: one more is past it.
            bound = limits_of(bulk.plan).active_tasks or 0
            refuse_past(bulk.plan, Lever.ACTIVE_TASKS, bound, 1)
        except PlanLimitReached as refused:
            return PlanBound(
                lever=refused.lever,
                plan=refused.plan,
                limit=refused.limit,
                suggested_plan=refused.suggested_plan,
            )
        raise AssertionError("a plan with room left has a bound")

    @staticmethod
    def _finished(ctx: OpContext, bulk: _BulkChange) -> BulkOutcome:
        log.info(
            "bulk %s in org %s: %d changed, %d skipped",
            bulk.action.value,
            ctx.org_id,
            bulk.changed_count,
            bulk.skipped_count,
        )
        return bulk.outcome()

    async def delete_task(self, ctx: OpContext, task_id: UUID, expected_version: int) -> Task:
        ctx.require(Permission.WRITE)
        task = await self.get_task(ctx, task_id)
        now = utcnow()
        deleted = task.model_copy(
            update={
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "updated_at": now,
                "updated_by": ctx.user_id,
                "version": expected_version + 1,
            }
        )
        await self._write(ctx, deleted, expected_version, "deleted")
        await self._detach_all(ctx, task_id)
        return deleted

    async def restore_task(self, ctx: OpContext, task_id: UUID, expected_version: int) -> Task:
        ctx.require(Permission.WRITE)
        task = await self.get_task(ctx, task_id)
        if task.archived_at is None:
            raise ValidationFailed(f"task {task_id} is not archived")
        restored = task.model_copy(
            update={
                "archived_at": None,
                "updated_at": utcnow(),
                "updated_by": ctx.user_id,
                "version": expected_version + 1,
            }
        )
        await self._write(ctx, restored, expected_version, "restored")
        return restored

    # The import.

    async def create_import_file(self, ctx: OpContext, file: File) -> File:
        ctx.require(Permission.WRITE)
        upload = file.model_copy(update={"purpose": FilePurpose.TASK_IMPORT, "subject_id": None})
        return await self._media.create_file(ctx, upload)

    async def start_import(self, ctx: OpContext, import_id: UUID, file_id: UUID) -> Orchestration:
        ctx.require(Permission.WRITE)
        file = await self._media.get_file(ctx, file_id)
        if file.purpose is not FilePurpose.TASK_IMPORT:
            raise ValidationFailed(f"file {file_id} was not uploaded to be imported")
        if file.status is not FileStatus.STORED:
            raise ValidationFailed(f"file {file_id} has not been uploaded")
        now = utcnow()
        record = Orchestration(
            id=import_id,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            kind=OrchestrationKind.TASK_IMPORT,
            input=TaskImportInput(file_id=file_id).model_dump(mode="json"),
        )
        return await self._orchestrations.start(ctx, record)

    async def get_import(self, ctx: OpContext, import_id: UUID) -> Orchestration:
        record = await self._orchestrations.get(ctx, import_id)
        if record.kind is not OrchestrationKind.TASK_IMPORT:
            raise NotFound(f"import {import_id} not found")
        return record

    async def get_imports(self, ctx: OpContext, limit: int) -> OrchestrationPage:
        return await self._orchestrations.get_recent(ctx, OrchestrationKind.TASK_IMPORT, limit)

    async def resume_import(self, ctx: OpContext, import_id: UUID) -> Orchestration:
        await self.get_import(ctx, import_id)
        return await self._orchestrations.resume(ctx, import_id)

    async def step_import(self, ctx: OpContext, record: Orchestration) -> Orchestration:
        ctx.require(Permission.WRITE)
        file_id = TaskImportInput.model_validate(dict(record.input)).file_id
        try:
            file = await self._media.get_file(ctx, file_id)
        except NotFound:
            return await self._orchestrations.fail(ctx, record, FailReason.FILE_GONE)
        if file.status is not FileStatus.STORED:
            return await self._orchestrations.fail(ctx, record, FailReason.FILE_GONE)
        data = await self._media.get_content(ctx, file_id)
        try:
            rows = parse_import(data, BOUNDS[FilePurpose.TASK_IMPORT].max_bytes)
        except ImportFileRefused as refused:
            return await self._orchestrations.fail(ctx, record, FailReason(refused.reason))
        batch = rows[record.cursor : record.cursor + IMPORT_BATCH]
        members = await self._members(ctx) if any(r.assignee_email for r in batch) else {}
        room = await self._room(ctx)
        made: list[ImportedTask] = []
        skipped: list[RowError] = []
        cursor = record.cursor
        park: ParkReason | None = None
        for row in batch:
            refusal = import_refusal(row, members)
            if refusal is not None:
                skipped.append(RowError(row=row.number, reason=refusal))
            elif room is not None and len(made) >= room:
                # The guard: this row would take the org past its plan. The
                # record parks with the cursor on it, keeping every task the
                # rows before it made.
                park = ParkReason.PLAN_LIMIT
                break
            else:
                made.append(imported(row, members))
            cursor += 1
        now = utcnow()
        after = advanced(
            record,
            now,
            ctx.user_id,
            cursor=cursor,
            total=len(rows),
            skipped=skipped,
            finished=cursor >= len(rows),
            park=park,
        )
        tasks = await self._imported_tasks(ctx, record, made, now)
        rows_after = step_rows(ctx, after)
        written = await self._storage.create_tasks_in_step(
            ctx.org_id, tasks, Step(record=after, expected_version=record.version), rows_after
        )
        # The step's tasks relay together: their events take one run of
        # numbers under one hold of the tenant's cursor, not one hold each.
        landed_rows = [
            row
            for (_, task_rows), landed in zip(tasks, written, strict=True)
            if landed
            for row in task_rows
        ]
        await self._relay_all(ctx, landed_rows)
        await self._relay_all(ctx, rows_after)
        return after.model_copy(update={"applied": record.applied + sum(written)})

    async def _imported_tasks(
        self, ctx: OpContext, record: Orchestration, made: Sequence[ImportedTask], now: datetime
    ) -> list[tuple[Task, tuple[OutboxRow, ...]]]:
        """The tasks a step makes, with the rows that announce them and
        schedule their reminders. They go to the bottom of the open list, in
        the file's order: an import adds to the list and does not reorder
        what the team placed. A task's id is derived from the import and its
        row, so a step run twice presents the same ids, and it is the task
        of the person who started the import. An imported task
        posts nothing to Slack: a file of a thousand rows is not a thousand
        messages."""
        if not made:
            return []
        last = await self._storage.read_last_place(ctx.org_id)
        ranks = spread(last[0] if last is not None else None, None, len(made))
        tasks: list[tuple[Task, tuple[OutboxRow, ...]]] = []
        for rank, row in zip(ranks, made, strict=True):
            task = Task(
                id=derived_id(record.id, record.created_at, import_row_part(row.number)),
                created_at=now,
                updated_at=now,
                # The person who started the import, whoever runs the step.
                created_by=record.created_by,
                updated_by=record.created_by,
                title=row.title,
                notes=row.notes,
                assignee_id=row.assignee_id,
                due_on=row.due_on,
                rank=rank,
            )
            rows = (
                versioned_row(ctx, "tasks.task.created", task.id, task.version),
                *self._reminder_rows(ctx, task),
            )
            tasks.append((task, rows))
        return tasks

    async def _members(self, ctx: OpContext) -> dict[str, UUID]:
        """The org's members by address, folded: whom a row may assign."""
        members: dict[str, UUID] = {}
        after: UUID | None = None
        while True:
            page = await self._tenancy.get_users(ctx, after, self._options.max_limit)
            for user in page.items:
                if user.deleted_at is None:
                    members[fold_email(user.email)] = user.id
            if not page.has_more or not page.items:
                return members
            after = page.items[-1].id

    async def _room(self, ctx: OpContext) -> int | None:
        """How many more tasks the plan lets the org open now; None when it
        has no bound. Read at each step, so a plan raised between two steps
        lets the next one go further."""
        entitlements = await self._entitlements.get_entitlements(ctx)
        bound = entitlements.limits.active_tasks
        if bound is None:
            return None
        return room_for(
            bound, await self._storage.count_open_tasks(ctx.org_id, self._everyone(ctx))
        )

    # The cleanup.

    async def open_cleanup(self, ctx: OpContext) -> Orchestration | None:
        ctx.require(Permission.WRITE)
        now = utcnow()
        before = archive_cutoff(now, self._options.archive_after)
        if not await self._storage.read_archivable(ctx.org_id, before, 1):
            return None
        period = cleanup_period(now)
        day = datetime.combine(date.fromisoformat(period), time(), tzinfo=UTC)
        record_id = derived_id(ctx.org_id, day, cleanup_part(period))
        try:
            return await self._orchestrations.get(ctx, record_id)
        except NotFound:
            pass
        record = Orchestration(
            id=record_id,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            kind=OrchestrationKind.TASK_CLEANUP,
            input=TaskCleanupInput(
                older_than_days=self._options.archive_after.days, before=before
            ).model_dump(mode="json"),
            period=period,
        )
        return await self._orchestrations.start(ctx, record)

    async def step_cleanup(self, ctx: OpContext, record: Orchestration) -> Orchestration:
        ctx.require(Permission.WRITE)
        before = TaskCleanupInput.model_validate(dict(record.input)).before
        ids = await self._storage.read_archivable(ctx.org_id, before, CLEANUP_BATCH)
        now = utcnow()
        after = advanced(
            record,
            now,
            ctx.user_id,
            cursor=record.cursor + len(ids),
            total=None,
            finished=len(ids) < CLEANUP_BATCH,
        )
        candidates = [
            (task_id, (outbox_row(ctx, "tasks.task.archived", task_id, {}),)) for task_id in ids
        ]
        rows_after = step_rows(ctx, after)
        archived = await self._storage.update_archived_in_step(
            ctx.org_id,
            candidates,
            before,
            now,
            ctx.user_id,
            Step(record=after, expected_version=record.version),
            rows_after,
        )
        landed_rows = [
            row
            for (_, task_rows), landed in zip(candidates, archived, strict=True)
            if landed
            for row in task_rows
        ]
        await self._relay_all(ctx, landed_rows)
        await self._relay_all(ctx, rows_after)
        if any(archived):
            log.info("archived %d done tasks in org %s", sum(archived), ctx.org_id)
        return after.model_copy(update={"applied": record.applied + sum(archived)})

    async def _relay_all(self, ctx: OpContext, rows: Sequence[OutboxRow]) -> None:
        await self._relay.relay_all(ctx.org_id, rows)

    async def count_active_tasks(self, ctx: OpContext) -> int:
        ctx.require(Permission.READ)
        return await self._storage.count_open_tasks(ctx.org_id, self._everyone(ctx))

    async def attach_file(self, ctx: OpContext, task_id: UUID, file: File) -> File:
        ctx.require(Permission.WRITE)
        await self.get_task(ctx, task_id)  # a live task of this tenant, or NotFound
        attached = file.model_copy(
            update={"purpose": FilePurpose.TASK_ATTACHMENT, "subject_id": task_id}
        )
        return await self._media.create_file(ctx, attached)

    async def get_attachments(
        self, ctx: OpContext, task_id: UUID, after: UUID | None, limit: int
    ) -> FilePage:
        await self.get_task(ctx, task_id)
        return await self._media.get_files(ctx, FilePurpose.TASK_ATTACHMENT, task_id, after, limit)

    async def remove_attachment(self, ctx: OpContext, task_id: UUID, file_id: UUID) -> File:
        ctx.require(Permission.WRITE)
        await self.get_task(ctx, task_id)
        file = await self._media.get_file(ctx, file_id)
        if file.purpose is not FilePurpose.TASK_ATTACHMENT or file.subject_id != task_id:
            raise NotFound(f"file {file_id} is not attached to task {task_id}")
        return await self._media.delete_file(ctx, file_id)

    async def _detach_all(self, ctx: OpContext, task_id: UUID) -> None:
        """The attachments go after the task, in writes of their own: the task
        is another namespace's row, so no commit holds both. The task's delete
        has committed by now and is the answer; a failure here is logged and
        counted, never raised, and the files it left stay out of every list,
        since a deleted task lists nothing, while they still count toward the
        tenant's usage."""
        try:
            await self._media.delete_subject_files(ctx, FilePurpose.TASK_ATTACHMENT, task_id)
        except Exception:
            log.exception("the attachments of deleted task %s were left live", task_id)
            OUTCOMES.labels(subsystem="tasks", outcome="detach_failed").inc()

    async def get_due_reminder(self, ctx: OpContext, task_id: UUID) -> DueReminder | None:
        ctx.require(Permission.READ)
        task = await self._storage.read_task(ctx.org_id, task_id)
        if (
            task is None
            or task.deleted_at is not None
            or task.status is not TaskStatus.OPEN
            or task.due_on is None
            or task.reminded_at is not None
        ):
            return None
        zone = await self._tenancy.get_time_zone(ctx, reminder_person(task))
        return DueReminder(due_on=task.due_on, at=reminder_time(task.due_on, zone))

    async def fire_reminder(self, ctx: OpContext, task_id: UUID, due_on: date) -> Task | None:
        ctx.require(Permission.WRITE)
        rows = (
            outbox_row(ctx, "tasks.task.reminded", task_id, {}),
            *await self._slack_rows(ctx, task_id, SlackPostEvent.REMINDED),
        )
        reminded = await self._storage.mark_reminded(ctx.org_id, task_id, due_on, utcnow(), rows)
        if reminded is None:
            return None
        await self._relay.relay_all(ctx.org_id, rows)
        return reminded

    async def respace_ranks(self, ctx: OpContext) -> int:
        ctx.require(Permission.WRITE)
        long = await self._storage.read_long_place(ctx.org_id)
        if long is None:
            return 0
        above = await self._storage.read_open_places_before(ctx.org_id, long, RESPACE_REACH)
        below = await self._storage.read_open_places(
            ctx.org_id, exclude=None, after=long, limit=RESPACE_REACH
        )
        run = respace_run(long, above, below, RESPACE_REACH)
        if run.low is not None and run.high is not None and not run.low < run.high:
            # Every place the reach read shares one rank: nothing fits between.
            log.warning("respace: no room around task %s in org %s", long[1], ctx.org_id)
            return 0
        found = await self._storage.read_tasks(ctx.org_id, [task_id for _, task_id in run.places])
        now = utcnow()
        updates: list[tuple[Task, int, tuple[OutboxRow, ...]]] = []
        for (rank, task_id), new_rank in zip(run.places, respaced(run), strict=True):
            task = found.get(task_id)
            if (
                task is None
                or task.deleted_at is not None
                or task.status != TaskStatus.OPEN
                or task.rank != rank
            ):
                return 0  # the run moved since it was read; the next pass reads it again
            written = task.model_copy(
                update={
                    "rank": new_rank,
                    "updated_at": now,
                    "updated_by": ctx.user_id,
                    "version": task.version + 1,
                }
            )
            rows = (versioned_row(ctx, "tasks.task.updated", task.id, written.version),)
            updates.append((written, task.version, rows))
        try:
            await self._storage.update_tasks(ctx.org_id, updates)
        except PreconditionFailed:
            return 0  # a task of the run was written meanwhile; the next pass reads again
        await self._relay_all(ctx, [row for _, _, rows in updates for row in rows])
        return len(updates)

    async def tenants_with_chores(self, after: UUID | None, limit: int) -> list[UUID]:
        cut = archive_cutoff(utcnow(), self._options.archive_after)
        return await self._storage.read_tenants_with_chores(cut, after, limit)

    async def purge_across_tenants(self, rctx: RequestContext) -> int:
        before = utcnow() - self._options.retention
        purgeable = await self._storage.read_deleted(before, self._options.purge_batch)
        by_tenant: dict[UUID, list[UUID]] = {}
        for org_id, task_id in purgeable:
            by_tenant.setdefault(org_id, []).append(task_id)
        detached: list[UUID] = []
        for org_id, task_ids in by_tenant.items():
            detached.extend(await self._detach(rctx, org_id, task_ids))
        return await self._storage.purge_deleted(before, detached)

    async def _detach(self, rctx: RequestContext, org_id: UUID, task_ids: list[UUID]) -> list[UUID]:
        """The attachments of a tenant's deleted tasks go before their tasks,
        through the media manager, whose sweep erases each object before its
        row. The delete detached them already unless that failed; asking again
        is what retries it, and a task whose files still will not go stays for
        the next pass. A tenant the sweep no longer visits (marked purged) has
        had every file taken with it, so its tasks go as they are."""
        ctx = await self._tenancy.sweep_context(rctx, org_id)
        if ctx is None:
            return task_ids
        detached: list[UUID] = []
        for task_id in task_ids:
            try:
                await self._media.delete_subject_files(ctx, FilePurpose.TASK_ATTACHMENT, task_id)
            except Exception:
                log.exception("the attachments of task %s are kept for the next purge", task_id)
                OUTCOMES.labels(subsystem="tasks", outcome="detach_failed").inc()
                continue
            detached.append(task_id)
        return detached

    async def purge_tenant(self, ctx: OpContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        # The tenant itself is past the retention, so it keeps nothing but
        # its org row. An open or a done task carries no `deleted_at`, so
        # the purge across tenants would leave every one of them behind
        # forever. Its files go by the media sweep, which takes every file
        # of it.
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def _rank_for_one_more(self, ctx: OpContext, exclude: UUID) -> Decimal:
        """The top rank one more open task takes, once the plan's bound on
        active tasks lets it open. Under a bound, the count and the top place
        are one read; with none, the place alone is. It reads the count and
        then writes, so two creates that race at the bound can both land: a
        lever and not a fence, and the next create after them is refused."""
        entitlements = await self._entitlements.get_entitlements(ctx)
        if entitlements.limits.active_tasks is None:
            return await self._top_rank(ctx, exclude)
        count, top = await self._storage.count_open_and_read_places(
            ctx.org_id, self._everyone(ctx), exclude, NEIGHBOURS
        )
        refuse_past(entitlements.plan, Lever.ACTIVE_TASKS, count)
        return top_rank(top)

    @staticmethod
    def _everyone(ctx: OpContext) -> TaskFilter:
        """Every open task of the org: what a plan's bound counts."""
        return TaskFilter(scope=TaskScope.TEAM, user_id=ctx.user_id)

    def _clamp(self, limit: int) -> int:
        """The page size a caller gets, at most `max_limit`."""
        return max(1, min(limit, self._options.max_limit))

    @staticmethod
    def _page(rows: list[Task], limit: int) -> TaskPage:
        """The clamp is on the page; the lookahead is one row past it, which
        storage was asked for and the page never carries. So a list truncated
        by the clamp still says a page follows, and the last page says none."""
        return TaskPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    @staticmethod
    def _own(ctx: OpContext, criterion: TaskFilter) -> None:
        if criterion.user_id != ctx.user_id:
            raise ValidationFailed("a task list is scoped to the caller")

    async def _top_rank(self, ctx: OpContext, exclude: UUID) -> Decimal:
        # The top place is all the rule reads.
        top = await self._storage.read_open_places(
            ctx.org_id, exclude=exclude, after=None, limit=NEIGHBOURS
        )
        return top_rank(top)

    async def _rank_past(self, ctx: OpContext, anchor: Place, exclude: UUID) -> Decimal | None:
        """The smallest open rank strictly past the anchor's, the moved task
        aside; None when the anchor is last. One place is read at a time; a
        place that shares the anchor's rank, which two writers placing at
        once can leave, is stepped over."""
        after = anchor
        while True:
            places = await self._storage.read_open_places(
                ctx.org_id, exclude=exclude, after=after, limit=NEIGHBOURS
            )
            if not places:
                return None
            if places[0][0] > anchor[0]:
                return places[0][0]
            after = places[0]

    async def _verify(self, ctx: OpContext, task: Task, current: Task | None = None) -> None:
        """What a caller may not write. The assignee is checked when the
        assignment changes, never over one already stored: a member removed
        from the org leaves their tasks assigned, and marking such a task done
        or editing its title is an update about something else, which the
        assignment must not refuse. Assigning or reassigning is checked, and
        clearing the assignee is always allowed."""
        if not task.title.strip():
            raise ValidationFailed("a task needs a title")
        assigned = task.assignee_id
        if assigned is not None and (current is None or assigned != current.assignee_id):
            try:
                await self._tenancy.get_user(ctx, assigned)
            except NotFound:
                raise ValidationFailed("the assignee is not a member of this org") from None

    async def _write(
        self,
        ctx: OpContext,
        task: Task,
        expected_version: int,
        action: str,
        work: tuple[OutboxRow, ...] = (),
    ) -> None:
        """The core row and the rows that announce it land in one storage call,
        conditioned on the version the caller read; the relay then appends the
        event and pushes at once, and the sweep catches what a crash left
        behind. Every push is also a record, so a client that missed the push
        replays by seq. A row carries the version the write set and never a
        field's value, so the relay and the stream hold nothing a person's
        erasure has to find; a client that hears of a change reads the task,
        unless it already holds that version. The rows of the work the write
        starts (`work`) land in the same commit."""
        rows = (versioned_row(ctx, f"tasks.task.{action}", task.id, task.version), *work)
        await self._storage.update_task(ctx.org_id, task, expected_version, rows)
        await self._relay.relay_all(ctx.org_id, rows)

    @staticmethod
    def _reminder_rows(ctx: OpContext, task: Task) -> tuple[OutboxRow, ...]:
        """The work row that schedules the reminder of the task's due date,
        or none when it has none. The item waits in the queue until the
        first moment any zone's morning of that date comes, and its handler
        waits the rest from the person's zone (`get_due_reminder`)."""
        if task.due_on is None:
            return ()
        payload = TaskReminderPayload(not_before=earliest_reminder_time(task.due_on))
        kind = work_row_kind(WorkKind.TASK_REMINDER)
        return (outbox_row(ctx, kind, task.id, payload.model_dump(mode="json")),)

    async def _slack_rows(
        self, ctx: OpContext, task_id: UUID, event: SlackPostEvent
    ) -> tuple[OutboxRow, ...]:
        """The work row that posts the event to the org's Slack channel, or
        none when the org has not installed Slack, bound no channel, or its
        installation is broken."""
        installation = await self._slack.get_installation(ctx)
        if (
            installation is None
            or installation.channel_id is None
            or installation.status is not SlackInstallationStatus.OK
        ):
            return ()
        payload = SlackPostPayload(event=event)
        kind = work_row_kind(WorkKind.SLACK_POST)
        return (outbox_row(ctx, kind, task_id, payload.model_dump(mode="json")),)
