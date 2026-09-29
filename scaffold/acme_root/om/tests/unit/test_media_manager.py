"""The media manager over the memory storage and the local store, and the task
attachments composed on top of it."""

import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.doubles import Members, context, media_of, no_slack, orchestrations_of, request
from contracts.factories import make_org
from contracts.plans import ON_TEAM

from acme.infra.buckets import Buckets
from acme.infra.exceptions import UploadRefused
from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id, utcnow
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.exceptions import NotAuthorized, NotFound, ValidationFailed
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.media.storage.impl.memory import MediaStorageMemoryImpl
from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.opcontext import OpContext, Role
from acme.om.orchestrations.storage.impl.memory import OrchestrationsStorageMemoryImpl
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.tasks.impl.manager import TasksManagerImpl, TasksOptions
from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl
from acme.om.tasks.types.task import Task

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
def tasks(
    outbox: OutboxStorageMemoryImpl,
    members: Members,
    relay: OutboxRelayImpl,
    media: MediaManagerImpl,
) -> TasksManagerImpl:
    return TasksManagerImpl(
        TasksStorageMemoryImpl(outbox),
        members,
        media,
        relay,
        no_slack(),
        TasksOptions(),
        entitlements=ON_TEAM,
        orchestrations=orchestrations_of(OrchestrationsStorageMemoryImpl(), members, relay),
    )


