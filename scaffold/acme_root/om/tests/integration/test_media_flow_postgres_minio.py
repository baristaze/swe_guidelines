"""The media flow over the compose stack: the rows in Postgres, the objects in
MinIO, and the bytes moved the way a browser moves them, a form POST to the
presigned URL and a GET of the presigned link. What the store refuses is
refused by the store: a body past the size, a body of another type."""

import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import aioboto3
import httpx
import pytest
from contracts.doubles import Members, context
from contracts.factories import make_org

from acme.infra.buckets import Buckets
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.flags.defaults import FlagsDefaultsImpl
from acme.infra.impl.settings import InfraSettings
from acme.infra.topics.memory import TopicsMemoryImpl
from acme.om.base import new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.events.storage.impl.postgres import EventStoragePostgresImpl
from acme.om.exceptions import NotFound, ValidationFailed
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.media.storage.impl.postgres import MediaStoragePostgresImpl
from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.postgres import OutboxStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7 " + b"x" * 200


def endpoint_of_stack() -> str:
    """The stack's MinIO, from .env: a second stack beside it has another port."""
    return InfraSettings().s3_endpoint_url or "http://127.0.0.1:59000"


def signed_endpoint() -> str:
    """The host a link is signed for: the stack's port, named `localhost`."""
    return endpoint_of_stack().replace("127.0.0.1", "localhost")


@pytest.fixture
async def store() -> AsyncIterator[tuple[BucketsS3Impl, str]]:
    """The S3 impl over MinIO, on an uploads bucket of its own, emptied and
    removed afterwards. It signs for `localhost` while it talks to
    `127.0.0.1`, the way the compose containers talk to `minio:9000` and sign
    for the host port: a signature over the wrong host would not open."""
    settings = InfraSettings()
    session = aioboto3.Session(
        aws_access_key_id=settings.s3_access_key or "acme",
        aws_secret_access_key=settings.s3_secret_key or "acme-minio-local",
        region_name=settings.aws_region,
    )
    endpoint = endpoint_of_stack()
    prefix = f"acme-it-{secrets.token_hex(4)}"
    name = f"{prefix}-{Buckets.USER_FILE_UPLOADS.value}"
    admin: Any = session.client("s3", endpoint_url=endpoint)
    async with admin as s3:
        await s3.create_bucket(Bucket=name)
    impl = BucketsS3Impl(
        session,
        endpoint_url=endpoint,
        region=settings.aws_region,
        bucket_prefix=prefix,
        timeout=timedelta(seconds=10),
        presign_endpoint_url=signed_endpoint(),
    )
    await impl.start()
    try:
        yield impl, name
    finally:
        await impl.close()
        cleanup: Any = session.client("s3", endpoint_url=endpoint)
        async with cleanup as s3:
            listed = await s3.list_object_versions(Bucket=name)
            for item in [*listed.get("Versions", []), *listed.get("DeleteMarkers", [])]:
                await s3.delete_object(Bucket=name, Key=item["Key"], VersionId=item["VersionId"])
            await s3.delete_bucket(Bucket=name)


def manager(
    sessions: LoginSessions, buckets: BucketsS3Impl, options: MediaOptions | None = None
) -> tuple[MediaManagerImpl, MediaStoragePostgresImpl]:
    storage = MediaStoragePostgresImpl(sessions)
    relay = OutboxRelayImpl(
        OutboxStoragePostgresImpl(sessions), EventStoragePostgresImpl(sessions), TopicsMemoryImpl()
    )
    members = Members()  # pyright: ignore[reportAbstractUsage] (a partial double)
    flags = FlagsDefaultsImpl()
    return MediaManagerImpl(
        storage, buckets, members, relay, flags, options or MediaOptions()
    ), storage


def a_file(ctx: TenantContext, size: int = len(PDF), content_type: str = "application/pdf") -> File:
    now = utcnow()
    return File(
        id=new_id(),
        name="plan.pdf",
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        content_type=content_type,
        size_bytes=size,
        purpose=FilePurpose.UPLOAD,
    )


