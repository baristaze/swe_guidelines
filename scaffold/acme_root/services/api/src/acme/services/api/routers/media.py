"""Media: the org's files, as references to objects in the store.

Starting an upload, under an Idempotency-Key; the org's stored files a page
at a time; a file, and deleting it; the parts of an upload the store does not
hold yet, each signed for a PUT straight to the store; the confirm once the
parts are there; the link a download follows straight from the store; and
the org's storage used (`/v1/media/files`, `/v1/media/files/{file_id}`,
`.../upload`, `.../confirm`, `.../download`, `/v1/media/usage`). No route
takes or serves a file's bytes. Each function is one call into the media
service; the start runs under the idempotency record. A file started here is
of purpose `upload`: a file the org keeps, about no subject."""

from uuid import UUID

from fastapi import APIRouter, Response

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.resolve import MediaService
from acme.services.api.types.common import LIMIT_DEFAULT, ErrorResponse
from acme.services.api.types.media import (
    FilePageView,
    FileView,
    IssuedDownloadView,
    IssuedUploadView,
    StartUploadRequest,
    StorageUsageView,
)

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/usage", response_model=StorageUsageView)
async def get_usage(ctx: Ctx, media: MediaService) -> StorageUsageView:
    return await media.get_usage(ctx)


@router.post(
    "/files",
    response_model=FileView,
    status_code=201,
    responses={
        403: {"model": ErrorResponse, "description": "feature_off: `media-uploads` is off"},
    },
)
async def start_upload(
    ctx: Ctx, media: MediaService, body: StartUploadRequest, idem: Idem
) -> Response:
    """A pending file, with the store's multipart upload of it started and
    nothing in the store yet: the parts and the confirm follow by its id."""
    return await idem.run(201, lambda attempt: media.start_upload(ctx, body, attempt.target_id))


@router.get("/files", response_model=FilePageView)
async def list_files(
    ctx: Ctx, media: MediaService, cursor: str | None = None, limit: int = LIMIT_DEFAULT
) -> FilePageView:
    """The org's stored files, oldest first; a pending upload is not listed."""
    return await media.get_files(ctx, cursor, limit)


@router.get("/files/{file_id}", response_model=FileView)
async def get_file(ctx: Ctx, media: MediaService, file_id: UUID) -> FileView:
    return await media.get_file(ctx, file_id)


@router.post("/files/{file_id}/upload", response_model=IssuedUploadView)
async def issue_upload(ctx: Ctx, media: MediaService, file_id: UUID) -> IssuedUploadView:
    """The parts the store does not hold yet. Asked again after an upload
    was cut off, it signs only the parts still missing, under the same
    upload."""
    return await media.issue_upload(ctx, file_id)


@router.post(
    "/files/{file_id}/confirm",
    response_model=FileView,
    responses={
        422: {
            "model": ErrorResponse,
            "description": "upload_refused: the parts the store holds do not sum to the size",
        },
    },
)
async def confirm_file(ctx: Ctx, media: MediaService, file_id: UUID) -> FileView:
    return await media.confirm_file(ctx, file_id)


@router.get("/files/{file_id}/download", response_model=IssuedDownloadView)
async def issue_download(
    ctx: Ctx, media: MediaService, file_id: UUID, inline: bool = False
) -> IssuedDownloadView:
    """`inline=true` is a preview the page shows; otherwise the link saves the
    file under its own name."""
    return await media.issue_download(ctx, file_id, inline)


@router.delete("/files/{file_id}", response_model=FileView)
async def delete_file(ctx: Ctx, media: MediaService, file_id: UUID) -> FileView:
    return await media.delete_file(ctx, file_id)
