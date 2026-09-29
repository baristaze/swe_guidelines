from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from itertools import takewhile
from uuid import UUID

from acme.om.events.storage import EventStorageInterface
from acme.om.events.types.event import Event
from acme.om.events.types.page import StreamPage
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class EventStorageMemoryImpl(MemoryStorageBase, EventStorageInterface):
    def __init__(self) -> None:
        super().__init__()
        self._events: MemoryTable[Event] = {}
        # The twin of the cursor row: the last seq assigned, per tenant, and
        # the highest seq the trim removed.
        self._cursors: dict[UUID, int] = {}
        self._floors: dict[UUID, int] = {}

    async def append_events(self, org_id: UUID, events: Sequence[Event]) -> tuple[Event, ...]:
        batch = list(events)
        if len({event.id for event in batch}) != len(batch):
            raise ValueError("an append names one event id twice")
        async with self._lock:
            stored: dict[UUID, Event] = {}
            for event in batch:
                found = self._get(self._events, org_id, event.id)
                if found is not None:
                    stored[event.id] = found
            # The tenant and the numbers are storage's, as the columns are in
            # Postgres: an event that names another tenant is corrected. Every
            # write is fenced before a number is spent, the way Postgres rolls
            # the whole call back with the insert it refused, so an id another
            # tenant owns never moves this tenant's cursor.
            fresh = [
                event.model_copy(update={"org_id": org_id})
                for event in batch
                if event.id not in stored
            ]
            for event in fresh:
                self._fence(self._events, org_id, event)
            head = self._cursors.get(org_id, 0)
            for seq, event in enumerate(fresh, start=head + 1):
                appended = event.model_copy(update={"seq": seq})
                self._put(self._events, org_id, appended)
                stored[event.id] = appended
            if fresh:
                self._cursors[org_id] = head + len(fresh)
            return tuple(stored[event.id] for event in batch)

    async def read_after(self, org_id: UUID, after_seq: int, limit: int) -> list[Event]:
        newer = [e for e in self._rows(self._events, org_id) if e.seq > after_seq]
        return sorted(newer, key=lambda e: e.seq)[:limit]

    async def read_page(self, org_id: UUID, after_seq: int, limit: int) -> StreamPage:
        return StreamPage(
            events=tuple(await self.read_after(org_id, after_seq, limit)),
            floor=await self.read_floor(org_id),
            head=await self.read_head(org_id),
        )

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [e.id for e in self._rows(self._events, org_id)][:limit]
            for event_id in gone:
                del self._events[event_id]
            if len(gone) < limit:
                # The stream is empty now, so its counter and floor go too.
                self._cursors.pop(org_id, None)
                self._floors.pop(org_id, None)
            return len(gone)

    async def trim(self, before: datetime, limit: int) -> int:
        async with self._lock:
            old = sorted(
                (
                    (e.produced_at, e.id, org_id)
                    for org_id, e in self._rows_across_tenants(self._events)
                    if e.produced_at < before and org_id in self._cursors
                ),
            )[:limit]
            shares = Counter(org_id for _, _, org_id in old)
            trimmed = 0
            for org_id, share in shares.items():
                floor = self._floors.get(org_id, 0)
                bottom = sorted(
                    (e for e in self._rows(self._events, org_id) if floor < e.seq <= floor + share),
                    key=lambda e: e.seq,
                )
                run = list(takewhile(lambda e: e.produced_at < before, bottom))
                if not run:
                    continue
                for event in run:
                    del self._events[event.id]
                self._floors[org_id] = run[-1].seq
                trimmed += len(run)
            return trimmed

    async def read_floor(self, org_id: UUID) -> int:
        return self._floors.get(org_id, 0)

    async def count_since(self, since: datetime) -> int:
        return sum(1 for event in self._every(self._events) if event.produced_at >= since)

    async def read_head(self, org_id: UUID) -> int:
        return self._cursors.get(org_id, 0)
