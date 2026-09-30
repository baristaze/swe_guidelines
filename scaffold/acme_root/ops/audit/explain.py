"""Plans and sizes on an audit database, read the way the application reads.

    uv run python ops/audit/explain.py plans audit_<run> <statements.sql>
    uv run python ops/audit/explain.py inventory audit_<run>

`plans` runs `EXPLAIN (ANALYZE, BUFFERS)` of each statement in the file under
the login and the scope the storage funnel would use: the runtime login with
`app.org_id` (and `app.user_id`) set for a tenant, the system login for the
system scope. Row-level security is in force, as it is for the application.
Each statement runs in its own transaction, which is rolled back, so a DELETE
or an UPDATE is measured and never kept. The file names each statement with
header lines:

    -- name: the org's files, first page
    -- scope: 01900000-0000-7000-8000-00000000b16b    (or: system)
    -- user: <uuid>                                   (optional)
    -- params: 'upload', 51                           (optional: the generic plan)
    SELECT ... ;

`-- params` asks for the plan a prepared statement settles on after five
runs, the one the driver's statement cache reaches: write the statement
with `$1`, `$2` where the driver binds a value, and give the values in
order as SQL literals. It is prepared, and executed under
`plan_cache_mode = force_generic_plan`.

`inventory` lists every table of every role with its estimated rows, its
size, and each index with its size and how many scans used it since the
counters were last zeroed, so an index nothing read is visible after a run.
`reset` zeroes them: run it after the seed, whose own reads count too.

`index` creates or drops one candidate index, as the superuser, so a fix
is measured on the same data before it is proposed:

    uv run python ops/audit/explain.py index audit_<run> \
        "CREATE INDEX ix_try ON core.api_keys (org_id, user_id)"

It takes a `CREATE INDEX` or a `DROP INDEX` and nothing else; the index
lives on the audit database only, which the run drops.

`rows` prints what one `SELECT` returns, as the superuser, so a statement
file can name the ids the seed made (the busiest member, a small tenant):

    uv run python ops/audit/explain.py rows audit_<run> "SELECT id FROM core.orgs LIMIT 3"
"""

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from auditdb import check_name, superuser_on, urls
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