def a_file(
    ctx: OpContext,
    name: str = "plan.pdf",
    *,
    content_type: str = "application/pdf",
    size_bytes: int = len(PDF),
    purpose: FilePurpose = FilePurpose.TASK_ATTACHMENT,
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


def make_task(ctx: OpContext, title: str = "Ship it") -> Task:
    now = utcnow()
    return Task(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        title=title,
    )


async def uploaded(media: MediaManagerImpl, ctx: OpContext, file: File, data: bytes = PDF) -> File:
    """The whole flow over the local store: start, ask for a form (the local
    store cannot presign, so it has no URL), move the bytes, confirm."""
    created = await media.create_file(ctx, file)
    form = await media.issue_upload(ctx, created.id)
    assert form.url is None
    await media.put_content(ctx, created.id, data)
    return await media.confirm_file(ctx, created.id)


async def test_an_upload_starts_pending_under_the_managers_key(media: MediaManagerImpl) -> None:
    ctx = context(Role.MEMBER)
    task_id = new_id()
    sent = a_file(ctx, "Quarterly Plan.PDF", subject_id=task_id).model_copy(
        update={"key": "../elsewhere", "status": FileStatus.STORED, "created_by": new_id()}
    )
    created = await media.create_file(ctx, sent)
    assert created.status is FileStatus.PENDING
    assert created.key == f"media/task_attachment/{created.id}"
    assert created.extension == "pdf"
    assert created.created_by == ctx.user_id
    assert created.name == "Quarterly Plan.PDF"


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
    file = a_file(ctx, name, content_type=content_type, size_bytes=size, subject_id=new_id())
    with pytest.raises(ValidationFailed, match=re.escape(why)):
        await media.create_file(ctx, file)
    assert (await media.get_usage(ctx)).pending_size_bytes == 0


async def test_a_purpose_names_a_subject_or_none(media: MediaManagerImpl) -> None:
    ctx = context(Role.MEMBER)
    with pytest.raises(ValidationFailed, match="names subject"):
        await media.create_file(ctx, a_file(ctx))
    with pytest.raises(ValidationFailed, match="has no subject"):
        await media.create_file(
            ctx,
            a_file(
                ctx,
                "memo.webm",
                content_type="audio/webm",
                purpose=FilePurpose.VOICE_DICTATION,
                subject_id=new_id(),
            ),
        )


async def test_a_viewer_cannot_start_an_upload(media: MediaManagerImpl) -> None:
    ctx = context(Role.VIEWER)
    with pytest.raises(NotAuthorized):
        await media.create_file(ctx, a_file(ctx, subject_id=new_id()))


async def test_the_whole_flow_stores_the_bytes_and_lists_the_file(
    media: MediaManagerImpl, infra: InfraLocalImpl
) -> None:
    ctx = context(Role.MEMBER)
    task_id = new_id()
    stored = await uploaded(media, ctx, a_file(ctx, subject_id=task_id))
    assert stored.status is FileStatus.STORED
    page = await media.get_files(ctx, FilePurpose.TASK_ATTACHMENT, task_id, None, 10)
    assert [f.id for f in page.items] == [stored.id] and not page.has_more
    link = await media.issue_download(ctx, stored.id)
    assert link.url is None  # the local store cannot presign; the bytes come through the API
    assert await media.get_content(ctx, stored.id) == PDF
    assert await infra.get_buckets().get(ctx.org_id, Buckets.USER_FILE_UPLOADS, stored.key) == PDF
    # A second confirm answers the file as it is.
    assert await media.confirm_file(ctx, stored.id) == stored


async def test_a_confirm_before_the_object_arrives_is_refused(media: MediaManagerImpl) -> None:
    ctx = context(Role.MEMBER)
    created = await media.create_file(ctx, a_file(ctx, subject_id=new_id()))
    with pytest.raises(ValidationFailed, match="has not arrived"):
        await media.confirm_file(ctx, created.id)
    assert (await media.get_file(ctx, created.id)).status is FileStatus.PENDING
    with pytest.raises(ValidationFailed, match="has not been uploaded"):
        await media.issue_download(ctx, created.id)


async def test_the_bytes_are_held_to_the_size_and_type_the_upload_named(
    media: MediaManagerImpl, infra: InfraLocalImpl
) -> None:
    ctx = context(Role.MEMBER)
    created = await media.create_file(ctx, a_file(ctx, size_bytes=4, subject_id=new_id()))
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
    created = await media.create_file(ann, a_file(ann, subject_id=new_id()))
    for attempt in (
        media.issue_upload(bob, created.id),
        media.put_content(bob, created.id, PDF),
        media.confirm_file(bob, created.id),
    ):
        with pytest.raises(NotAuthorized):
            await attempt


async def test_another_tenants_file_answers_as_a_missing_one(media: MediaManagerImpl) -> None:
    ann, eve = context(Role.MEMBER), context(Role.OWNER)
    stored = await uploaded(media, ann, a_file(ann, subject_id=new_id()))
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
    task_id = new_id()
    first = await uploaded(media, ctx, a_file(ctx, subject_id=task_id))
    await uploaded(
        media,
        ctx,
        a_file(ctx, "b.txt", content_type="text/plain", size_bytes=3, subject_id=task_id),
        b"abc",
    )
    await media.create_file(ctx, a_file(ctx, size_bytes=500, subject_id=task_id))
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
    media = MediaManagerImpl(storage, buckets, members, relay, MediaOptions())
    ctx = context(Role.MEMBER)
    gone = await uploaded(media, ctx, a_file(ctx, subject_id=new_id()))
    kept = await uploaded(media, ctx, a_file(ctx, subject_id=new_id()))
    abandoned = await media.create_file(ctx, a_file(ctx, subject_id=new_id()))
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
    kept = await uploaded(media, ctx, a_file(ctx, subject_id=new_id()))
    members.expired = True
    assert await media.purge_tenant(ctx) == 1
    assert not await infra.get_buckets().exists(ctx.org_id, Buckets.USER_FILE_UPLOADS, kept.key)


# Task attachments: the tasks namespace composes media.


async def test_a_file_attached_to_a_task_is_a_task_attachment_of_that_task(
    tasks: TasksManagerImpl, media: MediaManagerImpl
) -> None:
    ctx = context(Role.MEMBER)
    task = await tasks.create_task(ctx, make_task(ctx))
    sent = a_file(ctx, purpose=FilePurpose.TASK_ATTACHMENT, subject_id=new_id())
    attached = await tasks.attach_file(ctx, task.id, sent)
    assert (attached.purpose, attached.subject_id) == (FilePurpose.TASK_ATTACHMENT, task.id)
    assert (await tasks.get_attachments(ctx, task.id, None, 10)).items == ()
    await media.issue_upload(ctx, attached.id)
    await media.put_content(ctx, attached.id, PDF)
    stored = await media.confirm_file(ctx, attached.id)
    assert (await tasks.get_attachments(ctx, task.id, None, 10)).items == (stored,)


async def test_a_missing_or_foreign_task_takes_no_attachment(tasks: TasksManagerImpl) -> None:
    ann, eve = context(Role.MEMBER), context(Role.MEMBER)
    task = await tasks.create_task(ann, make_task(ann))
    for attempt in (
        tasks.attach_file(ann, new_id(), a_file(ann)),
        tasks.attach_file(eve, task.id, a_file(eve)),
        tasks.get_attachments(eve, task.id, None, 10),
    ):
        with pytest.raises(NotFound):
            await attempt


async def test_an_attachment_is_removed_from_its_own_task_only(
    tasks: TasksManagerImpl, media: MediaManagerImpl
) -> None:
    ctx = context(Role.MEMBER)
    one = await tasks.create_task(ctx, make_task(ctx, "one"))
    two = await tasks.create_task(ctx, make_task(ctx, "two"))
    attached = await uploaded(media, ctx, a_file(ctx, subject_id=one.id))
    with pytest.raises(NotFound):
        await tasks.remove_attachment(ctx, two.id, attached.id)
    removed = await tasks.remove_attachment(ctx, one.id, attached.id)
    assert removed.deleted_at is not None
    assert (await tasks.get_attachments(ctx, one.id, None, 10)).items == ()


async def test_deleting_a_task_deletes_its_attachments(
    tasks: TasksManagerImpl, media: MediaManagerImpl
) -> None:
    ctx = context(Role.MEMBER)
    task = await tasks.create_task(ctx, make_task(ctx))
    other = await tasks.create_task(ctx, make_task(ctx, "other"))
    await uploaded(media, ctx, a_file(ctx, subject_id=task.id))
    await media.create_file(ctx, a_file(ctx, subject_id=task.id))  # pending, gone too
    kept = await uploaded(media, ctx, a_file(ctx, subject_id=other.id))
    await tasks.delete_task(ctx, task.id, task.version)
    usage = await media.get_usage(ctx)
    assert (usage.total_count, usage.pending_size_bytes) == (1, 0)
    assert (await tasks.get_attachments(ctx, other.id, None, 10)).items == (kept,)


async def test_a_task_delete_stands_when_its_attachments_cannot_follow(
    outbox: OutboxStorageMemoryImpl,
    members: Members,
    relay: OutboxRelayImpl,
    media: MediaManagerImpl,
) -> None:
    class Failing(MediaManagerImpl):
        async def delete_subject_files(
            self, ctx: OpContext, purpose: FilePurpose, subject_id: UUID
        ) -> int:
            raise RuntimeError("storage down")

    failing = Failing(
        MediaStorageMemoryImpl(outbox),
        media._buckets,
        members,
        relay,
        MediaOptions(),  # type: ignore[attr-defined]
    )
    tasks = TasksManagerImpl(
        TasksStorageMemoryImpl(outbox),
        members,
        failing,
        relay,
        no_slack(),
        TasksOptions(),
        entitlements=ON_TEAM,
        orchestrations=orchestrations_of(OrchestrationsStorageMemoryImpl(), members, relay),
    )
    ctx = context(Role.MEMBER)
    task = await tasks.create_task(ctx, make_task(ctx))
    deleted = await tasks.delete_task(ctx, task.id, task.version)
    assert deleted.deleted_at is not None
    with pytest.raises(NotFound):
        await tasks.get_task(ctx, task.id)


async def test_the_task_purge_deletes_the_attachments_a_failed_detach_left_first(
    outbox: OutboxStorageMemoryImpl,
    members: Members,
    relay: OutboxRelayImpl,
    infra: InfraLocalImpl,
) -> None:
    """A delete whose detach failed leaves the task's files live. The purge
    asks the media manager to delete them before it takes the task; while
    that still fails, the task and its files both stay for the next pass,
    and once it works the files are deleted first and the task goes after.
    The media sweep then erases each object and its row."""
    files = MediaStorageMemoryImpl(outbox)
    order: list[str] = []

    class Flaky(MediaManagerImpl):
        down = True

        async def delete_subject_files(
            self, ctx: OpContext, purpose: FilePurpose, subject_id: UUID
        ) -> int:
            if self.down:
                raise RuntimeError("storage down")
            deleted = await super().delete_subject_files(ctx, purpose, subject_id)
            order.append("files")
            return deleted

    class Recorded(TasksStorageMemoryImpl):
        async def purge_deleted(self, before: datetime, task_ids: list[UUID]) -> int:
            order.append("task")
            return await super().purge_deleted(before, task_ids)

    flaky = Flaky(files, infra.get_buckets(), members, relay, MediaOptions())
    storage = Recorded(outbox)
    tasks = TasksManagerImpl(
        storage,
        members,
        flaky,
        relay,
        no_slack(),
        TasksOptions(retention=timedelta(0)),
        entitlements=ON_TEAM,
        orchestrations=orchestrations_of(OrchestrationsStorageMemoryImpl(), members, relay),
    )
    ctx = context(Role.MEMBER)
    task = await tasks.create_task(ctx, make_task(ctx))
    attached = await uploaded(flaky, ctx, a_file(ctx, subject_id=task.id))
    await tasks.delete_task(ctx, task.id, task.version)
    left = await files.read_file(ctx.org_id, attached.id)
    assert left is not None and left.deleted_at is None, "the detach failed"

    assert await tasks.purge_across_tenants(request()) == 0, (
        "the files would not go, so the task stays"
    )
    assert await storage.read_task(ctx.org_id, task.id) is not None
    still = await files.read_file(ctx.org_id, attached.id)
    assert still is not None and still.deleted_at is None

    flaky.down = False
    assert await tasks.purge_across_tenants(request()) == 1
    assert order[-2:] == ["files", "task"], "the files first, then the task"
    assert await storage.read_task(ctx.org_id, task.id) is None
    gone = await files.read_file(ctx.org_id, attached.id)
    assert gone is not None and gone.deleted_at is not None
    assert (await flaky.get_usage(ctx)).total_count == 0

    erase = MediaManagerImpl(
        files, infra.get_buckets(), members, relay, MediaOptions(retention=timedelta(0))
    )
    assert await erase.purge_across_tenants() == 1
    bucket = Buckets.USER_FILE_UPLOADS
    assert not await infra.get_buckets().exists(ctx.org_id, bucket, attached.key)
    assert await files.read_file(ctx.org_id, attached.id) is None
