import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from acme.infra.buckets import (
    BlobNotFound,
    Buckets,
    BucketsInterface,
    InvalidBucketKey,
    PresignedPost,
    UploadRefused,
    object_key,
)


class BucketsLocalImpl(BucketsInterface):
    """A filesystem impl with the same layout as the object store. It cannot
    presign, so both presign calls return None and the caller proxies. It
    still holds a presigned upload to its bounds: until the form would have
    expired, a `put` of that key must carry the content type it was presigned
    with and at most `max_bytes`, as the store would refuse otherwise. And it
    refuses a key that is absolute or climbs out of its root, as the cloud's
    keys cannot."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._bounds: dict[tuple[Buckets, str], tuple[str, int, float]] = {}

    def _path(self, org_id: UUID, bucket: Buckets, key: str) -> Path:
        path = self._root / bucket.value / object_key(org_id, key)
        if not path.resolve().is_relative_to((self._root / bucket.value).resolve()):
            raise InvalidBucketKey(f"bucket key {key!r} leaves the bucket")
        return path

    def _hold_to_bounds(self, bucket: Buckets, key: str, data: bytes, content_type: str) -> None:
        bound = self._bounds.get((bucket, key))
        if bound is None:
            return
        expected_type, max_bytes, expires = bound
        if time.monotonic() >= expires:
            del self._bounds[(bucket, key)]
            return
        if content_type != expected_type:
            raise UploadRefused(f"upload of {key!r} is {content_type}, not {expected_type}")
        if len(data) > max_bytes:
            raise UploadRefused(f"upload of {key!r} is {len(data)} bytes, over {max_bytes}")

    async def put(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        data: bytes,
        content_type: str,
        *,
        deadline: datetime | None = None,
    ) -> None:
        path = self._path(org_id, bucket, key)
        self._hold_to_bounds(bucket, object_key(org_id, key), data, content_type)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.with_name(path.name + ".content-type").write_text(content_type)

        await asyncio.to_thread(write)

    async def get(
        self, org_id: UUID, bucket: Buckets, key: str, *, deadline: datetime | None = None
    ) -> bytes:
        path = self._path(org_id, bucket, key)
        if not await asyncio.to_thread(path.is_file):
            raise BlobNotFound(f"{bucket.value}/{key}")
        return await asyncio.to_thread(path.read_bytes)

    async def exists(
        self, org_id: UUID, bucket: Buckets, key: str, *, deadline: datetime | None = None
    ) -> bool:
        return await asyncio.to_thread(self._path(org_id, bucket, key).is_file)

    async def list(
        self, org_id: UUID, bucket: Buckets, prefix: str, limit: int, after: str | None = None
    ) -> list[str]:
        base = self._root / bucket.value / str(org_id)

        def walk() -> list[str]:
            if not base.is_dir():
                return []
            keys = [
                p.relative_to(base).as_posix()
                for p in base.rglob("*")
                if p.is_file() and not p.name.endswith(".content-type")
            ]
            # Code-point order, which is the byte order of the object store's
            # UTF-8 keys; a filesystem has no listing that stops early, so the
            # walk is whole and the answer is bounded.
            listed = sorted(
                k for k in keys if k.startswith(prefix) and (after is None or k > after)
            )
            return listed[:limit]

        return await asyncio.to_thread(walk)

    async def delete(self, org_id: UUID, bucket: Buckets, key: str) -> None:
        path = self._path(org_id, bucket, key)

        def remove() -> None:
            path.unlink(missing_ok=True)
            path.with_name(path.name + ".content-type").unlink(missing_ok=True)

        await asyncio.to_thread(remove)

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
        return None

    async def presign_post(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        content_type: str,
        max_bytes: int,
        ttl: timedelta,
    ) -> PresignedPost | None:
        if max_bytes <= 0:
            raise ValueError(f"an upload is bounded by a positive size, not {max_bytes}")
        self._path(org_id, bucket, key)  # the key is refused here, as the store would
        now = time.monotonic()
        # A form expires, so its bound does too; the expired ones go as new ones come.
        self._bounds = {k: b for k, b in self._bounds.items() if b[2] > now}
        self._bounds[(bucket, object_key(org_id, key))] = (
            content_type,
            max_bytes,
            now + ttl.total_seconds(),
        )
        return None

    def describe(self) -> str:
        return f"buckets=local({self._root})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
