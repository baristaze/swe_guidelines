"""The migration runner: one Alembic chain and one version table per role,
each migration a pair of hand-written SQL files behind a thin wrapper.

Every migration runs under the migration login, which owns the schema. The
master opens one command only, `ensure-logins`, which makes the three logins
and hands the migration login what it owns (`acme.om.storage.logins`).

Every statement the runner sends waits for a lock
`ACME_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS` at most. A run that gives up
waiting applied nothing of the role it was on and exits `RUN_AGAIN`, which
asks for the same command again (ADR 0071).

Run as a module: `python -m acme.om.storage.migrate ensure-logins`, then
`python -m acme.om.storage.migrate upgrade --all`.
"""

import argparse
import asyncio
import importlib
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alembic import command, op
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, MetaData
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from acme.om.storage.logins import ensure_logins
from acme.om.storage.roles import DROPPED_TABLE_ROLES, TABLE_ROLES, DatabaseRole, role_for
from acme.om.storage.settings import MIGRATION_LOCK_TIMEOUT_SECONDS, MigrationSettings
from acme.om.storage.tables.base import Base

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "migrations"
VERSION_TABLE = "alembic_version"
_SCHEMA_REF = re.compile(r"\b(core|activity|queue|admin)\.([a-z_][a-z0-9_]*)")
_INDEX_REF = re.compile(
    r"\b(?:DROP|ALTER) INDEX\s+(?:IF EXISTS\s+)?(core|activity|queue|admin)\.([a-z_][a-z0-9_]*)"
    r"|\bFUNCTION\s+(?:IF EXISTS\s+)?(core|activity|queue|admin)\.([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
LOCK_NOT_AVAILABLE = "55P03"
"""The SQLSTATE of a statement that waited for a lock past `lock_timeout`."""
RUN_AGAIN = os.EX_TEMPFAIL
"""The exit code of a run that gave up waiting for a lock: 75, the temporary
failure of sysexits. The role it was on rolled back, so running the same
command again is the whole remedy; the deploy's pre-rollout task does that a
bounded number of times."""


def alembic_config(role: DatabaseRole) -> Config:
    config = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions" / role.value))
    config.attributes["role"] = role
    return config


def head(role: DatabaseRole) -> str | None:
    """The single head of a role's chain; more than one is a real conflict."""
    heads = ScriptDirectory.from_config(alembic_config(role)).get_heads()
    if len(heads) > 1:
        raise RuntimeError(f"role {role.value} has {len(heads)} heads: {heads}")
    return heads[0] if heads else None


def split_statements(sql: str) -> list[str]:
    """Hand-written DDL splits on the semicolon, except inside a body quoted
    with `$$` (a function's), whose semicolons are the body's own."""
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    statements: list[str] = []
    current: list[str] = []
    quoted = False
    for part in re.split(r"(\$\$)", "\n".join(lines)):
        if part == "$$" or quoted:
            quoted = quoted != (part == "$$")
            current.append(part)
            continue
        first, *rest = part.split(";")
        current.append(first)
        for piece in rest:
            statements.append("".join(current))
            current = [piece]
    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


def check_role_of_sql(role: DatabaseRole, sql: str) -> None:
    """Refuses a file that names a table of another role, by schema or by the role map.
    A dropped or altered index, and a function, are schema-qualified too; only
    their schema is checked."""
    for match in _INDEX_REF.finditer(sql):
        schema, name = match.group(1, 2) if match.group(1) else match.group(3, 4)
        if schema != role.value:
            raise RuntimeError(f"{role.value} migration names {schema}.{name}")
    for schema, table in _SCHEMA_REF.findall(_INDEX_REF.sub("", sql)):
        if schema != role.value:
            raise RuntimeError(f"{role.value} migration references {schema}.{table}")
        if table == VERSION_TABLE:
            continue
        owner = DROPPED_TABLE_ROLES.get(table) or role_for(table)
        if owner is not role:
            raise RuntimeError(f"table {table} belongs to role {owner.value}")


def run_sql(role: DatabaseRole, filename: str) -> None:
    """Called by a wrapper under versions/<role>/ to execute sql/<role>/<filename>."""
    sql = (MIGRATIONS_DIR / "sql" / role.value / filename).read_text()
    check_role_of_sql(role, sql)
    bind = op.get_bind()
    for statement in split_statements(sql):
        bind.exec_driver_sql(statement)


def backfill(
    connection: Connection, role: DatabaseRole, table: str, count_sql: str, update_sql: str
) -> int:
    """A data migration under the fence it runs beneath. The migration login
    owns the table and FORCE binds the owner, and a migration names no tenant,
    so a bare UPDATE would match no row and succeed. This lifts the fence for
    its own statements and puts it back, in the caller's transaction.

    A backfill that touched nothing looks like one that had nothing to do, so
    the rows it means to touch are counted first (`count_sql`, one number) and
    compared with the rows `update_sql` reports. A difference fails, and the
    whole transaction rolls back, the fence included. Returns the count."""
    if role_for(table) is not role:
        raise RuntimeError(f"table {table} belongs to role {role_for(table).value}")
    check_role_of_sql(role, f"{count_sql};{update_sql}")
    qualified = f"{role.value}.{table}"
    connection.exec_driver_sql(f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY")
    expected = connection.exec_driver_sql(count_sql).scalar_one()
    touched = connection.exec_driver_sql(update_sql).rowcount
    if touched != expected:
        raise RuntimeError(f"backfill of {qualified} touched {touched} rows of {expected}")
    connection.exec_driver_sql(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    return touched


def run_backfill(role: DatabaseRole, table: str, count_sql: str, update_sql: str) -> int:
    """Called by a wrapper under versions/<role>/ for a data migration; see `backfill`."""
    return backfill(op.get_bind(), role, table, count_sql, update_sql)


def load_tables() -> None:
    """Imports every namespace's table modules so Base.metadata holds them all.
    Without this the CLI compares an empty metadata and reports every migrated
    table as drift; a table named in TABLE_ROLES but never defined is refused."""
    om_root = Path(__file__).resolve().parents[1]
    for path in sorted(om_root.glob("*/storage/tables/*.py")):
        if path.stem != "__init__":
            relative = path.relative_to(om_root).with_suffix("")
            importlib.import_module(f"acme.om.{'.'.join(relative.parts)}")
    missing = sorted(
        name for name in TABLE_ROLES if name not in {t.name for t in Base.metadata.tables.values()}
    )
    if missing:
        raise RuntimeError(f"tables in TABLE_ROLES without an ORM definition: {missing}")


def role_metadata(role: DatabaseRole) -> MetaData:
    load_tables()
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        if table.schema == role.value:
            table.to_metadata(metadata)
    return metadata


def schema_diff(connection: Connection, role: DatabaseRole) -> list[Any]:
    """The differences between the ORM metadata and the migrated schema of one role."""

    def include_name(name: str | None, type_: str, parent_names: dict[str, Any]) -> bool:
        if type_ == "schema":
            return name == role.value
        if type_ == "table":
            return name != VERSION_TABLE
        return True

    context = MigrationContext.configure(
        connection,
        opts={
            "include_schemas": True,
            "include_name": include_name,
            "compare_type": True,
            "compare_server_default": False,
        },
    )
    return compare_metadata(context, role_metadata(role))


def lock_bound(lock_timeout_seconds: float) -> dict[str, Any]:
    """What the runner's connection is opened with. `lock_timeout` is a server
    setting in the startup packet, so every statement on the connection waits
    for a lock that long at most and then fails with LOCK_NOT_AVAILABLE. There
    is no `statement_timeout`: a backfill may run long, and a lock it holds is
    one it was granted."""
    milliseconds = round(lock_timeout_seconds * 1000)
    return {"server_settings": {"lock_timeout": str(milliseconds)}}


def lock_not_granted(error: DBAPIError) -> bool:
    """Whether a statement failed because it waited for a lock past its bound.
    The asyncpg adapter wraps the driver's error, which carries the SQLSTATE,
    as the cause of the one SQLAlchemy raises."""
    cause: BaseException | None = error.orig
    while cause is not None:
        if getattr(cause, "sqlstate", None) == LOCK_NOT_AVAILABLE:
            return True
        cause = cause.__cause__
    return False


async def _with_connection(
    url: str, work: Callable[[Connection], Any], lock_timeout_seconds: float
) -> Any:
    """The one connection every command of the runner opens, under the lock
    bound, with the work in one transaction."""
    engine = create_async_engine(
        url, connect_args=lock_bound(lock_timeout_seconds), hide_parameters=True
    )
    try:
        async with engine.begin() as connection:
            return await connection.run_sync(work)
    finally:
        await engine.dispose()


async def upgrade(
    role: DatabaseRole,
    url: str,
    revision: str = "head",
    *,
    lock_timeout_seconds: float = MIGRATION_LOCK_TIMEOUT_SECONDS,
) -> None:
    def work(connection: Connection) -> None:
        config = alembic_config(role)
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    await _with_connection(url, work, lock_timeout_seconds)


async def downgrade(
    role: DatabaseRole,
    url: str,
    revision: str,
    *,
    lock_timeout_seconds: float = MIGRATION_LOCK_TIMEOUT_SECONDS,
) -> None:
    def work(connection: Connection) -> None:
        config = alembic_config(role)
        config.attributes["connection"] = connection
        command.downgrade(config, revision)

    await _with_connection(url, work, lock_timeout_seconds)


async def check(
    role: DatabaseRole, url: str, *, lock_timeout_seconds: float = MIGRATION_LOCK_TIMEOUT_SECONDS
) -> list[Any]:
    return await _with_connection(
        url, lambda connection: schema_diff(connection, role), lock_timeout_seconds
    )


async def upgrade_all(
    urls: dict[DatabaseRole, str], *, lock_timeout_seconds: float = MIGRATION_LOCK_TIMEOUT_SECONDS
) -> None:
    for role in DatabaseRole:
        await upgrade(role, urls[role], lock_timeout_seconds=lock_timeout_seconds)


async def ensure_logins_at(
    master_url: str,
    passwords: dict[str, str],
    *,
    lock_timeout_seconds: float = MIGRATION_LOCK_TIMEOUT_SECONDS,
) -> None:
    """`ensure_logins` in one transaction on the master's connection."""
    await _with_connection(
        master_url, lambda connection: ensure_logins(connection, passwords), lock_timeout_seconds
    )


def gave_up(what: str, lock_timeout_seconds: float) -> int:
    """Says that a run waited past its lock bound, and returns RUN_AGAIN."""
    print(
        f"{what}: a lock was not granted within {lock_timeout_seconds:g} s, so nothing of it"
        " was applied; run the command again",
        file=sys.stderr,
    )
    return RUN_AGAIN


def _roles_from_args(args: argparse.Namespace) -> list[DatabaseRole]:
    if args.all and args.role:
        raise SystemExit("name --role or --all, not both")
    if args.all:
        return list(DatabaseRole)
    if args.role:
        return [DatabaseRole(args.role)]
    raise SystemExit("refusing to guess: name --role <role> or --all")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acme-migrate")
    sub = parser.add_subparsers(dest="command", required=True)
    logins = sub.add_parser(
        "ensure-logins",
        help="as the master: the three logins, the migration login's ownership, the grants",
    )
    logins.add_argument(
        "--local",
        action="store_true",
        help="refuse a database whose host is not local; every Makefile target passes it",
    )
    for name in ("upgrade", "downgrade", "check"):
        p = sub.add_parser(name)
        p.add_argument("--role", choices=[r.value for r in DatabaseRole])
        p.add_argument("--all", action="store_true")
        p.add_argument(
            "--local",
            action="store_true",
            help="refuse a database whose host is not local; every Makefile target passes it",
        )
        if name == "downgrade":
            p.add_argument("--to", required=True, help="revision, or -1 for one step")
    args = parser.parse_args(argv)
    settings = MigrationSettings()
    if args.local:
        settings.refuse_remote()
    bound = settings.database_migration_lock_timeout_seconds
    if args.command == "ensure-logins":
        try:
            asyncio.run(
                ensure_logins_at(
                    settings.master_url(), settings.login_passwords(), lock_timeout_seconds=bound
                )
            )
        except DBAPIError as error:
            if not lock_not_granted(error):
                raise
            return gave_up("logins", bound)
        print("logins: the migration login owns every role schema; the serving logins hold DML")
        return 0
    urls = settings.migration_role_urls()
    roles = _roles_from_args(args)

    async def run() -> int:
        failed = 0
        for role in roles:
            try:
                if args.command == "upgrade":
                    await upgrade(role, urls[role], lock_timeout_seconds=bound)
                    print(f"{role.value}: upgraded to {head(role) or 'empty'}")
                elif args.command == "downgrade":
                    await downgrade(role, urls[role], args.to, lock_timeout_seconds=bound)
                    print(f"{role.value}: downgraded to {args.to}")
                else:
                    diff = await check(role, urls[role], lock_timeout_seconds=bound)
                    print(f"{role.value}: {'in sync' if not diff else diff}")
                    failed += bool(diff)
            except DBAPIError as error:
                if not lock_not_granted(error):
                    raise
                return gave_up(role.value, bound)
        return 1 if failed else 0

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
