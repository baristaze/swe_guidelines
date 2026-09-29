from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.media.rules import is_purgeable, usage_of
from acme.om.media.storage import MediaStorageInterface
from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.media.types.usage import StorageUsage
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class MediaStorageMemoryImpl(MemoryStorageBase, MediaStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._files: MemoryTable[File] = {}

    async def read_file(self, org_id: UUID, file_id: UUID) -> File | None:
        return self._get(self._files, org_id, file_id)

    async def read_files(
        self,
        org_id: UUID,
        purpose: FilePurpose,
        subject_id: UUID | None,
        status: FileStatus | None,
        after: UUID | None,
        limit: int,
    ) -> list[File]:
        return [
            f
            for f in self._rows(self._files, org_id)
            if f.purpose is purpose
            and f.subject_id == subject_id
            and f.deleted_at is None
            and (status is None or f.status is status)
            and (after is None or f.id > after)
        ][:limit]

    async def read_every_file(self, org_id: UUID, after: UUID | None, limit: int) -> list[File]:
        return [f for f in self._rows(self._files, org_id) if after is None or f.id > after][:limit]

    async def read_purgeable(
        self, deleted_before: datetime, pending_before: datetime, limit: int
    ) -> list[tuple[UUID, File]]:
        return [
            (org_id, f)
            for org_id, f in self._rows_across_tenants(self._files)
            if is_purgeable(f, deleted_before, pending_before)
        ][:limit]

    async def read_usage(self, org_id: UUID) -> StorageUsage:
        return usage_of(self._rows(self._files, org_id))

    async def create_file(
        self, org_id: UUID, file: File, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            return self._insert(self._files, org_id, file, outbox_rows)

    async def write_file(
        self, org_id: UUID, file: File, outbox_rows: tuple[OutboxRow, ...]
    ) -> None:
        async with self._lock:
            self._put(self._files, org_id, file, outbox_rows)

    async def purge_files(self, org_id: UUID, file_ids: Sequence[UUID]) -> int:
        async with self._lock:
            gone = [
                i for i in dict.fromkeys(file_ids) if self._get(self._files, org_id, i) is not None
            ]
            for file_id in gone:
                del self._files[file_id]
            return len(gone)

    async def purge_files_across_tenants(self, file_ids: Sequence[UUID]) -> int:
        async with self._lock:
            gone = [i for i in dict.fromkeys(file_ids) if i in self._files]
            for file_id in gone:
                del self._files[file_id]
            return len(gone)
