import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import aioboto3
import pytest

from acme.infra.base import new_id
from acme.infra.buckets import BlobNotFound, Buckets, BucketsInterface
from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.exceptions import InvalidBucketKey, UploadRefused
from acme.infra.impl.settings import InfraSettings


async def test_put_get_exists_list_delete(tmp_path: Path) -> None:
    buckets = BucketsLocalImpl(tmp_path)
    org = new_id()
    await buckets.put(org, Buckets.EXPORTS, "reports/a.csv", b"a,b", "text/csv")
    await buckets.put(org, Buckets.EXPORTS, "reports/b.csv", b"c,d", "text/csv")
    assert await buckets.exists(org, Buckets.EXPORTS, "reports/a.csv")
    assert await buckets.get(org, Buckets.EXPORTS, "reports/a.csv") == b"a,b"
    assert await buckets.list(org, Buckets.EXPORTS, "reports/", limit=10) == [
        "reports/a.csv",
        "reports/b.csv",
    ]
    await buckets.delete(org, Buckets.EXPORTS, "reports/a.csv")
    assert not await buckets.exists(org, Buckets.EXPORTS, "reports/a.csv")
    with pytest.raises(BlobNotFound):
        await buckets.get(org, Buckets.EXPORTS, "reports/a.csv")


async def test_tenants_cannot_see_each_other(tmp_path: Path) -> None:
    buckets = BucketsLocalImpl(tmp_path)
    org_a, org_b = new_id(), new_id()
    await buckets.put(org_a, Buckets.USER_FILE_UPLOADS, "x.bin", b"1", "application/octet-stream")
    assert await buckets.list(org_b, Buckets.USER_FILE_UPLOADS, "", limit=10) == []
    assert not await buckets.exists(org_b, Buckets.USER_FILE_UPLOADS, "x.bin")


async def test_keys_stay_inside_the_tenant_prefix(tmp_path: Path) -> None:
    buckets = BucketsLocalImpl(tmp_path)
    with pytest.raises(InvalidBucketKey):
        await buckets.put(new_id(), Buckets.EXPORTS, "../escape", b"", "text/plain")
    with pytest.raises(InvalidBucketKey):
        await buckets.get(new_id(), Buckets.EXPORTS, "/absolute")
    with pytest.raises(InvalidBucketKey):
        await buckets.put(new_id(), Buckets.EXPORTS, "a/../../b", b"", "text/plain")
    with pytest.raises(InvalidBucketKey):
        await buckets.presign_post(
            new_id(), Buckets.EXPORTS, "../up", "text/plain", 10, timedelta(minutes=1)
        )
    # A link inside the tree that points out of it is refused too.
    org = new_id()
    (tmp_path / "outside").mkdir()
    (tmp_path / Buckets.EXPORTS.value / str(org)).mkdir(parents=True)
    (tmp_path / Buckets.EXPORTS.value / str(org) / "out").symlink_to(tmp_path / "outside")
    with pytest.raises(InvalidBucketKey):
        await buckets.put(org, Buckets.EXPORTS, "out/x", b"", "text/plain")


async def test_local_impl_cannot_presign(tmp_path: Path) -> None:
    buckets = BucketsLocalImpl(tmp_path)
    assert await buckets.presign_get(new_id(), Buckets.EXPORTS, "k", timedelta(minutes=1)) is None
    assert (
        await buckets.presign_post(
            new_id(), Buckets.EXPORTS, "k", "text/plain", 1024, timedelta(minutes=1)
        )
        is None
    )


