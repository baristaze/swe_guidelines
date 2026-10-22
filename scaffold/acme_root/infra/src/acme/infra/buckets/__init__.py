"""Buckets: large blobs. Every call takes org_id and the impl prefixes keys
with it, so one tenant's blobs cannot be read or listed by another.

A blob that crosses between a client and a bucket goes by a presigned URL,
whatever its size: a download is a presigned GET, and an upload is a
multipart upload whose parts the client sends straight to the store, each by
a URL signed for its own number and length. No service holds the bytes of a
transfer. A service's own write, such as a worker's export, is a `put`."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from acme.infra.base import InfraModel
from acme.infra.exceptions import BlobNotFound, InvalidBucketKey, UploadNotFound, UploadRefused

__all__ = [
    "MAX_PARTS",
    "MIB",
    "MIN_PART_BYTES",
    "PART_BYTES",
    "BlobNotFound",
    "Buckets",
    "BucketsInterface",
    "InvalidBucketKey",
    "UploadNotFound",
    "UploadPart",
    "UploadRefused",
    "held_to_plan",
    "object_key",
    "part_sizes",
]


class Buckets(StrEnum):
    USER_FILE_UPLOADS = "user-file-uploads"
    EXPORTS = "exports"


def object_key(org_id: UUID, key: str) -> str:
    """The storage key: the tenant first, then the caller's path."""
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise InvalidBucketKey(f"bucket key {key!r} is not a relative path")
    return f"{org_id}/{key}"


MIB = 1024 * 1024
PART_BYTES = 8 * MIB
"""The size of every part but the last, while the parts stay within
`MAX_PARTS`: small enough that a part lost to a dropped connection costs
seconds, large enough that a file at a purpose's ceiling is a few parts."""
MIN_PART_BYTES = 5 * MIB
"""The store refuses to complete an upload whose part, other than the last,
is smaller than this."""
MAX_PARTS = 10_000
"""The most parts one upload has."""


def part_sizes(size_bytes: int) -> tuple[int, ...]:
    """The parts an upload of `size_bytes` is cut into, in order: each
    `PART_BYTES`, or the whole MiB above it that keeps the count within
    `MAX_PARTS`, and the last what remains. The plan is a function of the
    size alone, so the signer, the client, and the complete agree on it."""
    if size_bytes <= 0:
        raise ValueError(f"an upload is bounded by a positive size, not {size_bytes}")
    part = max(PART_BYTES, -(-size_bytes // MAX_PARTS // MIB) * MIB)
    full, rest = divmod(size_bytes, part)
    return (part,) * full + ((rest,) if rest else ())


class UploadPart(InfraModel):
    """One part the store holds for an upload: its number, from 1, and its
    length."""

    number: int
    size_bytes: int


def held_to_plan(held: list[UploadPart], size_bytes: int) -> str | None:
    """Why the parts the store holds do not make the upload of `size_bytes`,
    or None when they do: every part of the plan, by number and length, and
    no other, so they sum to the declared size. What a complete is refused
    for."""
    plan = part_sizes(size_bytes)
    have = {part.number: part.size_bytes for part in held}
    missing = [n for n, size in enumerate(plan, 1) if have.get(n) != size]
    extra = sorted(n for n in have if n > len(plan))
    if not missing and not extra:
        return None
    total = sum(have.values())
    return (
        f"the parts held sum to {total} bytes in {len(have)} parts, not {size_bytes} "
        f"in {len(plan)}: missing or short {missing[:10]}, beyond the plan {extra[:10]}"
    )


class BucketsInterface(ABC):
    """A call a request makes to the store carries the request's deadline,
    and ends by then, unreachable (`acme.infra.deadline`); a worker's
    carries none. Presigning makes no call. Every impl signs, the local one
    included, so no presign answers None and no caller moves a client's
    bytes itself."""

    @abstractmethod
    async def put(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        data: bytes,
        content_type: str,
        *,
        deadline: datetime | None = None,
    ) -> None: ...

    @abstractmethod
    async def get(
        self, org_id: UUID, bucket: Buckets, key: str, *, deadline: datetime | None = None
    ) -> bytes: ...

    @abstractmethod
    async def exists(
        self, org_id: UUID, bucket: Buckets, key: str, *, deadline: datetime | None = None
    ) -> bool: ...

    @abstractmethod
    async def list(
        self, org_id: UUID, bucket: Buckets, prefix: str, limit: int, after: str | None = None
    ) -> list[str]:
        """The tenant's keys under `prefix`, in lexical order, at most `limit`
        of them, strictly after `after` when one is given. The next page
        starts after the last key returned. The caller picks the bound."""
        ...

    @abstractmethod
    async def delete(self, org_id: UUID, bucket: Buckets, key: str) -> None: ...

    @abstractmethod
    async def presign_get(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        ttl: timedelta,
        *,
        content_type: str | None = None,
        content_disposition: str | None = None,
    ) -> str:
        """A link to one object for `ttl`. `content_type` and
        `content_disposition`, when given, are signed into the link as the
        headers the store answers with: a preview is `inline` under the
        stored type, a download an `attachment` under the file's name."""
        ...

    @abstractmethod
    async def start_upload(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        content_type: str,
        *,
        deadline: datetime | None = None,
    ) -> str:
        """A multipart upload of one object, under the type it lands with;
        answers the upload id every later call names. Nothing lands at the
        key until the complete."""
        ...

    @abstractmethod
    async def presign_part(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        upload_id: str,
        number: int,
        size_bytes: int,
        ttl: timedelta,
    ) -> str:
        """A PUT of part `number` for `ttl`, signed for its length: the
        store refuses a body of any other length, so a client cannot send
        more than the part it was handed. Sending a part again replaces it."""
        ...

    @abstractmethod
    async def list_parts(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        upload_id: str,
        limit: int,
        *,
        deadline: datetime | None = None,
    ) -> list[UploadPart]:
        """The parts the store holds for the upload, by number, at most
        `limit` of them, and never more than `MAX_PARTS`: what a resume
        signs around. `UploadNotFound` when the store holds no such upload:
        completed, aborted, or never started."""
        ...

    @abstractmethod
    async def complete_upload(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        upload_id: str,
        size_bytes: int,
        *,
        deadline: datetime | None = None,
    ) -> None:
        """The object lands at the key, made of the parts the store holds.
        `UploadRefused`, with nothing landing, unless they are the plan of
        `size_bytes` (`held_to_plan`); `UploadNotFound` when the store holds
        no such upload."""
        ...

    @abstractmethod
    async def abort_upload(self, org_id: UUID, bucket: Buckets, key: str, upload_id: str) -> None:
        """The upload and every part of it go; one the store no longer holds
        is done already."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None:
        """Opened by the infra root at boot. An impl that holds no connection
        of its own returns None."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Closed by the infra root at shutdown, in reverse order of start."""
        ...
