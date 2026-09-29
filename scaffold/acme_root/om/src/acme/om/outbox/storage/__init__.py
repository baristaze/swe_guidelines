"""Storage of the outbox. A row is never written on its own: the storage
base lands the rows that announce a write in the same commit as the core row
(`_insert(..., outbox_rows)` and `_upsert(..., outbox_rows)` in the `core`
role). The claim, the purge, the read of the oldest pending row, and the
count of the dead letters are cross-tenant and serve the sweep."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from acme.om.outbox.types.row import OutboxRow


class OutboxLandingInterface(ABC):
    """What a storage base needs from the outbox: somewhere for the row to land
    in the same commit as the core row. The Postgres base lands it in its own
    session; the memory base lands it here."""

    @abstractmethod
    def land(self, org_id: UUID, row: OutboxRow) -> None: ...


class OutboxStorageInterface(ABC):
    @abstractmethod
    async def claim_pending(
        self,
        limit: int,
        now: datetime,
        grace: timedelta,
        backoff_base: timedelta,
        backoff_cap: timedelta,
    ) -> list[OutboxRow]:
        """Cross-tenant, for the sweep, one statement: up to `limit` rows that are
        neither done nor failed, whose next attempt is due at `now`, and that
        landed before `now - grace` (a younger row is the request path's to
        relay), oldest first, skipping rows another sweep holds locked. Each row
        returned has spent one more attempt and carries its next attempt, `now`
        plus a delay that doubles per attempt (`rules.relay_delay`), so a row
        that will not relay stops nothing behind it and two concurrent sweeps
        relay disjoint sets. Returns the rows as written; each one names its own
        tenant, so nothing travels beside it."""
        ...

    @abstractmethod
    async def mark_done(self, org_id: UUID, row_ids: Sequence[UUID]) -> None:
        """Stamps `done_at` on the tenant's rows among `row_ids`, in one
        statement, however many rows one relay delivered; a row already done,
        another tenant's, or unknown, is left as is."""
        ...

    @abstractmethod
    async def record_failure(
        self, org_id: UUID, row_id: UUID, error: str, failed_at: datetime | None
    ) -> None:
        """Stamps `last_error` on a row that is not done, and `failed_at` when
        given: the row is then a dead letter, never claimed again, purged with
        the done ones. A row already done, or unknown, is left as is."""
        ...

    @abstractmethod
    async def oldest_pending_at(self) -> datetime | None:
        """Cross-tenant, for the sweep's relay gauge: when the oldest row that
        is neither done nor failed landed; None when every row is settled. A
        row waiting out its delay after a failed attempt is pending."""
        ...

    @abstractmethod
    async def count_failed_since(self, since: datetime) -> int:
        """Cross-tenant, for the sweep's dead-letter gauge: how many rows
        failed for good after `since`, in any tenant. A row that failed an
        attempt and waits for its next one is not failed."""
        ...

    @abstractmethod
    async def purge_done(self, before: datetime, limit: int) -> int:
        """Cross-tenant, for the sweep: deletes rows done before `before`, then
        rows failed before it, at most `limit` of each, skipping rows another
        transaction holds; returns how many. A count below `limit` says the
        rows past the cut are gone; the caller calls again for the rest."""
        ...