SYSTEM = "00000000-0000-0000-0000-000000000000"
HEADER = re.compile(r"^--\s*(name|scope|user|params)\s*:\s*(.*)$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass
class Statement:
    name: str
    sql: str
    scope: str = SYSTEM
    user: str | None = None
    params: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def system(self) -> bool:
        return self.scope == SYSTEM


def parse(source: str) -> list[Statement]:
    """The statements of a file, each with the headers above it. A statement
    ends at a line that ends with `;`; every statement needs a name and a scope."""
    found: list[Statement] = []
    headers: dict[str, str] = {}
    body: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        match = HEADER.match(stripped)
        if match and not body:
            headers[match.group(1)] = match.group(2).strip()
            continue
        if not stripped or (stripped.startswith("--") and not body):
            continue
        body.append(line)
        if stripped.endswith(";"):
            found.append(_statement(headers, "\n".join(body).rstrip().rstrip(";")))
            headers, body = {}, []
    if body:
        raise SystemExit(f"a statement has no closing ';': {body[0].strip()[:60]}")
    return found


def _statement(headers: dict[str, str], sql: str) -> Statement:
    name = headers.get("name")
    scope = headers.get("scope")
    if not name or not scope:
        raise SystemExit(f"every statement names its name and its scope: {sql.strip()[:60]}")
    scope = SYSTEM if scope == "system" else scope
    user = headers.get("user") or None
    for value in (scope, user):
        if value is not None and not UUID.match(value):
            raise SystemExit(f"{name}: {value!r} is not a uuid")
    return Statement(name, sql, scope, user, headers.get("params") or None, headers)


OPTIONS = "ANALYZE, BUFFERS, COSTS OFF"


def explain_sql(statement: Statement) -> list[str]:
    """The statements that measure one: the plan last, whose rows are the answer."""
    if statement.params is None:
        return [f"EXPLAIN ({OPTIONS}) {statement.sql}"]
    return [
        f"PREPARE audit_statement AS {statement.sql}",
        "SET LOCAL plan_cache_mode = force_generic_plan",
        f"EXPLAIN ({OPTIONS}) EXECUTE audit_statement({statement.params})",
    ]


async def _scoped(connection: AsyncConnection, statement: Statement) -> None:
    calls = [f"set_config('app.org_id', '{statement.scope}', true)"]
    if statement.user:
        calls.append(f"set_config('app.user_id', '{statement.user}', true)")
    await connection.exec_driver_sql(f"SELECT {', '.join(calls)}")


async def plans(name: str, path: Path) -> None:
    statements = parse(await asyncio.to_thread(path.read_text))
    values = urls(check_name(name))
    # A connection per statement: a prepared statement lives as long as its
    # connection, and a rollback does not take it away.
    runtime = create_async_engine(
        values["ACME_DATABASE_URL"], poolclass=NullPool, hide_parameters=True
    )
    system = create_async_engine(
        values["ACME_DATABASE_SYSTEM_URL"], poolclass=NullPool, hide_parameters=True
    )
    try:
        for statement in statements:
            engine = system if statement.system else runtime
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.exec_driver_sql(
                        "SET LOCAL max_parallel_workers_per_gather = 0"
                    )
                    await _scoped(connection, statement)
                    *setup, measure = explain_sql(statement)
                    for sql in setup:
                        await connection.exec_driver_sql(sql)
                    rows = await connection.exec_driver_sql(measure)
                    plan = "\n".join(row[0] for row in rows)
                finally:
                    await transaction.rollback()
            login = "system" if statement.system else "runtime"
            generic = ", generic plan" if statement.params is not None else ""
            print(f"#### {statement.name} ({login}, scope {statement.scope}{generic})")
            print(plan)
            print()
    finally:
        await runtime.dispose()
        await system.dispose()


INVENTORY = """
SELECT n.nspname, c.relname, c.reltuples::bigint, pg_total_relation_size(c.oid),
       coalesce(json_agg(json_build_array(i.relname, pg_relation_size(i.oid), s.idx_scan)
                         ORDER BY i.relname) FILTER (WHERE i.oid IS NOT NULL), '[]')
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_index x ON x.indrelid = c.oid
LEFT JOIN pg_class i ON i.oid = x.indexrelid
LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.oid
WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('core', 'activity', 'queue', 'admin')
  AND c.relname <> 'alembic_version'
GROUP BY n.nspname, c.relname, c.reltuples, c.oid
ORDER BY pg_total_relation_size(c.oid) DESC
"""


def megabytes(size: int) -> str:
    return f"{size / 1_048_576:.1f} MB"


async def inventory(name: str) -> None:
    engine = create_async_engine(superuser_on(check_name(name)), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            rows = (await connection.exec_driver_sql(INVENTORY)).all()
    finally:
        await engine.dispose()
    print("| Table | Rows (estimate) | Total size | Indexes (size, scans) |")
    print("|---|---|---|---|")
    for schema, table, estimate, size, indexes in rows:
        listed = "; ".join(f"{i} ({megabytes(s)}, {scans})" for i, s, scans in indexes)
        print(f"| {schema}.{table} | {max(estimate, 0)} | {megabytes(size)} | {listed} |")


async def reset(name: str) -> None:
    engine = create_async_engine(superuser_on(check_name(name)), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.exec_driver_sql("SELECT pg_stat_reset()")
    finally:
        await engine.dispose()
    print(f"{name}: index scan counters zeroed")


INDEX_DDL = re.compile(r"^\s*(CREATE\s+(UNIQUE\s+)?INDEX|DROP\s+INDEX)\s", re.IGNORECASE)


def index_statement(sql: str) -> str:
    """One CREATE INDEX or DROP INDEX, and nothing chained after it."""
    statement = sql.strip().rstrip(";")
    if not INDEX_DDL.match(statement) or ";" in statement:
        raise SystemExit("index takes one CREATE INDEX or DROP INDEX statement")
    return statement


async def index(name: str, sql: str) -> None:
    statement = index_statement(sql)
    engine = create_async_engine(superuser_on(check_name(name)), hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(statement)
            await connection.exec_driver_sql("ANALYZE")
    finally:
        await engine.dispose()
    print(f"{name}: {statement}")


READ = re.compile(r"^\s*(SELECT|WITH)\s", re.IGNORECASE)


async def rows(name: str, sql: str) -> None:
    statement = sql.strip().rstrip(";")
    if not READ.match(statement) or ";" in statement:
        raise SystemExit("rows takes one SELECT")
    engine = create_async_engine(superuser_on(check_name(name)), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                found = (await connection.exec_driver_sql(statement)).all()
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
    for row in found:
        print(" | ".join(str(value) for value in row))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="explain", description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plans", help="EXPLAIN every statement of a file under its login and scope")
    p.add_argument("name")
    p.add_argument("file", type=Path)
    i = sub.add_parser(
        "inventory", help="every table's rows and size, every index's size and scans"
    )
    i.add_argument("name")
    r = sub.add_parser("reset", help="zero the scan counters, after the seed")
    r.add_argument("name")
    x = sub.add_parser("index", help="create or drop one candidate index")
    x.add_argument("name")
    x.add_argument("sql")
    q = sub.add_parser("rows", help="what one SELECT returns, to name the seeded ids")
    q.add_argument("name")
    q.add_argument("sql")
    args = parser.parse_args(argv)
    if args.command == "plans":
        asyncio.run(plans(args.name, args.file))
    elif args.command == "inventory":
        asyncio.run(inventory(args.name))
    elif args.command == "index":
        asyncio.run(index(args.name, args.sql))
    elif args.command == "rows":
        asyncio.run(rows(args.name, args.sql))
    else:
        asyncio.run(reset(args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
