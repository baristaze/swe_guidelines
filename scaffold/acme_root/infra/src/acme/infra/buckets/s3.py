from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import aioboto3
from botocore.config import Config

from acme.infra.aws_clients import AwsClientHolder, client_config
from acme.infra.aws_errors import ClientError, error_code, translated
from acme.infra.buckets import (
    MAX_PARTS,
    BlobNotFound,
    Buckets,
    BucketsInterface,
    UploadNotFound,
    UploadPart,
    UploadRefused,
    held_to_plan,
    object_key,
)
from acme.infra.deadline import bounded, unreachable

NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
NO_SUCH_UPLOAD = "NoSuchUpload"
MAX_KEYS_PER_CALL = 1000
"""The most keys one ListObjectsV2 call returns."""
MAX_PARTS_PER_CALL = 1000
"""The most parts one ListParts call returns."""


class BucketsS3Impl(BucketsInterface):
    """One client, opened by `start()` and closed by `close()` through the
    holder; every call borrows it.

    A presigned URL names the host the client will reach, and its signature
    covers that host, so a store the process reaches at one address and a
    client at another (MinIO inside the compose network, at a host port
    outside it) signs with a second client aimed at the client's address.
    Signing makes no request, so that client never connects.

    Every URL is signed with SigV4, which signs the headers a request must
    carry: a part's `Content-Length` among them, so the store refuses a body
    of any other length. The older signature signs no length.

    With no endpoint named, the store is AWS, and a URL names the bucket's
    regional host (`<bucket>.s3.<region>.amazonaws.com`). The global host
    answers a bucket outside us-east-1 with a redirect until its name has
    spread, which can take a day after the bucket is made, and a browser
    follows no redirect on a cross-origin upload."""

    def __init__(
        self,
        session: aioboto3.Session,
        *,
        endpoint_url: str | None,
        region: str,
        bucket_prefix: str,
        timeout: timedelta,
        presign_endpoint_url: str | None = None,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._region = region
        config = client_config(timeout).merge(Config(signature_version="s3v4"))
        if endpoint_url is None:
            config = config.merge(Config(s3={"addressing_style": "virtual"}))
        self._holder = AwsClientHolder(
            "s3",
            lambda: session.client(
                "s3", endpoint_url=endpoint_url, region_name=region, config=config
            ),
        )
        self._signer: AwsClientHolder | None = None
        if presign_endpoint_url is not None and presign_endpoint_url != endpoint_url:
            self._signer = AwsClientHolder(
                "s3 signer",
                lambda: session.client(
                    "s3", endpoint_url=presign_endpoint_url, region_name=region, config=config
                ),
            )
        self._bucket_prefix = bucket_prefix

    def _client(self) -> Any:
        return self._holder.client()

    def _signing_client(self) -> Any:
        return (self._signer or self._holder).client()

    def _bucket(self, bucket: Buckets) -> str:
        return f"{self._bucket_prefix}-{bucket.value}"

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
        async with bounded(deadline, unreachable("s3", "put")):
            with translated("s3", "put"):
                s3 = self._client()
                await s3.put_object(
                    Bucket=self._bucket(bucket),
                    Key=object_key(org_id, key),
                    Body=data,
                    ContentType=content_type,
                )

    async def get(
        self, org_id: UUID, bucket: Buckets, key: str, *, deadline: datetime | None = None
    ) -> bytes:
        async with bounded(deadline, unreachable("s3", "get")):
            with translated("s3", "get"):
                s3 = self._client()
                try:
                    response = await s3.get_object(
                        Bucket=self._bucket(bucket), Key=object_key(org_id, key)
                    )
                except ClientError as error:
                    if error_code(error) in NOT_FOUND_CODES:
                        raise BlobNotFound(f"{bucket.value}/{key}") from None
                    raise
                async with response["Body"] as body:
                    return await body.read()

    async def exists(
        self, org_id: UUID, bucket: Buckets, key: str, *, deadline: datetime | None = None
    ) -> bool:
        async with bounded(deadline, unreachable("s3", "exists")):
            with translated("s3", "exists"):
                s3 = self._client()
                try:
                    await s3.head_object(Bucket=self._bucket(bucket), Key=object_key(org_id, key))
                except ClientError as error:
                    if error_code(error) in NOT_FOUND_CODES:
                        return False
                    raise
                return True

    async def list(
        self, org_id: UUID, bucket: Buckets, prefix: str, limit: int, after: str | None = None
    ) -> list[str]:
        """The store lists in UTF-8 byte order and stops at `MaxKeys`, at most
        a thousand a call; a larger `limit` continues within itself."""
        tenant_prefix = f"{org_id}/"
        keys: list[str] = []
        request: dict[str, Any] = {
            "Bucket": self._bucket(bucket),
            "Prefix": tenant_prefix + prefix,
        }
        if after is not None:
            request["StartAfter"] = tenant_prefix + after
        with translated("s3", "list"):
            s3 = self._client()
            while len(keys) < limit:
                page = await s3.list_objects_v2(
                    **request, MaxKeys=min(limit - len(keys), MAX_KEYS_PER_CALL)
                )
                keys.extend(item["Key"][len(tenant_prefix) :] for item in page.get("Contents", []))
                if not page.get("IsTruncated"):
                    break
                request["ContinuationToken"] = page["NextContinuationToken"]
        return keys[:limit]

    async def delete(self, org_id: UUID, bucket: Buckets, key: str) -> None:
        with translated("s3", "delete"):
            s3 = self._client()
            await s3.delete_object(Bucket=self._bucket(bucket), Key=object_key(org_id, key))

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
        params: dict[str, Any] = {"Bucket": self._bucket(bucket), "Key": object_key(org_id, key)}
        if content_type is not None:
            params["ResponseContentType"] = content_type
        if content_disposition is not None:
            params["ResponseContentDisposition"] = content_disposition
        with translated("s3", "presign_get"):
            s3 = self._signing_client()
            return await s3.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=int(ttl.total_seconds()),
            )

    async def start_upload(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        content_type: str,
        *,
        deadline: datetime | None = None,
    ) -> str:
        async with bounded(deadline, unreachable("s3", "start_upload")):
            with translated("s3", "start_upload"):
                s3 = self._client()
                started = await s3.create_multipart_upload(
                    Bucket=self._bucket(bucket),
                    Key=object_key(org_id, key),
                    ContentType=content_type,
                )
                return str(started["UploadId"])

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
        """`ContentLength` is signed into the URL as a header the PUT must
        carry, so the store answers a body of any other length with a
        signature that does not match."""
        if not 1 <= number <= MAX_PARTS or size_bytes <= 0:
            raise ValueError(f"part {number} of {size_bytes} bytes is outside an upload")
        with translated("s3", "presign_part"):
            s3 = self._signing_client()
            return await s3.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._bucket(bucket),
                    "Key": object_key(org_id, key),
                    "UploadId": upload_id,
                    "PartNumber": number,
                    "ContentLength": size_bytes,
                },
                ExpiresIn=int(ttl.total_seconds()),
            )

    async def _held(
        self, org_id: UUID, bucket: Buckets, key: str, upload_id: str, limit: int, operation: str
    ) -> list[dict[str, Any]]:
        """The store's parts of the upload, with the entity tags a complete
        names, a page of a thousand at a time up to `limit`."""
        limit = min(limit, MAX_PARTS)
        request: dict[str, Any] = {
            "Bucket": self._bucket(bucket),
            "Key": object_key(org_id, key),
            "UploadId": upload_id,
        }
        parts: list[dict[str, Any]] = []
        with translated("s3", operation):
            s3 = self._client()
            while len(parts) < limit:
                try:
                    page = await s3.list_parts(
                        **request, MaxParts=min(limit - len(parts), MAX_PARTS_PER_CALL)
                    )
                except ClientError as error:
                    if error_code(error) == NO_SUCH_UPLOAD:
                        raise UploadNotFound(f"{bucket.value}/{key}") from None
                    raise
                parts.extend(page.get("Parts", []))
                if not page.get("IsTruncated"):
                    break
                request["PartNumberMarker"] = page["NextPartNumberMarker"]
        return parts[:limit]

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
        async with bounded(deadline, unreachable("s3", "list_parts")):
            held = await self._held(org_id, bucket, key, upload_id, limit, "list_parts")
        return [UploadPart(number=p["PartNumber"], size_bytes=p["Size"]) for p in held]

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
        """The parts are the store's own listing, never the client's word, so
        the complete names exactly what the store holds."""
        async with bounded(deadline, unreachable("s3", "complete_upload")):
            held = await self._held(org_id, bucket, key, upload_id, MAX_PARTS, "complete_upload")
            refusal = held_to_plan(
                [UploadPart(number=p["PartNumber"], size_bytes=p["Size"]) for p in held],
                size_bytes,
            )
            if refusal is not None:
                raise UploadRefused(f"upload of {key!r} is not complete: {refusal}")
            with translated("s3", "complete_upload"):
                s3 = self._client()
                try:
                    await s3.complete_multipart_upload(
                        Bucket=self._bucket(bucket),
                        Key=object_key(org_id, key),
                        UploadId=upload_id,
                        MultipartUpload={
                            "Parts": [
                                {"PartNumber": p["PartNumber"], "ETag": p["ETag"]}
                                for p in sorted(held, key=lambda p: p["PartNumber"])
                            ]
                        },
                    )
                except ClientError as error:
                    if error_code(error) == NO_SUCH_UPLOAD:
                        raise UploadNotFound(f"{bucket.value}/{key}") from None
                    raise

    async def abort_upload(self, org_id: UUID, bucket: Buckets, key: str, upload_id: str) -> None:
        with translated("s3", "abort_upload"):
            s3 = self._client()
            try:
                await s3.abort_multipart_upload(
                    Bucket=self._bucket(bucket), Key=object_key(org_id, key), UploadId=upload_id
                )
            except ClientError as error:
                if error_code(error) != NO_SUCH_UPLOAD:
                    raise

    def describe(self) -> str:
        return f"buckets=s3({self._endpoint_url or self._region})"

    async def start(self) -> None:
        await self._holder.open()
        if self._signer is not None:
            await self._signer.open()

    async def close(self) -> None:
        if self._signer is not None:
            await self._signer.close()
        await self._holder.close()
