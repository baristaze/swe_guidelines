import base64
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from acme.om.base import utcnow
from acme.om.exceptions import ValidationFailed
from acme.om.media.types.file import File, FilePurpose
from acme.om.opcontext import OpContext
from acme.om.orchestrations.types.orchestration import Orchestration, TaskImportInput
from acme.om.tasks import TasksManagerInterface
from acme.om.tasks.types.bulk import BulkOutcome
from acme.om.tasks.types.filter import OpenTaskCursor, TaskCursor, TaskFilter
from acme.om.tasks.types.page import TaskPage
from acme.om.tasks.types.task import Task, TaskScope, TaskStatus
from acme.services.api.services.impl.media import file_view
from acme.services.api.services.tasks import TasksServiceInterface
from acme.services.api.types.common import PlanLimitDetail, clamp_limit
from acme.services.api.types.media import AddFileRequest, FilePageView, FileView
from acme.services.api.types.tasks import (
    AddTaskRequest,
    BulkTasksRequest,
    BulkTasksView,
    ImportPageView,
    ImportView,
    MoveTaskRequest,
    RestoreTaskRequest,
    RowErrorView,
    SkippedTaskView,
    StartImportRequest,
    TaskCountView,
    TaskPageView,
    TaskView,
    UpdateTaskRequest,
)


def encode_cursor(status: TaskStatus, task: Task) -> str:
    """Opaque on the wire: the list it belongs to and where its page ended,
    the last task's (rank, id) for the open list and (updated_at, id) for
    the done one."""
    mark = format(task.rank, "f") if status == TaskStatus.OPEN else task.updated_at.isoformat()
    raw = f"{status.value}|{mark}|{task.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(status: TaskStatus, cursor: str) -> OpenTaskCursor | TaskCursor:
    """The cursor of the list asked for; one from the other list, or from
    nowhere, is refused."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        issued_for, mark, task_id = raw.split("|")
        if issued_for != status.value:
            raise ValueError(issued_for)
        if status == TaskStatus.OPEN:
            rank = Decimal(mark)
            if not rank.is_finite():
                raise ValueError(mark)
            return OpenTaskCursor(rank=rank, id=UUID(task_id))
        return TaskCursor(updated_at=datetime.fromisoformat(mark), id=UUID(task_id))
    except ValueError, ArithmeticError:
        raise ValidationFailed("the cursor is not one this list issued") from None


def decode_file_cursor(cursor: str) -> UUID:
    """An attachment page's cursor is the id the previous page ended on."""
    try:
        return UUID(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode())
    except ValueError:
        raise ValidationFailed("the cursor is not one this list issued") from None


def encode_file_cursor(file_id: UUID) -> str:
    return base64.urlsafe_b64encode(str(file_id).encode()).decode().rstrip("=")


def import_view(record: Orchestration) -> ImportView:
    """An import on the wire: the record's counts under the words of an
    import, and its file. What a defect was is the operators' to read."""
    return ImportView(
        id=record.id,
        file_id=TaskImportInput.model_validate(dict(record.input)).file_id,
        status=record.status,
        total=record.total,
        cursor=record.cursor,
        created=record.applied,
        skipped=record.skipped,
        row_errors=[RowErrorView(row=e.row, reason=e.reason) for e in record.row_errors],
        park_reason=record.park_reason,
        fail_reason=record.fail_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
        finished_at=record.finished_at,
        created_by=record.created_by,
    )


def bulk_view(outcome: BulkOutcome) -> BulkTasksView:
    """A bulk change on the wire; the plan's bound as a refusal carries it."""
    bound = outcome.plan_bound
    return BulkTasksView(
        action=outcome.action,
        changed=list(outcome.changed),
        changed_count=outcome.changed_count,
        skipped=[SkippedTaskView(id=s.id, reason=s.reason) for s in outcome.skipped],
        skipped_count=outcome.skipped_count,
        plan_limit=None if bound is None else PlanLimitDetail.model_validate(bound),
    )


def expected_version(named: int | None) -> int:
    """The version a write compares with: the one the request names, in
    `If-Match` or `expected_version`. A request that names none would
    overwrite blind, so it is refused."""
    if named is None:
        raise ValidationFailed("a write names the version it read, in If-Match or expected_version")
    return named


