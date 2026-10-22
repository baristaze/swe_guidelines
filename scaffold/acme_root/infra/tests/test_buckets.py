import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aioboto3
import httpx
import pytest
from store_support import local_store

from acme.infra.base import new_id
from acme.infra.buckets import (
    MAX_PARTS,
    MIB,
    MIN_PART_BYTES,
    PART_BYTES,
    BlobNotFound,
    Buckets,
    BucketsInterface,
    UploadPart,
    held_to_plan,
    part_sizes,
)
from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.exceptions import InvalidBucketKey, UploadNotFound, UploadRefused
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
        await buckets.start_upload(new_id(), Buckets.EXPORTS, "../up", "text/plain")
    # A link inside the tree that points out of it is refused too.
    org = new_id()
    (tmp_path / "outside").mkdir()
    (tmp_path / Buckets.EXPORTS.value / str(org)).mkdir(parents=True)
    (tmp_path / Buckets.EXPORTS.value / str(org) / "out").symlink_to(tmp_path / "outside")
    with pytest.raises(InvalidBucketKey):
        await buckets.put(org, Buckets.EXPORTS, "out/x", b"", "text/plain")


def test_an_upload_is_planned_in_parts_from_its_size_alone() -> None:
    """Every part but the last is one size, never under the store's floor,
    and never more than the store's count of parts; the parts sum to the
    size."""
    assert part_sizes(1) == (1,)
    assert part_sizes(PART_BYTES) == (PART_BYTES,)
    assert part_sizes(2 * PART_BYTES + 3) == (PART_BYTES, PART_BYTES, 3)
    for size in (PART_BYTES * MAX_PARTS + 1, 5 * 1024**4):
        plan = part_sizes(size)
        assert len(plan) <= MAX_PARTS
        assert sum(plan) == size
        assert all(part >= MIN_PART_BYTES and part % MIB == 0 for part in plan[:-1])
    with pytest.raises(ValueError, match="bounded"):
        part_sizes(0)


def test_held_parts_make_an_upload_only_as_its_plan() -> None:
    size = PART_BYTES + 5
    whole = [UploadPart(number=1, size_bytes=PART_BYTES), UploadPart(number=2, size_bytes=5)]
    assert held_to_plan(whole, size) is None
    assert held_to_plan(whole[:1], size) is not None  # a part missing
    assert held_to_plan(whole, size + 1) is not None  # short of the declared size
    beyond = [*whole, UploadPart(number=3, size_bytes=1)]
    assert held_to_plan(beyond, size) is not None


async def multipart_upload_lands_whole_from_its_signed_parts(
    buckets: BucketsInterface, store: httpx.AsyncClient
) -> None:
    """The upload contract every impl meets: the parts of the plan, each by a
    URL signed for its number and length and sent straight to the store,
    land as one object of the declared size, and only at the complete. A
    part longer than its URL names is refused by the store; a complete short
    of the declared size is refused, and nothing lands. The S3 impl runs it
    over the compose stack's MinIO in the integration suite, below."""
    org = new_id()
    data = secrets.token_bytes(PART_BYTES + 1024)
    plan = part_sizes(len(data))
    ttl = timedelta(minutes=5)
    upload_id = await buckets.start_upload(org, Buckets.EXPORTS, "big.bin", "application/zip")
    assert await buckets.list_parts(org, Buckets.EXPORTS, "big.bin", upload_id, MAX_PARTS) == []
    first = await buckets.presign_part(org, Buckets.EXPORTS, "big.bin", upload_id, 1, plan[0], ttl)
    assert (await store.put(first, content=data[: plan[0]])).status_code == 200
    # Nothing lands before the complete, and a complete short of the size is refused.
    assert not await buckets.exists(org, Buckets.EXPORTS, "big.bin")
    with pytest.raises(UploadRefused):
        await buckets.complete_upload(org, Buckets.EXPORTS, "big.bin", upload_id, len(data))
    assert not await buckets.exists(org, Buckets.EXPORTS, "big.bin")
    last = await buckets.presign_part(org, Buckets.EXPORTS, "big.bin", upload_id, 2, plan[1], ttl)
    # A body longer than the part it was signed for, declared as such: refused.
    longer = await store.put(last, content=data[plan[0] :] + b"more")
    assert longer.status_code in (400, 403), longer.text
    assert await buckets.list_parts(org, Buckets.EXPORTS, "big.bin", upload_id, MAX_PARTS) == [
        UploadPart(number=1, size_bytes=plan[0])
    ]
    assert (await store.put(last, content=data[plan[0] :])).status_code == 200
    held = await buckets.list_parts(org, Buckets.EXPORTS, "big.bin", upload_id, MAX_PARTS)
    assert [(p.number, p.size_bytes) for p in held] == list(enumerate(plan, 1))
    # A complete that names more than the parts hold is refused as well.
    with pytest.raises(UploadRefused):
        await buckets.complete_upload(org, Buckets.EXPORTS, "big.bin", upload_id, len(data) + 1)
    await buckets.complete_upload(org, Buckets.EXPORTS, "big.bin", upload_id, len(data))
    assert await buckets.get(org, Buckets.EXPORTS, "big.bin") == data
    with pytest.raises(UploadNotFound):
        await buckets.list_parts(org, Buckets.EXPORTS, "big.bin", upload_id, MAX_PARTS)
    link = await buckets.presign_get(
        org, Buckets.EXPORTS, "big.bin", ttl, content_disposition='attachment; filename="b"'
    )
    fetched = await store.get(link)
    assert fetched.status_code == 200 and fetched.content == data
    assert fetched.headers["content-disposition"] == 'attachment; filename="b"'


