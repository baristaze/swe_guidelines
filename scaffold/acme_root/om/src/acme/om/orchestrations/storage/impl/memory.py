from datetime import datetime
from uuid import UUID

from acme.om.exceptions import PreconditionFailed
from acme.om.orchestrations.rules import is_settled
from acme.om.orchestrations.storage import OrchestrationsStorageInterface, StepLandingInterface
from acme.om.orchestrations.types.orchestration import (
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    ParkReason,
    Step,
)
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class OrchestrationsStorageMemoryImpl(
    MemoryStorageBase, OrchestrationsStorageInterface, StepLandingInterface
):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._records: MemoryTable[Orchestration] = {}

    async def create_orchestration(
        self, org_id: UUID, record: Orchestration, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            if record.period is not None and any(
                r.kind == record.kind and r.period == record.period
                for r in self._rows(self._records, org_id)
            ):
                return False  # the (org, kind, period) key, as the index is in Postgres
            return self._insert(self._records, org_id, record, outbox_rows)

    async def read_orchestration(self, org_id: UUID, record_id: UUID) -> Orchestration | None:
        return self._get(self._records, org_id, record_id)

    async def read_recent(
        self, org_id: UUID, kind: OrchestrationKind, limit: int
    ) -> list[Orchestration]:
        found = [r for r in self._rows(self._records, org_id) if r.kind == kind]
        return sorted(found, key=lambda r: r.id, reverse=True)[:limit]

    async def read_parked(
        self, org_id: UUID, reason: ParkReason, limit: int, after: UUID | None = None
    ) -> list[Orchestration]:
        found = [
            r
            for r in self._rows(self._records, org_id)
            if r.status is OrchestrationStatus.PARKED
            and r.park_reason is reason
            and (after is None or r.id > after)
        ]
        return sorted(found, key=lambda r: r.id)[:limit]

    async def write_orchestration(
        self,
        org_id: UUID,
        record: Orchestration,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._lock:
            self._check(org_id, record.id, expected_version)
            self._put(self._records, org_id, record, outbox_rows)

    async def purge_settled(self, before: datetime, limit: int) -> int:
        async with self._lock:
            gone = [
                r.id
                for _, r in self._rows_across_tenants(self._records)
                if is_settled(r) and r.updated_at < before
            ][:limit]
            for record_id in gone:
                del self._records[record_id]
            return len(gone)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [r.id for r in self._rows(self._records, org_id)][:limit]
            for record_id in gone:
                del self._records[record_id]
            return len(gone)

    # The step's landing: called by another namespace's memory storage, in the
    # same step as its effect, as the statement shares its transaction in
    # Postgres. Both are synchronous, so nothing runs between the check and
    # the write.

    def check_step(self, org_id: UUID, step: Step) -> None:
        self._check(org_id, step.record.id, step.expected_version)

    def land_step(self, org_id: UUID, step: Step, applied: int) -> None:
        record = step.record.model_copy(update={"applied": step.record.applied + applied})
        self._records[record.id] = (org_id, record)

    def _check(self, org_id: UUID, record_id: UUID, expected_version: int) -> None:
        """Another tenant's record is no record here, as the policy makes it
        in Postgres: the write misses, and the caller's snapshot is stale."""
        found = self._records.get(record_id)
        if found is None or found[0] != org_id:
            raise PreconditionFailed(f"orchestration {record_id} is gone")
        if found[1].version != expected_version:
            raise PreconditionFailed(
                f"orchestration {record_id} is at version {found[1].version}, "
                f"not {expected_version}"
            )
