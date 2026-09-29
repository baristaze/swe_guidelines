"""The media storage contract. The cases named in `CROSS_TENANT_CASES` are the
tenant fence's evidence: each one presents another tenant's identifier and
asserts that nothing is found and nothing changes."""

from datetime import timedelta
from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.exceptions import RowDeleted, TenantMismatch
from acme.om.media.rules import object_key
from acme.om.media.storage import MediaStorageInterface
from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.outbox.types.row import OutboxRow

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "create_file",
        "purge_files",
        "read_every_file",
        "read_file",
        "read_files",
        "read_usage",
        "write_file",
    }
)
"""Every method of `MediaStorageInterface` that takes a tenant has a case in
this module that presents another tenant's."""


def make_file(
    *,
    subject_id: UUID | None = None,
    purpose: FilePurpose = FilePurpose.TASK_ATTACHMENT,
    status: FileStatus = FileStatus.STORED,
    size_bytes: int = 1000,
    created_ago: timedelta = timedelta(0),
) -> File:
    now = utcnow() - created_ago
    file_id = new_id()
    user = new_id()
    return File(
        id=file_id,
        name="notes.pdf",
        created_at=now,
        updated_at=now,
        created_by=user,
        updated_by=user,
        key=object_key(purpose, file_id),
        extension="pdf",
        content_type="application/pdf",
        size_bytes=size_bytes,
        purpose=purpose,
        subject_id=subject_id if purpose is FilePurpose.TASK_ATTACHMENT else None,
        status=status,
    )


def make_row(org_id: UUID, file: File, action: str = "created") -> OutboxRow:
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind=f"media.file.{action}",
        target_id=file.id,
        payload={},
        actor_id=file.created_by,
        request_id=new_id(),
        app="api",
    )


async def seed(storage: MediaStorageInterface, org_id: UUID, file: File) -> File:
    assert await storage.create_file(org_id, file, (make_row(org_id, file),)) is True
    return file


def deleted(file: File, ago: timedelta = timedelta(0)) -> File:
    at = utcnow() - ago
    return file.model_copy(update={"deleted_at": at, "deleted_by": file.created_by})


async def drained(storage: MediaStorageInterface) -> timedelta:
    """How far back a purge case stands: a century, so no other case's file is
    past its cuts, with whatever an earlier run of these cases left behind
    them purged first. The read reaches across tenants, so a case owns the
    files behind its cuts."""
    back = timedelta(days=36500)
    cut = utcnow() - back - timedelta(days=1)
    while left := await storage.read_purgeable(cut, cut, 1000):
        await storage.purge_files_across_tenants([f.id for _, f in left])
    return back


