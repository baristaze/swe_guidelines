"""The media service: what the wire can do with files, in views."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.opcontext import OpContext
from acme.services.api.types.media import (
    FileContentResponse,
    FilePageView,
    FileView,
    IssuedDownloadView,
    IssuedUploadView,
    StartUploadRequest,
    StorageUsageView,
)


class MediaServiceInterface(ABC):
    @abstractmethod
    async def start_upload(
        self, ctx: OpContext, body: StartUploadRequest, file_id: UUID
    ) -> FileView:
        """A pending file of purpose `upload` under `file_id`, the id the
        idempotency record minted, so a retry finds the row it made."""
        ...

    @abstractmethod
    async def get_files(self, ctx: OpContext, cursor: str | None, limit: int) -> FilePageView:
        """One page of the org's stored files of purpose `upload`; `cursor` is
        the previous page's `next_cursor`."""
        ...

    @abstractmethod
    async def get_file(self, ctx: OpContext, file_id: UUID) -> FileView: ...

    @abstractmethod
    async def issue_upload(self, ctx: OpContext, file_id: UUID) -> IssuedUploadView: ...

    @abstractmethod
    async def put_content(self, ctx: OpContext, file_id: UUID, data: bytes) -> FileView: ...

    @abstractmethod
    async def confirm_file(self, ctx: OpContext, file_id: UUID) -> FileView: ...

    @abstractmethod
    async def issue_download(
        self, ctx: OpContext, file_id: UUID, inline: bool
    ) -> IssuedDownloadView: ...

    @abstractmethod
    async def get_content(self, ctx: OpContext, file_id: UUID, inline: bool) -> FileContentResponse:
        """The bytes through the API, for a store that cannot sign a link."""
        ...

    @abstractmethod
    async def delete_file(self, ctx: OpContext, file_id: UUID) -> FileView:
        """The soft delete: the file stops counting at once, and the sweep
        removes its bytes."""
        ...

    @abstractmethod
    async def get_usage(self, ctx: OpContext) -> StorageUsageView: ...