async def test_the_local_impl_holds_a_presigned_upload_to_its_bounds(tmp_path: Path) -> None:
    """It cannot sign, so the caller puts the bytes itself; the put is held to
    the type and the size the upload was presigned with, as the store holds a
    posted body, and no longer once the form would have expired."""
    buckets = BucketsLocalImpl(tmp_path)
    org, other = new_id(), new_id()
    ttl = timedelta(minutes=5)
    await buckets.presign_post(org, Buckets.USER_FILE_UPLOADS, "a.png", "image/png", 4, ttl)
    with pytest.raises(UploadRefused):
        await buckets.put(org, Buckets.USER_FILE_UPLOADS, "a.png", b"12345", "image/png")
    with pytest.raises(UploadRefused):
        await buckets.put(org, Buckets.USER_FILE_UPLOADS, "a.png", b"1", "text/plain")
    assert not await buckets.exists(org, Buckets.USER_FILE_UPLOADS, "a.png")
    await buckets.put(org, Buckets.USER_FILE_UPLOADS, "a.png", b"", "image/png")
    await buckets.put(org, Buckets.USER_FILE_UPLOADS, "a.png", b"1234", "image/png")
    assert await buckets.get(org, Buckets.USER_FILE_UPLOADS, "a.png") == b"1234"
    # The bound is the tenant's key, not another tenant's same path.
    await buckets.put(other, Buckets.USER_FILE_UPLOADS, "a.png", b"12345", "text/plain")
    await buckets.presign_post(
        org, Buckets.USER_FILE_UPLOADS, "b.png", "image/png", 1, timedelta(0)
    )
    await buckets.put(org, Buckets.USER_FILE_UPLOADS, "b.png", b"123", "image/png")
    with pytest.raises(ValueError, match="bounded"):
        await buckets.presign_post(org, Buckets.EXPORTS, "k", "text/plain", 0, ttl)


async def test_a_presigned_upload_is_bounded_by_size_and_type() -> None:
    """The policy the store enforces names the type and the largest body; it is
    signed here, with no call to the store."""
    import base64
    import json

    session = aioboto3.Session(
        aws_access_key_id="k", aws_secret_access_key="s", region_name="us-west-2"
    )
    buckets = BucketsS3Impl(
        session,
        endpoint_url=None,
        region="us-west-2",
        bucket_prefix="t",
        timeout=timedelta(seconds=1),
    )
    await buckets.start()
    org = new_id()
    upload = await buckets.presign_post(
        org, Buckets.USER_FILE_UPLOADS, "a.png", "image/png", 5_000_000, timedelta(minutes=5)
    )
    await buckets.close()
    assert upload is not None
    fields = dict(upload.fields)
    assert fields["key"] == f"{org}/a.png"
    assert fields["Content-Type"] == "image/png"
    policy = json.loads(base64.b64decode(fields["policy"]))
    assert ["content-length-range", 0, 5_000_000] in policy["conditions"]
    assert {"Content-Type": "image/png"} in policy["conditions"]


async def test_an_unbounded_upload_is_refused() -> None:
    session = aioboto3.Session(
        aws_access_key_id="k", aws_secret_access_key="s", region_name="us-west-2"
    )
    buckets = BucketsS3Impl(
        session,
        endpoint_url=None,
        region="us-west-2",
        bucket_prefix="t",
        timeout=timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="bounded"):
        await buckets.presign_post(
            new_id(), Buckets.EXPORTS, "k", "text/plain", 0, timedelta(minutes=1)
        )


async def listing_is_bounded_lexical_and_resumes_after(buckets: BucketsInterface) -> None:
    """The listing contract every impl meets: keys in lexical order, at most
    `limit` of them, and the next page starting after the last key returned.
    The S3 impl runs it over the compose stack's MinIO in the integration
    suite, below."""
    org, other = new_id(), new_id()
    keys = ["r/b", "r/a", "r/c/1", "r/c/2", "r/d", "s/x"]
    for key in keys:
        await buckets.put(org, Buckets.EXPORTS, key, b"x", "text/plain")
    await buckets.put(other, Buckets.EXPORTS, "r/a0", b"x", "text/plain")
    every = sorted(k for k in keys if k.startswith("r/"))
    assert await buckets.list(org, Buckets.EXPORTS, "r/", limit=100) == every
    assert await buckets.list(org, Buckets.EXPORTS, "r/", limit=2) == every[:2]
    pages: list[str] = []
    after: str | None = None
    while page := await buckets.list(org, Buckets.EXPORTS, "r/", limit=2, after=after):
        assert len(page) <= 2
        pages.extend(page)
        after = page[-1]
    assert pages == every
    assert await buckets.list(org, Buckets.EXPORTS, "r/", limit=10, after="r/c") == every[2:]
    assert await buckets.list(org, Buckets.EXPORTS, "r/", limit=10, after="r/d") == []


