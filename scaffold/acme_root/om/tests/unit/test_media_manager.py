"""The media manager over the memory storage and the local store, and the
files of a subject another namespace composes it for."""

import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.doubles import Members, context, media_of
from contracts.factories import make_org

from acme.infra.buckets import Buckets
from acme.infra.exceptions import UploadRefused
from acme.infra.flags import Flag
from acme.infra.flags.memory import FlagRule, FlagsMemoryImpl
from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.exceptions import FeatureOff, NotAuthorized, NotFound, ValidationFailed
from acme.om.media import rules
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.media.storage.impl.memory import MediaStorageMemoryImpl
from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl

PDF = b"%PDF-1.7 a small file"


@pytest.fixture
def infra(tmp_path: Path) -> InfraLocalImpl:
    return InfraLocalImpl(tmp_path)


@pytest.fixture
def members() -> Members:
    return Members()  # pyright: ignore[reportAbstractUsage] (a partial double)


@pytest.fixture
def outbox() -> OutboxStorageMemoryImpl:
    return OutboxStorageMemoryImpl()


@pytest.fixture
def relay(outbox: OutboxStorageMemoryImpl, infra: InfraLocalImpl) -> OutboxRelayImpl:
    return OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics())


@pytest.fixture
def media(
    outbox: OutboxStorageMemoryImpl, members: Members, relay: OutboxRelayImpl, infra: InfraLocalImpl
) -> MediaManagerImpl:
    return media_of(outbox, members, relay, infra)


@pytest.fixture
def with_subjects(monkeypatch: pytest.MonkeyPatch) -> None:
    """The purpose as a product's purpose whose files belong to a record of
    another namespace, which `subject_id` names."""
    monkeypatch.setattr(rules, "SUBJECT_REQUIRED", frozenset({FilePurpose.UPLOAD}))


def a_file(
    ctx: TenantContext,
    name: str = "plan.pdf",
    *,
    content_type: str = "application/pdf",
    size_bytes: int = len(PDF),
    purpose: FilePurpose = FilePurpose.UPLOAD,
    subject_id: UUID | None = None,
) -> File:
    now = utcnow()
    return File(
        id=new_id(),
        name=name,
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        content_type=content_type,
        size_bytes=size_bytes,
        purpose=purpose,
        subject_id=subject_id,
    )


async def uploaded(
    media: MediaManagerImpl, ctx: TenantContext, file: File, data: bytes = PDF
) -> File:
    """The whole flow over the local store: start, ask for a form (the local
    store cannot presign, so it has no URL), move the bytes, confirm."""
    created = await media.create_file(ctx, file)
    form = await media.issue_upload(ctx, created.id)
    assert form.url is None
    await media.put_content(ctx, created.id, data)
    return await media.confirm_file(ctx, created.id)


async def test_an_upload_starts_pending_under_the_managers_key(media: MediaManagerImpl) -> None:
    ctx = context(Role.MEMBER)
    sent = a_file(ctx, "Quarterly Plan.PDF").model_copy(
        update={"key": "../elsewhere", "status": FileStatus.STORED, "created_by": new_id()}
    )
    created = await media.create_file(ctx, sent)
    assert created.status is FileStatus.PENDING
    assert created.key == f"media/upload/{created.id}"
    assert created.extension == "pdf"
    assert created.created_by == ctx.user_id
    assert created.name == "Quarterly Plan.PDF"


async def test_an_upload_is_refused_where_media_uploads_is_off_and_taken_where_it_is_on(
    outbox: OutboxStorageMemoryImpl,
    members: Members,
    relay: OutboxRelayImpl,
    infra: InfraLocalImpl,
) -> None:
    """Two orgs in one process, the flag off for one: the refusal is the
    server's, whatever a client shows."""
    off, on = context(Role.MEMBER), context(Role.MEMBER)
    flags = FlagsMemoryImpl({Flag.MEDIA_UPLOADS.value: FlagRule(orgs={off.org_id: False})})
    storage = MediaStorageMemoryImpl(outbox)
    media = MediaManagerImpl(storage, infra.get_buckets(), members, relay, flags, MediaOptions())
    refused = a_file(off)
    with pytest.raises(FeatureOff) as raised:
        await media.create_file(off, refused)
    assert raised.value.code == "feature_off"
    assert await storage.read_file(off.org_id, refused.id) is None
    created = await media.create_file(on, a_file(on))
    assert created.status is FileStatus.PENDING