class MediaStorageContract:
    @pytest.fixture
    def storage(self) -> MediaStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip_and_update_by_copy(self, storage: MediaStorageInterface) -> None:
        org = new_id()
        file = await seed(storage, org, make_file(subject_id=new_id(), status=FileStatus.PENDING))
        assert await storage.read_file(org, file.id) == file
        stored = file.model_copy(update={"status": FileStatus.STORED, "updated_at": utcnow()})
        await storage.write_file(org, stored, (make_row(org, stored, "updated"),))
        assert await storage.read_file(org, file.id) == stored

    async def test_a_second_create_of_one_id_reports_it_and_changes_nothing(
        self, storage: MediaStorageInterface
    ) -> None:
        org = new_id()
        file = await seed(storage, org, make_file(subject_id=new_id()))
        again = file.model_copy(update={"name": "other.pdf"})
        assert await storage.create_file(org, again, (make_row(org, again),)) is False
        assert await storage.read_file(org, file.id) == file

    async def test_a_subjects_files_by_id_in_a_status_after_a_cursor(
        self, storage: MediaStorageInterface
    ) -> None:
        org, task, other = new_id(), new_id(), new_id()
        first = await seed(storage, org, make_file(subject_id=task))
        second = await seed(storage, org, make_file(subject_id=task))
        pending = await seed(storage, org, make_file(subject_id=task, status=FileStatus.PENDING))
        await seed(storage, org, make_file(subject_id=other))
        gone = await seed(storage, org, make_file(subject_id=task))
        await storage.write_file(org, deleted(gone), ())
        stored = await storage.read_files(
            org, FilePurpose.TASK_ATTACHMENT, task, FileStatus.STORED, None, 10
        )
        assert [f.id for f in stored] == [first.id, second.id]
        page = await storage.read_files(
            org, FilePurpose.TASK_ATTACHMENT, task, FileStatus.STORED, first.id, 10
        )
        assert [f.id for f in page] == [second.id]
        assert [
            f.id
            for f in await storage.read_files(org, FilePurpose.TASK_ATTACHMENT, task, None, None, 2)
        ] == [first.id, second.id]
        every = await storage.read_files(org, FilePurpose.TASK_ATTACHMENT, task, None, None, 10)
        assert [f.id for f in every] == [first.id, second.id, pending.id]

    async def test_a_purpose_with_no_subject_lists_its_own(
        self, storage: MediaStorageInterface
    ) -> None:
        org = new_id()
        voice = await seed(storage, org, make_file(purpose=FilePurpose.VOICE_DICTATION))
        await seed(storage, org, make_file(subject_id=new_id()))
        listed = await storage.read_files(
            org, FilePurpose.VOICE_DICTATION, None, FileStatus.STORED, None, 10
        )
        assert [f.id for f in listed] == [voice.id]

    async def test_usage_sums_the_live_rows_per_purpose_and_status(
        self, storage: MediaStorageInterface
    ) -> None:
        org = new_id()
        task = new_id()
        await seed(storage, org, make_file(subject_id=task, size_bytes=100))
        await seed(storage, org, make_file(subject_id=task, size_bytes=250))
        await seed(
            storage, org, make_file(subject_id=task, size_bytes=40, status=FileStatus.PENDING)
        )
        await seed(
            storage,
            org,
            make_file(purpose=FilePurpose.VOICE_DICTATION, size_bytes=3_000_000_000),
        )
        gone = await seed(storage, org, make_file(subject_id=task, size_bytes=9999))
        await storage.write_file(org, deleted(gone), ())
        usage = await storage.read_usage(org)
        by = {p.purpose: p for p in usage.purposes}
        assert [p.purpose for p in usage.purposes] == list(FilePurpose)
        assert (
            by[FilePurpose.TASK_ATTACHMENT].count,
            by[FilePurpose.TASK_ATTACHMENT].size_bytes,
        ) == (
            2,
            350,
        )
        assert (
            by[FilePurpose.TASK_ATTACHMENT].pending_count,
            by[FilePurpose.TASK_ATTACHMENT].pending_size_bytes,
        ) == (1, 40)
        # A sum past what 32 bits hold, which is why the column is a bigint.
        assert by[FilePurpose.VOICE_DICTATION].size_bytes == 3_000_000_000
        assert usage.total_count == 3
        assert usage.total_size_bytes == 3_000_000_350

    async def test_an_empty_tenant_uses_nothing_in_every_purpose(
        self, storage: MediaStorageInterface
    ) -> None:
        usage = await storage.read_usage(new_id())
        assert [p.purpose for p in usage.purposes] == list(FilePurpose)
        assert usage.total_count == 0 and usage.total_size_bytes == 0

    async def test_the_purge_reads_the_deleted_past_the_cut_and_the_abandoned(
        self, storage: MediaStorageInterface
    ) -> None:
        """The read and the purge reach across tenants: every tenant's files
        past their cut, each with its tenant, and none other."""
        org, other = new_id(), new_id()
        back = await drained(storage)
        old_delete = await seed(storage, org, make_file(subject_id=new_id()))
        await storage.write_file(org, deleted(old_delete, back + timedelta(days=2)), ())
        fresh_delete = await seed(storage, org, make_file(subject_id=new_id()))
        await storage.write_file(org, deleted(fresh_delete), ())
        abandoned = await seed(
            storage,
            org,
            make_file(
                subject_id=new_id(),
                status=FileStatus.PENDING,
                created_ago=back + timedelta(days=2),
            ),
        )
        await seed(storage, org, make_file(subject_id=new_id(), status=FileStatus.PENDING))
        await seed(
            storage, org, make_file(subject_id=new_id(), created_ago=back + timedelta(days=2))
        )
        theirs = await seed(storage, other, make_file(subject_id=new_id()))
        await storage.write_file(other, deleted(theirs, back + timedelta(days=2)), ())
        cut = utcnow() - back - timedelta(days=1)
        purgeable = await storage.read_purgeable(cut, cut, 10)
        assert sorted((org_id, f.id) for org_id, f in purgeable) == sorted(
            [(org, old_delete.id), (org, abandoned.id), (other, theirs.id)]
        )
        assert len(await storage.read_purgeable(cut, cut, 1)) == 1, "a batch at most"
        assert await storage.purge_files_across_tenants([f.id for _, f in purgeable]) == 3
        assert await storage.read_file(org, old_delete.id) is None
        assert await storage.read_file(other, theirs.id) is None
        assert await storage.read_file(org, fresh_delete.id) is not None
        assert await storage.read_purgeable(cut, cut, 10) == []
        assert await storage.purge_files_across_tenants([old_delete.id]) == 0, "idempotent"

    async def test_every_file_of_a_tenant_pages_by_id(self, storage: MediaStorageInterface) -> None:
        org = new_id()
        first = await seed(storage, org, make_file(subject_id=new_id()))
        second = await seed(storage, org, make_file(subject_id=new_id()))
        await storage.write_file(org, deleted(second), ())
        assert [f.id for f in await storage.read_every_file(org, None, 10)] == [first.id, second.id]
        assert [f.id for f in await storage.read_every_file(org, first.id, 10)] == [second.id]
        assert [f.id for f in await storage.read_every_file(org, None, 1)] == [first.id]

    async def test_a_write_never_brings_a_deleted_row_back(
        self, storage: MediaStorageInterface
    ) -> None:
        org = new_id()
        file = await seed(storage, org, make_file(subject_id=new_id()))
        await storage.write_file(org, deleted(file), ())
        with pytest.raises(RowDeleted):
            await storage.write_file(org, file, ())

    # The tenant fence, one case per method.

    async def test_reads_are_tenant_scoped(self, storage: MediaStorageInterface) -> None:
        org_a, org_b, task = new_id(), new_id(), new_id()
        file = await seed(storage, org_a, make_file(subject_id=task))
        assert await storage.read_file(org_b, file.id) is None
        assert (
            await storage.read_files(org_b, FilePurpose.TASK_ATTACHMENT, task, None, None, 10) == []
        )
        assert await storage.read_every_file(org_b, None, 10) == []
        assert (await storage.read_usage(org_b)).total_count == 0

    async def test_the_tenant_purge_erases_nothing_of_another_tenant(
        self, storage: MediaStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        file = await seed(storage, org_a, make_file(subject_id=new_id()))
        await storage.write_file(org_a, deleted(file, timedelta(days=2)), ())
        assert await storage.purge_files(org_b, [file.id]) == 0
        assert await storage.read_file(org_a, file.id) is not None

    async def test_write_refuses_another_tenant(self, storage: MediaStorageInterface) -> None:
        org_a, org_b = new_id(), new_id()
        file = await seed(storage, org_a, make_file(subject_id=new_id(), status=FileStatus.PENDING))
        stolen = file.model_copy(update={"status": FileStatus.STORED, "name": "stolen.pdf"})
        with pytest.raises(TenantMismatch):
            await storage.write_file(org_b, stolen, (make_row(org_b, stolen, "updated"),))
        assert await storage.read_file(org_a, file.id) == file

    async def test_create_reports_another_tenants_id_and_lands_nothing(
        self, storage: MediaStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        file = await seed(storage, org_a, make_file(subject_id=new_id()))
        stolen = file.model_copy(update={"name": "stolen.pdf"})
        assert await storage.create_file(org_b, stolen, (make_row(org_b, stolen),)) is False
        assert await storage.read_file(org_b, file.id) is None
        assert await storage.read_file(org_a, file.id) == file
