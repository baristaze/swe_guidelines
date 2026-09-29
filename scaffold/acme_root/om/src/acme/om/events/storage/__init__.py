"""Storage of the event stream. Append assigns the tenant's next sequence
numbers from the tenant's cursor row, inside its own transaction; they are the
one number storage assigns, because only the database can order commits. The trim
is the other atomic method: it deletes from the bottom of each stream and moves
each tenant's floor in one transaction, so the stream above the floor is
whole; it reaches across tenants, in the system scope, once for all of them."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.events.types.event import Event
from acme.om.events.types.page import StreamPage


class EventStorageInterface(ABC):
    @abstractmethod
    async def append_events(self, org_id: UUID, events: Sequence[Event]) -> tuple[Event, ...]:
        """One transaction: takes as many of the tenant's next seqs as there are
        new events from its cursor row, writes the events with them in the
        order given, and returns every event in that order. One event is a
        batch of one. The numbers of one call are contiguous, and the events
        commit together, so a reader never sees a later number before an
        earlier one. Two concurrent appends queue on the cursor and never share
        a seq or leave a gap behind; an append that rolls back returns its
        numbers with it. Idempotent on the id: an event already appended is
        returned as stored and consumes no number. An id another tenant holds
        refuses the whole call and spends nothing; a call that names one id
        twice is refused before anything is written."""
        ...

    @abstractmethod
    async def read_after(self, org_id: UUID, after_seq: int, limit: int) -> list[Event]:
        """The tenant's events with seq greater than `after_seq`, ascending."""
        ...

    @abstractmethod
    async def read_page(self, org_id: UUID, after_seq: int, limit: int) -> StreamPage:
        """One transaction: the events `read_after` answers, then the floor
        and the head `read_floor` and `read_head` answer, read after the
        events. A trim that committed before the events were read is in the
        floor, so a page with a hole below it is always seen as one."""
        ...

    @abstractmethod
    async def count_since(self, since: datetime) -> int:
        """Global: how many events were produced at or after `since`, across
        every tenant; the traffic the operator plane's size reads."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """The hard delete of a deleted tenant's stream once its retention has
        passed: every event and the cursor row, as every namespace drops a
        tenant past it, at most `limit` events per call and the cursor row
        with the last of them; returns how many events went. The one delete
        this append-only stream has."""
        ...

    @abstractmethod
    async def trim(self, before: datetime, limit: int) -> int:
        """Cross-tenant, for the sweep, in the system scope: one statement, one
        transaction, for every tenant at once. The `limit` events produced
        longest before `before`, whatever their tenant, name the tenants to
        trim and each one's share of the batch. Under each tenant's cursor row
        lock (a row an append holds is skipped, not waited on, and that
        tenant waits for the next call), of the tenant's lowest events above
        its floor, as many as its share, the run from the bottom produced
        before `before` is deleted, and the floor moves to the last seq of
        that run. The run stops at the first younger event, even when older
        ones follow it, because `seq` follows the relay and not the write:
        the stream above the floor stays whole. Returns how many events went,
        at most `limit`; 0 when none was old enough."""
        ...

    @abstractmethod
    async def read_floor(self, org_id: UUID) -> int:
        """The tenant's floor, read from the cursor row: the highest seq the
        trim removed, 0 while it removed none. Every event above it, up to
        the head, is stored."""
        ...

    @abstractmethod
    async def read_head(self, org_id: UUID) -> int:
        """The tenant's last assigned seq, read from the cursor row; 0 before the
        first append. What a client that has seen no push yet replays from, and
        the number every pong carries."""
        ...
