"""File routes: the form an upload posts to the store, the bytes through the
API when the store cannot presign, the confirm, the file, the link a download
follows, and the org's storage usage. Each function is one call into the
media service. A file is started by the namespace it belongs to (a task's
attachment by the task's route), never here."""

from uuid import UUID

from fastapi import APIRouter

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.body import BoundedBody
from acme.services.api.gateway.resolve import MediaService
from acme.services.api.types.media import (
    FileContentResponse,
    FileView,
    IssuedDownloadView,
    IssuedUploadView,
    StorageUsageView,
)

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/usage", response_model=StorageUsageView)
async def get_usage(ctx: Ctx, media: MediaService) -> StorageUsageView:
    return await media.get_usage(ctx)


@router.get("/files/{file_id}", response_model=FileView)
async def get_file(ctx: Ctx, media: MediaService, file_id: UUID) -> FileView:
    return await media.get_file(ctx, file_id)


@router.post("/files/{file_id}/upload", response_model=IssuedUploadView)
async def issue_upload(ctx: Ctx, media: MediaService, file_id: UUID) -> IssuedUploadView:
    return await media.issue_upload(ctx, file_id)


@router.put(
    "/files/{file_id}/content",
    response_model=FileView,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def put_content(ctx: Ctx, media: MediaService, file_id: UUID, body: BoundedBody) -> FileView:
    return await media.put_content(ctx, file_id, body)


@router.post("/files/{file_id}/confirm", response_model=FileView)
async def confirm_file(ctx: Ctx, media: MediaService, file_id: UUID) -> FileView:
    return await media.confirm_file(ctx, file_id)


@router.get("/files/{file_id}/download", response_model=IssuedDownloadView)
async def issue_download(
    ctx: Ctx, media: MediaService, file_id: UUID, inline: bool = False
) -> IssuedDownloadView:
    """`inline=true` is a preview the page shows; otherwise the link saves the
    file under its own name."""
    return await media.issue_download(ctx, file_id, inline)


@router.get(
    "/files/{file_id}/content",
    response_class=FileContentResponse,
    status_code=200,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def get_content(
    ctx: Ctx, media: MediaService, file_id: UUID, inline: bool = False
) -> FileContentResponse:
    return await media.get_content(ctx, file_id, inline)
