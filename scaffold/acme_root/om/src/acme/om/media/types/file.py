from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from acme.om.base import Identifiable, Named, SoftDeletable, Trackable


class FilePurpose(StrEnum):
    """Which domain context a file came from. The purpose decides the bounds an
    upload is held to and what `subject_id` names."""

    TASK_ATTACHMENT = "task_attachment"  # subject_id is the task
    VOICE_DICTATION = "voice_dictation"  # no subject
    TASK_IMPORT = "task_import"  # a CSV of tasks to import; no subject


class FileStatus(StrEnum):
    PENDING = "pending"  # the row exists, the object may not yet
    STORED = "stored"  # the object was found in the store when the upload was confirmed


class File(Identifiable, Named, Trackable, SoftDeletable):
    """A reference to one object in the store, never its bytes. `name` is the
    name the file had on the uploader's machine; `created_by` is who uploaded
    it. The size is the one the upload was bounded by: the store refuses a
    body longer than that, so the stored object is at most this long."""

    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("key", "extension", "status")
    """The object key is derived from the purpose and the id, the extension
    from the name, and the status is the confirm's; a caller sets none."""

    key: str = ""
    extension: str = ""
    content_type: str
    size_bytes: int
    purpose: FilePurpose
    subject_id: UUID | None = None
    status: FileStatus = FileStatus.PENDING