@pytest.mark.parametrize(
    ("name", "content_type", "size", "why"),
    [
        ("page.html", "text/html", 10, "cannot be of type text/html"),
        ("drawing.svg", "image/svg+xml", 10, "cannot be of type image/svg+xml"),
        ("plan.exe", "application/pdf", 10, "ends in .pdf"),
        ("../plan.pdf", "application/pdf", 10, "not a path"),
        ("plan.pdf", "application/pdf", 0, "at least one byte"),
        ("plan.pdf", "application/pdf", 100 * 1024 * 1024 + 1, "at most"),
        ("", "application/pdf", 10, "1 to 255"),
    ],
)
async def test_an_upload_outside_its_purposes_bounds_is_refused(
    media: MediaManagerImpl, name: str, content_type: str, size: int, why: str
) -> None:
    ctx = context(Role.MEMBER)
    file = a_file(ctx, name, content_type=content_type, size_bytes=size)
    with pytest.raises(ValidationFailed, match=re.escape(why)):
        await media.create_file(ctx, file)
    assert (await media.get_usage(ctx)).pending_size_bytes == 0


async def test_a_purpose_names_a_subject_or_none(
    media: MediaManagerImpl, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(Role.MEMBER)
    with pytest.raises(ValidationFailed, match="has no subject"):
        await media.create_file(ctx, a_file(ctx, subject_id=new_id()))
    monkeypatch.setattr(rules, "SUBJECT_REQUIRED", frozenset({FilePurpose.UPLOAD}))
    with pytest.raises(ValidationFailed, match="names subject"):
        await media.create_file(ctx, a_file(ctx))


async def test_a_viewer_cannot_start_an_upload(media: MediaManagerImpl) -> None:
    ctx = context(Role.VIEWER)
    with pytest.raises(NotAuthorized):
        await media.create_file(ctx, a_file(ctx))


async def test_the_whole_flow_stores_the_bytes_and_lists_the_file(
    media: MediaManagerImpl, infra: InfraLocalImpl
) -> None:
    ctx = context(Role.MEMBER)
    stored = await uploaded(media, ctx, a_file(ctx))
    assert stored.status is FileStatus.STORED
    page = await media.get_files(ctx, FilePurpose.UPLOAD, None, None, 10)
    assert [f.id for f in page.items] == [stored.id] and not page.has_more
    link = await media.issue_download(ctx, stored.id)
    assert link.url is None  # the local store cannot presign; the bytes come through the API
    assert await media.get_content(ctx, stored.id) == PDF
    assert await infra.get_buckets().get(ctx.org_id, Buckets.USER_FILE_UPLOADS, stored.key) == PDF
    # A second confirm answers the file as it is.
    assert await media.confirm_file(ctx, stored.id) == stored


async def test_a_confirm_before_the_object_arrives_is_refused(media: MediaManagerImpl) -> None:
    ctx = context(Role.MEMBER)
    created = await media.create_file(ctx, a_file(ctx))
    with pytest.raises(ValidationFailed, match="has not arrived"):
        await media.confirm_file(ctx, created.id)
    assert (await media.get_file(ctx, created.id)).status is FileStatus.PENDING
    with pytest.raises(ValidationFailed, match="has not been uploaded"):
        await media.issue_download(ctx, created.id)


async def test_the_bytes_are_held_to_the_size_and_type_the_upload_named(
    media: MediaManagerImpl, infra: InfraLocalImpl
) -> None:
    ctx = context(Role.MEMBER)
    created = await media.create_file(ctx, a_file(ctx, size_bytes=4))
    await media.issue_upload(ctx, created.id)
    with pytest.raises(ValidationFailed, match="over 4"):
        await media.put_content(ctx, created.id, b"12345")
    # The store holds the presigned key to the same bounds, as S3 holds a form.
    buckets = infra.get_buckets()
    with pytest.raises(UploadRefused):
        await buckets.put(
            ctx.org_id, Buckets.USER_FILE_UPLOADS, created.key, b"12345", "application/pdf"
        )
    with pytest.raises(UploadRefused):
        await buckets.put(ctx.org_id, Buckets.USER_FILE_UPLOADS, created.key, b"1", "text/html")


async def test_an_upload_is_its_starters(media: MediaManagerImpl) -> None:
    org = make_org()
    ann, bob = context(Role.MEMBER, org), context(Role.MEMBER, org)
    created = await media.create_file(ann, a_file(ann))
    for attempt in (
        media.issue_upload(bob, created.id),
        media.put_content(bob, created.id, PDF),
        media.confirm_file(bob, created.id),
    ):
        with pytest.raises(NotAuthorized):
            await attempt


async def test_another_tenants_file_answers_as_a_missing_one(media: MediaManagerImpl) -> None:
    ann, eve = context(Role.MEMBER), context(Role.OWNER)
    stored = await uploaded(media, ann, a_file(ann))
    for attempt in (
        media.get_file(eve, stored.id),
        media.issue_download(eve, stored.id),
        media.get_content(eve, stored.id),
        media.issue_upload(eve, stored.id),
        media.confirm_file(eve, stored.id),
        media.delete_file(eve, stored.id),
    ):
        with pytest.raises(NotFound):
            await attempt
    assert (await media.get_usage(eve)).total_count == 0
    assert (await media.get_file(ann, stored.id)).deleted_at is None


async def test_usage_counts_the_live_files_and_a_delete_stops_counting_at_once(
    media: MediaManagerImpl,
) -> None:
    ctx = context(Role.MEMBER)
    first = await uploaded(media, ctx, a_file(ctx))
    await uploaded(
        media, ctx, a_file(ctx, "b.txt", content_type="text/plain", size_bytes=3), b"abc"
    )
    await media.create_file(ctx, a_file(ctx, size_bytes=500))
    usage = await media.get_usage(ctx)
    assert (usage.total_count, usage.total_size_bytes, usage.pending_size_bytes) == (
        2,
        len(PDF) + 3,
        500,
    )
    await media.delete_file(ctx, first.id)
    usage = await media.get_usage(ctx)
    assert (usage.total_count, usage.total_size_bytes) == (1, 3)
    with pytest.raises(NotFound):
        await media.get_file(ctx, first.id)


async def test_the_sweep_erases_the_object_and_the_row_past_the_retention(
    outbox: OutboxStorageMemoryImpl,
    members: Members,
    relay: OutboxRelayImpl,
    infra: InfraLocalImpl,
) -> None:
    storage = MediaStorageMemoryImpl(outbox)
    buckets = infra.get_buckets()
    media = MediaManagerImpl(storage, buckets, members, relay, infra.get_flags(), MediaOptions())
    ctx = context(Role.MEMBER)
    gone = await uploaded(media, ctx, a_file(ctx))
    kept = await uploaded(media, ctx, a_file(ctx))
    abandoned = await media.create_file(ctx, a_file(ctx))
    await media.issue_upload(ctx, abandoned.id)
    await media.put_content(ctx, abandoned.id, PDF)  # uploaded, never confirmed
    await media.delete_file(ctx, gone.id)
    assert await media.purge_across_tenants() == 0, "neither window has passed"
    assert await media.purge_tenant(ctx) == 0, "a living tenant keeps its files"
    past = MediaManagerImpl(
        storage,
        buckets,
        members,
        relay,
        infra.get_flags(),
        MediaOptions(retention=timedelta(0), pending_expiry=timedelta(0)),
    )
    assert await past.purge_across_tenants() == 2
    bucket = Buckets.USER_FILE_UPLOADS
    assert not await buckets.exists(ctx.org_id, bucket, gone.key)
    assert not await buckets.exists(ctx.org_id, bucket, abandoned.key)
    assert await buckets.exists(ctx.org_id, bucket, kept.key)
    assert await storage.read_file(ctx.org_id, gone.id) is None
    assert await storage.read_file(ctx.org_id, kept.id) is not None


async def test_a_tenant_past_its_retention_loses_every_file(
    media: MediaManagerImpl, members: Members, infra: InfraLocalImpl
) -> None:
    ctx = context(Role.MEMBER)
    kept = await uploaded(media, ctx, a_file(ctx))
    members.expired = True
    assert await media.purge_tenant(ctx) == 1
    assert not await infra.get_buckets().exists(ctx.org_id, Buckets.USER_FILE_UPLOADS, kept.key)


# A subject's files: what another namespace composes media for.


async def test_a_subjects_stored_files_list_apart_from_another_subjects(
    media: MediaManagerImpl, with_subjects: None
) -> None:
    ctx = context(Role.MEMBER)
    one, two = new_id(), new_id()
    stored = await uploaded(media, ctx, a_file(ctx, subject_id=one))
    await media.create_file(ctx, a_file(ctx, subject_id=one))  # pending, not listed
    other = await uploaded(media, ctx, a_file(ctx, subject_id=two))
    assert (await media.get_files(ctx, FilePurpose.UPLOAD, one, None, 10)).items == (stored,)
    assert (await media.get_files(ctx, FilePurpose.UPLOAD, two, None, 10)).items == (other,)


async def test_a_subjects_files_go_with_it_and_no_other_subjects(
    media: MediaManagerImpl, with_subjects: None
) -> None:
    """What a namespace calls when its record goes: every live file of the
    subject, pending or stored, is deleted, and another subject's are kept."""
    ctx = context(Role.MEMBER)
    one, two = new_id(), new_id()
    await uploaded(media, ctx, a_file(ctx, subject_id=one))
    await media.create_file(ctx, a_file(ctx, subject_id=one))  # pending, gone too
    kept = await uploaded(media, ctx, a_file(ctx, subject_id=two))
    assert await media.delete_subject_files(ctx, FilePurpose.UPLOAD, one) == 2
    usage = await media.get_usage(ctx)
    assert (usage.total_count, usage.pending_size_bytes) == (1, 0)
    assert (await media.get_files(ctx, FilePurpose.UPLOAD, two, None, 10)).items == (kept,)
    assert await media.delete_subject_files(ctx, FilePurpose.UPLOAD, one) == 0, "idempotent"


async def test_another_tenants_subject_deletes_nothing(
    media: MediaManagerImpl, with_subjects: None
) -> None:
    ann, eve = context(Role.MEMBER), context(Role.OWNER)
    subject = new_id()
    stored = await uploaded(media, ann, a_file(ann, subject_id=subject))
    assert await media.delete_subject_files(eve, FilePurpose.UPLOAD, subject) == 0
    assert (await media.get_files(eve, FilePurpose.UPLOAD, subject, None, 10)).items == ()
    assert (await media.get_file(ann, stored.id)).deleted_at is None


async def test_a_viewer_cannot_delete_a_subjects_files(
    media: MediaManagerImpl, with_subjects: None
) -> None:
    org = make_org()
    ann, viewer = context(Role.MEMBER, org), context(Role.VIEWER, org)
    subject = new_id()
    stored = await uploaded(media, ann, a_file(ann, subject_id=subject))
    with pytest.raises(NotAuthorized):
        await media.delete_subject_files(viewer, FilePurpose.UPLOAD, subject)
    assert (await media.get_file(ann, stored.id)).deleted_at is None
