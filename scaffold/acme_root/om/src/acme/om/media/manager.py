"""The media swimlane: files a tenant keeps in the object store, as references.
It is horizontal: another namespace composes it for its own files, naming
the purpose and the subject, and it knows nothing of what the subject is."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.media.types.file import File, FilePurpose
from acme.om.media.types.page import FilePage
from acme.om.media.types.transfer import DownloadLink, UploadForm
from acme.om.media.types.usage import StorageUsage
from acme.om.opcontext import OpContext


class MediaManagerInterface(ABC):
    @abstractmethod
    async def create_file(self, ctx: OpContext, file: File) -> File:
        """Starts an upload: the row lands pending, bounded by the purpose's
        size and types (`media.rules.upload_refusal`). The key, the extension,
        the status, and the provenance are the manager's. No bytes move yet."""
        ...

    @abstractmethod
    async def issue_upload(self, ctx: OpContext, file_id: UUID) -> UploadForm:
        """A form the uploader posts straight to the store, signed for this
        file's key, content type, and size, with a short expiry. Only the
        person who started the upload gets one, and only while it is pending.
        A form with no URL means the store cannot presign: the bytes go
        through `put_content` instead."""
        ...

    @abstractmethod
    async def put_content(self, ctx: OpContext, file_id: UUID, data: bytes) -> File:
        """The bytes of a pending upload moved through the API, for a store that
        cannot presign; held to the same bounds the form carries."""
        ...

    @abstractmethod
    async def confirm_file(self, ctx: OpContext, file_id: UUID) -> File:
        """The upload is done: the object is looked for in the store, and the
        row turns stored only when it is there. Confirming a stored file
        answers it as it is."""
        ...

    @abstractmethod
    async def get_file(self, ctx: OpContext, file_id: UUID) -> File:
        """A live file of the tenant; a deleted one, or one another tenant
        holds, is `NotFound` as one that never existed is."""
        ...

    @abstractmethod
    async def get_files(
        self,
        ctx: OpContext,
        purpose: FilePurpose,
        subject_id: UUID | None,
        after: UUID | None,
        limit: int,
    ) -> FilePage:
        """One page of the stored files of a subject, oldest first, strictly
        after `after`; `limit` is clamped."""
        ...

    @abstractmethod
    async def issue_download(
        self, ctx: OpContext, file_id: UUID, *, inline: bool = False
    ) -> DownloadLink:
        """A short-lived link to a stored file's object, answered under its
        stored type: `inline` for a preview the page shows, else an
        attachment saved under the file's name. A link with no URL means the
        store cannot presign: the bytes come from `get_content`."""
        ...

    @abstractmethod
    async def get_content(self, ctx: OpContext, file_id: UUID) -> bytes:
        """A stored file's bytes, through the API, for a store that cannot
        presign."""
        ...

    @abstractmethod
    async def delete_file(self, ctx: OpContext, file_id: UUID) -> File:
        """The soft delete. The row stops counting at once; the object is
        removed by the sweep."""
        ...

    @abstractmethod
    async def delete_subject_files(
        self, ctx: OpContext, purpose: FilePurpose, subject_id: UUID
    ) -> int:
        """Soft-deletes every live file of a subject, pending or stored; returns
        how many. What a namespace calls when the subject itself goes."""
        ...

    @abstractmethod
    async def get_usage(self, ctx: OpContext) -> StorageUsage:
        """What the tenant keeps, counted from the rows, per purpose: the
        number a limit on storage is held against."""
        ...

    @abstractmethod
    async def purge_across_tenants(self) -> int:
        """Platform-internal: the sweep, across tenants, once a pass, one batch
        at a time: removes the objects of files deleted past the retention
        and of uploads abandoned past the pending window, whatever their
        tenant, then their rows. Returns how many rows went. It takes no
        context, because it runs for no tenant and no principal: an object is
        found by its tenant and its key."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: OpContext) -> int:
        """The sweep, for one tenant past its retention, one batch at a time:
        every file's object, live or not, then its row. Returns how many rows
        went. Any other tenant returns 0 and reads nothing: its files past
        their cut go across tenants."""
        ...
