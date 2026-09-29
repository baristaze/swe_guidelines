import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Integer,
    Select,
    Table,
    Text,
    Uuid,
    and_,
    bindparam,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    true,
    update,
)
from sqlalchemy import cast as cast_
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import ReturningInsert

from acme.om.base import EMPTY_UUID
from acme.om.events.storage import EventStorageInterface
from acme.om.events.storage.tables.event_cursors import EventCursors
from acme.om.events.storage.tables.events import Events
from acme.om.events.types.event import Event
from acme.om.events.types.page import StreamPage
from acme.om.exceptions import TenantMismatch, UniqueKeyTaken
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted, violated_constraint
from acme.om.storage.utils.translation import to_model, to_values

WRITTEN: tuple[str, ...] = tuple(
    name for name in cast(Table, Events.__table__).c.keys() if name not in ("org_id", "seq")
)
"""The columns an append writes from the event; storage writes `org_id` and `seq`."""


def _append_statement() -> ReturningInsert[Any]:
    """The append, in one statement. The events arrive as one array per column,
    so the statement is the same for one event and for a hundred, and it is
    compiled and prepared once. Those not yet in the stream take the next
    numbers from the tenant's cursor row, `head + n` under the row's lock, and
    are written with them, in the order given. The lock is held from this
    statement to the commit and no longer: two appends to one tenant queue on
    it, each leaves with the next contiguous run, and they commit in the order
    of their numbers. The first append inserts the row. Never `MAX(seq) + 1`
    and a retry on the unique index: on a busy tenant that loop is a Conflict
    generator.

    Built on the tables and not the mapped classes, so it runs as one Core
    statement with its arrays as plain parameters."""
    table = cast(Table, Events.__table__)
    cursors = cast(Table, EventCursors.__table__)
    org_id = bindparam("org_id", type_=Uuid())
    incoming = (
        func.unnest(
            *(
                bindparam(name, type_=ARRAY(Text() if name == "payload" else table.c[name].type))
                for name in WRITTEN
            )
        )
        .table_valued(*WRITTEN, with_ordinality="ordinal")
        .render_derived(name="incoming")
    )
    # An event already appended is left out, and takes no number.
    fresh = (
        select(
            *(incoming.c[name] for name in WRITTEN),
            func.row_number().over(order_by=incoming.c.ordinal).label("rank"),
        )
        .where(~exists().where(table.c.org_id == org_id, table.c.id == incoming.c.id))
        .cte("fresh")
    )
    # As many numbers as there are new events, and the cursor untouched when
    # there are none.
    wanted = select(org_id, func.count()).select_from(fresh).having(func.count() > 0)
    reserve = pg_insert(cursors).from_select(["org_id", "head"], wanted)
    taken = (
        reserve.on_conflict_do_update(
            index_elements=[cursors.c.org_id],
            set_={"head": cursors.c.head + reserve.excluded.head},
        )
        .returning(cursors.c.head)
        .cte("taken")
    )
    size = select(func.count()).select_from(fresh).scalar_subquery()
    written = [
        cast_(fresh.c.payload, JSONB) if name == "payload" else fresh.c[name] for name in WRITTEN
    ]
    return (
        insert(table)
        .from_select(
            [*WRITTEN, "org_id", "seq"],
            select(*written, org_id, taken.c.head - size + fresh.c.rank).select_from(
                fresh.join(taken, true())
            ),
        )
        .returning(*table.c)
    )


APPEND = _append_statement()


