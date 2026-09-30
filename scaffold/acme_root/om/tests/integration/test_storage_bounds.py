"""The pool bounds, held by a live Postgres.

The unit suite proves the engine is built with the numbers the settings name.
These cases prove the numbers do what they say on a real connection: every
contract case over Postgres runs under the settings' own deadline, a
statement past its deadline is cancelled by the database, and a checkout past
the pool waits its bound and fails. Through the storage funnel, both leave as
`Unavailable`, on every role and under both logins. The cases after the first
set small bounds of their own, so each answers in a fraction of a second; the
defaults are not changed by anything here.
"""

import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from acme.om.base import EMPTY_UUID, new_id
from acme.om.events.storage.tables.events import Events
from acme.om.exceptions import Unavailable
from acme.om.media.storage.tables.files import Files
from acme.om.storage.impl.pg_base import LoginSessions, PgStorageBase
from acme.om.storage.impl.postgres import login_sessions
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings, RolePool
from acme.om.tenancy.storage.tables.platform_sizes import PlatformSizes
from acme.om.work.storage.tables.work_items import WorkItems

pytestmark = pytest.mark.integration

QUERY_CANCELED = "57014"
"""Postgres's SQLSTATE for a statement cancelled by `statement_timeout`."""


@pytest.fixture
async def tight_sessions(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> AsyncIterator[LoginSessions]:
    """The runtime login's sessions under one connection, a short checkout
    bound, and a short deadline."""
    pool = RolePool(size=1, checkout_timeout_seconds=0.3, statement_timeout_seconds=0.3)
    sessions, engines = login_sessions(
        migration_settings.role_urls(),
        dict.fromkeys(DatabaseRole, pool),
        system_urls=migration_settings.system_role_urls(),
    )
    yield sessions
    for engine in engines.values():
        await engine.dispose()


async def test_the_contract_suites_run_under_the_settings_deadline(
    pg_sessions: LoginSessions, migration_settings: MigrationSettings
) -> None:
    """Every login the contract cases use carries the deadline the settings
    name, which a bare engine would not: Postgres's own default is none."""
    pools = migration_settings.role_pools()
    for factories in (pg_sessions, pg_sessions.system):
        for role in DatabaseRole:
            async with factories[role]() as session:
                shown = (await session.execute(text("SHOW statement_timeout"))).scalar_one()
            expected = round(pools[role].statement_timeout_seconds * 1000)
            assert shown in {f"{expected}ms", f"{expected // 1000}s"}, (role, shown)


async def test_a_statement_past_its_deadline_is_cancelled_by_postgres(
    tight_sessions: LoginSessions,
) -> None:
    started = time.monotonic()
    with pytest.raises(DBAPIError) as refused:
        async with tight_sessions[DatabaseRole.CORE]() as session:
            await session.execute(text("SELECT pg_sleep(5)"))
    assert getattr(refused.value.orig, "sqlstate", None) == QUERY_CANCELED
    assert time.monotonic() - started < 2.0


async def test_a_checkout_past_the_pool_waits_its_bound_and_fails(
    tight_sessions: LoginSessions,
) -> None:
    """One connection held, a second asked for: the pool opens none past its
    size, so the second waits the checkout bound and fails instead of queueing
    without end."""
    factory = tight_sessions[DatabaseRole.CORE]
    async with factory() as holder:
        await holder.execute(text("SELECT 1"))
        started = time.monotonic()
        with pytest.raises(PoolTimeoutError):
            async with factory() as second:
                await second.execute(text("SELECT 1"))
        waited = time.monotonic() - started
    assert 0.3 <= waited < 2.0


A_TABLE_OF: dict[DatabaseRole, type[Any]] = {
    DatabaseRole.CORE: Files,
    DatabaseRole.ACTIVITY: Events,
    DatabaseRole.QUEUE: WorkItems,
    DatabaseRole.ADMIN: PlatformSizes,
}
"""A table of each role, which is what routes a session to that role's pool.
The worker's claim is the queue role under the system login, and the outbox
relay's claim the core role under it; a request's reads and writes are every
role under the runtime login."""

LOGINS = ("runtime", "system")


def scope_of(login: str) -> UUID:
    """The system scope opens the system login's pool; a tenant's, the runtime one's."""
    return EMPTY_UUID if login == "system" else new_id()


@pytest.mark.parametrize("login", LOGINS)
@pytest.mark.parametrize("role", list(DatabaseRole))
async def test_a_statement_past_its_deadline_leaves_the_funnel_unavailable(
    tight_sessions: LoginSessions, role: DatabaseRole, login: str
) -> None:
    storage = PgStorageBase(tight_sessions)
    with pytest.raises(Unavailable) as refused:
        async with storage._session_for(A_TABLE_OF[role], org_id=scope_of(login)) as session:
            await session.execute(text("SELECT pg_sleep(5)"))
    assert refused.value.code == "unavailable"
    assert f"a statement on the {role.value} role ({login} login) passed its deadline" in (
        refused.value.message
    )
    assert isinstance(refused.value.__cause__, DBAPIError)


@pytest.mark.parametrize("login", LOGINS)
@pytest.mark.parametrize("role", list(DatabaseRole))
async def test_a_checkout_past_its_bound_leaves_the_funnel_unavailable(
    tight_sessions: LoginSessions, role: DatabaseRole, login: str
) -> None:
    """Another checkout holds the one connection of the pool the call would draw on."""
    storage = PgStorageBase(tight_sessions)
    pool = tight_sessions.system if login == "system" else tight_sessions
    async with pool[role]() as holder:
        await holder.connection()
        with pytest.raises(Unavailable) as refused:
            async with storage._session_for(A_TABLE_OF[role], org_id=scope_of(login)) as session:
                await session.execute(text("SELECT 1"))
    assert f"no connection to the {role.value} role ({login} login) within the checkout bound" in (
        refused.value.message
    )
    assert isinstance(refused.value.__cause__, PoolTimeoutError)


async def test_a_failing_statement_is_logged_without_the_values_bound_to_it(
    pg_sessions: LoginSessions, caplog: pytest.LogCaptureFixture
) -> None:
    """A bound value is a tenant's word or a person's address. The engine
    hides its parameters, so the exception of a statement that fails names
    the statement and the database's own message, and a traceback in a log
    line carries no value."""
    words = "WIPZ_DOWN_ALL_RULZ"
    statement = text("SELECT CAST(:name AS text), 1 / CAST(:zero AS integer)")
    with pytest.raises(DBAPIError) as failed:
        async with pg_sessions[DatabaseRole.CORE]() as session:
            await session.execute(statement, {"name": words, "zero": 0})
    with caplog.at_level(logging.ERROR):
        logging.getLogger(__name__).error("a statement failed", exc_info=failed.value)
    assert "division by zero" in caplog.text
    assert "hide_parameters=True" in caplog.text
    assert words not in caplog.text
