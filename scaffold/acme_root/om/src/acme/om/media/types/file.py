from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from acme.om.base import Identifiable, Named, SoftDeletable, Trackable


class FilePurpose(StrEnum):
    """Which context a file came from. The purpose decides the bounds an
    upload is held to and what `subject_id` names. A product adds a purpose
    per kind of file it keeps, with its bounds (`media.rules.BOUNDS`)."""

    UPLOAD = "upload"  # a file a member keeps in the org; no subject


class FileStatus(StrEnum):
    PENDING = "pending"  # the row exists, the object may not yet
    STORED = "stored"  # the store completed the upload from its parts when it was confirmed


class File(Identifiable, Named, Trackable, SoftDeletable):
    """A reference to one object in the store, never its bytes. `name` is the
    name the file had on the uploader's machine; `created_by` is who uploaded
    it. The size is the one the upload was declared at: each part is signed
    for its own length and the complete is refused unless the parts sum to
    it, so the stored object is exactly this long. `upload_id` names the
    store's multipart upload, from the start to the complete."""

    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("key", "extension", "status", "upload_id")
    """The object key is derived from the purpose and the id, the extension
    from the name, the status is the confirm's, and the upload id is the
    store's; a caller sets none."""

    key: str = ""
    extension: str = ""
    content_type: str
    size_bytes: int
    purpose: FilePurpose
    subject_id: UUID | None = None
    status: FileStatus = FileStatus.PENDING
    upload_id: str | None = None
