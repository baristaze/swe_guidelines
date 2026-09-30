"""The relational storage root: one engine and pool per distinct role URL and
its bounds, under the runtime login and under the system login, every
namespace impl constructed here."""

import asyncio
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from acme.om.events.storage import EventStorageInterface
from acme.om.events.storage.impl.postgres import EventStoragePostgresImpl
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.postgres import IdempotencyStoragePostgresImpl
from acme.om.media.storage import MediaStorageInterface
from acme.om.media.storage.impl.postgres import MediaStoragePostgresImpl
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.impl.postgres import OrchestrationsStoragePostgresImpl
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.postgres import OutboxStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions, ScopedConnection, SessionFactory
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.root import StorageInterface
from acme.om.storage.settings import RolePool
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.impl.postgres import TenancyStoragePostgresImpl
from acme.om.work.storage import WorkStorageInterface
from acme.om.work.storage.impl.postgres import WorkStoragePostgresImpl


def connect_args(pool: RolePool) -> dict[str, Any]:
    """What every connection of a pool is opened with. `statement_timeout` is a
    server setting in the startup packet, so Postgres cancels any statement that
    runs past the deadline on every connection the pool opens: the deadline
    holds for every statement of every role and no call site carries it. The
    driver's own `timeout` bounds opening a connection, so a database that
    accepts no connection fails a call within the same bound a checkout has.
    Every connection is a `ScopedConnection`, which carries the funnel's scope
    in the message that begins a transaction."""
    milliseconds = round(pool.statement_timeout_seconds * 1000)
    return {
        "timeout": pool.checkout_timeout_seconds,
        "server_settings": {"statement_timeout": str(milliseconds)},
        "connection_class": ScopedConnection,
    }


POOL_RECYCLE_SECONDS = 300
"""The age past which a pooled connection is closed at checkout and a new one
opened in its place. The shortest idle cutoff between a task and its database
is the security group's connection tracking: 350 seconds on the newest EC2
hosts, and a tracked connection idle past it is dropped without a word to
either end. A connection is never idle longer than it is old, so none is used
past that cutoff. Postgres itself closes no idle session: RDS leaves
`idle_session_timeout` off, and the parameter group does not set it."""


def engine_for(url: str, pool: RolePool) -> AsyncEngine:
    """One engine on a URL under one role's bounds. `max_overflow` is zero, not a
    knob of its own: the declared size is then the number of connections the
    process can hold, which is the number a worker's capacity is set against, and
    a checkout past it waits `pool_timeout` and fails rather than queueing
    without end.

    No ping before a checkout: that is three round trips (BEGIN, a probe,
    ROLLBACK) before every transaction. The pool recycles a connection before
    anything on the path can drop it, and a connection the server closed anyway
    fails the message that begins the transaction and sets its scope, which
    writes nothing, so the storage funnel drops it and begins again on the
    next connection (`pg_base.scoped_session`).

    The engine hides its parameters: a statement that fails is an exception
    whose text names the statement and never the values bound to it, which
    are a tenant's words and a person's address, so no traceback in a log
    line carries them."""
    return create_async_engine(
        url,
        hide_parameters=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
        pool_size=pool.size,
        max_overflow=0,
        pool_timeout=pool.checkout_timeout_seconds,
        connect_args=connect_args(pool),
    )


def login_sessions(
    urls: Mapping[DatabaseRole, str],
    pools: Mapping[DatabaseRole, RolePool],
    *,
    system_urls: Mapping[DatabaseRole, str],
) -> tuple[LoginSessions, dict[tuple[str, RolePool], AsyncEngine]]:
    """The session factories of both logins, and the engines behind them keyed
    by URL and bounds so the caller can dispose of them. This is the one way a
    pool is opened: the storage root builds its sessions here, and so do the
    integration suites, which then run every case under the bounds a deployed
    process holds and not under a library's defaults."""
    engines: dict[tuple[str, RolePool], AsyncEngine] = {}

    def factories(by_role: Mapping[DatabaseRole, str]) -> dict[DatabaseRole, SessionFactory]:
        found: dict[DatabaseRole, SessionFactory] = {}
        for role in DatabaseRole:
            key = (by_role[role], pools[role])
            if key not in engines:
                engines[key] = engine_for(*key)
            found[role] = async_sessionmaker(engines[key], expire_on_commit=False)
        return found

    return LoginSessions(factories(urls), factories(system_urls)), engines


class StoragePostgresImpl(StorageInterface):
    def __init__(
        self,
        urls: Mapping[DatabaseRole, str],
        pools: Mapping[DatabaseRole, RolePool],
        *,
        system_urls: Mapping[DatabaseRole, str],
    ) -> None:
        """One engine per distinct URL and bounds: roles that share both share a
        pool, and a role given a size, a checkout bound, or a statement deadline
        of its own gets a pool of its own, which is what makes the role the
        bulkhead between two load profiles on one database. Both arguments come
        from the settings object the composition root read at boot: the impl
        reads no environment variable of its own.

        `system_urls` are the same roles under the system login. They name
        another login, so they open pools of their own under the same bounds,
        and only the system scope draws on them."""
        sessions, engines = login_sessions(urls, pools, system_urls=system_urls)
        self._engines = engines
        self._sessions = sessions
        self._tenancy = TenancyStoragePostgresImpl(sessions)
        self._work = WorkStoragePostgresImpl(sessions)
        self._media = MediaStoragePostgresImpl(sessions)
        self._idempotency = IdempotencyStoragePostgresImpl(sessions)
        self._events = EventStoragePostgresImpl(sessions)
        self._outbox = OutboxStoragePostgresImpl(sessions)
        self._orchestrations = OrchestrationsStoragePostgresImpl(sessions)

    def get_tenancy_storage(self) -> TenancyStorageInterface:
        return self._tenancy

    def get_work_storage(self) -> WorkStorageInterface:
        return self._work

    def get_media_storage(self) -> MediaStorageInterface:
        return self._media

    def get_idempotency_storage(self) -> IdempotencyStorageInterface:
        return self._idempotency

    def get_event_storage(self) -> EventStorageInterface:
        return self._events

    def get_outbox_storage(self) -> OutboxStorageInterface:
        return self._outbox

    def get_orchestrations_storage(self) -> OrchestrationsStorageInterface:
        return self._orchestrations

    async def healthcheck(self) -> bool:
        """A connect and a `SELECT 1` on every engine, each under the bounds its
        own pool declares: a checkout waits at most the checkout bound and the
        statement at most its deadline, so a saturated or unreachable pool
        answers false instead of holding the caller. The deadline here is the
        sum of the two, the worst a healthy answer can cost, and it is not a
        knob: the bounds it is made of already are."""
        try:
            for (_, pool), engine in self._engines.items():
                deadline = pool.checkout_timeout_seconds + pool.statement_timeout_seconds
                async with asyncio.timeout(deadline):
                    async with engine.connect() as connection:
                        await connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    async def close(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()
