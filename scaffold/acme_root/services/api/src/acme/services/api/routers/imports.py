"""Import routes: the CSV file's upload started, the import started naming
it, the org's imports, one import, and a parked import resumed. The file's
bytes and its confirm are the media routes'. An import runs in the worker; a
caller reads it here, or hears of it on the realtime channel. Mounted before
the task routes, so `/tasks/imports` is never read as a task's id."""

from uuid import UUID

from fastapi import APIRouter, Response

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.resolve import TasksService
from acme.services.api.types.media import AddFileRequest, FileView
from acme.services.api.types.tasks import ImportPageView, ImportView, StartImportRequest

router = APIRouter(prefix="/tasks/imports", tags=["tasks"])

IMPORTS_LIMIT_DEFAULT = 10


@router.post("/files", response_model=FileView, status_code=201)
async def create_import_file(
    ctx: Ctx, tasks: TasksService, body: AddFileRequest, idem: Idem
) -> Response:
    """Starts the upload of a CSV file to import (`text/csv`, a `.csv` name, at
    most 1 MB). Then `POST /v1/media/files/{id}/upload`, the form, and
    `POST /v1/media/files/{id}/confirm`."""
    return await idem.run(
        201, lambda attempt: tasks.create_import_file(ctx, body, attempt.target_id)
    )


@router.post("", response_model=ImportView, status_code=202)
async def start_import(
    ctx: Ctx, tasks: TasksService, body: StartImportRequest, idem: Idem
) -> Response:
    """Accepted: the import runs in the background, a hundred rows a step."""
    return await idem.run(202, lambda attempt: tasks.start_import(ctx, body, attempt.target_id))


@router.get("", response_model=ImportPageView)
async def list_imports(
    ctx: Ctx, tasks: TasksService, limit: int = IMPORTS_LIMIT_DEFAULT
) -> ImportPageView:
    return await tasks.get_imports(ctx, limit)


@router.get("/{import_id}", response_model=ImportView)
async def get_import(ctx: Ctx, tasks: TasksService, import_id: UUID) -> ImportView:
    return await tasks.get_import(ctx, import_id)


@router.post("/{import_id}/resume", response_model=ImportView)
async def resume_import(ctx: Ctx, tasks: TasksService, import_id: UUID) -> ImportView:
    """A parked import runs again from the row it stopped at, and parks again
    at once if the plan still has no room. A running one is answered as it
    is; a finished one is refused (422)."""
    return await tasks.resume_import(ctx, import_id)