async def an_aborted_upload_leaves_no_part(
    buckets: BucketsInterface, store: httpx.AsyncClient
) -> None:
    org = new_id()
    upload_id = await buckets.start_upload(org, Buckets.EXPORTS, "gone.bin", "text/plain")
    part = await buckets.presign_part(
        org, Buckets.EXPORTS, "gone.bin", upload_id, 1, 3, timedelta(minutes=5)
    )
    assert (await store.put(part, content=b"abc")).status_code == 200
    await buckets.abort_upload(org, Buckets.EXPORTS, "gone.bin", upload_id)
    await buckets.abort_upload(org, Buckets.EXPORTS, "gone.bin", upload_id)  # gone is done
    with pytest.raises(UploadNotFound):
        await buckets.list_parts(org, Buckets.EXPORTS, "gone.bin", upload_id, MAX_PARTS)
    with pytest.raises(UploadNotFound):
        await buckets.complete_upload(org, Buckets.EXPORTS, "gone.bin", upload_id, 3)
    assert not await buckets.exists(org, Buckets.EXPORTS, "gone.bin")


async def test_the_local_multipart_upload_lands_whole_from_its_signed_parts(
    tmp_path: Path,
) -> None:
    buckets = BucketsLocalImpl(tmp_path)
    async with local_store(buckets) as store:
        await multipart_upload_lands_whole_from_its_signed_parts(buckets, store)
        await an_aborted_upload_leaves_no_part(buckets, store)


async def test_the_local_store_holds_a_part_to_the_length_it_was_signed_for(
    tmp_path: Path,
) -> None:
    """A part declared at its signed length whose body runs past it, one
    declared at another length, and one cut short are all refused, and none
    is kept."""
    buckets = BucketsLocalImpl(tmp_path)
    org = new_id()
    upload_id = await buckets.start_upload(org, Buckets.EXPORTS, "p.bin", "text/plain")
    url = await buckets.presign_part(
        org, Buckets.EXPORTS, "p.bin", upload_id, 1, 4, timedelta(minutes=5)
    )
    async with local_store(buckets) as store:
        past = await store.put(url, content=b"123456", headers={"Content-Length": "4"})
        other = await store.put(url, content=b"12")
        assert (past.status_code, other.status_code) == (400, 403)
        assert await buckets.list_parts(org, Buckets.EXPORTS, "p.bin", upload_id, MAX_PARTS) == []
        assert (await store.put(url, content=b"1234")).status_code == 200


async def test_the_local_store_answers_only_what_it_signed(tmp_path: Path) -> None:
    """Every download is a signed link, never None. A link whose path or
    query was changed, one another store signed, or one past its expiry is
    refused; a part's URL is no link to the object."""
    buckets = BucketsLocalImpl(tmp_path)
    org, other = new_id(), new_id()
    await buckets.put(org, Buckets.EXPORTS, "a.txt", b"mine", "text/plain")
    await buckets.put(other, Buckets.EXPORTS, "a.txt", b"theirs", "text/plain")
    link = await buckets.presign_get(org, Buckets.EXPORTS, "a.txt", timedelta(minutes=1))
    assert link.startswith("http://buckets.test/exports/")
    async with local_store(buckets) as store:
        fetched = await store.get(link)
        assert (fetched.content, fetched.headers["content-type"]) == (b"mine", "text/plain")
        assert (await store.get(link.replace(str(org), str(other)))).status_code == 403
        assert (await store.get(link + "&response-content-type=text%2Fhtml")).status_code == 403
        expired = await buckets.presign_get(org, Buckets.EXPORTS, "a.txt", timedelta(seconds=-1))
        assert (await store.get(expired)).status_code == 403
    foreign = await BucketsLocalImpl(tmp_path).presign_get(
        org, Buckets.EXPORTS, "a.txt", timedelta(minutes=1)
    )
    async with local_store(buckets) as store:
        assert (await store.get(foreign)).status_code == 403
    upload_id = await buckets.start_upload(org, Buckets.EXPORTS, "b.txt", "text/plain")
    part = await buckets.presign_part(
        org, Buckets.EXPORTS, "b.txt", upload_id, 1, 1, timedelta(minutes=1)
    )
    async with local_store(buckets) as store:
        assert (await store.get(part)).status_code == 403
    # An upload id is the key's own: another key, or another tenant's, holds none.
    with pytest.raises(UploadNotFound):
        await buckets.list_parts(org, Buckets.EXPORTS, "a.txt", upload_id, MAX_PARTS)
    with pytest.raises(UploadNotFound):
        await buckets.list_parts(other, Buckets.EXPORTS, "b.txt", upload_id, MAX_PARTS)
    with pytest.raises(UploadNotFound):
        await buckets.list_parts(org, Buckets.EXPORTS, "b.txt", "../../x", MAX_PARTS)


