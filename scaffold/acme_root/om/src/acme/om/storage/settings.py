"""The storage half of every process's settings object: one URL per role and
one set of pool bounds per role, each defaulting to the shared one."""

from dataclasses import dataclass

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from acme.om.storage.logins import (
    MIGRATION_LOGIN,
    RUNTIME_LOGIN,
    SYSTEM_LOGIN,
    login_of,
    with_login,
)
from acme.om.storage.roles import DatabaseRole

LOCAL_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "::1",
        "postgres-core",
        "postgres-activity",
        "postgres-queue",
        "postgres-admin",
    }
)
"""Where a development seed may write; the compose stack's service names
included, one instance per role."""

MIGRATION_LOCK_TIMEOUT_SECONDS = 5.0
"""The default of `database_migration_lock_timeout_seconds`: half the serving
statements' deadline, so a request queued behind a migration that waits still
ends inside its own."""


@dataclass(frozen=True)
class RolePool:
    """The bounds one role's pool runs under: how many connections it opens, how
    long a checkout waits before it fails, and the deadline every statement on
    it carries. Both bounds are in seconds."""

    size: int
    checkout_timeout_seconds: float
    statement_timeout_seconds: float


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACME_", env_file=".env", extra="ignore")

    # The runtime login's URL, which every request's connection uses, and the
    # system login's beside it, which only the listed system-scope methods
    # use, on a pool of their own. A role on a database of its own names its
    # own URL below, and the system login follows it there. Unset, every role
    # reads the one URL, as the cloud's one instance serves all four; the
    # local stack sets all four, one instance per role.
    database_url: str = "postgresql+asyncpg://acme_runtime:acme_runtime@127.0.0.1:55432/acme"
    database_system_url: str = "postgresql+asyncpg://acme_system:acme_system@127.0.0.1:55432/acme"
    database_url_core: str | None = None
    database_url_activity: str | None = None
    database_url_queue: str | None = None
    database_url_admin: str | None = None

    # Every role's pool declares its size and the bound on waiting for a
    # connection, and every statement it runs carries a deadline, so a role
    # under load fails the call that could not get a connection instead of
    # queueing without end, and a query that hangs costs one call and not a
    # connection held until it dies. The size is the whole pool: the storage
    # root opens no connection past it. The shared value serves every role;
    # a role with its own load profile takes its own. Every value is positive:
    # a zero would read as "unset" below and silently take the shared value.
    database_pool_size: int = Field(default=12, gt=0)
    database_pool_size_core: int | None = Field(default=None, gt=0)
    database_pool_size_activity: int | None = Field(default=None, gt=0)
    database_pool_size_queue: int | None = Field(default=None, gt=0)
    database_pool_size_admin: int | None = Field(default=None, gt=0)

    database_checkout_timeout_seconds: float = Field(default=5.0, gt=0)
    database_checkout_timeout_seconds_core: float | None = Field(default=None, gt=0)
    database_checkout_timeout_seconds_activity: float | None = Field(default=None, gt=0)
    database_checkout_timeout_seconds_queue: float | None = Field(default=None, gt=0)
    database_checkout_timeout_seconds_admin: float | None = Field(default=None, gt=0)

    database_statement_timeout_seconds: float = Field(default=10.0, gt=0)
    database_statement_timeout_seconds_core: float | None = Field(default=None, gt=0)
    database_statement_timeout_seconds_activity: float | None = Field(default=None, gt=0)
    database_statement_timeout_seconds_queue: float | None = Field(default=None, gt=0)
    database_statement_timeout_seconds_admin: float | None = Field(default=None, gt=0)

    def refuse_remote(self) -> None:
        """For a development command (the seed, `make migrate`, the integration
        tests): refuses a role URL whose host is not local, so a stray `.env`
        never points a local command at a shared database."""
        for role, url in self.local_urls():
            host = make_url(url).host
            if host not in LOCAL_HOSTS:
                raise SystemExit(
                    f"refusing to touch {role} at {host}: ACME_DATABASE_URL must be local"
                )

    def local_urls(self) -> list[tuple[str, str]]:
        """Every URL a local command may open, named for the refusal."""
        return [
            *((role.value, url) for role, url in self.role_urls().items()),
            *((f"{role.value} (system)", url) for role, url in self.system_role_urls().items()),
        ]

    def system_role_urls(self) -> dict[DatabaseRole, str]:
        """Every role's URL under the system login (see `under_login`)."""
        return self.under_login(self.database_system_url)

    def role_overrides(self) -> dict[DatabaseRole, str | None]:
        """The URL each role names of its own, or None for the shared one."""
        return {
            DatabaseRole.CORE: self.database_url_core,
            DatabaseRole.ACTIVITY: self.database_url_activity,
            DatabaseRole.QUEUE: self.database_url_queue,
            DatabaseRole.ADMIN: self.database_url_admin,
        }

    def role_urls(self) -> dict[DatabaseRole, str]:
        return {
            role: override or self.database_url for role, override in self.role_overrides().items()
        }

    def under_login(self, login_url: str) -> dict[DatabaseRole, str]:
        """Every role's URL under another login: the login's own URL for a role
        on the shared database, and the role's database under the login's user
        and password for a role that moved to its own."""
        return {
            role: with_login(override, login_url) if override else login_url
            for role, override in self.role_overrides().items()
        }

    def role_pools(self) -> dict[DatabaseRole, RolePool]:
        """The bounds of every role's pool, each knob defaulting to its shared
        value the way a role URL does. Nothing here is optional: a role whose
        settings name no override still carries a size, a checkout bound, and a
        statement deadline, so no engine is built on a library default."""
        sizes = {
            DatabaseRole.CORE: self.database_pool_size_core,
            DatabaseRole.ACTIVITY: self.database_pool_size_activity,
            DatabaseRole.QUEUE: self.database_pool_size_queue,
            DatabaseRole.ADMIN: self.database_pool_size_admin,
        }
        checkouts = {
            DatabaseRole.CORE: self.database_checkout_timeout_seconds_core,
            DatabaseRole.ACTIVITY: self.database_checkout_timeout_seconds_activity,
            DatabaseRole.QUEUE: self.database_checkout_timeout_seconds_queue,
            DatabaseRole.ADMIN: self.database_checkout_timeout_seconds_admin,
        }
        statements = {
            DatabaseRole.CORE: self.database_statement_timeout_seconds_core,
            DatabaseRole.ACTIVITY: self.database_statement_timeout_seconds_activity,
            DatabaseRole.QUEUE: self.database_statement_timeout_seconds_queue,
            DatabaseRole.ADMIN: self.database_statement_timeout_seconds_admin,
        }
        return {
            role: RolePool(
                size=sizes[role] or self.database_pool_size,
                checkout_timeout_seconds=(
                    checkouts[role] or self.database_checkout_timeout_seconds
                ),
                statement_timeout_seconds=(
                    statements[role] or self.database_statement_timeout_seconds
                ),
            )
            for role in DatabaseRole
        }


