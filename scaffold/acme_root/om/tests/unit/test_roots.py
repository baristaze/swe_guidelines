import dataclasses
import inspect
import typing
from pathlib import Path

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.impl.configured import absent_integrations
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.root import StorageInterface
from acme.om.storage.settings import StorageSettings
from acme.om.tenancy import (
    TenancyCredentialsManagerInterface,
    TenancyMembersManagerInterface,
    TenancyOrgManagerInterface,
    TenancySignInManagerInterface,
)
from acme.om.tenancy.storage import TenancyStorageInterface


def storage_getters() -> list[tuple[str, type]]:
    """Every namespace's getter on the storage root, with the interface it
    answers: read off the root itself, so a namespace a copy adds is held
    here with no line of this file changed."""
    return [
        (name, typing.get_type_hints(member)["return"])
        for name, member in inspect.getmembers(StorageInterface, inspect.isfunction)
        if name.startswith("get_") and name.endswith("_storage")
    ]


async def test_memory_root_serves_every_storage() -> None:
    root = StorageMemoryImpl()
    getters = storage_getters()
    assert getters, "the storage root declares no getter"
    for name, interface in getters:
        assert isinstance(getattr(root, name)(), interface), name
    assert await root.healthcheck() is True
    await root.close()


async def test_postgres_root_opens_one_engine_per_distinct_url_and_login() -> None:
    shared = "postgresql+asyncpg://acme_runtime:r@127.0.0.1:55432/acme"
    system = "postgresql+asyncpg://acme_system:s@127.0.0.1:55432/acme"
    # Every argument comes from a settings object, the way a composition root
    # hands them over; the impl reads nothing itself.
    # Away from the checkout's .env, whose role URLs would split the roles.
    settings = StorageSettings.model_validate(
        {"_env_file": None, "database_url": shared, "database_system_url": system}
    )
    pools = settings.role_pools()
    root = StoragePostgresImpl(settings.role_urls(), pools, system_urls=settings.system_role_urls())
    assert isinstance(root.get_tenancy_storage(), TenancyStorageInterface)
    # One pool under the runtime login and one under the system login, each
    # with its reserve beside it.
    assert sorted(reserve for _, _, reserve in root._engines) == [False, False, True, True]
    await root.close()

    split = StorageSettings.model_validate(
        {
            "_env_file": None,
            "database_url": shared,
            "database_system_url": system,
            "database_url_queue": "postgresql+asyncpg://acme_runtime:r@127.0.0.1:55432/acme_queue",
        }
    )
    root = StoragePostgresImpl(split.role_urls(), pools, system_urls=split.system_role_urls())
    assert len([key for key in root._engines if not key[2]]) == 4
    await root.close()


def test_role_urls_default_to_the_shared_one() -> None:
    settings = StorageSettings.model_validate(
        {
            "_env_file": None,
            "database_url": "postgresql+asyncpg://x@127.0.0.1/a",
            "database_url_queue": "postgresql+asyncpg://x@127.0.0.1/q",
        }
    )
    urls = settings.role_urls()
    assert urls[DatabaseRole.CORE].endswith("/a")
    assert urls[DatabaseRole.QUEUE].endswith("/q")


def test_the_system_login_follows_a_role_to_its_own_database() -> None:
    settings = StorageSettings.model_validate(
        {
            "_env_file": None,
            "database_url": "postgresql+asyncpg://acme_runtime:r@db-a:5432/a",
            "database_url_queue": "postgresql+asyncpg://acme_runtime:r@db-q:5432/q",
            "database_system_url": "postgresql+asyncpg://acme_system:s@db-a:5432/a",
        }
    )
    system = settings.system_role_urls()
    assert system[DatabaseRole.CORE] == "postgresql+asyncpg://acme_system:s@db-a:5432/a"
    assert system[DatabaseRole.QUEUE] == "postgresql+asyncpg://acme_system:s@db-q:5432/q"


def test_business_root_has_a_field_per_manager(tmp_path: Path) -> None:
    managers = build_managers(
        StorageMemoryImpl(),
        InfraLocalImpl(tmp_path),
        integrations=absent_integrations(),
    )
    hints = typing.get_type_hints(Managers)
    for field in dataclasses.fields(Managers):
        assert isinstance(getattr(managers, field.name), hints[field.name]), field.name


def test_the_tenancy_manager_carries_each_delegate(tmp_path: Path) -> None:
    """A delegate is built by the root and reached through its manager."""
    tenancy = build_managers(
        StorageMemoryImpl(),
        InfraLocalImpl(tmp_path),
        integrations=absent_integrations(),
    ).tenancy
    assert isinstance(tenancy.sign_in, TenancySignInManagerInterface)
    assert isinstance(tenancy.org, TenancyOrgManagerInterface)
    assert isinstance(tenancy.members, TenancyMembersManagerInterface)
    assert isinstance(tenancy.credentials, TenancyCredentialsManagerInterface)


def test_the_system_scope_is_the_same_value_on_both_sides() -> None:
    # Infra compares the scope by value rather than importing the model.
    from acme.infra.base import SYSTEM_SCOPE
    from acme.om.base import EMPTY_UUID

    assert SYSTEM_SCOPE == EMPTY_UUID
