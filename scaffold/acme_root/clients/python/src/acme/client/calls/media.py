"""The media namespace's calls: the org's files, as references to objects in
the store. A file's bytes never cross the API: an upload's parts go straight
to the store, each by a URL signed for it, and a download comes straight from
the store by a signed link."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from acme.client.transport import LIMIT_MAX, ApiError, Transport, trust_store
from acme.client.types.media import (
    FilePageView,
    FileView,
    IssuedDownloadView,
    IssuedPartView,
    IssuedUploadView,
    StorageUsageView,
)

CHUNK_BYTES = 1024 * 1024
"""What a part is read and sent in: an upload holds one chunk of its file in
memory, whatever the file's size."""


class MediaCalls(Transport):
    """Uploads, files, downloads, and the org's storage."""

    async def start_upload(
        self,
        name: str,
        content_type: str,
        size_bytes: int,
        *,
        idempotency_key: str | None = None,
    ) -> FileView:
        """A pending file the org keeps, its multipart upload started, and
        nothing in the store yet: `upload` sends the parts and confirms it.
        Always under an idempotency key, so a retry lands one file."""
        body = {"name": name, "content_type": content_type, "size_bytes": size_bytes}
        started = await self.request(
            "POST",
            "/v1/media/files",
            json=body,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return FileView.model_validate(started)

    async def files(self, *, cursor: str | None = None, limit: int = LIMIT_MAX) -> FilePageView:
        """One page of the org's stored files, oldest first; a pending upload
        is not listed."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return FilePageView.model_validate(
            await self.request("GET", "/v1/media/files", params=params)
        )

    async def file(self, file_id: UUID) -> FileView:
        return FileView.model_validate(await self.request("GET", f"/v1/media/files/{file_id}"))

    async def issue_upload(self, file_id: UUID) -> IssuedUploadView:
        """The parts the store does not hold yet, each with the URL signed for
        it; asked again, only the parts still missing."""
        issued = await self.request("POST", f"/v1/media/files/{file_id}/upload")
        return IssuedUploadView.model_validate(issued)

    async def confirm_file(self, file_id: UUID) -> FileView:
        return FileView.model_validate(
            await self.request("POST", f"/v1/media/files/{file_id}/confirm")
        )

    async def issue_download(self, file_id: UUID) -> IssuedDownloadView:
        link = await self.request("GET", f"/v1/media/files/{file_id}/download")
        return IssuedDownloadView.model_validate(link)

    async def delete_file(self, file_id: UUID) -> FileView:
        removed = await self.request("DELETE", f"/v1/media/files/{file_id}")
        return FileView.model_validate(removed)

    async def storage_usage(self) -> StorageUsageView:
        return StorageUsageView.model_validate(await self.request("GET", "/v1/media/usage"))

    def _store(self) -> httpx.AsyncClient:
        """The object store, reached apart from the API: no bearer, no app
        headers, no retry. A signed URL is the credential, good for one part
        or one file for a few minutes."""
        return httpx.AsyncClient(
            transport=self.store_transport,
            timeout=self.timeout,
            verify=True if self.store_transport is not None else trust_store(),
        )

    async def upload(self, started: FileView, source: Path | bytes) -> FileView:
        """The parts of a started upload, then its confirm. Each part goes
        straight to the store by the URL the API signed for it, read from
        `source` a chunk at a time, so a file of any size holds one chunk in
        memory. An upload cut off resumes by calling this again for the same
        file: the API signs only the parts the store does not hold. Each part
        is sent once a call, so a store that keeps losing one ends the call
        rather than sending it again and again."""
        if isinstance(source, bytes):
            size = len(source)
        else:
            size = (await asyncio.to_thread(source.stat)).st_size
        if size != started.size_bytes:
            raise ValueError(
                f"{started.name} is {size} bytes; its upload started at {started.size_bytes}"
            )
        sent: set[int] = set()
        async with self._store() as store:
            while (issued := await self.issue_upload(started.id)).parts:
                for part in issued.parts:
                    if part.number in sent:
                        raise ApiError(
                            409, "upload_stalled", f"the store kept no part {part.number}", None
                        )
                    await send_part(store, part, source)
                    sent.add(part.number)
        return await self.confirm_file(started.id)

    async def download(self, file_id: UUID) -> bytes:
        """A stored file's bytes, straight from the store by its signed link."""
        link = await self.issue_download(file_id)
        async with self._store() as store:
            fetched = await store.get(link.url)
        if fetched.is_error:
            raise ApiError(fetched.status_code, "download_refused", fetched.text[:200], None)
        return fetched.content


async def send_part(store: httpx.AsyncClient, part: IssuedPartView, source: Path | bytes) -> None:
    """One part's bytes of `source`, as the body of a PUT to its URL. The
    length is declared, since the URL is signed for it, and the body is
    streamed in chunks."""
    sent = await store.put(
        part.url,
        content=chunks(source, part.offset, part.size_bytes),
        headers={"Content-Length": str(part.size_bytes)},
    )
    if sent.is_error:
        raise ApiError(sent.status_code, "part_refused", sent.text[:200], None)


async def chunks(source: Path | bytes, offset: int, size: int) -> AsyncIterator[bytes]:
    """`size` bytes of `source` from `offset`, a chunk at a time."""
    if isinstance(source, bytes):
        for start in range(offset, offset + size, CHUNK_BYTES):
            yield source[start : min(start + CHUNK_BYTES, offset + size)]
        return
    file = await asyncio.to_thread(source.open, "rb")
    try:
        await asyncio.to_thread(file.seek, offset)
        left = size
        while left > 0:
            chunk = await asyncio.to_thread(file.read, min(CHUNK_BYTES, left))
            if not chunk:
                raise ValueError(f"{source.name} ended {left} bytes short of its part")
            left -= len(chunk)
            yield chunk
    finally:
        file.close()