def _trim_statement() -> Select[tuple[int, int]]:
    """The trim, across tenants, in one statement.

    `old` walks `ix_events_produced_at` from its low end and takes the
    `limit` events produced longest ago: they name the tenants the call
    trims, and each tenant's share of them bounds its run, so the call
    deletes `limit` events at most. `locked` takes each named tenant's cursor
    row as the append does, `FOR UPDATE`, but skips a row an append holds
    rather than waiting on it. `bounds` reads each tenant's window, its
    lowest events above the floor, as many as its share, by the `(org_id,
    seq)` index: the stream above the floor is whole, so those are the seqs
    from `floor + 1` to `floor + share`. The run is the window below its first
    young event, and `runs` holds its top. The events of each run go, and the
    floor moves to its top, in the same statement. A tenant whose window
    starts with a young event has no run and keeps its floor.

    Built on the tables and not the mapped classes, so it runs as one Core
    statement, as the append does."""
    events = cast(Table, Events.__table__)
    cursors = cast(Table, EventCursors.__table__)
    before = bindparam("before", type_=DateTime(timezone=True))
    limit = bindparam("limit", type_=Integer())
    old = (
        select(events.c.org_id)
        .where(events.c.produced_at < before)
        .order_by(events.c.produced_at)
        .limit(limit)
        .cte("old")
        .prefix_with("MATERIALIZED")
    )
    shares = (
        select(old.c.org_id, func.count().label("share")).group_by(old.c.org_id).subquery("shares")
    )
    # The window's top is a column here and not a sum in the joins below:
    # the policy's clause is evaluated before any operator that may raise,
    # and `+` may, so a sum in a join would read the window's rows through
    # the policy's filter instead of bounding the index scan with it.
    locked = (
        select(
            cursors.c.org_id,
            cursors.c.floor,
            (cursors.c.floor + shares.c.share).label("upto"),
        )
        .join_from(cursors, shares, shares.c.org_id == cursors.c.org_id)
        .with_for_update(of=cursors, skip_locked=True)
        .cte("locked")
        .prefix_with("MATERIALIZED")
    )
    bounds = (
        select(
            locked.c.org_id,
            locked.c.floor,
            locked.c.upto,
            func.min(events.c.seq).filter(events.c.produced_at >= before).label("young"),
        )
        .join_from(
            locked,
            events,
            and_(
                events.c.org_id == locked.c.org_id,
                events.c.seq > locked.c.floor,
                events.c.seq <= locked.c.upto,
            ),
        )
        .group_by(locked.c.org_id, locked.c.floor, locked.c.upto)
        .cte("bounds")
    )
    runs = (
        select(bounds.c.org_id, bounds.c.floor, func.max(events.c.seq).label("top"))
        .join_from(
            bounds,
            events,
            and_(
                events.c.org_id == bounds.c.org_id,
                events.c.seq > bounds.c.floor,
                events.c.seq <= bounds.c.upto,
            ),
        )
        .where(or_(bounds.c.young.is_(None), events.c.seq < bounds.c.young))
        .group_by(bounds.c.org_id, bounds.c.floor)
        .cte("runs")
    )
    gone = (
        delete(events)
        .where(
            events.c.org_id == runs.c.org_id,
            events.c.seq > runs.c.floor,
            events.c.seq <= runs.c.top,
        )
        .returning(events.c.seq)
        .cte("gone")
    )
    moved = (
        update(cursors)
        .where(cursors.c.org_id == runs.c.org_id)
        .values(floor=runs.c.top)
        .returning(cursors.c.org_id)
        .cte("moved")
    )
    # Both writes are named in the statement's one row, so both run.
    return select(
        select(func.count()).select_from(gone).scalar_subquery().label("trimmed"),
        select(func.count()).select_from(moved).scalar_subquery().label("moved"),
    )


TRIM = _trim_statement()


