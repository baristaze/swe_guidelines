"""The funnel sends the scope in the message that begins the transaction.

The scope is the first thing inside the transaction and dies with it, as
`set_config(..., true)` always did; what these cases hold is the shape on the
wire: one simple query, `BEGIN; SELECT set_config(...);`, and nothing else
before the caller's first statement.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import text

from acme.om.base import EMPTY_UUID
from acme.om.storage.impl.pg_base import PgStorageBase, ScopedConnection
from acme.om.storage.impl.postgres import login_sessions
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings, RolePool
from acme.om.tenancy.storage.tables.orgs import Orgs
from acme.om.tenancy.storage.tables.users import Users

pytestmark = pytest.mark.integration


@pytest.fixture
async def funnel(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> AsyncIterator[PgStorageBase]:
    """The funnel on a pool of one, so every call reuses one connection."""
    pool = RolePool(size=1, checkout_timeout_seconds=2.0, statement_timeout_seconds=2.0)
    sessions, engines = login_sessions(
        migration_settings.role_urls(),
        dict.fromkeys(DatabaseRole, pool),
        system_urls=migration_settings.system_role_urls(),
    )
    yield PgStorageBase(sessions)
    for engine in engines.values():
        await engine.dispose()


@pytest.fixture
async def sent(funnel: PgStorageBase, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every simple query and every statement prepared on a driver connection,
    in order, as the driver sends it. Each login's one connection is opened
    first, so the dialect's own first-connect probes are not in it."""
    for org_id in (uuid4(), EMPTY_UUID):
        async with funnel._session_for(Orgs, org_id=org_id) as session:
            await session.execute(text("SELECT 1"))
    seen: list[str] = []
    execute = asyncpg.Connection.execute
    prepare = asyncpg.Connection.prepare

    async def recording_execute(self: asyncpg.Connection, query: str, *args: Any, **kw: Any) -> str:
        seen.append(query)
        return await execute(self, query, *args, **kw)

    async def recording_prepare(self: asyncpg.Connection, query: str, **kw: Any) -> Any:
        seen.append(f"PREPARE {query}")
        return await prepare(self, query, **kw)

    monkeypatch.setattr(asyncpg.Connection, "execute", recording_execute)
    monkeypatch.setattr(asyncpg.Connection, "prepare", recording_prepare)
    return seen


async def test_the_scope_rides_in_the_message_that_begins(
    funnel: PgStorageBase, sent: list[str]
) -> None:
    org_id, user_id = uuid4(), uuid4()
    async with funnel._session_for(Users, org_id=org_id, user_id=user_id) as session:
        begun = list(sent)
        await session.execute(text("SELECT 1"))
    assert begun == [
        "BEGIN; SELECT set_config('app.org_id', "
        f"'{org_id}', true), set_config('app.user_id', '{user_id}', true);"
    ]


async def test_the_scope_is_set_before_the_first_statement_and_dies_with_the_transaction(
    funnel: PgStorageBase,
) -> None:
    org_id = uuid4()
    async with funnel._session_for(Orgs, org_id=org_id) as session:
        inside = (
            await session.execute(
                text(
                    "SELECT current_setting('app.org_id', true),"
                    " current_setting('app.user_id', true), pg_backend_pid()"
                )
            )
        ).one()
    assert UUID(inside[0]) == org_id
    assert inside[1] is None
    # The pool holds one connection, so the next call is on the same backend,
    # and a transaction that began without the funnel sees nothing left over.
    async with funnel._session_for(Orgs, org_id=org_id) as session:
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        driver = raw.driver_connection
        assert isinstance(driver, ScopedConnection)
        await session.commit()
        found = await driver.fetchrow(
            "SELECT pg_backend_pid(), current_setting('app.org_id', true)"
        )
    assert found is not None
    assert found[0] == inside[2]
    assert not found[1]


async def test_the_system_scope_rides_the_same_way(funnel: PgStorageBase, sent: list[str]) -> None:
    async with funnel._session_for(Orgs, org_id=EMPTY_UUID) as session:
        await session.execute(text("SELECT 1"))
    assert sent[0] == f"BEGIN; SELECT set_config('app.org_id', '{EMPTY_UUID}', true);"


async def test_a_scope_waiting_for_a_begin_is_never_sent_with_another_statement(
    funnel: PgStorageBase,
) -> None:
    async with funnel._session_for(Orgs, org_id=uuid4()) as session:
        connection = await session.connection()
        driver = (await connection.get_raw_connection()).driver_connection
        assert isinstance(driver, ScopedConnection)
        driver.scope_next_begin("SELECT set_config('app.org_id', '', true)")
        with pytest.raises(RuntimeError):
            await driver.execute("SELECT 1")
        # Taken once: the refusal spent it.
        assert await driver.execute("SELECT 1") == "SELECT 1"
