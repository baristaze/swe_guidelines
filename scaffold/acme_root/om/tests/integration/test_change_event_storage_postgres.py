import asyncio

import pytest
from sqlalchemy import insert, update

from acme.om.base import new_id
from acme.om.change_events.storage import ChangeEventStorageInterface
from acme.om.change_events.storage.impl.postgres import ChangeEventStoragePostgresImpl
from acme.om.change_events.storage.tables.change_event_cursors import ChangeEventCursors
from acme.om.change_events.storage.tables.change_events import ChangeEvents
from acme.om.storage.impl.pg_base import SessionFactory, set_scope
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.utils.translation import to_values

from contracts.change_event_storage import ChangeEventStorageContract, make_change_event

pytestmark = pytest.mark.integration


class TestChangeEventStoragePostgres(ChangeEventStorageContract):
    @pytest.fixture
    def storage(
        self, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> ChangeEventStorageInterface:
        return ChangeEventStoragePostgresImpl(pg_sessions)

    async def test_the_cursor_row_is_the_head_and_the_next_number(
        self, storage: ChangeEventStorageInterface, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> None:
        # The head is the cursor row, never a scan of the stream: a cursor set
        # ahead of the stream is what read_head says and what the append continues from.
        # Both transactions here reach around the impl, so each names the
        # tenant itself: a transaction that names none reads nothing and
        # writes nothing, which is the second fence holding.
        org = new_id()
        async with pg_sessions[DatabaseRole.ACTIVITY]() as session:
            await set_scope(session, org, None, None)
            session.add(ChangeEventCursors(org_id=org, head=7))
            await session.commit()
        assert await storage.read_head(org) == 7
        assert (await storage.append_change_events(org, [make_change_event(org)]))[0].seq == 8
        async with pg_sessions[DatabaseRole.ACTIVITY]() as session:
            await set_scope(session, org, None, None)
            assert (await session.get(ChangeEventCursors, org)).head == 8  # type: ignore[union-attr]

    async def test_a_number_commits_only_after_the_numbers_below_it(
        self, storage: ChangeEventStorageInterface, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> None:
        """What a reader relies on: the cursor, the head a pong carries, and
        the replay after it never meet seq N + 1 committed while N is still
        pending. An append in flight holds the cursor row from its statement
        to its commit, so the next append waits behind it and commits after
        it. Here the one in flight is spelled out by hand: it took 2 and wrote
        its event, and has not committed."""
        org = new_id()
        await storage.append_change_events(org, [make_change_event(org)])
        async with pg_sessions[DatabaseRole.ACTIVITY]() as in_flight:
            await set_scope(in_flight, org, None, None)
            await in_flight.execute(
                update(ChangeEventCursors)
                .where(ChangeEventCursors.org_id == org)
                .values(head=ChangeEventCursors.head + 1)
            )
            pending = make_change_event(org)
            await in_flight.execute(
                insert(ChangeEvents).values(
                    {**to_values(pending, ChangeEvents), "org_id": org, "seq": 2}
                )
            )
            behind = asyncio.create_task(
                storage.append_change_events(org, [make_change_event(org)])
            )
            await asyncio.sleep(0.3)
            assert not behind.done(), "the next append did not wait on the cursor"
            assert [e.seq for e in await storage.read_after(org, 0, 10)] == [1]
            assert await storage.read_head(org) == 1
            await in_flight.commit()
        (appended,) = await behind
        assert appended.seq == 3
        assert [e.seq for e in await storage.read_after(org, 0, 10)] == [1, 2, 3]
