from datetime import datetime
from uuid import UUID

from pydantic import Field
from starlette.responses import Response

from acme.om.media.rules import MAX_NAME_LENGTH, content_disposition
from acme.om.media.types.file import FilePurpose, FileStatus
from acme.services.api.types.common import RequestBody, View


class FileView(View):
    """A file the tenant keeps: its name, type, and size, never its bytes and
    never where they live. `status` is `pending` until the upload is
    confirmed, then `stored`."""

    id: UUID
    name: str
    extension: str
    content_type: str
    size_bytes: int
    purpose: FilePurpose
    subject_id: UUID | None
    status: FileStatus
    created_at: datetime
    created_by: UUID
    deleted_at: datetime | None


class FilePageView(View):
    """One page of files, oldest first. `next_cursor` fetches the next page and
    is null on the last one."""

    items: list[FileView]
    next_cursor: str | None


class AddFileRequest(RequestBody):
    """An upload to start: the file's name as the uploader had it, its type,
    and its size in bytes. The type must be one the purpose accepts and match
    the name's extension; the size is the most the store will take."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)


class UploadFieldView(View):
    name: str
    value: str


class IssuedUploadView(View):
    """A form to post the file with, straight to the store: every field, in
    order, then the file as the last field, named `file`. The signed policy
    holds the post to the file's type and at most its size, until
    `expires_at`. A null `url` means the store cannot take a post: the bytes
    go to `PUT /v1/media/files/{id}/content` instead. Then the upload is
    confirmed."""

    url: str | None
    fields: list[UploadFieldView]
    expires_at: datetime


class IssuedDownloadView(View):
    """A link to the file's bytes that works until `expires_at`. A null `url`
    means the store cannot sign one: the bytes come from
    `GET /v1/media/files/{id}/content`."""

    url: str | None
    expires_at: datetime


class PurposeUsageView(View):
    purpose: FilePurpose
    count: int
    size_bytes: int
    pending_count: int
    pending_size_bytes: int


class StorageUsageView(View):
    """What the org keeps in the store, counted from its files: the stored ones
    per purpose and in total, and the uploads started and not yet confirmed.
    A removed file stops counting at once."""

    purposes: list[PurposeUsageView]
    total_count: int
    total_size_bytes: int
    pending_size_bytes: int


class FileContentResponse(Response):
    """A file's bytes on the wire: its own type, shown inline for a preview or
    saved under its own name. `nosniff`, so a browser never reads the bytes
    as another type."""

    media_type = "application/octet-stream"

    def __init__(self, name: str, content_type: str, data: bytes, *, inline: bool = False) -> None:
        super().__init__(
            content=data,
            media_type=content_type,
            headers={
                "Content-Disposition": content_disposition(name, inline),
                "X-Content-Type-Options": "nosniff",
            },
        )