async def post_form(
    media: MediaManagerImpl,
    ctx: TenantContext,
    file: File,
    data: bytes,
    **tampered: str,
) -> httpx.Response:
    """What the browser does with the form: every field in order, then the
    file. `tampered` rewrites a field, as a page that edits its form would."""
    form = await media.issue_upload(ctx, file.id)
    assert form.url is not None and form.url.startswith(signed_endpoint() + "/")
    fields = {**dict(form.fields), **tampered}
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.post(
            form.url, data=fields, files={"file": ("plan.pdf", data, file.content_type)}
        )


async def test_a_file_goes_up_to_the_store_and_comes_down_by_its_link(
    pg_sessions: LoginSessions, store: tuple[BucketsS3Impl, str]
) -> None:
    buckets, _ = store
    media, _ = manager(pg_sessions, buckets)
    ctx = context(Role.MEMBER)
    created = await media.create_file(ctx, a_file(ctx))
    with pytest.raises(ValidationFailed, match="has not arrived"):
        await media.confirm_file(ctx, created.id)
    posted = await post_form(media, ctx, created, PDF)
    assert posted.status_code == 204, posted.text
    stored = await media.confirm_file(ctx, created.id)
    assert stored.status is FileStatus.STORED
    link = await media.issue_download(ctx, stored.id)
    assert link.url is not None and link.url.startswith(signed_endpoint() + "/")
    async with httpx.AsyncClient(timeout=10) as client:
        fetched = await client.get(link.url)
    assert fetched.status_code == 200 and fetched.content == PDF
    assert fetched.headers["content-disposition"] == "attachment; filename*=UTF-8''plan.pdf"
    preview = await media.issue_download(ctx, stored.id, inline=True)
    assert preview.url is not None
    async with httpx.AsyncClient(timeout=10) as client:
        shown = await client.get(preview.url, headers={"Range": "bytes=0-7"})
    # A preview is shown in the page under its type, and a player's range
    # request is answered in part.
    assert shown.status_code == 206 and shown.content == PDF[:8]
    assert shown.headers["content-disposition"] == "inline"
    assert shown.headers["content-type"] == "application/pdf"
    usage = await media.get_usage(ctx)
    assert (usage.total_count, usage.total_size_bytes) == (1, len(PDF))
    other = context(Role.OWNER, make_org())
    with pytest.raises(NotFound):
        await media.issue_download(other, stored.id)


async def test_the_store_refuses_a_body_past_the_size_or_of_another_type(
    pg_sessions: LoginSessions, store: tuple[BucketsS3Impl, str]
) -> None:
    buckets, _ = store
    media, _ = manager(pg_sessions, buckets)
    ctx = context(Role.MEMBER)
    small = await media.create_file(ctx, a_file(ctx, size=10))
    too_long = await post_form(media, ctx, small, PDF)
    assert too_long.status_code == 400 and "EntityTooLarge" in too_long.text
    typed = await media.create_file(ctx, a_file(ctx))
    # The type is a signed field: a form whose type was rewritten is refused.
    retyped = await post_form(media, ctx, typed, PDF, **{"Content-Type": "text/html"})
    assert retyped.status_code == 403, retyped.text
    for refused in (small, typed):
        with pytest.raises(ValidationFailed, match="has not arrived"):
            await media.confirm_file(ctx, refused.id)


async def test_the_sweep_removes_the_object_then_the_row(
    pg_sessions: LoginSessions, store: tuple[BucketsS3Impl, str]
) -> None:
    buckets, _ = store
    media, storage = manager(pg_sessions, buckets)
    ctx = context(Role.MEMBER)
    created = await media.create_file(ctx, a_file(ctx))
    assert (await post_form(media, ctx, created, PDF)).status_code == 204
    await media.confirm_file(ctx, created.id)
    await media.delete_file(ctx, created.id)
    assert await buckets.exists(ctx.org_id, Buckets.USER_FILE_UPLOADS, created.key)
    assert (await media.get_usage(ctx)).total_count == 0, "a delete stops counting at once"
    past, _ = manager(pg_sessions, buckets, MediaOptions(retention=timedelta(0)))
    # The purge reaches across tenants, so other cases' deleted files go too,
    # a batch at a time.
    while await past.purge_across_tenants():
        pass
    assert not await buckets.exists(ctx.org_id, Buckets.USER_FILE_UPLOADS, created.key)
    assert await storage.read_file(ctx.org_id, created.id) is None
