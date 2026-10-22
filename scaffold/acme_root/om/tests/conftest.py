"""Fixtures shared by the OM test suites. The integration fixtures refuse
any database that is not a local address.

The suites connect the way a deployed process does: the storage impls under
the runtime login, with the system login's pool beside it, and the migrations
and the truncation between cases under the migration login, which owns the
tables. The master runs `ensure-logins` once per run, on each database a role
lives on: one locally per role, as the compose stack runs them."""

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from acme.om.storage.impl.pg_base import LoginSessions
from acme.om.storage.impl.postgres import login_sessions
from acme.om.storage.migrate import (
    MIGRATIONS_DIR,
    VERSION_TABLE,
    ensure_logins_everywhere,
    head,
    migrations_of,
    upgrade_all,
)
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import LOCAL_HOSTS, MigrationSettings


@pytest.fixture(scope="session")
def migration_settings() -> MigrationSettings:
    settings = MigrationSettings()
    for _, url in settings.local_urls():
        host = make_url(url).host
        assert host in LOCAL_HOSTS, f"refusing to run integration tests against {host}"
    return settings


@pytest.fixture
def checkout_ahead(tmp_path: Path) -> tuple[Path, str]:
    """Another checkout whose core chain holds one revision this one's does
    not, as a later branch's would, and that revision; every other chain is
    this one's."""
    there = migrations_of(tmp_path)
    shutil.copytree(MIGRATIONS_DIR, there, ignore=shutil.ignore_patterns("__pycache__"))
    ahead = "209912310000"
    (there / "versions" / "core" / f"{ahead}_ahead.py").write_text(
        f'revision = "{ahead}"\n'
        f'down_revision = "{head(DatabaseRole.CORE)}"\n'
        "branch_labels = None\n"
        "depends_on = None\n\n\n"
        "def upgrade() -> None:\n    pass\n\n\n"
        "def downgrade() -> None:\n    pass\n"
    )
    return tmp_path, ahead


@pytest.fixture(scope="session")
def migrated(migration_settings: MigrationSettings) -> dict[DatabaseRole, str]:
    """Every role migrated to its head, under the migration login, after the
    logins are in place; the URLs are the migration login's."""
    settings = migration_settings
    bound = settings.database_migration_lock_timeout_seconds
    asyncio.run(
        ensure_logins_everywhere(
            settings.master_databases(), settings.login_passwords(), lock_timeout_seconds=bound
        )
    )
    urls = settings.migration_role_urls()
    asyncio.run(upgrade_all(urls, lock_timeout_seconds=bound))
    return urls


@pytest.fixture
async def pg_sessions(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> AsyncIterator[LoginSessions]:
    """The session factories a storage root builds, built the way it builds
    them: every role under the runtime login and under the system login beside
    it, each pool under the bounds the settings name, so every contract case
    over Postgres runs with the pool size, the checkout bound, and the
    statement deadline a deployed process holds."""
    sessions, engines = login_sessions(
        migration_settings.role_urls(),
        migration_settings.role_pools(),
        system_urls=migration_settings.system_role_urls(),
    )
    yield sessions
    for engine in engines.values():
        await engine.dispose()


# A table written since its last truncate, in a role's schema, and every one
# that names it in a foreign key, emptied in one round trip. A truncate leaves
# a table no pages, and a row written in it adds one, so a table with none
# holds no row. Its size is the test, not its rows: the migration login is
# bound by each tenant table's fence, so it sees none of their rows, while a
# truncate passes the fence. The version table of each role says where the
# schema stands and is not this suite's to clear.
EMPTY_WRITTEN_TABLES = f"""
DO $$
DECLARE
    written text;
BEGIN
    SELECT string_agg(format('%I.%I', n.nspname, c.relname), ', ') INTO written
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ({", ".join(f"'{role.value}'" for role in DatabaseRole)})
    AND c.relkind = 'r' AND c.relname <> '{VERSION_TABLE}'
    AND pg_relation_size(c.oid) > 0;
    IF written IS NOT NULL THEN
        EXECUTE 'TRUNCATE ' || written || ' RESTART IDENTITY CASCADE';
    END IF;
END
$$"""


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def wipe_engines(migrated: dict[DatabaseRole, str]) -> AsyncIterator[list[AsyncEngine]]:
    """One engine per database under the migration login, for the whole run.
    Each test has an event loop of its own, and a connection belongs to the
    loop that opened it, so these live on the session's loop, and so does the
    wipe. Each statement commits on its own."""
    engines = [
        create_async_engine(url, isolation_level="AUTOCOMMIT")
        for url in dict.fromkeys(migrated.values())
    ]
    yield engines
    for engine in engines:
        await engine.dispose()


async def empty_written_tables(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.exec_driver_sql(EMPTY_WRITTEN_TABLES)


@pytest_asyncio.fixture(loop_scope="session")
async def emptied(wipe_engines: list[AsyncEngine]) -> None:
    """Every database's written tables emptied, the databases side by side."""
    await asyncio.gather(*(empty_written_tables(engine) for engine in wipe_engines))


@pytest.fixture(autouse=True)
def empty_schemas(request: pytest.FixtureRequest) -> None:
    """Every integration test starts on empty tables.

    These suites are the contract cases, and each one is written as if it owns
    the tables: a memory impl hands each test a fresh dict, while Postgres
    keeps what the last run left in one local database. Rows that outlive a
    test do not fail it honestly, they fail it for the wrong reason, and the
    failures move with the order the tests ran in: a `claim_pending(100)` that
    never reaches the row the case just wrote, a downgrade that cannot put a
    full unique index back over rows a soft delete left beside each other.
    Truncating is the whole isolation this suite needs, since no case depends
    on what another wrote, and it is what makes `make test-integration`
    re-runnable without `make reset` in front of it.

    Only a table written since its last truncate is truncated, with its
    identity restarted. A table the wipe passes over has had no row written
    since then, so it holds none. A unit test reaches no database, so it asks
    for none of this.
    """
    if request.node.get_closest_marker("integration") is not None:
        request.getfixturevalue("emptied")