class MigrationSettings(StorageSettings):
    """What the migrate command reads beyond a process's settings: the
    migration login's URL, which runs every migration, and the master's, which
    only `ensure-logins` opens. No serving process holds either; the deploy's
    one-off migrate task is the one place both are set."""

    database_migration_url: str = (
        "postgresql+asyncpg://acme_migration:acme_migration@127.0.0.1:55432/acme"
    )
    database_master_url: str | None = None

    # The longest a statement of the migrate command waits for a lock, in
    # seconds. A migration's DDL waits behind any transaction that holds its
    # table, and every read and write of the table queues behind the DDL while
    # it waits. Past the bound the statement fails, the role's chain rolls
    # back, and the command asks to be run again (ADR 0071). The statements
    # carry no deadline of their own: a backfill may run long.
    database_migration_lock_timeout_seconds: float = Field(
        default=MIGRATION_LOCK_TIMEOUT_SECONDS, gt=0
    )

    def migration_role_urls(self) -> dict[DatabaseRole, str]:
        """Every role's URL under the migration login."""
        return self.under_login(self.database_migration_url)

    def master_url(self) -> str:
        if not self.database_master_url:
            raise SystemExit("ensure-logins runs as the master: set ACME_DATABASE_MASTER_URL")
        return self.database_master_url

    def master_databases(self) -> dict[str, list[DatabaseRole]]:
        """Every database a role lives on, as the master's URL there, with the
        roles it holds: one entry with all four on the cloud's one instance,
        and one per role on the local stack's. `ensure-logins` runs once on
        each, since a login, a grant, and a schema's owner live on one
        instance and no other sees them."""
        found: dict[str, list[DatabaseRole]] = {}
        for role, url in self.under_login(self.master_url()).items():
            found.setdefault(url, []).append(role)
        return found

    def login_passwords(self) -> dict[str, str]:
        """Each login's password, from the URL that names it; a URL that names
        another login is refused, since the policies name these three."""
        return dict(
            (
                login_of(self.database_migration_url, MIGRATION_LOGIN),
                login_of(self.database_url, RUNTIME_LOGIN),
                login_of(self.database_system_url, SYSTEM_LOGIN),
            )
        )

    def local_urls(self) -> list[tuple[str, str]]:
        found = [
            *super().local_urls(),
            *(
                (f"{role.value} (migration)", url)
                for role, url in self.migration_role_urls().items()
            ),
        ]
        if self.database_master_url:
            found.append(("the master", self.database_master_url))
            found.extend(
                (f"{', '.join(role.value for role in roles)} (master)", url)
                for url, roles in self.master_databases().items()
            )
        return found
