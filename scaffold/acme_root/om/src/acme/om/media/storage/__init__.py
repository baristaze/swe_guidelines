"""Storage of the media swimlane: the file rows, never the bytes. Every
operation takes org_id first."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.media.types.usage import StorageUsage
from acme.om.outbox.types.row import OutboxRow


class MediaStorageInterface(ABC):
    @abstractmethod
    async def read_file(self, org_id: UUID, file_id: UUID) -> File | None:
        """The row as stored, deleted or not; None when the tenant has none."""
        ...

    @abstractmethod
    async def read_files(
        self,
        org_id: UUID,
        purpose: FilePurpose,
        subject_id: UUID | None,
        status: FileStatus | None,
        after: UUID | None,
        limit: int,
    ) -> list[File]:
        """The live files of one subject under one purpose, in the given status
        or any, by id, strictly after `after`, at most `limit` of them."""
        ...

    @abstractmethod
    async def read_every_file(self, org_id: UUID, after: UUID | None, limit: int) -> list[File]:
        """Every row of the tenant, live or deleted, by id, strictly after
        `after`: what the sweep erases of a tenant past its retention."""
        ...

    @abstractmethod
    async def read_purgeable(
        self, deleted_before: datetime, pending_before: datetime, limit: int
    ) -> list[tuple[UUID, File]]:
        """Cross-tenant, for the sweep, in the system scope: the rows
        `media.rules.is_purgeable` names, at most `limit`, whatever their
        tenant, each with its tenant: deleted before one cut, or pending since
        before the other. One read a pass for every tenant, so a tenant with
        nothing to purge costs nothing."""
        ...

    @abstractmethod
    async def read_usage(self, org_id: UUID) -> StorageUsage:
        """The live files summed per purpose and status
        (`media.rules.usage_of`)."""
        ...

    @abstractmethod
    async def create_file(
        self, org_id: UUID, file: File, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create: lands the row and the rows that announce it together, or
        neither when the id is already written, which it reports as False."""
        ...

    @abstractmethod
    async def write_file(
        self, org_id: UUID, file: File, outbox_rows: tuple[OutboxRow, ...]
    ) -> None:
        """The update by copy: the confirm and the soft delete. Refuses another
        tenant's row and never brings a deleted one back."""
        ...

    @abstractmethod
    async def purge_files(self, org_id: UUID, file_ids: Sequence[UUID]) -> int:
        """The one hard delete: removes the tenant's rows of these ids; returns
        how many went. The sweep calls it after the objects are gone."""
        ...

    @abstractmethod
    async def purge_files_across_tenants(self, file_ids: Sequence[UUID]) -> int:
        """Cross-tenant, for the sweep, in the system scope: `purge_files` for
        the rows `read_purgeable` gave, whatever their tenant, in one
        statement; returns how many went. The sweep calls it after the
        objects are gone."""
        ...