class EventStoragePostgresImpl(PgStorageBase, EventStorageInterface):
    async def append_events(self, org_id: UUID, events: Sequence[Event]) -> tuple[Event, ...]:
        batch = list(events)
        if len({event.id for event in batch}) != len(batch):
            raise ValueError("an append names one event id twice")
        stored: dict[UUID, Event] = {}
        while pending := [event for event in batch if event.id not in stored]:
            try:
                stored.update(await self._append(org_id, pending))
            except IntegrityError as error:
                constraint = violated_constraint(error)
                if constraint != cast(Table, Events.__table__).primary_key.name:
                    raise UniqueKeyTaken(
                        f"events of {org_id}: {constraint or 'a unique key'} is taken"
                    ) from error
                # An id of the batch was appended since the statement read the
                # stream: a relay that ran twice at once. The rollback returned
                # every number, and the retry leaves out what is stored now.
                # An id that stays out of reach is another tenant's.
                found = await self._read_ids(org_id, [event.id for event in pending])
                if not found:
                    raise TenantMismatch(
                        f"events {[str(event.id) for event in pending]}: an id is not in {org_id}"
                    ) from error
                stored.update(found)
                continue
            # What the statement left out was already appended: the relay ran
            # twice, and the second run returns what is stored.
            skipped = [event.id for event in pending if event.id not in stored]
            if skipped:
                stored.update(await self._read_ids(org_id, skipped))
            break
        return tuple(stored[event.id] for event in batch)

    async def _append(self, org_id: UUID, events: list[Event]) -> dict[UUID, Event]:
        """`APPEND` for these events: the new ones take the next numbers from
        the cursor row and are written with them, and come back by id."""
        rows = [to_values(event, Events) for event in events]
        params: dict[str, object] = {name: [row[name] for row in rows] for name in WRITTEN}
        # The payload travels as JSON text and is cast back in the statement.
        params["payload"] = [json.dumps(row["payload"]) for row in rows]
        params["org_id"] = org_id
        async with self._session_for(Events, org_id=org_id) as session:
            try:
                result = (await session.execute(APPEND, params)).all()
                appended = {row.id: to_model(row, Event) for row in result}
                await session.commit()
            except IntegrityError:
                # The rollback returns the numbers with it, so the stream stays gapless.
                await session.rollback()
                raise
            return appended

    async def _read_ids(self, org_id: UUID, event_ids: list[UUID]) -> dict[UUID, Event]:
        stmt = select(Events).where(Events.org_id == org_id, Events.id.in_(event_ids))
        async with self._session_for(stmt, org_id=org_id) as session:
            return {row.id: to_model(row, Event) for row in (await session.execute(stmt)).scalars()}

    async def read_after(self, org_id: UUID, after_seq: int, limit: int) -> list[Event]:
        stmt = (
            select(Events)
            .where(Events.org_id == org_id, Events.seq > after_seq)
            .order_by(Events.seq)
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            result = await session.execute(stmt)
            return [to_model(row, Event) for row in result.scalars()]

    async def read_page(self, org_id: UUID, after_seq: int, limit: int) -> StreamPage:
        page = (
            select(Events)
            .where(Events.org_id == org_id, Events.seq > after_seq)
            .order_by(Events.seq)
            .limit(limit)
        )
        cursor = select(EventCursors.floor, EventCursors.head).where(EventCursors.org_id == org_id)
        async with self._session_for(Events, org_id=org_id) as session:
            events = tuple(to_model(row, Event) for row in (await session.execute(page)).scalars())
            # Read committed: this statement sees every trim committed before
            # it, the ones before the page included.
            found = (await session.execute(cursor)).one_or_none()
        floor, head = (0, 0) if found is None else (int(found.floor), int(found.head))
        return StreamPage(events=events, floor=floor, head=head)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        events = delete_batch(Events, Events.org_id == org_id, limit=limit)
        cursor = delete(EventCursors).where(EventCursors.org_id == org_id)
        async with self._session_for(Events, org_id=org_id) as session:
            purged = deleted(await session.execute(events))
            if purged < limit:
                # The stream is empty now, so its counter goes too.
                await session.execute(cursor)
            await session.commit()
            return purged

    async def trim(self, before: datetime, limit: int) -> int:
        # Every tenant's stream, so the system scope, spelled here. One
        # statement: the cursor rows are locked, the events deleted, and the
        # floors moved in one transaction, as the per-tenant trim did it.
        async with self._session_for(Events, org_id=EMPTY_UUID) as session:
            trimmed = (await session.execute(TRIM, {"before": before, "limit": limit})).scalar_one()
            await session.commit()
            return int(trimmed)

    async def read_floor(self, org_id: UUID) -> int:
        stmt = select(EventCursors.floor).where(EventCursors.org_id == org_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            return int(await session.scalar(stmt) or 0)

    async def count_since(self, since: datetime) -> int:
        stmt = select(func.count()).select_from(Events).where(Events.produced_at >= since)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return (await session.execute(stmt)).scalar_one()

    async def read_head(self, org_id: UUID) -> int:
        # The cursor row is the head: one row, never a scan of the stream.
        stmt = select(EventCursors.head).where(EventCursors.org_id == org_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            return int(await session.scalar(stmt) or 0)
