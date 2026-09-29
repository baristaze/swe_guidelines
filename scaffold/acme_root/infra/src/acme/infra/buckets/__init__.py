"""Buckets: large blobs. Every call takes org_id and the impl prefixes keys
with it, so one tenant's blobs cannot be read or listed by another."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from acme.infra.base import InfraModel
from acme.infra.exceptions import BlobNotFound, InvalidBucketKey, UploadRefused

__all__ = [
    "BlobNotFound",
    "Buckets",
    "BucketsInterface",
    "InvalidBucketKey",
    "PresignedPost",
    "UploadRefused",
    "object_key",
]


class Buckets(StrEnum):
    USER_FILE_UPLOADS = "user-file-uploads"
    EXPORTS = "exports"


def object_key(org_id: UUID, key: str) -> str:
    """The storage key: the tenant first, then the caller's path."""
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise InvalidBucketKey(f"bucket key {key!r} is not a relative path")
    return f"{org_id}/{key}"


class PresignedPost(InfraModel):
    """One bounded upload a browser makes straight to the store: a form POST
    of `fields`, in order, then the file, to `url`. The signed policy in the
    fields carries the content type and a `content-length-range` from zero to
    `max_bytes`; the store refuses a body larger than that, or of another
    type, so a form handed to a browser cannot fill the bucket. A presigned
    PUT cannot bound a body's size, which is why an upload is a POST."""

    url: str
    fields: tuple[tuple[str, str], ...]


class BucketsInterface(ABC):
    """A put, get, or exists a request makes carries the request's deadline,
    and ends by then, unreachable (`acme.infra.deadline`); a worker's
    carries none. Presigning makes no call."""

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
    ) -> str | None:
        """A link to one object for `ttl`, or None where the store cannot sign
        (the local impl). `content_type` and `content_disposition`, when
        given, are signed into the link as the headers the store answers
        with: a preview is `inline` under the stored type, a download an
        `attachment` under the file's name."""
        ...

    @abstractmethod
    async def presign_post(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        content_type: str,
        max_bytes: int,
        ttl: timedelta,
    ) -> PresignedPost | None:
        """An upload bounded by `content_type` and `max_bytes`, or None where
        the store cannot presign (the local impl). On None the caller moves
        the bytes through `put` itself and holds them to the same bounds."""
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
