from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from acme.om.base import utcnow
from acme.om.outbox.rules import relay_delay
from acme.om.outbox.storage import OutboxLandingInterface, OutboxStorageInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryTable


class OutboxStorageMemoryImpl(OutboxStorageInterface, OutboxLandingInterface):
    """Holds no base of its own: the memory base of every other namespace lands
    rows here through `OutboxLandingInterface`, which the storage root hands them.
    The claim reads and writes with no await in between, so two sweeps racing
    for the same rows never both hold one."""

    def __init__(self) -> None:
        self._rows: MemoryTable[OutboxRow] = {}

    def land(self, org_id: UUID, row: OutboxRow) -> None:
        # The memory twin of "inserted in the same commit as the core row".
        # The tenant landed is the one the write names, as the column is in
        # Postgres, so a row that names another is corrected, never trusted.
        self._rows[row.id] = (org_id, row.model_copy(update={"org_id": org_id}))

    async def claim_pending(
        self,
        limit: int,
        now: datetime,
        grace: timedelta,
        backoff_base: timedelta,
        backoff_cap: timedelta,
    ) -> list[OutboxRow]:
        due = [
            (org, row)
            for org, row in self._rows.values()
            if row.done_at is None
            and row.failed_at is None
            and (row.next_attempt_at is None or row.next_attempt_at <= now)
            and row.created_at < now - grace
        ]
        claimed: list[OutboxRow] = []
        for org, row in sorted(due, key=lambda pair: pair[1].id)[:limit]:
            attempts = row.attempts + 1
            spent = row.model_copy(
                update={
                    "attempts": attempts,
                    "next_attempt_at": now + relay_delay(attempts, backoff_base, backoff_cap),
                }
            )
            self._rows[row.id] = (org, spent)
            claimed.append(spent)
        return claimed

    async def mark_done(self, org_id: UUID, row_ids: Sequence[UUID]) -> None:
        now = utcnow()
        for row_id in row_ids:
            found = self._rows.get(row_id)
            if found is None or found[0] != org_id or found[1].done_at is not None:
                continue
            self._rows[row_id] = (org_id, found[1].model_copy(update={"done_at": now}))

    async def record_failure(
        self, org_id: UUID, row_id: UUID, error: str, failed_at: datetime | None
    ) -> None:
        found = self._rows.get(row_id)
        if found is None or found[0] != org_id or found[1].done_at is not None:
            return
        self._rows[row_id] = (
            org_id,
            found[1].model_copy(update={"last_error": error, "failed_at": failed_at}),
        )

    async def oldest_pending_at(self) -> datetime | None:
        return min(
            (
                row.created_at
                for _, row in self._rows.values()
                if row.done_at is None and row.failed_at is None
            ),
            default=None,
        )

    async def count_failed_since(self, since: datetime) -> int:
        return sum(
            1
            for _, row in self._rows.values()
            if row.failed_at is not None and row.failed_at > since
        )

    async def purge_done(self, before: datetime, limit: int) -> int:
        done = [
            row_id
            for row_id, (_, row) in self._rows.items()
            if row.done_at is not None and row.done_at < before
        ][:limit]
        failed = [
            row_id
            for row_id, (_, row) in self._rows.items()
            if row.failed_at is not None and row.failed_at < before
        ][:limit]
        for row_id in {*done, *failed}:
            del self._rows[row_id]
        return len({*done, *failed})
