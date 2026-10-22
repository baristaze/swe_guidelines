from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.om.media.rules import MAX_NAME_LENGTH
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
    """One page of the org's stored files, oldest first. `next_cursor` fetches
    the next page and is null on the last one."""

    items: list[FileView]
    next_cursor: str | None


class StartUploadRequest(RequestBody):
    """An upload to start: the file's name as the uploader had it, its type,
    and its size in bytes. The type must be one the purpose accepts and match
    the name's extension; the size is the most the store will take."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)


class IssuedPartView(View):
    """One part to send: the `size_bytes` bytes of the file from `offset`, as
    the body of a PUT to `url`, straight to the store. The URL is signed for
    that length, and the store refuses a body of any other."""

    number: int
    offset: int
    size_bytes: int
    url: str


class IssuedUploadView(View):
    """The parts of the upload the store does not hold yet, at most a window
    of them, each good until `expires_at`. `remaining` counts every part
    still missing, these included: send these and ask again while it is
    above zero, then confirm. Asked again after an upload was cut off, it
    signs only the parts still missing, under the same `upload_id`."""

    upload_id: str
    parts: list[IssuedPartView]
    remaining: int
    expires_at: datetime


class IssuedDownloadView(View):
    """A link to the file's bytes, straight from the store, that works until
    `expires_at`."""

    url: str
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
