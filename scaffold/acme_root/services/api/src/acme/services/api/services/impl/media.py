from uuid import UUID

from acme.om.media import MediaManagerInterface
from acme.om.media.types.file import File
from acme.om.opcontext import OpContext
from acme.services.api.services.media import MediaServiceInterface
from acme.services.api.types.media import (
    FileContentResponse,
    FileView,
    IssuedDownloadView,
    IssuedUploadView,
    PurposeUsageView,
    StorageUsageView,
    UploadFieldView,
)


def file_view(file: File) -> FileView:
    return FileView.model_validate(file)


class MediaServiceImpl(MediaServiceInterface):
    def __init__(self, media: MediaManagerInterface) -> None:
        self._media = media

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

    async def get_usage(self, ctx: OpContext) -> StorageUsageView:
        usage = await self._media.get_usage(ctx)
        return StorageUsageView(
            purposes=[PurposeUsageView.model_validate(p) for p in usage.purposes],
            total_count=usage.total_count,
            total_size_bytes=usage.total_size_bytes,
            pending_size_bytes=usage.pending_size_bytes,
        )
