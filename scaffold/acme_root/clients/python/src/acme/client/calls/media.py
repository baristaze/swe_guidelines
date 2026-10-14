"""The media namespace's calls: the org's files, as references to objects in
the store."""

from typing import Any, cast
from uuid import UUID, uuid4

import httpx

from acme.client.transport import LIMIT_MAX, ApiError, Transport, trust_store
from acme.client.types.media import (
    FilePageView,
    FileView,
    IssuedDownloadView,
    IssuedUploadView,
    StorageUsageView,
)


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
        """A pending file the org keeps, and nothing in the store yet: `upload`
        sends the bytes and confirms it. Always under an idempotency key, so
        a retry lands one file."""
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
        form = await self.request("POST", f"/v1/media/files/{file_id}/upload")
        return IssuedUploadView.model_validate(form)

    async def put_content(self, file_id: UUID, data: bytes) -> FileView:
        """The bytes through the API, for a store that cannot take a form post."""
        put = await self.request("PUT", f"/v1/media/files/{file_id}/content", content=data)
        return FileView.model_validate(put)

    async def confirm_file(self, file_id: UUID) -> FileView:
        return FileView.model_validate(
            await self.request("POST", f"/v1/media/files/{file_id}/confirm")
        )

    async def issue_download(self, file_id: UUID) -> IssuedDownloadView:
        link = await self.request("GET", f"/v1/media/files/{file_id}/download")
        return IssuedDownloadView.model_validate(link)

    async def content(self, file_id: UUID) -> bytes:
        return cast(
            bytes, await self.request("GET", f"/v1/media/files/{file_id}/content", raw=True)
        )

    async def delete_file(self, file_id: UUID) -> FileView:
        removed = await self.request("DELETE", f"/v1/media/files/{file_id}")
        return FileView.model_validate(removed)

    async def storage_usage(self) -> StorageUsageView:
        return StorageUsageView.model_validate(await self.request("GET", "/v1/media/usage"))

    async def upload(self, started: FileView, data: bytes) -> FileView:
        """The bytes of a started upload, then its confirm. They go straight to
        the store with the form the API signed, or through the API when the
        store cannot take a post. The post to the store carries no credential
        of ours: the form is the credential, and it holds the post to this
        file's type and size."""
        form = await self.issue_upload(started.id)
        if form.url is None:
            await self.put_content(started.id, data)
        else:
            async with httpx.AsyncClient(timeout=self.timeout, verify=trust_store()) as store:
                posted = await store.post(
                    form.url,
                    data={field.name: field.value for field in form.fields},
                    files={"file": (started.name, data, started.content_type)},
                )
            if posted.is_error:
                raise ApiError(posted.status_code, "upload_refused", posted.text[:200], None)
        return await self.confirm_file(started.id)

    async def download(self, file_id: UUID) -> bytes:
        """A stored file's bytes: by the signed link, or through the API when
        the store cannot sign one."""
        link = await self.issue_download(file_id)
        if link.url is None:
            return await self.content(file_id)
        async with httpx.AsyncClient(timeout=self.timeout, verify=trust_store()) as store:
            fetched = await store.get(link.url)
        if fetched.is_error:
            raise ApiError(fetched.status_code, "download_refused", fetched.text[:200], None)
        return fetched.content
