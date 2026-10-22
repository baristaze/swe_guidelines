"""The wipe between integration cases: the rows one case leaves, in every
database, are gone when the next case starts. They sit behind a fence the
migration login cannot see through, as every tenant table's fence is, and in
a table that names another in a foreign key, beside one that names it and
holds no row."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from acme.om.storage.migrate import VERSION_TABLE
from acme.om.storage.roles import DatabaseRole

pytestmark = pytest.mark.integration

# A schema's three tables, in each database: a parent whose fence hides every
# row from the migration login, a child that holds a row and names it, and a
# child that names it and holds none, which the parent's truncate takes along.
TABLES = """
CREATE TABLE {schema}.wipe_parents (id integer PRIMARY KEY);
ALTER TABLE {schema}.wipe_parents ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.wipe_parents FORCE ROW LEVEL SECURITY;
CREATE POLICY hidden ON {schema}.wipe_parents USING (false) WITH CHECK (true);
CREATE TABLE {schema}.wipe_children (
    id integer PRIMARY KEY, parent_id integer NOT NULL REFERENCES {schema}.wipe_parents (id)
);
CREATE TABLE {schema}.wipe_idle_children (
    id integer PRIMARY KEY, parent_id integer NOT NULL REFERENCES {schema}.wipe_parents (id)
)"""
DROP = "DROP TABLE IF EXISTS {schema}.wipe_idle_children, {schema}.wipe_children, {schema}.wipe_parents"

left: list[str] = []
"""The schemas the first case left its rows in. Without them, as when the
second case runs alone, it has nothing to show."""


async def run(url: str, statements: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            for statement in statements.split(";"):
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


async def counted(url: str, tables: list[str]) -> dict[str, int]:
    """Each table's rows as its owner, the migration login, counts them past
    its fence, which the transaction lifts and rolls back, as a backfill
    lifts it."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            counts: dict[str, int] = {}
            for table in tables:
                await connection.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
                found = await connection.execute(text(f"SELECT count(*) FROM {table}"))
                counts[table] = found.scalar_one()
            await connection.rollback()
            return counts
    finally:
        await engine.dispose()


async def fenced_count(url: str, table: str) -> int:
    """A table's rows as the migration login sees them through the fence."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return (await connection.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def schemas(migrated: dict[DatabaseRole, str]) -> AsyncIterator[dict[str, str]]:
    """Each database's migration URL and the schema of its first role, which
    holds the three tables while this module runs."""
    found: dict[str, str] = {}
    for role, url in migrated.items():
        found.setdefault(url, role.value)
    for url, schema in found.items():
        await run(url, f"{DROP.format(schema=schema)};{TABLES.format(schema=schema)}")
    yield found
    for url, schema in found.items():
        await run(url, DROP.format(schema=schema))


async def test_a_case_leaves_rows_in_every_database(schemas: dict[str, str]) -> None:
    for url, schema in schemas.items():
        await run(
            url,
            f"INSERT INTO {schema}.wipe_parents VALUES (1);"
            f"INSERT INTO {schema}.wipe_children VALUES (1, 1)",
        )
        assert await fenced_count(url, f"{schema}.wipe_parents") == 0, "the fence shows the row"
        tables = [f"{schema}.{name}" for name in ("wipe_parents", "wipe_children")]
        assert await counted(url, tables) == dict.fromkeys(tables, 1)
        left.append(schema)


async def test_the_next_case_starts_on_empty_tables(migrated: dict[DatabaseRole, str]) -> None:
    """Every table of every role's schema but its version table, in every
    database, the three tables among them."""
    if not left:
        pytest.skip("runs after the case that leaves the rows")
    databases: dict[str, list[str]] = {}
    for role, url in migrated.items():
        databases.setdefault(url, []).append(role.value)
    for url, roles in databases.items():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                found = await connection.execute(
                    text(
                        "SELECT format('%I.%I', n.nspname, c.relname) FROM pg_class c"
                        " JOIN pg_namespace n ON n.oid = c.relnamespace"
                        " WHERE n.nspname = ANY(:schemas) AND c.relkind = 'r'"
                        " AND c.relname <> :version_table"
                    ),
                    {"schemas": roles, "version_table": VERSION_TABLE},
                )
                tables = list(found.scalars())
        finally:
            await engine.dispose()
        assert any(table.endswith(".wipe_children") for table in tables)
        assert await counted(url, tables) == dict.fromkeys(tables, 0)
