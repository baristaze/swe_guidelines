"""checkers/src/arch_check/rules/imports.py: CON-12 and CON-10, the import direction."""

import pytest

pytest.importorskip("tomllib")

from arch_check_fixtures import check, check_json, rules_found, write_project


def test_the_base_tree_is_clean(tmp_path):
    write_project(tmp_path)
    code, out, _ = check(tmp_path, "--group", "contracts")
    assert code == 0
    assert "arch-check ok" in out


@pytest.mark.parametrize(
    "rel,source",
    [
        ("om/src/acme/om/tasks/impl/manager.py", "from acme.services.api import app\n"),
        ("om/src/acme/om/tasks/impl/manager.py", "import acme.workers.maintenance\n"),
        ("om/src/acme/om/tasks/impl/manager.py", "from acme import services\n"),
        ("om/src/acme/om/tasks/impl/manager.py", "def f():\n    from acme.services import api\n"),
        (
            "om/src/acme/om/tasks/impl/manager.py",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import acme.services\n",
        ),
        ("infra/src/acme/infra/cache/__init__.py", "from acme.workers import maintenance\n"),
        ("om/src/acme/om/tasks/impl/manager.py", "from acme.gateway.auth import principal\n"),
        ("infra/src/acme/infra/cache/__init__.py", "import acme.gateway\n"),
    ],
)
def test_om_or_infra_importing_a_service_or_a_worker_is_con_12(tmp_path, rel, source):
    write_project(tmp_path, {rel: source})
    code, report = check_json(tmp_path, "--rule", "CON-12")
    assert code == 1
    assert [(r, p) for r, p, _ in rules_found(report)] == [("CON-12", rel)]
    assert "a lower layer never imports a service, the gateway, or a worker" in report["findings"][0]["message"]


def test_a_relative_import_is_resolved_before_it_is_judged(tmp_path):
    # om/src/acme/om/tasks/impl/manager.py is acme.om.tasks.impl.manager; `....services` is acme.services.
    write_project(tmp_path, {"om/src/acme/om/tasks/impl/manager.py": "from ....services import api\n"})
    code, report = check_json(tmp_path, "--rule", "CON-12")
    assert code == 1
    assert report["findings"][0]["message"].startswith("acme.om.tasks.impl.manager imports acme.services;")


def test_a_relative_import_from_a_package_init_resolves_against_the_package(tmp_path):
    # In acme/om/tasks/__init__.py, `..` is acme.om, so `...` is acme.
    write_project(tmp_path, {"om/src/acme/om/tasks/__init__.py": "from ... import workers\n"})
    code, report = check_json(tmp_path, "--rule", "CON-12")
    assert code == 1
    assert "imports acme.workers;" in report["findings"][0]["message"]


def test_a_name_that_only_starts_like_services_is_not_a_service(tmp_path):
    write_project(tmp_path, {"om/src/acme/om/tasks/impl/manager.py": "from acme.servicesx import y\nimport acme.om.services\n"})
    code, _, _ = check(tmp_path, "--rule", "CON-12")
    assert code == 0


def test_a_service_importing_the_om_is_the_right_direction(tmp_path):
    write_project(tmp_path, {"services/api/src/acme/services/api/routes.py": "from acme.om.tasks import impl\n"})
    code, _, _ = check(tmp_path, "--group", "contracts")
    assert code == 0


def test_infra_importing_the_om_is_con_10(tmp_path):
    write_project(tmp_path, {"infra/src/acme/infra/cache/__init__.py": "from acme.om.root import build\n"})
    code, out, _ = check(tmp_path, "--rule", "CON-10")
    assert code == 1
    assert (
        "infra/src/acme/infra/cache/__init__.py:1:1: CON-10 acme.infra.cache imports acme.om.root; infra imports nothing" in out
    )


def test_infra_importing_the_om_through_the_root_package_is_con_10(tmp_path):
    write_project(tmp_path, {"infra/src/acme/infra/cache/__init__.py": "\n\nfrom acme import om\n"})
    code, report = check_json(tmp_path, "--rule", "CON-10")
    assert code == 1
    assert rules_found(report) == [("CON-10", "infra/src/acme/infra/cache/__init__.py", 3)]


def test_the_om_importing_infra_is_not_con_10(tmp_path):
    write_project(tmp_path)
    code, report = check_json(tmp_path, "--rule", "CON-10")
    assert code == 0
    assert report["findings"] == []


