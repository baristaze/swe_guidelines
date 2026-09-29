from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, delete, func, literal_column, or_, select

from acme.om.base import EMPTY_UUID
from acme.om.media.rules import usage_from_totals
from acme.om.media.storage import MediaStorageInterface
from acme.om.media.storage.tables.files import Files
from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.media.types.usage import StorageUsage
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PgStorageBase, deleted
from acme.om.storage.utils.translation import to_model


class MediaStoragePostgresImpl(PgStorageBase, MediaStorageInterface):
    async def read_file(self, org_id: UUID, file_id: UUID) -> File | None:
        stmt = select(Files).where(Files.org_id == org_id, Files.id == file_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, File)

    async def read_files(
        self,
        org_id: UUID,
        purpose: FilePurpose,
        subject_id: UUID | None,
        status: FileStatus | None,
        after: UUID | None,
        limit: int,
    ) -> list[File]:
        subject = (
            Files.subject_id.is_(None) if subject_id is None else Files.subject_id == subject_id
        )
        stmt = select(Files).where(
            Files.org_id == org_id,
            Files.purpose == purpose.value,
            subject,
            Files.deleted_at.is_(None),
        )
        if status is not None:
            stmt = stmt.where(Files.status == status.value)
        if after is not None:
            stmt = stmt.where(Files.id > after)
        stmt = stmt.order_by(Files.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, File) for row in (await session.execute(stmt)).scalars()]

    async def read_every_file(self, org_id: UUID, after: UUID | None, limit: int) -> list[File]:
        stmt = select(Files).where(Files.org_id == org_id)
        if after is not None:
            stmt = stmt.where(Files.id > after)
        stmt = stmt.order_by(Files.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, File) for row in (await session.execute(stmt)).scalars()]

    async def read_purgeable(
        self, deleted_before: datetime, pending_before: datetime, limit: int
    ) -> list[tuple[UUID, File]]:
        # Mirrors media.rules.is_purgeable in SQL. No order: the batch is any
        # `limit` of the rows the two indexes hold, so a backlog is never
        # sorted to take a batch of it. The pending uploads are named by the
        # partial index's own predicate, a literal, never a bound status: a
        # prepared statement's generic plan proves it and reads the index.
        stmt = (
            select(Files)
            .where(
                or_(
                    Files.deleted_at < deleted_before,
                    and_(
                        Files.deleted_at.is_(None),
                        Files.status == literal_column(f"'{FileStatus.PENDING.value}'"),
                        Files.created_at < pending_before,
                    ),
                ),
            )
            .limit(limit)
        )
        # Every tenant's files past their cut, so the system scope, spelled here.
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return [
                (row.org_id, to_model(row, File)) for row in (await session.execute(stmt)).scalars()
            ]

    async def read_usage(self, org_id: UUID) -> StorageUsage:
        # Mirrors media.rules.usage_of in SQL: the live rows, summed per
        # purpose and status; the rule then lays the totals out.
        stmt = (
            select(Files.purpose, Files.status, func.count(), func.sum(Files.size_bytes))
            .where(Files.org_id == org_id, Files.deleted_at.is_(None))
            .group_by(Files.purpose, Files.status)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            rows = (await session.execute(stmt)).all()
        return usage_from_totals(
            {
                (FilePurpose(purpose), FileStatus(status)): (count, int(size or 0))
                for purpose, status, count, size in rows
            }
        )

    async def create_file(
        self, org_id: UUID, file: File, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(Files, org_id, file, outbox_rows)

    async def write_file(
        self, org_id: UUID, file: File, outbox_rows: tuple[OutboxRow, ...]
    ) -> None:
        await self._upsert(Files, org_id, file, outbox_rows)

    async def purge_files(self, org_id: UUID, file_ids: Sequence[UUID]) -> int:
        if not file_ids:
            return 0
        stmt = (
            delete(Files)
            .where(Files.org_id == org_id, Files.id.in_(list(file_ids)))
            .returning(Files.id)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = len((await session.execute(stmt)).scalars().all())
            await session.commit()
            return purged

    async def purge_files_across_tenants(self, file_ids: Sequence[UUID]) -> int:
        if not file_ids:
            return 0
        stmt = delete(Files).where(Files.id.in_(list(file_ids)))
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
