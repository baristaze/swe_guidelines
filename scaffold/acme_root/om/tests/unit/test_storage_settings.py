"""The local-database guard: a development command refuses a role URL whose
host is not local, and the migration runner refuses before it connects. With
no role URL set every role reads the one URL, the cloud's shape, and with
each set every login follows its role to its own database. The migration
runner's connection carries its lock bound, and a run that waited past it
asks to be run again."""

from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from acme.om.storage import migrate
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings, StorageSettings

REMOTE = "postgresql+asyncpg://acme:secret@db.example.internal:5432/acme"


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "[::1]",
        "postgres-core",
        "postgres-activity",
        "postgres-queue",
        "postgres-admin",
    ],
)
def test_a_local_host_passes(host: str) -> None:
    settings = StorageSettings.model_validate(
        {"_env_file": None, "database_url": f"postgresql+asyncpg://t:t@{host}:5432/t"}
    )
    settings.refuse_remote()


def test_a_remote_host_on_any_role_is_refused() -> None:
    # Away from the checkout's .env, whose role URLs would take every role
    # off the shared URL.
    shared = StorageSettings.model_validate({"_env_file": None, "database_url": REMOTE})
    with pytest.raises(SystemExit, match=r"refusing to touch core at db\.example\.internal"):
        shared.refuse_remote()
    one_role = StorageSettings.model_validate({"_env_file": None, "database_url_queue": REMOTE})
    with pytest.raises(SystemExit, match="refusing to touch queue"):
        one_role.refuse_remote()


def test_the_migration_runner_refuses_a_remote_database_with_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Away from the checkout's .env, whose role URLs would take every role
    # off the shared URL this sets.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACME_DATABASE_URL", REMOTE)
    for command in (["upgrade", "--all"], ["check", "--all"], ["downgrade", "--all", "--to", "-1"]):
        with pytest.raises(SystemExit, match="refusing to touch"):
            migrate.main([*command, "--local"])


@pytest.mark.parametrize(
    "field",
    [
        "database_pool_size",
        "database_pool_size_queue",
        "database_checkout_timeout_seconds_core",
        "database_statement_timeout_seconds_queue",
    ],
)
def test_a_zero_bound_is_refused_rather_than_read_as_unset(field: str) -> None:
    """A role's zero would fall through to the shared value, and a statement
    deadline of zero means none to Postgres; neither is what was asked for."""
    with pytest.raises(ValueError):
        StorageSettings.model_validate({"_env_file": None, field: 0})


LOCAL = "postgresql+asyncpg://{login}:{login}-pw@127.0.0.1:55432/acme"


def logins(**overrides: str) -> MigrationSettings:
    values = {
        "database_url": LOCAL.format(login="acme_runtime"),
        "database_system_url": LOCAL.format(login="acme_system"),
        "database_migration_url": LOCAL.format(login="acme_migration"),
        "database_master_url": LOCAL.format(login="acme"),
        **overrides,
    }
    return MigrationSettings.model_validate({"_env_file": None, **values})


def test_each_login_password_comes_from_its_url() -> None:
    assert logins().login_passwords() == {
        "acme_migration": "acme_migration-pw",
        "acme_runtime": "acme_runtime-pw",
        "acme_system": "acme_system-pw",
    }


def test_a_url_naming_another_login_is_refused() -> None:
    # The policies name the system login, so a URL that names another one
    # would make logins the fence does not know.
    wrong = logins(database_system_url=LOCAL.format(login="someone"))
    with pytest.raises(SystemExit, match="names the login 'someone'"):
        wrong.login_passwords()


def test_the_migrations_run_under_the_migration_login() -> None:
    urls = logins().migration_role_urls()
    assert set(urls.values()) == {LOCAL.format(login="acme_migration")}


