"""The bounds every role's pool runs under: the per-role knobs fall back to
the shared one the way the role URLs do, the engine is built with the numbers
settings name, and the healthcheck answers within them."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from acme.om.storage.impl.postgres import (
    POOL_RECYCLE_SECONDS,
    StoragePostgresImpl,
    connect_args,
    engine_for,
)
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import RolePool, StorageSettings

URL = "postgresql+asyncpg://acme:acme@127.0.0.1:55432/acme"


def pools_of(**overrides: Any) -> Mapping[DatabaseRole, RolePool]:
    return StorageSettings(database_url=URL, **overrides).role_pools()


def test_a_role_pool_falls_back_to_the_shared_bounds() -> None:
    pools = pools_of(
        database_pool_size=9,
        database_pool_size_queue=3,
        database_checkout_timeout_seconds=4.0,
        database_checkout_timeout_seconds_queue=1.5,
        database_statement_timeout_seconds=8.0,
        database_statement_timeout_seconds_activity=30.0,
    )
    assert pools[DatabaseRole.QUEUE] == RolePool(
        size=3, checkout_timeout_seconds=1.5, statement_timeout_seconds=8.0
    )
    assert pools[DatabaseRole.ACTIVITY] == RolePool(
        size=9, checkout_timeout_seconds=4.0, statement_timeout_seconds=30.0
    )
    assert pools[DatabaseRole.CORE] == RolePool(
        size=9, checkout_timeout_seconds=4.0, statement_timeout_seconds=8.0
    )


def test_every_role_carries_a_bound_with_nothing_set() -> None:
    for role, pool in pools_of().items():
        assert pool.size > 0, role
        assert pool.checkout_timeout_seconds > 0, role
        assert pool.statement_timeout_seconds > 0, role


def test_the_worker_can_hold_a_connection_and_a_lease_per_item() -> None:
    # ACME_WORKER_CAPACITY is 4 and an item may hold a storage connection and
    # a lease renewal at once; the pool the process draws on serves both, with
    # room left for the claim, the sweep, and the relay.
    assert pools_of()[DatabaseRole.QUEUE].size >= 2 * 4


def test_the_statement_deadline_reaches_postgres_on_every_connection() -> None:
    args = connect_args(RolePool(size=1, checkout_timeout_seconds=2.5, statement_timeout_seconds=7))
    assert args["server_settings"] == {"statement_timeout": "7000"}
    assert args["timeout"] == 2.5


def test_the_engine_is_built_with_the_bounds_the_settings_name() -> None:
    bounds = RolePool(size=6, checkout_timeout_seconds=2.5, statement_timeout_seconds=7)
    pool = cast(QueuePool, engine_for(URL, bounds).pool)
    assert pool.size() == 6
    assert pool._max_overflow == 0
    assert pool.timeout() == 2.5


def test_the_pool_recycles_before_the_path_drops_an_idle_connection_and_sends_no_ping() -> None:
    """The shortest idle cutoff on the path is the security group's 350
    seconds. A dead connection is caught on the scope, the transaction's first
    statement, not by a ping before every checkout."""
    pool = cast(QueuePool, engine_for(URL, RolePool(1, 1.0, 1.0)).pool)
    assert pool._recycle == POOL_RECYCLE_SECONDS < 350
    assert pool._pre_ping is False


async def test_roles_share_a_pool_only_when_url_and_bounds_agree() -> None:
    shared = RolePool(size=4, checkout_timeout_seconds=1.0, statement_timeout_seconds=2.0)
    urls = dict.fromkeys(DatabaseRole, URL)

    root = StoragePostgresImpl(urls, dict.fromkeys(DatabaseRole, shared), system_urls=urls)
    assert len(root._engines) == 1
    await root.close()

    split = dict.fromkeys(DatabaseRole, shared)
    split[DatabaseRole.QUEUE] = RolePool(
        size=8, checkout_timeout_seconds=1.0, statement_timeout_seconds=2.0
    )
    root = StoragePostgresImpl(urls, split, system_urls=urls)
    assert len(root._engines) == 2
    await root.close()


class HangingEngine:
    """An engine whose connect never returns: a pool with nothing to give."""

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[None]:
        await asyncio.Event().wait()
        yield None

    async def dispose(self) -> None:
        return None


async def test_the_healthcheck_answers_false_within_its_role_bounds() -> None:
    pool = RolePool(size=1, checkout_timeout_seconds=0.05, statement_timeout_seconds=0.05)
    urls = dict.fromkeys(DatabaseRole, URL)
    root = StoragePostgresImpl(urls, dict.fromkeys(DatabaseRole, pool), system_urls=urls)
    await root.close()
    root._engines = {(URL, pool): cast(AsyncEngine, HangingEngine())}

    async with asyncio.timeout(1.0):
        assert await root.healthcheck() is False
