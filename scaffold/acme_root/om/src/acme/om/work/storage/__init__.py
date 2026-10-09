"""Storage of the work queue. The claim is the one named atomic method. The
claim, the requeue of expired leases, the purge, and the two reads of the
sweep's gauges reach across tenants in the system scope; every other
operation takes org_id first. The claim mints a token, and the writes that
move a claimed item are conditional on that token still being on the row, so
a lost lease can never be written over, not even by the worker that held the
item before and holds it again."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from acme.om.work.types.work_item import WorkItem, WorkKind


class InsertOutcome(StrEnum):
    """What a create whose row can collide on more than one key did: the
    caller reads the existing row back by the key that collided."""

    INSERTED = "inserted"
    ID_EXISTS = "id_exists"  # the id is already written; nothing changes
    KEY_EXISTS = "key_exists"  # another unique key is held, under another id


class WorkStorageInterface(ABC):
    @abstractmethod
    async def create_item(self, org_id: UUID, item: WorkItem) -> InsertOutcome:
        """The create primitive: inserts the row and commits. `ID_EXISTS` when
        the id is already written, and `KEY_EXISTS` when the idempotency key is
        held under another id; nothing changes either way, the claim on the
        row included, and neither is raised as a driver error. That is what
        makes the relayed enqueue safe to run twice: the relay presents the
        outbox row's id as the key on every run and meets the row already
        there. Raises TenantMismatch when the id is another tenant's row."""
        ...

    @abstractmethod
    async def read_item_by_key(self, org_id: UUID, idempotency_key: UUID) -> WorkItem | None:
        """The row the tenant holds under this key, for the create that
        reported `KEY_EXISTS`; None when the key is unknown here. The key is
        unique per tenant, `(org_id, idempotency_key)`, so a key another tenant
        holds reads back as None."""
        ...

    @abstractmethod
    async def write_item_if_held(
        self, org_id: UUID, claim_token: UUID, item: WorkItem
    ) -> WorkItem | None:
        """One conditional statement: writes `item` over its row only while the row
        is still claimed under `claim_token`; returns None when it is not."""
        ...

    @abstractmethod
    async def write_item_if_failed(self, org_id: UUID, item: WorkItem) -> WorkItem | None:
        """One conditional statement: writes `item` over its row only while the
        row is failed; returns None when it is not, or is gone. An operator's
        requeue is the one write that moves an item out of failed, and two
        that race move it once."""
        ...

    @abstractmethod
    async def claim_next(
        self,
        lane: str,
        kinds: Sequence[WorkKind],
        worker_id: str,
        lease: timedelta,
        tenant_cap: int | None = None,
    ) -> tuple[UUID, WorkItem] | None:
        """Cross-tenant claim, one statement: the row on the lane that has been
        ready longest (the earliest `available_at`, then the lowest id),
        skipping locked ones, stamped with the claim, a freshly minted claim
        token, and the lease. The claim is the platform's write, so it signs
        `updated_by` with EMPTY_UUID.

        With `tenant_cap`, the same statement passes over every tenant that
        already holds that many items claimed on the lane under a live lease
        (`rules.is_at_cap`): their items stay as they are, unwritten, and the
        row taken is the oldest of a tenant under its cap. None counts
        nothing, and the statement is the one without a cap."""
        ...

    @abstractmethod
    async def requeue_stale(
        self, now: datetime, stagger: timedelta, limit: int
    ) -> list[tuple[UUID, WorkItem]]:
        """Cross-tenant, for the sweep, in the system scope: one conditional
        statement. The claimed items whose lease expired before `now`, the
        first `limit` of them by id across every tenant, skipping items another
        transaction holds, go back to the queue, or fail when their attempts
        are spent. The claim token is cleared either way, so the holder it had
        is refused. Each tenant's items are staggered by their position among
        that tenant's items in the batch, so one tenant's items come back
        spread out as they did when the sweep ran per tenant. Returns each
        item it changed with its tenant, by id. The caller picks the bound:
        the sweep passes its batch size and calls again while a batch comes
        back full. The requeue is the platform's write, like the claim, so it
        signs `updated_by` with EMPTY_UUID and takes no principal."""
        ...

    @abstractmethod
    async def purge_items(self, before: datetime, limit: int) -> int:
        """Cross-tenant, for the sweep, in the system scope: deletes at most
        `limit` items done or failed whose last change was before `before`,
        skipping items another transaction holds; returns how many. The one
        hard delete of the namespace. It takes no tenant: one statement
        reaches every tenant's settled items."""
        ...

    @abstractmethod
    async def oldest_ready_at(self, now: datetime) -> datetime | None:
        """Cross-tenant, for the sweep's backlog gauge, in the system scope:
        the `available_at` of the queued item that has been ready longest at
        `now`, on any lane and in any tenant; None when nothing is ready. An
        item parked until later is not ready. One read of the first entry of
        the index of queued items."""
        ...

    @abstractmethod
    async def count_failed_since(self, since: datetime) -> int:
        """Cross-tenant, for the sweep's dead-letter gauge, in the system
        scope: how many items are failed and were last changed after `since`,
        which is when they failed. An item an operator sent back is no longer
        failed and is not counted."""
        ...

    @abstractmethod
    async def read_item(self, org_id: UUID, item_id: UUID) -> WorkItem | None: ...
