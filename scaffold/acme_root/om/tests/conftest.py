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
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

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


@pytest.fixture(autouse=True)
async def empty_schemas(request: pytest.FixtureRequest) -> None:
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
    """
    if request.node.get_closest_marker("integration") is None:
        return
    urls: dict[DatabaseRole, str] = request.getfixturevalue("migrated")
    for url in dict.fromkeys(urls.values()):
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                schemas = ", ".join(f"'{role.value}'" for role in DatabaseRole)
                # Every table but each role's migration bookkeeping, which
                # says where the schema stands and is not this suite's to
                # clear.
                rows = await connection.execute(
                    text(
                        "SELECT table_schema, table_name FROM information_schema.tables"
                        f" WHERE table_schema IN ({schemas}) AND table_type = 'BASE TABLE'"
                        " AND table_name <> :version_table"
                    ),
                    {"version_table": VERSION_TABLE},
                )
                tables = [f'{schema}."{name}"' for schema, name in rows]
                if tables:
                    await connection.execute(
                        text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
                    )
        finally:
            await engine.dispose()
