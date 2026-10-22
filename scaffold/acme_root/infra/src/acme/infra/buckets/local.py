import asyncio
import hashlib
import hmac
import json
import re
import secrets
import shutil
import time
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode
from uuid import UUID

from acme.infra.buckets import (
    MAX_PARTS,
    BlobNotFound,
    Buckets,
    BucketsInterface,
    InvalidBucketKey,
    UploadNotFound,
    UploadPart,
    UploadRefused,
    held_to_plan,
    object_key,
)

LOCAL_STORE_URL = "http://buckets.test"
"""The host the local impl's URLs name. Nothing resolves it: a test mounts
the impl's `store` there, as the transport its client reaches the store by."""
UPLOADS = ".uploads"
"""The folder of a bucket that holds the parts of its uploads under way. A
tenant's objects live under its id, so no object key reaches it."""
UPLOAD_ID = re.compile(r"[0-9a-f]{32}")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class BucketsLocalImpl(BucketsInterface):
    """A filesystem store with the object store's layout and its contract.
    It signs its own URLs, with a key of its own that no other process
    holds: a download is a signed GET, and an upload a multipart upload
    whose parts are signed PUTs, each for its own number and length. `store`
    is the store side, an ASGI app that answers those URLs and nothing
    else: it refuses a URL whose signature or expiry fails, and a part of
    any other length than it was signed for, with nothing landing. A test
    mounts it as the store's host (`httpx.ASGITransport(app=buckets.store)`),
    and no route of a service serves it, so no service holds the bytes. The
    local stack runs MinIO, which the S3 impl reaches.

    It refuses a key that is absolute or climbs out of its root, as the
    cloud's keys cannot. A bucket's lifecycle has no counterpart here: a
    test's root goes with the test."""

    def __init__(self, root: Path, *, url: str = LOCAL_STORE_URL) -> None:
        self._root = root
        self._url = url.rstrip("/")
        self._key = secrets.token_bytes(32)

    def _bucket_root(self, bucket: Buckets) -> Path:
        return self._root / bucket.value

    def _object_path(self, bucket: Buckets, stored_key: str) -> Path:
        path = self._bucket_root(bucket) / stored_key
        if not path.resolve().is_relative_to(self._bucket_root(bucket).resolve()):
            raise InvalidBucketKey(f"bucket key {stored_key!r} leaves the bucket")
        return path

    def _path(self, org_id: UUID, bucket: Buckets, key: str) -> Path:
        return self._object_path(bucket, object_key(org_id, key))

    def _upload(self, bucket: Buckets, stored_key: str, upload_id: str) -> Path:
        """The folder of an upload under way of this key; `UploadNotFound`
        when there is none, or it is another key's."""
        if not UPLOAD_ID.fullmatch(upload_id):
            raise UploadNotFound(f"no upload {upload_id!r}")
        folder = self._bucket_root(bucket) / UPLOADS / upload_id
        meta = folder / "upload.json"
        if not meta.is_file() or json.loads(meta.read_text())["key"] != stored_key:
            raise UploadNotFound(f"no upload {upload_id!r} of {stored_key!r}")
        return folder

    def _sign(self, method: str, path: str, params: dict[str, str]) -> str:
        message = "\n".join((method, path, urlencode(sorted(params.items()))))
        return hmac.new(self._key, message.encode(), hashlib.sha256).hexdigest()

    def _signed_url(self, method: str, path: str, params: dict[str, str], ttl: timedelta) -> str:
        params = {**params, "expires": str(int(time.time() + ttl.total_seconds()))}
        signature = self._sign(method, path, params)
        return f"{self._url}{quote(path)}?{urlencode({**params, 'signature': signature})}"

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
        base = self._bucket_root(bucket) / str(org_id)

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
    ) -> str:
        self._path(org_id, bucket, key)  # the key is refused here, as the store would
        params: dict[str, str] = {}
        if content_type is not None:
            params["response-content-type"] = content_type
        if content_disposition is not None:
            params["response-content-disposition"] = content_disposition
        return self._signed_url("GET", f"/{bucket.value}/{object_key(org_id, key)}", params, ttl)

    async def start_upload(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        content_type: str,
        *,
        deadline: datetime | None = None,
    ) -> str:
        self._path(org_id, bucket, key)
        upload_id = secrets.token_hex(16)
        folder = self._bucket_root(bucket) / UPLOADS / upload_id
        meta = {"key": object_key(org_id, key), "content_type": content_type}

        def begin() -> None:
            folder.mkdir(parents=True)
            (folder / "upload.json").write_text(json.dumps(meta))

        await asyncio.to_thread(begin)
        return upload_id

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
        if not 1 <= number <= MAX_PARTS or size_bytes <= 0:
            raise ValueError(f"part {number} of {size_bytes} bytes is outside an upload")
        self._path(org_id, bucket, key)
        params = {"uploadId": upload_id, "partNumber": str(number), "length": str(size_bytes)}
        return self._signed_url("PUT", f"/{bucket.value}/{object_key(org_id, key)}", params, ttl)

    @staticmethod
    def _held(folder: Path, limit: int = MAX_PARTS) -> list[UploadPart]:
        parts = [
            UploadPart(number=int(p.stem), size_bytes=p.stat().st_size)
            for p in folder.glob("*.part")
        ]
        return sorted(parts, key=lambda p: p.number)[: min(limit, MAX_PARTS)]

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
        stored_key = object_key(org_id, key)
        return await asyncio.to_thread(
            lambda: self._held(self._upload(bucket, stored_key, upload_id), limit)
        )

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
        path = self._path(org_id, bucket, key)
        stored_key = object_key(org_id, key)

        def complete() -> None:
            folder = self._upload(bucket, stored_key, upload_id)
            held = self._held(folder)
            refusal = held_to_plan(held, size_bytes)
            if refusal is not None:
                raise UploadRefused(f"upload of {key!r} is not complete: {refusal}")
            meta = json.loads((folder / "upload.json").read_text())
            path.parent.mkdir(parents=True, exist_ok=True)
            # The object appears whole or not at all: written aside, then moved.
            landing = path.with_name(f".{path.name}.{upload_id}")
            with landing.open("wb") as out:
                for part in held:
                    with (folder / f"{part.number}.part").open("rb") as source:
                        shutil.copyfileobj(source, out)
            landing.replace(path)
            path.with_name(path.name + ".content-type").write_text(meta["content_type"])
            shutil.rmtree(folder)

        await asyncio.to_thread(complete)

    async def abort_upload(self, org_id: UUID, bucket: Buckets, key: str, upload_id: str) -> None:
        stored_key = object_key(org_id, key)

        def abort() -> None:
            try:
                shutil.rmtree(self._upload(bucket, stored_key, upload_id))
            except UploadNotFound:
                return

        await asyncio.to_thread(abort)

    async def store(self, scope: Scope, receive: Receive, send: Send) -> None:
        """The store side of the local impl's URLs, as an ASGI app over HTTP:
        a signed GET answers the object, and a signed PUT of a part writes
        the part. Anything else, a signature that does not match, or an
        expired URL is refused, and a refused part leaves nothing behind."""
        if scope["type"] != "http":
            return
        method, path = scope["method"], scope["path"]
        params = dict(parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True))
        signature = params.pop("signature", "")
        if not hmac.compare_digest(signature, self._sign(method, path, params)):
            return await answer(send, 403, b"the signature does not match")
        if int(params.get("expires", "0")) < time.time():
            return await answer(send, 403, b"the URL has expired")
        bucket_name, _, stored_key = path.lstrip("/").partition("/")
        try:
            bucket = Buckets(bucket_name)
            target = self._object_path(bucket, stored_key)
        except ValueError, InvalidBucketKey:
            return await answer(send, 404, b"no such key")
        if method == "GET" and "uploadId" not in params:
            return await self._send_object(send, target, params)
        if method == "PUT" and "uploadId" in params:
            return await self._receive_part(scope, receive, send, bucket, stored_key, params)
        return await answer(send, 405, b"not a signed operation")

    async def _send_object(self, send: Send, target: Path, params: dict[str, str]) -> None:
        if not await asyncio.to_thread(target.is_file):
            return await answer(send, 404, b"no such key")
        data = await asyncio.to_thread(target.read_bytes)
        sidecar = target.with_name(target.name + ".content-type")
        stored = await asyncio.to_thread(sidecar.read_text) if sidecar.is_file() else ""
        headers = [(b"content-type", params.get("response-content-type", stored).encode())]
        if "response-content-disposition" in params:
            disposition = params["response-content-disposition"]
            headers.append((b"content-disposition", disposition.encode()))
        await answer(send, 200, data, headers)

    async def _receive_part(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        bucket: Buckets,
        stored_key: str,
        params: dict[str, str],
    ) -> None:
        """The part is held to the length signed into its URL: a declared
        length of any other size is refused before a byte is read, as the
        store refuses a signed header that does not match, and a body that
        runs past it is refused as it runs."""
        length = int(params["length"])
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared != str(length).encode():
            return await answer(send, 403, b"the length is not the one the part was signed for")
        try:
            folder = await asyncio.to_thread(self._upload, bucket, stored_key, params["uploadId"])
        except UploadNotFound:
            return await answer(send, 404, b"no such upload")
        chunks: list[bytes] = []
        received = 0
        while True:
            message = await receive()
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > length:
                return await answer(send, 400, b"the part runs past the length it was signed for")
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        if received != length:
            return await answer(send, 400, b"the part is shorter than its length")
        body = b"".join(chunks)
        part = folder / f"{int(params['partNumber'])}.part"

        def write() -> None:
            landing = part.with_suffix(f".{secrets.token_hex(4)}")
            landing.write_bytes(body)
            landing.replace(part)

        await asyncio.to_thread(write)
        etag = f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"'
        await answer(send, 200, b"", [(b"etag", etag.encode())])

    def describe(self) -> str:
        return f"buckets=local({self._root})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


async def answer(
    send: Send, status: int, body: bytes, headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    """One whole response of the local store."""
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [*(headers or []), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
