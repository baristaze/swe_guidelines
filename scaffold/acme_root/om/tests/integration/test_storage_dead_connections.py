"""A connection the server closed while it sat in the pool fails no call.

The pool sends no ping before a checkout. A dead connection shows itself on
the message that begins the transaction and sets its scope, which writes
nothing, and the funnel then drops it and begins again on the next one. These cases
close pooled connections from the server side, the way a restart or a
terminated backend does, and then run calls through the funnel. The last one
holds the other half of the rule: once the caller's own statements have run,
nothing is retried.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from acme.om.storage.impl.pg_base import PgStorageBase
from acme.om.storage.impl.postgres import login_sessions
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings, RolePool
from acme.om.tenancy.storage.tables.orgs import Orgs

pytestmark = pytest.mark.integration


@pytest.fixture
async def funnel(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> AsyncIterator[PgStorageBase]:
    """The storage funnel over the runtime login, on a pool of two so a case
    can fill it."""
    pool = RolePool(size=2, checkout_timeout_seconds=2.0, statement_timeout_seconds=2.0)
    sessions, engines = login_sessions(
        migration_settings.role_urls(),
        dict.fromkeys(DatabaseRole, pool),
        system_urls=migration_settings.system_role_urls(),
    )
    yield PgStorageBase(sessions)
    for engine in engines.values():
        await engine.dispose()


@pytest.fixture
def org_id() -> UUID:
    return uuid4()


async def backend_pid(funnel: PgStorageBase, org_id: UUID, hold: float = 0.0) -> int:
    async with funnel._session_for(Orgs, org_id=org_id) as session:
        pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        await asyncio.sleep(hold)
        return pid


async def terminate(migration_settings: MigrationSettings, pids: set[int]) -> None:
    """Close backends from the server side, as the login that owns them: a
    login may signal its own backends."""
    url = make_url(migration_settings.role_urls()[DatabaseRole.CORE])
    plain = url.set(drivername="postgresql").render_as_string(hide_password=False)
    connection = await asyncpg.connect(plain)
    try:
        for pid in pids:
            assert await connection.fetchval("SELECT pg_terminate_backend($1)", pid)
    finally:
        await connection.close()
    # pg_terminate_backend signals; give the backends a moment to exit.
    await asyncio.sleep(0.2)


async def test_a_call_on_a_connection_the_server_closed_is_served(
    funnel: PgStorageBase,
    migration_settings: MigrationSettings,
    org_id: UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = await backend_pid(funnel, org_id)

    await terminate(migration_settings, {before})

    with caplog.at_level(logging.WARNING, logger="acme.om.storage.impl.pg_base"):
        after = await backend_pid(funnel, org_id)
    assert after != before
    assert "beginning again on another" in caplog.text


async def test_calls_on_a_pool_whose_every_connection_was_closed_are_served(
    funnel: PgStorageBase, migration_settings: MigrationSettings, org_id: UUID
) -> None:
    """A restart closes every connection at once. The next calls, as many as
    the pool holds and at the same time, are each served."""
    held = set(await asyncio.gather(*(backend_pid(funnel, org_id, hold=0.1) for _ in range(2))))
    assert len(held) == 2

    await terminate(migration_settings, held)

    served = await asyncio.gather(*(backend_pid(funnel, org_id) for _ in range(2)))
    assert not set(served) & held


async def test_one_call_over_a_pool_whose_every_connection_was_closed_is_served(
    funnel: PgStorageBase, migration_settings: MigrationSettings, org_id: UUID
) -> None:
    """One call alone meets every closed connection in turn. Each is dropped
    at the message that begins, and the call is served on a new one."""
    held = set(await asyncio.gather(*(backend_pid(funnel, org_id, hold=0.1) for _ in range(2))))
    assert len(held) == 2

    await terminate(migration_settings, held)

    assert await backend_pid(funnel, org_id) not in held
    assert await backend_pid(funnel, org_id) not in held


async def test_a_connection_that_dies_after_the_callers_first_statement_fails_the_call(
    funnel: PgStorageBase, migration_settings: MigrationSettings, org_id: UUID
) -> None:
    """The caller's statement may have written, so the call fails and its body
    runs once."""
    runs = 0
    with pytest.raises(DBAPIError) as failed:
        async with funnel._session_for(Orgs, org_id=org_id) as session:
            runs += 1
            pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            await terminate(migration_settings, {pid})
            await session.execute(text("SELECT 1"))
    assert failed.value.connection_invalidated
    assert runs == 1