async def test_the_local_listing_is_bounded_and_resumes(tmp_path: Path) -> None:
    await listing_is_bounded_lexical_and_resumes_after(BucketsLocalImpl(tmp_path))


@pytest.fixture
async def s3_buckets() -> AsyncIterator[BucketsS3Impl]:
    """The S3 impl over the compose stack's MinIO, on a bucket of its own that
    is emptied and removed afterwards."""
    settings = InfraSettings()
    session = aioboto3.Session(
        aws_access_key_id=settings.s3_access_key or "acme",
        aws_secret_access_key=settings.s3_secret_key or "acme-minio-local",
        region_name=settings.aws_region,
    )
    endpoint = settings.s3_endpoint_url or "http://127.0.0.1:59000"
    prefix = f"acme-it-{secrets.token_hex(4)}"
    name = f"{prefix}-{Buckets.EXPORTS.value}"
    admin: Any = session.client("s3", endpoint_url=endpoint)
    async with admin as s3:
        await s3.create_bucket(Bucket=name)
    impl = BucketsS3Impl(
        session,
        endpoint_url=endpoint,
        region=settings.aws_region,
        bucket_prefix=prefix,
        timeout=timedelta(seconds=10),
    )
    await impl.start()
    try:
        yield impl
    finally:
        await impl.close()
        cleanup: Any = session.client("s3", endpoint_url=endpoint)
        async with cleanup as s3:
            listed = await s3.list_objects_v2(Bucket=name)
            for item in listed.get("Contents", []):
                await s3.delete_object(Bucket=name, Key=item["Key"])
            await s3.delete_bucket(Bucket=name)


@pytest.mark.integration
async def test_the_s3_listing_is_bounded_and_resumes(s3_buckets: BucketsS3Impl) -> None:
    await listing_is_bounded_lexical_and_resumes_after(s3_buckets)


@pytest.mark.integration
async def test_an_s3_limit_past_one_call_continues_within_itself(
    s3_buckets: BucketsS3Impl, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acme.infra.buckets.s3.MAX_KEYS_PER_CALL", 2)
    org = new_id()
    keys = [f"k/{i}" for i in range(5)]
    for key in keys:
        await s3_buckets.put(org, Buckets.EXPORTS, key, b"x", "text/plain")
    assert await s3_buckets.list(org, Buckets.EXPORTS, "k/", limit=4) == keys[:4]
    assert await s3_buckets.list(org, Buckets.EXPORTS, "k/", limit=10, after="k/0") == keys[1:]


def test_an_empty_presign_endpoint_is_the_endpoint_itself() -> None:
    settings = InfraSettings.model_validate(
        {"_env_file": None, "s3_presign_endpoint_url": "", "s3_endpoint_url": "http://s:9000"}
    )
    assert settings.s3_presign_endpoint_url is None


async def test_a_presigned_url_names_the_host_a_browser_reaches() -> None:
    """The process talks to one address and a browser to another: the URL a
    browser follows names the browser's, and the signing makes no request."""
    session = aioboto3.Session(
        aws_access_key_id="k", aws_secret_access_key="s", region_name="us-east-1"
    )
    buckets = BucketsS3Impl(
        session,
        endpoint_url="http://minio:9000",
        region="us-east-1",
        bucket_prefix="t",
        timeout=timedelta(seconds=1),
        presign_endpoint_url="http://127.0.0.1:59000",
    )
    await buckets.start()
    org = new_id()
    try:
        link = await buckets.presign_get(
            org,
            Buckets.USER_FILE_UPLOADS,
            "a.png",
            timedelta(minutes=1),
            content_type="image/png",
            content_disposition="inline",
        )
        form = await buckets.presign_post(
            org, Buckets.USER_FILE_UPLOADS, "a.png", "image/png", 10, timedelta(minutes=1)
        )
    finally:
        await buckets.close()
    assert link is not None and link.startswith("http://127.0.0.1:59000/t-user-file-uploads/")
    # The headers the store answers with are signed into the link.
    assert "response-content-disposition=inline" in link
    assert "response-content-type=image%2Fpng" in link
    assert form is not None and form.url.startswith("http://127.0.0.1:59000/t-user-file-uploads")