def s3_impl(**overrides: Any) -> BucketsS3Impl:
    session = aioboto3.Session(
        aws_access_key_id="k", aws_secret_access_key="s", region_name="us-west-2"
    )
    options: dict[str, Any] = {
        "endpoint_url": None,
        "region": "us-west-2",
        "bucket_prefix": "t",
        "timeout": timedelta(seconds=1),
        **overrides,
    }
    return BucketsS3Impl(session, **options)


async def test_a_part_is_signed_for_its_length() -> None:
    """The length is a header the signature covers, so the store refuses a
    body of any other length; it is signed here, with no call to the store."""
    buckets = s3_impl()
    await buckets.start()
    org = new_id()
    try:
        url = await buckets.presign_part(
            org, Buckets.USER_FILE_UPLOADS, "a.png", "u-1", 3, 5_000_000, timedelta(minutes=5)
        )
    finally:
        await buckets.close()
    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-SignedHeaders"] == ["content-length;host"]
    assert (query["uploadId"], query["partNumber"]) == (["u-1"], ["3"])
    assert urlsplit(url).path == f"/{org}/a.png"
    with pytest.raises(ValueError, match="outside an upload"):
        await buckets.presign_part(
            org, Buckets.EXPORTS, "k", "u-1", MAX_PARTS + 1, 1, timedelta(minutes=1)
        )


async def test_a_presigned_url_names_the_buckets_regional_host() -> None:
    """The global host redirects a new bucket outside us-east-1 until its name
    has spread, and a browser follows no redirect on a cross-origin upload, so
    both a part and a download name the regional host."""
    buckets = s3_impl()
    await buckets.start()
    org = new_id()
    try:
        part = await buckets.presign_part(
            org, Buckets.USER_FILE_UPLOADS, "a.csv", "u-1", 1, 1_000, timedelta(minutes=5)
        )
        download = await buckets.presign_get(org, Buckets.EXPORTS, "e.csv", timedelta(minutes=5))
    finally:
        await buckets.close()
    assert part.startswith("https://t-user-file-uploads.s3.us-west-2.amazonaws.com/")
    assert download.startswith("https://t-exports.s3.us-west-2.amazonaws.com/")


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
            under_way = await s3.list_multipart_uploads(Bucket=name)
            for upload in under_way.get("Uploads", []):
                await s3.abort_multipart_upload(
                    Bucket=name, Key=upload["Key"], UploadId=upload["UploadId"]
                )
            await s3.delete_bucket(Bucket=name)


@pytest.mark.integration
async def test_the_s3_listing_is_bounded_and_resumes(s3_buckets: BucketsS3Impl) -> None:
    await listing_is_bounded_lexical_and_resumes_after(s3_buckets)


@pytest.mark.integration
async def test_the_s3_multipart_upload_lands_whole_from_its_signed_parts(
    s3_buckets: BucketsS3Impl,
) -> None:
    """The store itself refuses the longer part and the short complete."""
    async with httpx.AsyncClient() as store:
        await multipart_upload_lands_whole_from_its_signed_parts(s3_buckets, store)
        await an_aborted_upload_leaves_no_part(s3_buckets, store)


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
    buckets = s3_impl(
        endpoint_url="http://minio:9000",
        region="us-east-1",
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
        part = await buckets.presign_part(
            org, Buckets.USER_FILE_UPLOADS, "a.png", "u-1", 1, 10, timedelta(minutes=1)
        )
    finally:
        await buckets.close()
    assert link.startswith("http://127.0.0.1:59000/t-user-file-uploads/")
    # The headers the store answers with are signed into the link.
    assert "response-content-disposition=inline" in link
    assert "response-content-type=image%2Fpng" in link
    assert part.startswith("http://127.0.0.1:59000/t-user-file-uploads/")