def test_the_package_name_comes_from_the_config(tmp_path):
    # With package "other", nothing here is under other.om, so nothing is judged.
    write_project(tmp_path, {"om/src/acme/om/tasks/impl/manager.py": "import acme.services\n"})
    code, _, _ = check(tmp_path, "--package", "other", "--group", "contracts")
    assert code == 0


# --- the network and business layers reach storage through interfaces (CON-10)

API = "services/api/src/acme/services/api"
OM = "om/src/acme/om"


@pytest.mark.parametrize(
    "rel,source",
    [
        (f"{API}/routers/tasks.py", "from acme.om.tasks.storage.tables.tasks import Tasks\n"),
        (f"{API}/services/tasks.py", "from acme.om.storage.impl.postgres import StoragePostgresImpl\n"),
        (f"{API}/impl/tasks.py", "from acme.om.tasks.impl.manager import TasksManagerImpl\n"),
        (f"{OM}/tasks/impl/manager.py", "from acme.om.storage.impl import pg_base\n"),
        (f"{OM}/tasks/impl/manager.py", "from ...storage.tables.base import Base\n"),
    ],
)
def test_reaching_past_an_interface_is_con_10(tmp_path, rel, source):
    write_project(tmp_path, {f"{OM}/tasks/impl/manager.py": "", rel: source})
    code, report = check_json(tmp_path, "--rule", "CON-10")
    assert code == 1
    assert [(r, p) for r, p, _ in rules_found(report)] == [("CON-10", rel)]


def test_the_roots_and_interfaces_are_the_right_imports_for_con_10(tmp_path):
    files = {
        f"{API}/container.py": "from acme.om.storage.impl.postgres import StoragePostgresImpl\n",
        f"{API}/services/impl/root.py": "from acme.services.api.services.impl.tasks import TasksServiceImpl\n",
        f"{API}/routers/tasks.py": "from acme.om.tasks.types.task import TaskStatus\n",
        f"{OM}/tasks/impl/__init__.py": "",
        f"{OM}/tasks/impl/manager.py": "from acme.om.tasks.storage import TasksStorageInterface\n",
    }
    write_project(tmp_path, files)
    code, _, _ = check(tmp_path, "--rule", "CON-10")
    assert code == 0


# --- storage never calls a manager, the OM never holds a service (CON-12)


@pytest.mark.parametrize(
    "rel,source,hit",
    [
        (
            f"{OM}/tasks/storage/impl/memory.py",
            "from acme.om.tasks.manager import TasksManagerInterface\n",
            "acme.om.tasks.manager",
        ),
        (f"{OM}/storage/impl/memory.py", "from acme.om.root import build_managers\n", "acme.om.root"),
        (f"{OM}/tasks/storage/__init__.py", "from acme.om.tasks import TasksManagerInterface\n", "TasksManagerInterface"),
    ],
)
def test_storage_importing_a_manager_is_con_12(tmp_path, rel, source, hit):
    write_project(tmp_path, {rel: source})
    code, report = check_json(tmp_path, "--rule", "CON-12")
    assert code == 1
    assert rules_found(report) == [("CON-12", rel, 1)]
    assert f"imports {hit}; storage never calls a manager" in report["findings"][0]["message"]


def test_storage_importing_types_and_its_own_interface_passes_con_12(tmp_path):
    source = "from acme.om.tasks.types.task import Task\nfrom acme.om.tasks.storage import TasksStorageInterface\n"
    write_project(tmp_path, {f"{OM}/tasks/storage/impl/memory.py": source})
    code, _, _ = check(tmp_path, "--rule", "CON-12")
    assert code == 0


def test_the_om_holding_an_integration_named_like_a_service_passes_con_12(tmp_path):
    source = (
        "from acme.integrations.tax import TaxServiceInterface\n\n\n"
        "class OrdersManagerImpl:\n    def __init__(self, tax: TaxServiceInterface) -> None:\n"
        "        self._tax = tax\n"
    )
    write_project(tmp_path, {f"{OM}/orders/impl.py": source})
    code, _, _ = check(tmp_path, "--rule", "CON-12")
    assert code == 0


def test_the_om_importing_a_service_interface_is_con_12(tmp_path):
    source = "from acme.services.api.services import OrdersServiceInterface\n"
    write_project(tmp_path, {f"{OM}/orders/impl.py": source})
    code, report = check_json(tmp_path, "--rule", "CON-12")
    assert code == 1
    assert rules_found(report) == [("CON-12", f"{OM}/orders/impl.py", 1)]
    assert "a lower layer never imports a service" in report["findings"][0]["message"]