class TasksServiceImpl(TasksServiceInterface):
    def __init__(self, tasks: TasksManagerInterface) -> None:
        self._tasks = tasks

    async def get_tasks(
        self,
        ctx: OpContext,
        status: TaskStatus,
        scope: TaskScope,
        cursor: str | None,
        limit: int,
    ) -> TaskPageView:
        # The public page size is clamped here and again by the manager; the
        # manager's lookahead past it is what makes `has_more` true.
        limit = clamp_limit(limit)
        criterion = TaskFilter(scope=scope, user_id=ctx.user_id)
        page: TaskPage
        if status == TaskStatus.OPEN:
            after = decode_cursor(status, cursor) if cursor else None
            assert after is None or isinstance(after, OpenTaskCursor)
            page = await self._tasks.get_open_tasks(ctx, criterion, after, limit)
        else:
            before = decode_cursor(status, cursor) if cursor else None
            assert before is None or isinstance(before, TaskCursor)
            page = await self._tasks.get_done_tasks(ctx, criterion, before, limit)
        return TaskPageView(
            items=[TaskView.model_validate(t) for t in page.items],
            next_cursor=encode_cursor(status, page.items[-1]) if page.has_more else None,
        )

    async def get_archived_tasks(
        self, ctx: OpContext, scope: TaskScope, cursor: str | None, limit: int
    ) -> TaskPageView:
        # The archived list pages as the done list does, by (updated_at, id).
        before = decode_cursor(TaskStatus.DONE, cursor) if cursor else None
        assert before is None or isinstance(before, TaskCursor)
        criterion = TaskFilter(scope=scope, user_id=ctx.user_id)
        page = await self._tasks.get_archived_tasks(ctx, criterion, before, clamp_limit(limit))
        return TaskPageView(
            items=[TaskView.model_validate(t) for t in page.items],
            next_cursor=encode_cursor(TaskStatus.DONE, page.items[-1]) if page.has_more else None,
        )

    async def count_tasks(
        self, ctx: OpContext, status: TaskStatus, scope: TaskScope
    ) -> TaskCountView:
        criterion = TaskFilter(scope=scope, user_id=ctx.user_id)
        count = await self._tasks.count_tasks(ctx, criterion, status)
        return TaskCountView(status=status, scope=scope, count=count)

    async def change_tasks(self, ctx: OpContext, body: BulkTasksRequest) -> BulkTasksView:
        if (body.ids is None) == (body.all is None):
            raise ValidationFailed("a bulk change names its tasks in ids or in all, not both")
        if body.ids is not None:
            return bulk_view(await self._tasks.change_tasks(ctx, body.action, body.ids))
        assert body.all is not None
        criterion = TaskFilter(scope=body.all.scope, user_id=ctx.user_id)
        outcome = await self._tasks.change_list(ctx, body.action, criterion, body.all.status)
        return bulk_view(outcome)

    async def get_task(self, ctx: OpContext, task_id: UUID) -> TaskView:
        return TaskView.model_validate(await self._tasks.get_task(ctx, task_id))

    async def restore_task(
        self, ctx: OpContext, task_id: UUID, body: RestoreTaskRequest
    ) -> TaskView:
        expected = expected_version(body.expected_version)
        return TaskView.model_validate(await self._tasks.restore_task(ctx, task_id, expected))

    async def create_import_file(
        self, ctx: OpContext, body: AddFileRequest, file_id: UUID
    ) -> FileView:
        now = utcnow()
        file = File(
            id=file_id,
            name=body.name,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            purpose=FilePurpose.TASK_IMPORT,
        )
        return file_view(await self._tasks.create_import_file(ctx, file))

    async def start_import(
        self, ctx: OpContext, body: StartImportRequest, import_id: UUID
    ) -> ImportView:
        return import_view(await self._tasks.start_import(ctx, import_id, body.file_id))

    async def get_imports(self, ctx: OpContext, limit: int) -> ImportPageView:
        page = await self._tasks.get_imports(ctx, clamp_limit(limit))
        return ImportPageView(items=[import_view(r) for r in page.items])

    async def get_import(self, ctx: OpContext, import_id: UUID) -> ImportView:
        return import_view(await self._tasks.get_import(ctx, import_id))

    async def resume_import(self, ctx: OpContext, import_id: UUID) -> ImportView:
        return import_view(await self._tasks.resume_import(ctx, import_id))

    async def create_task(self, ctx: OpContext, body: AddTaskRequest, task_id: UUID) -> TaskView:
        now = utcnow()
        task = Task(
            id=task_id,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            title=body.title,
            notes=body.notes,
            assignee_id=body.assignee_id,
            due_on=body.due_on,
        )
        return TaskView.model_validate(await self._tasks.create_task(ctx, task))

    async def update_task(
        self, ctx: OpContext, task_id: UUID, body: UpdateTaskRequest, if_match: int | None
    ) -> TaskView:
        expected = expected_version(if_match)
        current = await self._tasks.get_task(ctx, task_id)
        # Only the assignee and the due date can be cleared; a null title,
        # notes, or status is ignored. The version is the caller's, never the stored one, and it
        # travels beside the entity: the manager conditions the write on it.
        changes = {
            name: value
            for name, value in body.model_dump(exclude_unset=True).items()
            if value is not None or name in ("assignee_id", "due_on")
        }
        # model_copy does not validate; a copy that carries caller input does.
        changed = Task.model_validate({**current.model_dump(), **changes})
        return TaskView.model_validate(await self._tasks.update_task(ctx, changed, expected))

    async def move_task(self, ctx: OpContext, task_id: UUID, body: MoveTaskRequest) -> TaskView:
        expected = expected_version(body.expected_version)
        moved = await self._tasks.move_task(ctx, task_id, body.after_id, expected)
        return TaskView.model_validate(moved)

    async def delete_task(self, ctx: OpContext, task_id: UUID, if_match: int | None) -> TaskView:
        expected = expected_version(if_match)
        return TaskView.model_validate(await self._tasks.delete_task(ctx, task_id, expected))

    async def attach_file(
        self, ctx: OpContext, task_id: UUID, body: AddFileRequest, file_id: UUID
    ) -> FileView:
        now = utcnow()
        file = File(
            id=file_id,
            name=body.name,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            purpose=FilePurpose.TASK_ATTACHMENT,
            subject_id=task_id,
        )
        return file_view(await self._tasks.attach_file(ctx, task_id, file))

    async def get_attachments(
        self, ctx: OpContext, task_id: UUID, cursor: str | None, limit: int
    ) -> FilePageView:
        after = decode_file_cursor(cursor) if cursor else None
        page = await self._tasks.get_attachments(ctx, task_id, after, clamp_limit(limit))
        return FilePageView(
            items=[file_view(f) for f in page.items],
            next_cursor=encode_file_cursor(page.items[-1].id) if page.has_more else None,
        )

    async def remove_attachment(self, ctx: OpContext, task_id: UUID, file_id: UUID) -> FileView:
        return file_view(await self._tasks.remove_attachment(ctx, task_id, file_id))
