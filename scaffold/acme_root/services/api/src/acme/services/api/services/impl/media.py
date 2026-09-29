from uuid import UUID

from acme.om.base import utcnow
from acme.om.media import MediaManagerInterface
from acme.om.media.types.file import File, FilePurpose
from acme.om.opcontext import OpContext
from acme.services.api.services.impl.tenancy import decode_cursor, encode_cursor
from acme.services.api.services.media import MediaServiceInterface
from acme.services.api.types.common import clamp_limit
from acme.services.api.types.media import (
    FileContentResponse,
    FilePageView,
    FileView,
    IssuedDownloadView,
    IssuedUploadView,
    PurposeUsageView,
    StartUploadRequest,
    StorageUsageView,
    UploadFieldView,
)


def file_view(file: File) -> FileView:
    return FileView.model_validate(file)


class MediaServiceImpl(MediaServiceInterface):
    def __init__(self, media: MediaManagerInterface) -> None:
        self._media = media

    async def start_upload(
        self, ctx: OpContext, body: StartUploadRequest, file_id: UUID
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
            purpose=FilePurpose.UPLOAD,
        )
        return file_view(await self._media.create_file(ctx, file))

    async def get_files(self, ctx: OpContext, cursor: str | None, limit: int) -> FilePageView:
        after = decode_cursor("files", cursor) if cursor else None
        page = await self._media.get_files(ctx, FilePurpose.UPLOAD, None, after, clamp_limit(limit))
        return FilePageView(
            items=[file_view(f) for f in page.items],
            next_cursor=encode_cursor("files", page.items[-1].id) if page.has_more else None,
        )

    async def get_file(self, ctx: OpContext, file_id: UUID) -> FileView:
        return file_view(await self._media.get_file(ctx, file_id))

    async def issue_upload(self, ctx: OpContext, file_id: UUID) -> IssuedUploadView:
        form = await self._media.issue_upload(ctx, file_id)
        return IssuedUploadView(
            url=form.url,
            fields=[UploadFieldView(name=name, value=value) for name, value in form.fields],
            expires_at=form.expires_at,
        )

    async def put_content(self, ctx: OpContext, file_id: UUID, data: bytes) -> FileView:
        return file_view(await self._media.put_content(ctx, file_id, data))

    async def confirm_file(self, ctx: OpContext, file_id: UUID) -> FileView:
        return file_view(await self._media.confirm_file(ctx, file_id))

    async def issue_download(
        self, ctx: OpContext, file_id: UUID, inline: bool
    ) -> IssuedDownloadView:
        link = await self._media.issue_download(ctx, file_id, inline=inline)
        return IssuedDownloadView(url=link.url, expires_at=link.expires_at)

    async def get_content(self, ctx: OpContext, file_id: UUID, inline: bool) -> FileContentResponse:
        file = await self._media.get_file(ctx, file_id)
        data = await self._media.get_content(ctx, file_id)
        return FileContentResponse(file.name, file.content_type, data, inline=inline)

    async def delete_file(self, ctx: OpContext, file_id: UUID) -> FileView:
        return file_view(await self._media.delete_file(ctx, file_id))

    async def get_usage(self, ctx: OpContext) -> StorageUsageView:
        usage = await self._media.get_usage(ctx)
        return StorageUsageView(
            purposes=[PurposeUsageView.model_validate(p) for p in usage.purposes],
            total_count=usage.total_count,
            total_size_bytes=usage.total_size_bytes,
            pending_size_bytes=usage.pending_size_bytes,
        )
