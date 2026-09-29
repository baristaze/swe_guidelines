from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import aioboto3

from acme.infra.aws_clients import AwsClientHolder, client_config
from acme.infra.aws_errors import ClientError, error_code, translated
from acme.infra.buckets import (
    BlobNotFound,
    Buckets,
    BucketsInterface,
    PresignedPost,
    object_key,
)
from acme.infra.deadline import bounded, unreachable

NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
MAX_KEYS_PER_CALL = 1000
"""The most keys one ListObjectsV2 call returns."""


class BucketsS3Impl(BucketsInterface):
    """One client, opened by `start()` and closed by `close()` through the
    holder; every call borrows it.

    A presigned URL names the host the browser will reach, and a signature
    over a GET covers that host, so a store the process reaches at one
    address and a browser at another (MinIO inside the compose network, at a
    host port outside it) signs with a second client aimed at the browser's
    address. Signing makes no request, so that client never connects."""

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
        config = client_config(timeout)
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
    ) -> str | None:
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

    async def presign_post(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        content_type: str,
        max_bytes: int,
        ttl: timedelta,
    ) -> PresignedPost | None:
        """A presigned POST, not a PUT: a signed PUT can fix a length but never
        bound one, and the policy of a POST carries both conditions, which
        the store enforces on the body it receives."""
        if max_bytes <= 0:
            raise ValueError(f"an upload is bounded by a positive size, not {max_bytes}")
        with translated("s3", "presign_post"):
            s3 = self._signing_client()
            post = await s3.generate_presigned_post(
                Bucket=self._bucket(bucket),
                Key=object_key(org_id, key),
                Fields={"Content-Type": content_type},
                Conditions=[
                    {"Content-Type": content_type},
                    ["content-length-range", 0, max_bytes],
                ],
                ExpiresIn=int(ttl.total_seconds()),
            )
        return PresignedPost(url=post["url"], fields=tuple(sorted(post["fields"].items())))

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