def test_a_role_on_its_own_database_keeps_its_database_under_every_login() -> None:
    moved = logins(database_url_queue="postgresql+asyncpg://acme_runtime:r@db-q:5432/q")
    assert moved.migration_role_urls()[DatabaseRole.QUEUE] == (
        "postgresql+asyncpg://acme_migration:acme_migration-pw@db-q:5432/q"
    )
    assert moved.system_role_urls()[DatabaseRole.QUEUE] == (
        "postgresql+asyncpg://acme_system:acme_system-pw@db-q:5432/q"
    )


def test_with_no_role_url_every_role_reads_the_one_url() -> None:
    """The cloud's shape: one instance serves all four roles, so every login
    reaches every role on the one URL, and the master shapes one database."""
    settings = logins()
    for urls in (
        settings.role_urls(),
        settings.system_role_urls(),
        settings.migration_role_urls(),
    ):
        assert set(urls) == set(DatabaseRole)
        assert len(set(urls.values())) == 1
    assert settings.role_urls()[DatabaseRole.CORE] == LOCAL.format(login="acme_runtime")
    assert settings.master_databases() == {LOCAL.format(login="acme"): list(DatabaseRole)}


def test_a_role_on_each_instance_has_the_master_on_each() -> None:
    """The local stack's shape: each role on an instance of its own, so the
    master shapes four databases, one role each, under its own login."""
    own = "postgresql+asyncpg://acme_runtime:r@127.0.0.1:{port}/acme"
    ports = {DatabaseRole.CORE: 55432, DatabaseRole.ACTIVITY: 55433}
    ports |= {DatabaseRole.QUEUE: 55434, DatabaseRole.ADMIN: 55435}
    settings = logins(
        **{f"database_url_{role.value}": own.format(port=port) for role, port in ports.items()}
    )
    master = "postgresql+asyncpg://acme:acme-pw@127.0.0.1:{port}/acme"
    assert settings.master_databases() == {
        master.format(port=port): [role] for role, port in ports.items()
    }
    assert {make_url(url).port for url in settings.system_role_urls().values()} == set(
        ports.values()
    )


def test_ensure_logins_needs_the_master() -> None:
    with pytest.raises(SystemExit, match="ACME_DATABASE_MASTER_URL"):
        MigrationSettings.model_validate({"_env_file": None}).master_url()


def test_a_remote_master_is_refused_with_local() -> None:
    with pytest.raises(SystemExit, match="refusing to touch the master"):
        logins(database_master_url=REMOTE).refuse_remote()


def test_the_migration_connection_carries_the_lock_bound_in_milliseconds() -> None:
    """A server setting in the startup packet, as a pool's statement deadline
    is: every statement the runner sends waits that long for a lock at most.
    No statement deadline rides with it."""
    bound = logins(database_migration_lock_timeout_seconds="2.5")
    seconds = bound.database_migration_lock_timeout_seconds
    assert migrate.lock_bound(seconds) == {"server_settings": {"lock_timeout": "2500"}}
    assert logins().database_migration_lock_timeout_seconds == 5.0


def test_a_zero_lock_bound_is_refused() -> None:
    """Zero is no bound at all to Postgres, which is what the setting exists
    to prevent."""
    with pytest.raises(ValueError):
        logins(database_migration_lock_timeout_seconds="0")


class _Driver(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _raised(sqlstate: str) -> DBAPIError:
    """What SQLAlchemy raises over asyncpg: its error, over the adapter's,
    over the driver's, which carries the SQLSTATE."""
    adapted = Exception("adapted")
    adapted.__cause__ = _Driver(sqlstate)
    return DBAPIError("ALTER TABLE core.users ...", None, adapted)


def test_only_a_lock_wait_past_its_bound_asks_to_run_again() -> None:
    assert migrate.lock_not_granted(_raised("55P03"))
    assert not migrate.lock_not_granted(_raised("57014"))  # a statement deadline
    assert not migrate.lock_not_granted(_raised("40P01"))  # a deadlock
    assert migrate.RUN_AGAIN == 75
