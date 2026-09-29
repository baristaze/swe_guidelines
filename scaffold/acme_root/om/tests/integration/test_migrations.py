"""Every role's migrated schema agrees with the ORM metadata, the latest
revision of every role downgrades and upgrades again, the logins are safe to
make twice, a migration behind a held lock gives up within its bound, and a
data migration passes the fence it runs under and fails when it misses rows."""

import asyncio
import time

import pytest
from contracts.event_storage import make_event
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine

from acme.om.base import new_id
from acme.om.events.storage.impl.postgres import EventStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions
from acme.om.storage.migrate import (
    RUN_AGAIN,
    VERSION_TABLE,
    backfill,
    check,
    downgrade,
    ensure_logins_at,
    head,
    main,
    upgrade,
)
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", list(DatabaseRole))
async def test_orm_and_schema_agree(migrated: dict[DatabaseRole, str], role: DatabaseRole) -> None:
    assert await check(role, migrated[role]) == []


@pytest.mark.parametrize("role", list(DatabaseRole))
async def test_latest_revision_round_trips(
    migrated: dict[DatabaseRole, str], role: DatabaseRole
) -> None:
    if head(role) is None:
        pytest.skip(f"role {role.value} has no migrations yet")
    await downgrade(role, migrated[role], "-1")
    await upgrade(role, migrated[role])
    assert await check(role, migrated[role]) == []


async def test_ensure_logins_runs_again_on_a_migrated_database(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> None:
    """The deploy runs it before every migrate, so the second run over a
    database it already shaped changes nothing and fails nothing."""
    settings = migration_settings
    await ensure_logins_at(settings.master_url(), settings.login_passwords())
    for role in DatabaseRole:
        assert await check(role, migrated[role]) == []


async def test_a_migration_behind_a_held_lock_gives_up_within_its_bound(
    migrated: dict[DatabaseRole, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A transaction holds core's version table, which every run of core's
    chain reads first, so it stands for a lock on any table a migration
    touches. With the bound at one second, `migrate upgrade` gives up after
    that second, applies nothing, and exits RUN_AGAIN, which the deploy's
    pre-rollout task runs again on. Once the transaction ends, the same
    command applies the revision. Without the bound, the run waited for as
    long as the transaction stayed open."""
    core = migrated[DatabaseRole.CORE]
    await downgrade(DatabaseRole.CORE, core, "-1")
    before = await on_core(core, f"SELECT version_num FROM core.{VERSION_TABLE}")
    monkeypatch.setenv("ACME_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS", "1")
    holder = create_async_engine(core)
    try:
        async with holder.begin() as held:
            await held.execute(text(f"LOCK TABLE core.{VERSION_TABLE} IN ACCESS EXCLUSIVE MODE"))
            started = time.monotonic()
            code = await asyncio.to_thread(main, ["upgrade", "--role", "core"])
            waited = time.monotonic() - started
    finally:
        await holder.dispose()
    assert code == RUN_AGAIN
    # The second of waiting, and what it costs to open a connection and read
    # the chain around it.
    assert 1.0 <= waited < 3.0
    assert "core: a lock was not granted within 1 s" in capsys.readouterr().err
    assert await on_core(core, f"SELECT version_num FROM core.{VERSION_TABLE}") == before
    assert await asyncio.to_thread(main, ["upgrade", "--role", "core"]) == 0
    assert await check(DatabaseRole.CORE, core) == []


async def seed_two_tenants(pg_sessions: LoginSessions) -> None:
    events = EventStoragePostgresImpl(pg_sessions)
    for org in (new_id(), new_id()):
        await events.append_events(org, [make_event(org)])


def forced(connection: Connection) -> bool:
    return connection.exec_driver_sql(
        "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'activity.events'::regclass"
    ).scalar_one()


COUNT = "SELECT count(*) FROM activity.events"
UPDATE = "UPDATE activity.events SET kind = kind"


async def test_a_backfill_under_the_fence_touches_every_tenant(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    """A migration names no tenant, and FORCE binds the migration login that
    owns the table, so a bare UPDATE matches no row and succeeds; its count
    says zero too, and zero equals zero. Seeded with two tenants' rows, the
    backfill counts and touches both, and the fence is back after it."""
    await seed_two_tenants(pg_sessions)
    engine = create_async_engine(migrated[DatabaseRole.ACTIVITY])
    try:
        async with engine.connect() as connection:

            def bare(sync: Connection) -> tuple[int, int]:
                expected = sync.exec_driver_sql(COUNT).scalar_one()
                return expected, sync.exec_driver_sql(UPDATE).rowcount

            assert await connection.run_sync(bare) == (0, 0)
            await connection.rollback()

            def fenced(sync: Connection) -> tuple[int, bool]:
                touched = backfill(sync, DatabaseRole.ACTIVITY, "events", COUNT, UPDATE)
                return touched, forced(sync)

            assert await connection.run_sync(fenced) == (2, True)
            await connection.rollback()
    finally:
        await engine.dispose()


async def test_a_backfill_that_misses_rows_fails_and_keeps_the_fence(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    await seed_two_tenants(pg_sessions)
    engine = create_async_engine(migrated[DatabaseRole.ACTIVITY])
    try:
        async with engine.connect() as connection:
            partial = (
                "UPDATE activity.events SET kind = kind WHERE seq = 1"
                " AND org_id = (SELECT org_id FROM activity.events ORDER BY org_id LIMIT 1)"
            )
            with pytest.raises(RuntimeError, match="touched 1 rows of 2"):
                await connection.run_sync(
                    lambda sync: backfill(sync, DatabaseRole.ACTIVITY, "events", COUNT, partial)
                )
            await connection.rollback()
            assert await connection.run_sync(forced) is True
    finally:
        await engine.dispose()


async def on_core(url: str, sql: str) -> list[tuple[object, ...]]:
    """One statement on the core role, as the migration login, which owns the
    version table the lock test reads."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            result = await connection.exec_driver_sql(sql)
            return [tuple(row) for row in result] if result.returns_rows else []
    finally:
        await engine.dispose()
