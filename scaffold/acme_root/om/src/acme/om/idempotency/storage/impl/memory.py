from datetime import datetime
from uuid import UUID

from acme.om.exceptions import DuplicateIdempotencyKey
from acme.om.idempotency.storage import AttemptFenceInterface, IdempotencyStorageInterface
from acme.om.idempotency.types.record import IdempotencyRecord
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class IdempotencyStorageMemoryImpl(
    MemoryStorageBase, IdempotencyStorageInterface, AttemptFenceInterface
):
    """Every conditional write reads and writes under the one lock with no
    await in between, so two callers racing for a record never both match."""

    def __init__(self) -> None:
        super().__init__()
        self._records: MemoryTable[IdempotencyRecord] = {}

    def holds(self, org_id: UUID, target_id: UUID, attempt_id: UUID) -> bool:
        # The memory twin of reading the marker inside the other namespace's
        # statement: no await, so the answer cannot go stale before the caller
        # writes under its own lock.
        return any(
            row_org == org_id
            and record.target_id == target_id
            and record.pending
            and record.attempt_id == attempt_id
            for row_org, record in self._records.values()
        )

    async def write_record(self, org_id: UUID, record: IdempotencyRecord) -> None:
        async with self._lock:
            for row_org, existing in self._records.values():
                if (
                    existing.id != record.id
                    and row_org == org_id
                    and existing.user_id == record.user_id
                    and existing.key == record.key
                ):
                    raise DuplicateIdempotencyKey(f"idempotency key {record.key!r} is taken")
            self._put(self._records, org_id, record)

    async def read_record(self, org_id: UUID, user_id: UUID, key: str) -> IdempotencyRecord | None:
        return self._find(org_id, user_id, key)

    async def finish_pending(
        self, org_id: UUID, user_id: UUID, key: str, attempt_id: UUID, status: int, body: str
    ) -> IdempotencyRecord | None:
        async with self._lock:
            stored = self._find(org_id, user_id, key)
            if stored is None or not stored.pending or stored.attempt_id != attempt_id:
                return None
            finished = stored.model_copy(update={"status": status, "body": body})
            self._put(self._records, org_id, finished)
            return finished

    async def release_pending(
        self, org_id: UUID, user_id: UUID, key: str, attempt_id: UUID
    ) -> bool:
        async with self._lock:
            stored = self._find(org_id, user_id, key)
            if stored is None or not stored.pending or stored.attempt_id != attempt_id:
                return False
            self._put(self._records, org_id, stored.model_copy(update={"attempt_id": None}))
            return True

    async def purge_records(
        self, finished_before: datetime, attempts_before: UUID, limit: int
    ) -> int:
        async with self._lock:
            gone = [
                record.id
                for _, record in self._rows_across_tenants(self._records)
                if _past_its_cut(record, finished_before, attempts_before)
            ][:limit]
            for record_id in gone:
                del self._records[record_id]
            return len(gone)

    async def take_over_pending(
        self,
        org_id: UUID,
        user_id: UUID,
        key: str,
        abandoned_before: UUID,
        attempt_id: UUID,
    ) -> IdempotencyRecord | None:
        async with self._lock:
            stored = self._find(org_id, user_id, key)
            if (
                stored is None
                or not stored.pending
                or stored.attempt_id is None
                or stored.attempt_id >= abandoned_before
            ):
                return None
            # The new token starts the lease; the birth time stays as written.
            taken = stored.model_copy(update={"attempt_id": attempt_id})
            self._put(self._records, org_id, taken)
            return taken

    async def rearm_released(
        self, org_id: UUID, user_id: UUID, key: str, attempt_id: UUID
    ) -> IdempotencyRecord | None:
        async with self._lock:
            stored = self._find(org_id, user_id, key)
            if stored is None or not stored.released:
                return None
            armed = stored.model_copy(update={"attempt_id": attempt_id})
            self._put(self._records, org_id, armed)
            return armed

    def _find(self, org_id: UUID, user_id: UUID, key: str) -> IdempotencyRecord | None:
        for record in self._rows(self._records, org_id):
            if record.user_id == user_id and record.key == key:
                return record
        return None


def _past_its_cut(
    record: IdempotencyRecord, finished_before: datetime, attempts_before: UUID
) -> bool:
    """A held pending marker is measured from its attempt, like the lease is; a
    finished or a released one from its birth, by the retention."""
    if record.pending and record.attempt_id is not None:
        return record.attempt_id < attempts_before
    return record.created_at < finished_before
