import asyncio

import pytest
from contracts.event_storage import EventStorageContract, make_event
from sqlalchemy import insert, update

from acme.om.base import new_id
from acme.om.events.storage import EventStorageInterface
from acme.om.events.storage.impl.postgres import EventStoragePostgresImpl
from acme.om.events.storage.tables.event_cursors import EventCursors
from acme.om.events.storage.tables.events import Events
from acme.om.storage.impl.pg_base import SessionFactory, set_scope
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.utils.translation import to_values

pytestmark = pytest.mark.integration


class TestEventStoragePostgres(EventStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> EventStorageInterface:
        return EventStoragePostgresImpl(pg_sessions)

    async def test_the_cursor_row_is_the_head_and_the_next_number(
        self, storage: EventStorageInterface, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> None:
        # The head is the cursor row, never a scan of the stream: a cursor set
        # ahead of the stream is what read_head says and what the append continues from.
        # Both transactions here reach around the impl, so each names the
        # tenant itself: a transaction that names none reads nothing and
        # writes nothing, which is the second fence holding.
        org = new_id()
        async with pg_sessions[DatabaseRole.ACTIVITY]() as session:
            await set_scope(session, org, None, None)
            session.add(EventCursors(org_id=org, head=7))
            await session.commit()
        assert await storage.read_head(org) == 7
        assert (await storage.append_events(org, [make_event(org)]))[0].seq == 8
        async with pg_sessions[DatabaseRole.ACTIVITY]() as session:
            await set_scope(session, org, None, None)
            assert (await session.get(EventCursors, org)).head == 8  # type: ignore[union-attr]

    async def test_a_number_commits_only_after_the_numbers_below_it(
        self, storage: EventStorageInterface, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> None:
        """What a reader relies on: the cursor, the head a pong carries, and
        the replay after it never meet seq N + 1 committed while N is still
        pending. An append in flight holds the cursor row from its statement
        to its commit, so the next append waits behind it and commits after
        it. Here the one in flight is spelled out by hand: it took 2 and wrote
        its event, and has not committed."""
        org = new_id()
        await storage.append_events(org, [make_event(org)])
        async with pg_sessions[DatabaseRole.ACTIVITY]() as in_flight:
            await set_scope(in_flight, org, None, None)
            await in_flight.execute(
                update(EventCursors)
                .where(EventCursors.org_id == org)
                .values(head=EventCursors.head + 1)
            )
            pending = make_event(org)
            await in_flight.execute(
                insert(Events).values({**to_values(pending, Events), "org_id": org, "seq": 2})
            )
            behind = asyncio.create_task(storage.append_events(org, [make_event(org)]))
            await asyncio.sleep(0.3)
            assert not behind.done(), "the next append did not wait on the cursor"
            assert [e.seq for e in await storage.read_after(org, 0, 10)] == [1]
            assert await storage.read_head(org) == 1
            await in_flight.commit()
        (appended,) = await behind
        assert appended.seq == 3
        assert [e.seq for e in await storage.read_after(org, 0, 10)] == [1, 2, 3]
