"""Task routes: the open, done, and archived lists, a list's count, get,
create, a partial update, move, restore, delete, the change of many tasks at
once, and a task's attachments. Each function is one call into the tasks
service; the creating ones, and the bulk change, run under the idempotency
record. An attachment's
bytes, its confirm, and its download are the media routes', by the file's
id."""

from uuid import UUID

from fastapi import APIRouter, Response

from acme.om.tasks.types.task import TaskScope, TaskStatus
from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.precondition import IfMatch
from acme.services.api.gateway.resolve import TasksService
from acme.services.api.types.common import LIMIT_DEFAULT
from acme.services.api.types.media import AddFileRequest, FilePageView, FileView
from acme.services.api.types.tasks import (
    AddTaskRequest,
    BulkTasksRequest,
    BulkTasksView,
    MoveTaskRequest,
    RestoreTaskRequest,
    TaskCountView,
    TaskPageView,
    TaskView,
    UpdateTaskRequest,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=TaskPageView)
async def list_tasks(
    ctx: Ctx,
    tasks: TasksService,
    status: TaskStatus = TaskStatus.OPEN,
    scope: TaskScope = TaskScope.TEAM,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> TaskPageView:
    return await tasks.get_tasks(ctx, status, scope, cursor, limit)


@router.get("/archived", response_model=TaskPageView)
async def list_archived_tasks(
    ctx: Ctx,
    tasks: TasksService,
    scope: TaskScope = TaskScope.TEAM,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> TaskPageView:
    """The done tasks the daily cleanup archived, newest first. An archived
    task leaves the done list; it is still read by its id and restored."""
    return await tasks.get_archived_tasks(ctx, scope, cursor, limit)


@router.get("/count", response_model=TaskCountView)
async def count_tasks(
    ctx: Ctx,
    tasks: TasksService,
    status: TaskStatus = TaskStatus.OPEN,
    scope: TaskScope = TaskScope.TEAM,
) -> TaskCountView:
    """How many tasks one list shows: the number a "Mark all" asks about."""
    return await tasks.count_tasks(ctx, status, scope)


@router.post("/bulk", response_model=BulkTasksView)
async def change_tasks(
    ctx: Ctx, tasks: TasksService, body: BulkTasksRequest, idem: Idem
) -> Response:
    # A write of many rows is recorded like a create: a retry of "Mark all"
    # answers what the first call did, and never reaches tasks that joined
    # the list since.
    return await idem.run(200, lambda _attempt: tasks.change_tasks(ctx, body))


@router.get("/{task_id}", response_model=TaskView)
async def get_task(ctx: Ctx, tasks: TasksService, task_id: UUID) -> TaskView:
    return await tasks.get_task(ctx, task_id)


@router.post("", response_model=TaskView, status_code=201)
async def create_task(ctx: Ctx, tasks: TasksService, body: AddTaskRequest, idem: Idem) -> Response:
    return await idem.run(201, lambda attempt: tasks.create_task(ctx, body, attempt.target_id))


@router.patch("/{task_id}", response_model=TaskView)
async def update_task(
    ctx: Ctx, tasks: TasksService, task_id: UUID, body: UpdateTaskRequest, if_match: IfMatch
) -> TaskView:
    return await tasks.update_task(ctx, task_id, body, if_match)


@router.post("/{task_id}/move", response_model=TaskView)
async def move_task(
    ctx: Ctx, tasks: TasksService, task_id: UUID, body: MoveTaskRequest
) -> TaskView:
    return await tasks.move_task(ctx, task_id, body)


@router.post("/{task_id}/restore", response_model=TaskView)
async def restore_task(
    ctx: Ctx, tasks: TasksService, task_id: UUID, body: RestoreTaskRequest
) -> TaskView:
    return await tasks.restore_task(ctx, task_id, body)


@router.delete("/{task_id}", response_model=TaskView)
async def delete_task(ctx: Ctx, tasks: TasksService, task_id: UUID, if_match: IfMatch) -> TaskView:
    # A DELETE has no body, so the version rides the `If-Match` header, the
    # same precondition the update carries, refused with the same 412.
    return await tasks.delete_task(ctx, task_id, if_match)


@router.get("/{task_id}/attachments", response_model=FilePageView)
async def list_attachments(
    ctx: Ctx,
    tasks: TasksService,
    task_id: UUID,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> FilePageView:
    return await tasks.get_attachments(ctx, task_id, cursor, limit)


@router.post("/{task_id}/attachments", response_model=FileView, status_code=201)
async def attach_file(
    ctx: Ctx, tasks: TasksService, task_id: UUID, body: AddFileRequest, idem: Idem
) -> Response:
    return await idem.run(
        201, lambda attempt: tasks.attach_file(ctx, task_id, body, attempt.target_id)
    )


@router.delete("/{task_id}/attachments/{file_id}", response_model=FileView)
async def remove_attachment(
    ctx: Ctx, tasks: TasksService, task_id: UUID, file_id: UUID
) -> FileView:
    return await tasks.remove_attachment(ctx, task_id, file_id)
