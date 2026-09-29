"""The media swimlane's pure rules: what an upload may be, where its object
lives, which rows the sweep erases, and how usage is summed. The memory
storage calls these; the Postgres storage spells the purge and the usage in
SQL once more, and the contract cases hold the two spellings together."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.media.types.usage import PurposeUsage, StorageUsage

MAX_NAME_LENGTH = 255
MAX_EXTENSION_LENGTH = 16


@dataclass(frozen=True)
class UploadBounds:
    """What one purpose accepts: a size ceiling, and the content types, each
    with the extensions a file of that type may carry."""

    max_bytes: int
    types: Mapping[str, frozenset[str]]


_IMAGES = {
    "image/png": frozenset({"png"}),
    "image/jpeg": frozenset({"jpg", "jpeg"}),
    "image/gif": frozenset({"gif"}),
    "image/webp": frozenset({"webp"}),
}
_DOCUMENTS = {
    "application/pdf": frozenset({"pdf"}),
    "text/plain": frozenset({"txt", "log"}),
    "text/csv": frozenset({"csv"}),
    "text/markdown": frozenset({"md", "markdown"}),
    "application/json": frozenset({"json"}),
    "application/zip": frozenset({"zip"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": frozenset({"docx"}),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": frozenset({"xlsx"}),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": frozenset(
        {"pptx"}
    ),
}
_AUDIO = {
    "audio/webm": frozenset({"webm"}),
    "audio/ogg": frozenset({"ogg", "oga"}),
    "audio/mpeg": frozenset({"mp3"}),
    "audio/mp4": frozenset({"m4a", "mp4"}),
    "audio/wav": frozenset({"wav"}),
}
_VIDEO = {
    "video/mp4": frozenset({"mp4", "m4v"}),
    "video/webm": frozenset({"webm"}),
    "video/quicktime": frozenset({"mov"}),
}

BOUNDS: Mapping[FilePurpose, UploadBounds] = {
    # No type a browser renders as a page (HTML, SVG): a file is data, and the
    # store's origin is not the portal's, but nothing needs one either.
    FilePurpose.TASK_ATTACHMENT: UploadBounds(
        max_bytes=100 * 1024 * 1024, types={**_IMAGES, **_DOCUMENTS, **_AUDIO, **_VIDEO}
    ),
    FilePurpose.VOICE_DICTATION: UploadBounds(max_bytes=10 * 1024 * 1024, types=_AUDIO),
    # Five thousand rows of a title, a note, a date, and an address fit in
    # well under a megabyte.
    FilePurpose.TASK_IMPORT: UploadBounds(
        max_bytes=1024 * 1024, types={"text/csv": frozenset({"csv"})}
    ),
}
"""The bounds of every purpose. The numbers are illustrative; the shape is
that each purpose names its own ceiling and its own types, and a type
outside the list is refused before any form is signed."""

SUBJECT_REQUIRED: frozenset[FilePurpose] = frozenset({FilePurpose.TASK_ATTACHMENT})
"""The purposes whose file belongs to a subject (a task attachment's task)."""


def extension_of(name: str) -> str:
    """The lower-cased suffix after the last dot, or empty when there is none."""
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem or not suffix:
        return ""
    return suffix.lower()


def upload_refusal(
    purpose: FilePurpose,
    name: str,
    content_type: str,
    size_bytes: int,
    subject_id: UUID | None,
) -> str | None:
    """Why an upload may not start, or None when it may. The name is a name,
    not a path; the type is one the purpose lists, and the extension one that
    type carries; the size is positive and under the purpose's ceiling."""
    bounds = BOUNDS[purpose]
    if not name.strip() or len(name) > MAX_NAME_LENGTH:
        return f"a file name is 1 to {MAX_NAME_LENGTH} characters"
    if any(c in name for c in "/\\") or any(ord(c) < 32 for c in name):
        return "a file name is a name, not a path"
    extension = extension_of(name)
    if len(extension) > MAX_EXTENSION_LENGTH:
        return "the file's extension is too long"
    allowed = bounds.types.get(content_type)
    if allowed is None:
        return f"a {purpose.value} cannot be of type {content_type}"
    if extension not in allowed:
        return f"a file of type {content_type} ends in .{' or .'.join(sorted(allowed))}"
    if size_bytes <= 0:
        return "a file has at least one byte"
    if size_bytes > bounds.max_bytes:
        return f"a {purpose.value} is at most {bounds.max_bytes} bytes"
    if (purpose in SUBJECT_REQUIRED) != (subject_id is not None):
        return f"a {purpose.value} {'names' if purpose in SUBJECT_REQUIRED else 'has no'} subject"
    return None


def object_key(purpose: FilePurpose, file_id: UUID) -> str:
    """Where a file's object lives in the uploads bucket, under the tenant's
    prefix the bucket impl adds. Every key starts with `media/`, which is the
    prefix the deployed task role is granted and no more."""
    return f"media/{purpose.value}/{file_id}"


def content_disposition(name: str, inline: bool) -> str:
    """How the store or the API presents a file's bytes: `inline`, for a
    preview the page shows, or an `attachment` saved under the file's own
    name, spelled for any character a name holds (RFC 6266)."""
    if inline:
        return "inline"
    return f"attachment; filename*=UTF-8''{quote(name)}"


def is_purgeable(file: File, deleted_before: datetime, pending_before: datetime) -> bool:
    """A row the sweep erases, with its object: deleted before the retention
    cut, or an upload started before the pending cut and never confirmed."""
    if file.deleted_at is not None:
        return file.deleted_at < deleted_before
    return file.status is FileStatus.PENDING and file.created_at < pending_before


def usage_from_totals(
    totals: Mapping[tuple[FilePurpose, FileStatus], tuple[int, int]],
) -> StorageUsage:
    """Usage from (count, bytes) per purpose and status, every purpose present
    in the enum's order. Both storages end here: the memory one after summing
    the rows (`usage_of`), the Postgres one after its `GROUP BY`."""
    return StorageUsage(
        purposes=tuple(
            PurposeUsage(
                purpose=purpose,
                count=totals.get((purpose, FileStatus.STORED), (0, 0))[0],
                size_bytes=totals.get((purpose, FileStatus.STORED), (0, 0))[1],
                pending_count=totals.get((purpose, FileStatus.PENDING), (0, 0))[0],
                pending_size_bytes=totals.get((purpose, FileStatus.PENDING), (0, 0))[1],
            )
            for purpose in FilePurpose
        )
    )


def usage_of(files: Iterable[File]) -> StorageUsage:
    """The live files summed per purpose and status; a deleted file counts for
    nothing."""
    totals: dict[tuple[FilePurpose, FileStatus], tuple[int, int]] = {}
    for file in files:
        if file.deleted_at is not None:
            continue
        count, size = totals.get((file.purpose, file.status), (0, 0))
        totals[(file.purpose, file.status)] = (count + 1, size + file.size_bytes)
    return usage_from_totals(totals)
