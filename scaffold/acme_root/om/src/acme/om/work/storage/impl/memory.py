from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.exceptions import TenantMismatch
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable
from acme.om.work.rules import (
    attempts_after_claim,
    cap_for,
    is_at_cap,
    is_exhausted,
    stagger_delay,
)
from acme.om.work.storage import InsertOutcome, WorkStorageInterface
from acme.om.work.types.tenant_cap import TenantCap
from acme.om.work.types.work_item import WorkItem, WorkKind, WorkStatus


class WorkStorageMemoryImpl(MemoryStorageBase, WorkStorageInterface):
    def __init__(self) -> None:
        super().__init__()
        self._items: MemoryTable[WorkItem] = {}
        self._caps: MemoryTable[TenantCap] = {}

    async def create_item(self, org_id: UUID, item: WorkItem) -> InsertOutcome:
        async with self._lock:
            found = self._items.get(item.id)
            if found is not None:
                if found[0] != org_id:
                    raise TenantMismatch(f"work item {item.id} is not in {org_id}")
                return InsertOutcome.ID_EXISTS
            for existing in self._rows(self._items, org_id):
                if existing.idempotency_key == item.idempotency_key:
                    return InsertOutcome.KEY_EXISTS  # the key is taken in this tenant
            self._insert(self._items, org_id, item)
            return InsertOutcome.INSERTED

    async def write_item_if_held(
        self, org_id: UUID, claim_token: UUID, item: WorkItem
    ) -> WorkItem | None:
        async with self._lock:
            stored = self._get(self._items, org_id, item.id)
            if (
                stored is None
                or stored.status is not WorkStatus.CLAIMED
                or stored.claim_token != claim_token
            ):
                return None
            self._items[item.id] = (org_id, item)
            return item

    async def write_item_if_failed(self, org_id: UUID, item: WorkItem) -> WorkItem | None:
        async with self._lock:
            stored = self._get(self._items, org_id, item.id)
            if stored is None or stored.status is not WorkStatus.FAILED:
                return None
            self._items[item.id] = (org_id, item)
            return item

    async def claim_next(
        self,
        lane: str,
        kinds: Sequence[WorkKind],
        worker_id: str,
        lease: timedelta,
        tenant_cap: int | None = None,
    ) -> tuple[UUID, WorkItem] | None:
        now = utcnow()
        async with self._lock:
            ready = [
                (org_id, item)
                for org_id, item in self._rows_across_tenants(self._items)
                if item.lane == lane
                and item.status is WorkStatus.QUEUED
                and item.kind in kinds
                and item.available_at <= now
            ]
            own = {
                holder: row.cap
                for holder, row in self._rows_across_tenants(self._caps)
                if row.lane == lane
            }
            if tenant_cap is not None or own:
                # The tenants' claimed items on the lane under a live lease, as
                # the Postgres claim counts them in the same statement, each
                # held to its own cap there, or the lane's.
                held = Counter(
                    holder
                    for holder, other in self._rows_across_tenants(self._items)
                    if other.lane == lane
                    and other.status is WorkStatus.CLAIMED
                    and other.lease_expires_at is not None
                    and other.lease_expires_at > now
                )
                ready = [
                    pair
                    for pair in ready
                    if (cap := cap_for(own.get(pair[0]), tenant_cap)) is None
                    or not is_at_cap(held[pair[0]], cap)
                ]
            if not ready:
                return None
            # The item ready longest goes first, as the Postgres claim orders it.
            org_id, item = min(ready, key=lambda pair: (pair[1].available_at, pair[1].id))
            claimed = item.model_copy(
                update={
                    "status": WorkStatus.CLAIMED,
                    "claimed_by": worker_id,
                    "claim_token": new_id(),
                    "lease_expires_at": now + lease,
                    "attempts": attempts_after_claim(item.attempts),
                    "updated_at": now,
                    "updated_by": EMPTY_UUID,  # the claim is the platform's write
                }
            )
            self._items[item.id] = (org_id, claimed)
            return org_id, claimed

    async def write_tenant_cap(self, org_id: UUID, cap: TenantCap) -> TenantCap:
        async with self._lock:
            stored = self._cap_on(org_id, cap.lane)
            if stored is not None:
                # The row there keeps its id and its creation, as the
                # relational upsert keeps them.
                cap = cap.model_copy(
                    update={
                        "id": stored.id,
                        "created_at": stored.created_at,
                        "created_by": stored.created_by,
                    }
                )
            self._caps[cap.id] = (org_id, cap)
            return cap

    async def read_tenant_cap(self, org_id: UUID, lane: str) -> TenantCap | None:
        async with self._lock:
            return self._cap_on(org_id, lane)

    async def delete_tenant_cap(self, org_id: UUID, lane: str) -> TenantCap | None:
        async with self._lock:
            stored = self._cap_on(org_id, lane)
            if stored is not None:
                del self._caps[stored.id]
            return stored

    async def purge_tenant_caps(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            batch = self._rows(self._caps, org_id)[:limit]
            for row in batch:
                del self._caps[row.id]
            return len(batch)

    def _cap_on(self, org_id: UUID, lane: str) -> TenantCap | None:
        return next((row for row in self._rows(self._caps, org_id) if row.lane == lane), None)

    async def requeue_stale(
        self, now: datetime, stagger: timedelta, limit: int
    ) -> list[tuple[UUID, WorkItem]]:
        changed: list[tuple[UUID, WorkItem]] = []
        positions: dict[UUID, int] = {}
        async with self._lock:
            stale = [
                (org_id, item)
                for org_id, item in self._rows_across_tenants(self._items)
                if item.status is WorkStatus.CLAIMED
                and item.lease_expires_at is not None
                and item.lease_expires_at < now
            ][:limit]
            for org_id, item in stale:
                # The position among the tenant's own items in the batch.
                position = positions.get(org_id, 0)
                positions[org_id] = position + 1
                if is_exhausted(item):
                    update = {"status": WorkStatus.FAILED}
                else:
                    update = {
                        "status": WorkStatus.QUEUED,
                        "available_at": now + stagger_delay(position, stagger),
                    }
                requeued = item.model_copy(
                    update={
                        **update,
                        "claimed_by": None,
                        "claim_token": None,
                        "lease_expires_at": None,
                        "last_error": "lease expired",
                        "updated_at": now,
                        "updated_by": EMPTY_UUID,
                    }
                )
                self._items[item.id] = (org_id, requeued)
                changed.append((org_id, requeued))
        return changed

    async def purge_items(self, before: datetime, limit: int) -> int:
        async with self._lock:
            gone = [
                item.id
                for _, item in self._rows_across_tenants(self._items)
                if item.status in (WorkStatus.DONE, WorkStatus.FAILED) and item.updated_at < before
            ][:limit]
            for item_id in gone:
                del self._items[item_id]
            return len(gone)

    async def oldest_ready_at(self, now: datetime) -> datetime | None:
        ready = [
            item.available_at
            for _, item in self._rows_across_tenants(self._items)
            if item.status is WorkStatus.QUEUED and item.available_at <= now
        ]
        return min(ready, default=None)

    async def count_failed_since(self, since: datetime) -> int:
        return sum(
            1
            for _, item in self._rows_across_tenants(self._items)
            if item.status is WorkStatus.FAILED and item.updated_at > since
        )

    async def read_item(self, org_id: UUID, item_id: UUID) -> WorkItem | None:
        return self._get(self._items, org_id, item_id)

    async def read_item_by_key(self, org_id: UUID, idempotency_key: UUID) -> WorkItem | None:
        return next(
            (
                item
                for item in self._rows(self._items, org_id)
                if item.idempotency_key == idempotency_key
            ),
            None,
        )
