"""An audit's own database on the local Postgres: made, migrated, and
dropped by the run that uses it, and never a shared one.

    uv run python ops/audit/auditdb.py create audit_<run>
    uv run python ops/audit/auditdb.py env audit_<run>     # the URLs, as exports
    uv run python ops/audit/auditdb.py drop audit_<run>
    uv run python ops/audit/auditdb.py list                # every audit database there is

The name must start with `audit_`, and every URL must point at a local
host: a run can make and drop only a database that is plainly its own. The
logins are the local stack's (`make migrate` made them). The stack's
superuser, `postgres`, makes and drops the database and gives it to the
master, `acme`, which runs `ensure-logins` on it as `make migrate` does;
the seed runs as the superuser too, since no login it grants walks past the
row-level security policies. `ACME_AUDIT_SUPERUSER_URL` and
`ACME_DATABASE_MASTER_URL` name others.
"""

import argparse
import asyncio
import os
import re
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from acme.om.storage import migrate
from acme.om.storage.settings import LOCAL_HOSTS, MigrationSettings

PREFIX = "audit_"
NAME = re.compile(r"^audit_[a-z0-9_]{1,40}$")
MASTER_DEFAULT = "postgresql+asyncpg://acme:acme@127.0.0.1:55432/acme"
SUPERUSER = ("postgres", "postgres")
"""The local stack's superuser, as its compose file makes it."""


def check_name(name: str) -> str:
    """The one database a run may make or drop: `audit_` and a short slug."""
    if not NAME.match(name):
        raise SystemExit(f"refusing {name!r}: an audit database is named audit_<lowercase slug>")
    return name


def on_database(url: str, name: str) -> str:
    """The same login and host, on the audit database; a remote host is refused."""
    parsed = make_url(url)
    if parsed.host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing {parsed.host}: an audit database lives on the local stack")
    return parsed.set(database=name).render_as_string(hide_password=False)


def urls(name: str) -> dict[str, str]:
    """Every URL a process and the migrate module read, on the audit database."""
    check_name(name)
    settings = MigrationSettings()
    master = master_url()
    return {
        "ACME_DATABASE_URL": on_database(settings.database_url, name),
        "ACME_DATABASE_SYSTEM_URL": on_database(settings.database_system_url, name),
        "ACME_DATABASE_MIGRATION_URL": on_database(settings.database_migration_url, name),
        "ACME_DATABASE_MASTER_URL": on_database(master, name),
    }


def master_url() -> str:
    """The master's URL: the environment's, else the one `.env` names, so a
    stack on ports of its own is the one the audit reaches."""
    found = os.environ.get("ACME_DATABASE_MASTER_URL") or MigrationSettings().database_master_url
    return found or MASTER_DEFAULT


def superuser_on(database: str) -> str:
    """The local superuser's URL on a database: the maker and the seeder. It
    is `ACME_AUDIT_SUPERUSER_URL` when set, else the stack's superuser on the
    master's host and port."""
    superuser = os.environ.get("ACME_AUDIT_SUPERUSER_URL")
    if not superuser:
        user, password = SUPERUSER
        superuser = (
            make_url(master_url())
            .set(username=user, password=password, database="postgres")
            .render_as_string(hide_password=False)
        )
    return on_database(superuser, database)


def master_login() -> str:
    return make_url(master_url()).username or "acme"


async def _admin(sql: str) -> None:
    """One statement on the master's own database, outside a transaction."""
    engine = create_async_engine(
        superuser_on("postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(sql))
    finally:
        await engine.dispose()


async def exists(name: str) -> bool:
    engine = create_async_engine(superuser_on("postgres"), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            found = await connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            )
            return found.scalar() is not None
    finally:
        await engine.dispose()


def _with_env(values: dict[str, str]) -> dict[str, str | None]:
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    return saved


def _restore(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def create(name: str) -> None:
    """Make the database, then the logins' grants and every role's chain on it."""
    values = urls(check_name(name))
    if asyncio.run(exists(name)):
        raise SystemExit(f"{name} exists: drop it first, or name another run")
    asyncio.run(_admin(f'CREATE DATABASE "{name}" OWNER "{master_login()}"'))
    saved = _with_env(values)
    try:
        for argv in (["ensure-logins", "--local"], ["upgrade", "--all", "--local"]):
            if migrate.main(argv) != 0:
                raise SystemExit(f"migrate {' '.join(argv)} failed on {name}")
    finally:
        _restore(saved)
    print(f"{name}: created and migrated")


def drop(name: str) -> None:
    """Drop the database and every connection still open on it."""
    check_name(name)
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    print(f"{name}: dropped")


async def audit_databases() -> list[str]:
    engine = create_async_engine(superuser_on("postgres"), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            found = await connection.execute(
                text("SELECT datname FROM pg_database WHERE datname LIKE 'audit\\_%' ORDER BY 1")
            )
            return [row[0] for row in found]
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auditdb", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("command", choices=["create", "drop", "env", "list"])
    parser.add_argument("name", nargs="?", help="audit_<slug>")
    args = parser.parse_args(argv)
    if args.command == "list":
        print("\n".join(asyncio.run(audit_databases())) or "no audit database")
        return 0
    if args.name is None:
        parser.error("name the audit database")
    if args.command == "create":
        create(args.name)
    elif args.command == "drop":
        drop(args.name)
    else:
        for key, value in urls(args.name).items():
            print(f"export {key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
