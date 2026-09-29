"""Alembic environment: one chain and one version table per role. The role
and the connection come from `config.attributes`, set by
`acme.om.storage.migrate`; running without either is refused, so a role is
never migrated by accident, and every migration runs on the one connection
the runner opens, under its lock bound (ADR 0071)."""

from alembic import context
from sqlalchemy import Connection

from acme.om.storage.migrate import VERSION_TABLE
from acme.om.storage.roles import DatabaseRole

config = context.config


def required_role() -> DatabaseRole:
    found: DatabaseRole | None = config.attributes.get("role")
    if found is None:
        raise SystemExit("refusing to migrate without a role; run through acme.om.storage.migrate")
    return found


role = required_role()


def run_migrations(connection: Connection) -> None:
    connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{role.value}"')
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table=VERSION_TABLE,
        version_table_schema=role.value,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline mode is not supported; migrations run against a database")

connection: Connection | None = config.attributes.get("connection")
if connection is None:
    raise SystemExit(
        "refusing to migrate without the runner's connection; run through acme.om.storage.migrate"
    )
run_migrations(connection)
