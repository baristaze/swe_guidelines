"""checkers/src/arch_check/rules/contracts.py: the contracts group, a pass and a fail per rule."""

import pytest

pytest.importorskip("tomllib")

from arch_check_fixtures import PYPROJECT, check, check_json, rules_found, write_project

OM = "om/src/acme/om"
INFRA = "infra/src/acme/infra"
API = "services/api/src/acme/services/api"
WORKER = "workers/maintenance/src/acme/workers/maintenance"

MANAGER_INTERFACE = """from abc import ABC, abstractmethod


class TasksManagerInterface(ABC):
    @abstractmethod
    async def get_task(self, ctx, task_id):
        \"\"\"One task.\"\"\"
        ...
"""

STORAGE_INTERFACE = """from abc import ABC, abstractmethod


class TasksStorageInterface(ABC):
    @abstractmethod
    async def read_task(self, org_id, task_id): ...
"""

STORAGE_IMPLS = {
    f"{OM}/tasks/storage/__init__.py": STORAGE_INTERFACE,
    f"{OM}/tasks/storage/impl/__init__.py": "",
    f"{OM}/tasks/storage/impl/memory.py": (
        "from acme.om.tasks.storage import TasksStorageInterface\n\n\n"
        "class TasksStorageMemoryImpl(TasksStorageInterface):\n"
        "    async def read_task(self, org_id, task_id):\n        return self._rows.get((org_id, task_id))\n"
    ),
    f"{OM}/tasks/storage/impl/postgres.py": (
        "from acme.om.tasks.storage import TasksStorageInterface\n\n\n"
        "class TasksStoragePostgresImpl(TasksStorageInterface):\n"
        "    async def read_task(self, org_id, task_id):\n        return await self._one(org_id, task_id)\n"
    ),
}


def run(tmp_path, rule, files, pyproject=PYPROJECT):
    write_project(tmp_path, files, pyproject=pyproject)
    code, report = check_json(tmp_path, "--rule", rule)
    return code, rules_found(report), [f["message"] for f in report["findings"]]


def test_the_base_tree_is_clean(tmp_path):
    write_project(tmp_path)
    code, out, _ = check(tmp_path, "--group", "contracts")
    assert code == 0, out


# --- CON-01


def test_an_impl_of_an_interface_with_async_operations_passes_con_01(tmp_path):
    impl = (
        "from acme.om.tasks.manager import TasksManagerInterface\n\n\nclass TasksManagerImpl(TasksManagerInterface):\n    pass\n"
    )
    code, _, _ = run(tmp_path, "CON-01", {f"{OM}/tasks/manager.py": MANAGER_INTERFACE, f"{OM}/tasks/impl/manager.py": impl})
    assert code == 0


def test_an_impl_with_no_interface_and_a_sync_operation_are_con_01(tmp_path):
    iface = MANAGER_INTERFACE.replace("async def", "def")
    code, found, messages = run(
        tmp_path,
        "CON-01",
        {f"{OM}/tasks/manager.py": iface, f"{OM}/tasks/impl/manager.py": "class TasksManagerImpl:\n    pass\n"},
    )
    assert code == 1
    assert found == [("CON-01", f"{OM}/tasks/impl/manager.py", 1), ("CON-01", f"{OM}/tasks/manager.py", 6)]
    assert "subclasses no *Interface" in messages[0]
    assert "is synchronous" in messages[1]


def test_a_listed_sync_method_passes_con_01(tmp_path):
    pyproject = PYPROJECT + '\n[tool.arch-check.options.CON-01]\nsync_methods = ["TasksManagerInterface.get_task"]\n'
    iface = MANAGER_INTERFACE.replace("async def", "def")
    code, _, _ = run(tmp_path, "CON-01", {f"{OM}/tasks/manager.py": iface}, pyproject=pyproject)
    assert code == 0


# --- CON-02


def test_an_abstract_interface_passes_con_02(tmp_path):
    code, _, _ = run(tmp_path, "CON-02", {f"{OM}/tasks/manager.py": MANAGER_INTERFACE})
    assert code == 0


def test_a_plain_interface_with_logic_is_con_02(tmp_path):
    iface = (
        "class TasksManagerInterface:\n"
        "    async def get_task(self, ctx, task_id):\n        return None\n\n"
        "    from abc import abstractmethod\n\n"
        "    @abstractmethod\n    async def count(self, ctx):\n        return 0\n"
    )
    code, found, messages = run(tmp_path, "CON-02", {f"{OM}/tasks/manager.py": iface})
    assert code == 1
    assert [line for _, _, line in found] == [1, 2, 8]
    assert "is not an ABC" in messages[0]
    assert "is not @abstractmethod" in messages[1]
    assert "has a body" in messages[2]


# --- CON-03


def test_named_and_paired_impls_pass_con_03(tmp_path):
    code, _, _ = run(tmp_path, "CON-03", STORAGE_IMPLS)
    assert code == 0


def test_a_misnamed_impl_and_a_lone_storage_impl_are_con_03(tmp_path):
    files = {**STORAGE_IMPLS, f"{OM}/tasks/storage/impl/postgres.py": ""}
    files[f"{OM}/tasks/storage/impl/memory.py"] = files[f"{OM}/tasks/storage/impl/memory.py"].replace(
        "TasksStorageMemoryImpl", "MemoryTasks"
    )
    code, found, messages = run(tmp_path, "CON-03", files)
    assert code == 1
    assert [p for _, p, _ in found] == [f"{OM}/tasks/storage/__init__.py", f"{OM}/tasks/storage/impl/memory.py"]
    assert "fewer than two impls (MemoryTasks)" in messages[0]
    assert "its name ends in Impl" in messages[1]


def test_an_infra_capability_with_one_impl_is_con_03(tmp_path):
    iface = "from abc import ABC\n\n\nclass CacheInterface(ABC):\n    pass\n"
    impl = "from acme.infra.cache import CacheInterface\n\n\nclass CacheValkeyImpl(CacheInterface):\n    pass\n"
    code, found, _ = run(tmp_path, "CON-03", {f"{INFRA}/cache/__init__.py": iface, f"{INFRA}/cache/valkey.py": impl})
    assert code == 1
    assert found == [("CON-03", f"{INFRA}/cache/__init__.py", 4)]


# --- CON-04


def test_a_whole_memory_impl_passes_con_04(tmp_path):
    code, _, _ = run(tmp_path, "CON-04", STORAGE_IMPLS)
    assert code == 0


ORDERS_INTERFACE = (
    "from abc import ABC, abstractmethod\n\n\nclass OrdersStorageInterface(ABC):\n"
    "    @abstractmethod\n    async def list_orders(self, org_id): ...\n"
)


def orders_impls(memory_body, postgres_body):
    """The orders storage interface with a memory and a postgres impl of `list_orders`."""
    impl = "from acme.om.orders.storage import OrdersStorageInterface\n\n\nclass OrdersStorage{}Impl(OrdersStorageInterface):\n"
    return {
        f"{OM}/orders/storage/__init__.py": ORDERS_INTERFACE,
        f"{OM}/orders/storage/impl/__init__.py": "",
        f"{OM}/orders/storage/impl/memory.py": impl.format("Memory")
        + f"    async def list_orders(self, org_id):\n        {memory_body}\n",
        f"{OM}/orders/storage/impl/postgres.py": impl.format("Postgres")
        + f"    async def list_orders(self, org_id):\n        {postgres_body}\n",
    }


def test_a_stubbed_memory_impl_is_con_04(tmp_path):
    files = orders_impls("return []", "return await self._all(org_id)")
    files[f"{OM}/orders/storage/impl/memory.py"] += "\n\ndef f():\n    raise NotImplementedError\n"
    code, found, messages = run(tmp_path, "CON-04", files)
    assert code == 1
    assert [line for _, _, line in found] == [5, 10]
    assert "where its relational sibling does not" in messages[0]


def test_an_empty_answer_the_relational_impl_shares_passes_con_04(tmp_path):
    code, _, _ = run(tmp_path, "CON-04", orders_impls("return []", "return []"))
    assert code == 0


def test_an_empty_answer_with_no_relational_sibling_passes_con_04(tmp_path):
    memory = "class OrdersStorageMemoryImpl:\n    async def list_orders(self, org_id):\n        return []\n"
    code, _, _ = run(tmp_path, "CON-04", {f"{OM}/orders/storage/impl/memory.py": memory})
    assert code == 0


def test_an_infra_local_impl_is_not_con_04(tmp_path):
    local = "class BucketsLocalImpl:\n    async def presign(self, org_id, key):\n        raise NotImplementedError\n"
    code, _, _ = run(tmp_path, "CON-04", {f"{INFRA}/buckets/local.py": local})
    assert code == 0


def test_a_relational_impl_is_not_con_04(tmp_path):
    code, _, _ = run(tmp_path, "CON-04", {f"{OM}/tasks/storage/impl/postgres.py": "def f():\n    raise NotImplementedError\n"})
    assert code == 0


# --- CON-06


def test_a_constructor_typed_by_interface_passes_con_06(tmp_path):
    impl = (
        "from collections.abc import Callable\nfrom typing import Any\n\n\n"
        "class TasksManagerImpl(TasksManagerInterface):\n"
        "    def __init__(self, storage: TasksStorageInterface, clock: Callable[..., Any]) -> None:\n"
        "        self._storage = storage\n"
    )
    code, _, _ = run(tmp_path, "CON-06", {f"{OM}/tasks/impl/manager.py": impl})
    assert code == 0


def test_an_untyped_any_or_impl_dependency_and_a_self_built_one_are_con_06(tmp_path):
    impl = (
        "class TasksManagerImpl(TasksManagerInterface):\n"
        "    def __init__(self, storage, cache: Any, peer: 'EventsManagerImpl | None') -> None:\n"
        "        self._events = EventsManagerImpl()\n"
    )
    iface = (
        "from abc import ABC, abstractmethod\n\n\nclass TasksManagerInterface(ABC):\n"
        "    @abstractmethod\n    async def move(self, ctx, events: EventsManagerInterface): ...\n"
    )
    code, found, messages = run(tmp_path, "CON-06", {f"{OM}/tasks/impl/manager.py": impl, f"{OM}/tasks/manager.py": iface})
    assert code == 1
    assert [(p, line) for _, p, line in found] == [
        (f"{OM}/tasks/impl/manager.py", 2),
        (f"{OM}/tasks/impl/manager.py", 2),
        (f"{OM}/tasks/impl/manager.py", 2),
        (f"{OM}/tasks/impl/manager.py", 3),
        (f"{OM}/tasks/manager.py", 6),
    ]
    assert "untyped" in messages[0]
    assert "as Any" in messages[1]
    assert "as EventsManagerImpl" in messages[2]
    assert "constructs EventsManagerImpl" in messages[3]
    assert "takes a EventsManagerInterface" in messages[4]


def test_a_root_may_construct_impls_under_con_06(tmp_path):
    root = (
        "class StorageMemoryImpl(StorageInterface):\n    def __init__(self) -> None:\n"
        "        self._tasks = TasksStorageMemoryImpl()\n"
    )
    code, _, _ = run(tmp_path, "CON-06", {f"{OM}/storage/impl/memory.py": root})
    assert code == 0


# --- CON-07


def test_an_options_object_passes_con_07(tmp_path):
    impl = "class TasksManagerImpl:\n    def __init__(self, storage: S, options: TasksOptions) -> None:\n        pass\n"
    code, _, _ = run(tmp_path, "CON-07", {f"{OM}/tasks/impl/manager.py": impl})
    assert code == 0


def test_a_loose_tunable_is_con_07(tmp_path):
    impl = (
        "class TasksManagerImpl:\n"
        "    def __init__(self, storage: S, page_size: int, lease: timedelta | None) -> None:\n        pass\n"
    )
    code, found, _ = run(tmp_path, "CON-07", {f"{OM}/tasks/impl/manager.py": impl})
    assert code == 1
    assert len(found) == 2


# --- CON-08


def test_own_private_attributes_pass_con_08(tmp_path):
    src = "class A:\n    def __init__(self):\n        self._x = 1\n        cls = self\n        cls._y = 2\n"
    code, _, _ = run(tmp_path, "CON-08", {f"{OM}/root.py": src})
    assert code == 0


def test_setting_a_peer_private_attribute_is_con_08(tmp_path):
    code, found, messages = run(tmp_path, "CON-08", {f"{OM}/root.py": "def build(a, b):\n    a._peer, x = b, 1\n"})
    assert code == 1
    assert found == [("CON-08", f"{OM}/root.py", 2)]
    assert "assigns a._peer" in messages[0]


def test_a_factory_filling_a_fresh_instance_passes_con_08(tmp_path):
    src = (
        "class Order:\n    @classmethod\n    def restore(cls, row):\n"
        "        inst = cls.__new__(cls)\n        inst._row = row\n        return inst\n"
    )
    code, _, _ = run(tmp_path, "CON-08", {f"{OM}/orders/types/order.py": src})
    assert code == 0


def test_a_root_class_or_a_container_setting_a_peer_private_attribute_is_con_08(tmp_path):
    root = (
        "class StorageRootImpl(StorageInterface):\n    def __init__(self, orders, catalog):\n        orders._catalog = catalog\n"
    )
    container = "def boot(orders, inventory):\n    orders._inventory = inventory\n"
    code, found, _ = run(tmp_path, "CON-08", {f"{OM}/storage/impl/postgres.py": root, f"{API}/container.py": container})
    assert code == 1
    assert sorted(found) == [("CON-08", f"{OM}/storage/impl/postgres.py", 3), ("CON-08", f"{API}/container.py", 2)]


def test_two_namespaces_importing_each_others_impl_are_con_08(tmp_path):
    files = {
        f"{OM}/tasks/impl/__init__.py": "",
        f"{OM}/tasks/impl/manager.py": "from acme.om.events.impl.manager import EventsManagerImpl\n",
        f"{OM}/events/__init__.py": "",
        f"{OM}/events/impl/__init__.py": "",
        f"{OM}/events/impl/manager.py": "from acme.om.tasks.impl import manager\n",
    }
    code, found, _ = run(tmp_path, "CON-08", files)
    assert code == 1
    assert len(found) == 2


def test_one_direction_between_namespace_impls_passes_con_08(tmp_path):
    files = {
        f"{OM}/tasks/impl/__init__.py": "from acme.om.events.impl import x\n",
        f"{OM}/events/__init__.py": "",
        f"{OM}/events/impl/__init__.py": "",
    }
    code, _, _ = run(tmp_path, "CON-08", files)
    assert code == 0


# --- CON-09

ROOT = """from dataclasses import dataclass


@dataclass(frozen=True)
class Managers:
    tasks: TasksManagerInterface


def build_managers(storage, infra) -> Managers:
    return Managers(tasks=TasksManagerImpl(storage.get_tasks_storage()))
"""


def test_a_frozen_business_root_passes_con_09(tmp_path):
    code, _, _ = run(tmp_path, "CON-09", {f"{OM}/root.py": ROOT})
    assert code == 0


@pytest.mark.parametrize(
    "source,message",
    [
        (ROOT.replace("frozen=True", "frozen=False"), "Managers is mutable"),
        (ROOT.replace("tasks: TasksManagerInterface", "tasks: TasksManagerImpl"), "holds an impl type"),
        (ROOT.replace("-> Managers", "-> dict[str, object]"), "returns no frozen object"),
    ],
)
def test_a_mutable_or_concrete_business_root_is_con_09(tmp_path, source, message):
    code, _, messages = run(tmp_path, "CON-09", {f"{OM}/root.py": source})
    assert code == 1
    assert message in messages[0]


ORDERS_ROOT = """from attrs import frozen


@frozen
class Managers:
    orders: OrdersManagerInterface


def build_managers(storage, infra) -> Managers:
    return Managers(orders=OrdersManagerImpl(storage.get_orders_storage()))
"""


@pytest.mark.parametrize(
    "source",
    [ORDERS_ROOT, ORDERS_ROOT.replace(" -> Managers", "")],
    ids=["attrs-frozen", "unannotated"],
)
def test_an_attrs_frozen_or_unannotated_business_root_passes_con_09(tmp_path, source):
    code, _, _ = run(tmp_path, "CON-09", {f"{OM}/root.py": source})
    assert code == 0


def test_an_attrs_mutable_business_root_is_con_09(tmp_path):
    source = ORDERS_ROOT.replace("from attrs import frozen", "from attrs import define").replace("@frozen", "@define")
    code, _, messages = run(tmp_path, "CON-09", {f"{OM}/root.py": source})
    assert code == 1
    assert "Managers is mutable" in messages[0]


def test_a_root_getter_returning_an_impl_is_con_09(tmp_path):
    root = (
        "class StorageMemoryImpl(StorageInterface):\n    def get_tasks_storage(self) -> TasksStorageMemoryImpl:\n"
        "        return self._tasks\n"
    )
    code, found, _ = run(tmp_path, "CON-09", {f"{OM}/storage/impl/memory.py": root})
    assert code == 1
    assert found == [("CON-09", f"{OM}/storage/impl/memory.py", 2)]


# --- CON-10 and CON-12 are in test_arch_check_imports.py

# --- CON-11


def test_an_interface_module_without_vendors_passes_con_11(tmp_path):
    postgres = "import sqlalchemy\n\n\nclass TasksStoragePostgresImpl:\n    pass\n"
    code, _, _ = run(
        tmp_path, "CON-11", {f"{OM}/tasks/manager.py": MANAGER_INTERFACE, f"{OM}/tasks/storage/impl/postgres.py": postgres}
    )
    assert code == 0


def test_a_vendor_in_an_interface_module_or_a_manager_impl_is_con_11(tmp_path):
    files = {
        f"{INFRA}/cache/__init__.py": "from redis.exceptions import ConnectionError\n\n\nclass CacheInterface:\n    pass\n",
        f"{OM}/tasks/impl/__init__.py": "",
        f"{OM}/tasks/impl/manager.py": "def f():\n    from sqlalchemy.exc import IntegrityError\n",
    }
    code, found, _ = run(tmp_path, "CON-11", files)
    assert code == 1
    assert [(p, line) for _, p, line in found] == [(f"{INFRA}/cache/__init__.py", 1), (f"{OM}/tasks/impl/manager.py", 2)]


def test_a_manager_impl_reporting_through_the_sentry_sdk_passes_con_11(tmp_path):
    files = {
        f"{OM}/orders/impl/__init__.py": "",
        f"{OM}/orders/impl/manager.py": "import sentry_sdk\n\n\ndef f(exc):\n    sentry_sdk.capture_exception(exc)\n",
    }
    code, _, _ = run(tmp_path, "CON-11", files)
    assert code == 0


def test_the_vendor_list_is_an_option_of_con_11(tmp_path):
    pyproject = PYPROJECT + '\n[tool.arch-check.options.CON-11]\nvendors = ["stripe"]\n'
    files = {f"{OM}/tasks/manager.py": "import stripe\nimport redis\n\n\nclass TasksManagerInterface:\n    pass\n"}
    code, found, _ = run(tmp_path, "CON-11", files, pyproject=pyproject)
    assert (code, found) == (1, [("CON-11", f"{OM}/tasks/manager.py", 1)])


# --- CON-14

SERVICE_INTERFACE = "from abc import ABC\n\n\nclass TasksServiceInterface(ABC):\n    pass\n"


def test_a_service_interface_with_its_impl_passes_con_14(tmp_path):
    impl = "class TasksServiceImpl(TasksServiceInterface):\n    pass\n"
    files = {f"{API}/services/__init__.py": SERVICE_INTERFACE, f"{API}/impl/tasks.py": impl}
    code, _, _ = run(tmp_path, "CON-14", files)
    assert code == 0


def test_a_service_interface_with_no_impl_is_con_14(tmp_path):
    code, found, _ = run(tmp_path, "CON-14", {f"{API}/services/__init__.py": SERVICE_INTERFACE})
    assert (code, found) == (1, [("CON-14", f"{API}/services/__init__.py", 4)])


# --- CON-15

ROUTER = """from fastapi import APIRouter

router = APIRouter()


@router.get("/{task_id}")
async def get_task(ctx: Ctx, tasks: TasksService, task_id: UUID) -> TaskView:
    \"\"\"One task.\"\"\"
    return await tasks.get_task(ctx, task_id)


@router.post("")
async def create_task(ctx: Ctx, tasks: TasksService, body: AddTaskRequest, idem: Idem) -> Response:
    return await idem.run(201, lambda attempt: tasks.create_task(ctx, body, attempt.target_id))


def helper(x):
    if x:
        return 1
"""


def test_a_router_of_one_call_per_route_passes_con_15(tmp_path):
    code, _, _ = run(tmp_path, "CON-15", {f"{API}/routers/tasks.py": ROUTER})
    assert code == 0


def test_a_no_content_route_and_the_probes_pass_con_15(tmp_path):
    router = (
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n"
        '@router.delete("/{order_id}", status_code=204)\n'
        "async def delete_order(ctx: Ctx, orders: OrdersService, order_id: UUID) -> None:\n"
        "    await orders.delete_order(ctx, order_id)\n\n\n"
        '@router.get("/readyz")\n'
        "async def readyz(storage: Storage) -> dict[str, str]:\n"
        "    async with asyncio.timeout(2):\n        await storage.healthcheck()\n"
        '    return {"status": "ok"}\n'
    )
    code, _, _ = run(tmp_path, "CON-15", {f"{API}/routers/health.py": router})
    assert code == 0


def test_a_no_content_route_that_does_more_is_con_15(tmp_path):
    router = (
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n"
        '@router.delete("/{order_id}", status_code=204)\n'
        "async def delete_order(ctx: Ctx, orders: OrdersService, order_id: UUID) -> None:\n"
        "    order = await orders.get_order(ctx, order_id)\n"
        "    await orders.delete_order(ctx, order.id)\n"
    )
    code, found, _ = run(tmp_path, "CON-15", {f"{API}/routers/orders.py": router})
    assert code == 1
    assert found == [("CON-15", f"{API}/routers/orders.py", 7)]


def test_a_router_that_branches_or_takes_a_manager_is_con_15(tmp_path):
    router = ROUTER.replace(
        "    return await tasks.get_task(ctx, task_id)",
        "    if task_id is None:\n        raise NotFound()\n    return await tasks.get_task(ctx, task_id)",
    ).replace("idem: Idem", "manager: TasksManagerInterface")
    code, found, messages = run(tmp_path, "CON-15", {f"{API}/routers/tasks.py": router})
    assert code == 1
    assert [line for _, _, line in found] == [7, 15]
    assert "does more than" in messages[0]
    assert "takes a TasksManagerInterface" in messages[1]


# --- CON-16

CONTAINER = "class AppContainer:\n    async def start(self):\n        pass\n\n    async def close(self):\n        pass\n"


def test_a_process_with_a_container_passes_con_16(tmp_path):
    files = {f"{API}/main.py": "", f"{API}/container.py": CONTAINER}
    code, _, _ = run(tmp_path, "CON-16", files)
    assert code == 0


def test_a_process_with_no_container_or_no_lifecycle_is_con_16(tmp_path):
    files = {
        f"{API}/main.py": "",
        f"{API}/container.py": None,
        f"{WORKER}/main.py": "",
        f"{WORKER}/container.py": CONTAINER.replace("async def close", "def close"),
    }
    code, found, messages = run(tmp_path, "CON-16", files)
    assert code == 1
    assert [p for _, p, _ in found] == [f"{API}/main.py", f"{WORKER}/container.py"]
    assert "has no container module" in messages[0]
    assert "no class with async start() and close()" in messages[1]


# --- CON-18

STAGE_MODULE = f"{OM}/opcontext.py"


def test_contexts_of_ids_and_structural_constructors_pass_con_18(tmp_path):
    stages = "class RequestContext(Platform):\n    request_id: UUID\n"
    impl = (
        "class TasksManagerImpl(TasksManagerInterface):\n"
        "    def __init__(self, storage: TasksStorageInterface) -> None:\n        pass\n"
    )
    code, _, _ = run(tmp_path, "CON-18", {STAGE_MODULE: stages, f"{OM}/tasks/impl/manager.py": impl})
    assert code == 0


def test_an_ssl_context_in_a_constructor_passes_con_18(tmp_path):
    impl = (
        "class CatalogClientImpl(CatalogServiceInterface):\n"
        "    def __init__(self, context: ssl.SSLContext) -> None:\n        pass\n"
    )
    code, _, _ = run(tmp_path, "CON-18", {f"{API}/clients/catalog.py": impl})
    assert code == 0


def test_a_manager_on_a_context_or_a_tenant_in_a_constructor_is_con_18(tmp_path):
    stages = "class OpContext(Platform):\n    tasks: TasksManagerInterface\n"
    impl = (
        "class TasksManagerImpl(TasksManagerInterface):\n    def __init__(self, storage: S, org_id: UUID) -> None:\n"
        "        pass\n"
    )
    code, found, _ = run(tmp_path, "CON-18", {STAGE_MODULE: stages, f"{OM}/tasks/impl/manager.py": impl})
    assert code == 1
    assert found == [("CON-18", STAGE_MODULE, 2), ("CON-18", f"{OM}/tasks/impl/manager.py", 2)]


# --- CON-20


def test_getters_returning_members_pass_con_20(tmp_path):
    root = (
        "class InfraLocalImpl(InfraInterface):\n"
        "    def get_cache(self, scope):\n        return self._caches[scope]\n\n"
        '    def get_topics(self):\n        """The bus."""\n        return self._topics\n'
    )
    code, _, _ = run(tmp_path, "CON-20", {f"{INFRA}/impl/local.py": root})
    assert code == 0


def test_a_plain_property_over_a_held_member_passes_con_20(tmp_path):
    root = "class InfraLocalImpl(InfraInterface):\n    @property\n    def get_topics(self):\n        return self._topics\n"
    code, _, _ = run(tmp_path, "CON-20", {f"{INFRA}/impl/local.py": root})
    assert code == 0


def test_a_lazy_or_cached_getter_is_con_20(tmp_path):
    root = (
        "class InfraLocalImpl(InfraInterface):\n"
        "    def get_cache(self):\n        if self._cache is None:\n            self._cache = CacheMemoryImpl()\n"
        "        return self._cache\n\n"
        "    @cached_property\n    def get_topics(self):\n        return self._topics\n"
    )
    code, found, messages = run(tmp_path, "CON-20", {f"{INFRA}/impl/local.py": root})
    assert code == 1
    assert [line for _, _, line in found] == [2, 8]
    assert "does more than return" in messages[0]
    assert "caches on first use" in messages[1]


# --- CON-23


def test_a_breaker_fed_by_settings_passes_con_23(tmp_path):
    src = "def build(settings):\n    return Breaker('cache', settings.threshold, cool_down=settings.cool_down, enabled=True)\n"
    code, _, _ = run(tmp_path, "CON-23", {f"{INFRA}/impl/configured.py": src})
    assert code == 0


def test_a_literal_bound_or_a_breaker_in_the_om_is_con_23(tmp_path):
    files = {
        f"{INFRA}/impl/configured.py": "def build():\n    return Breaker('cache', 5, cool_down=30.0)\n",
        f"{OM}/tasks/impl/manager.py": "from acme.infra.breaker import Breaker\n",
    }
    code, found, _ = run(tmp_path, "CON-23", files)
    assert code == 1
    assert [(p, line) for _, p, line in found] == [(f"{INFRA}/impl/configured.py", 2), (f"{OM}/tasks/impl/manager.py", 1)]


def test_a_literal_probe_count_or_an_open_refusal_passes_con_23(tmp_path):
    src = (
        "def build(settings, inner):\n"
        "    return CatalogBreakerImpl(inner, threshold=settings.threshold, cooldown=settings.cooldown, probes=1)\n\n\n"
        "def refuse():\n    raise BreakerOpen(retry_after=0)\n"
    )
    code, _, _ = run(tmp_path, "CON-23", {f"{INFRA}/impl/configured.py": src})
    assert code == 0


def test_a_literal_failure_threshold_is_con_23(tmp_path):
    src = "def build(settings, inner):\n    return CatalogBreakerImpl(inner, failure_threshold=5, cooldown=settings.cooldown)\n"
    code, found, _ = run(tmp_path, "CON-23", {f"{INFRA}/impl/configured.py": src})
    assert code == 1
    assert found == [("CON-23", f"{INFRA}/impl/configured.py", 2)]


# --- review fixes


def test_an_overload_stub_before_the_abstract_method_passes_con_02(tmp_path):
    iface = (
        "from abc import ABC, abstractmethod\nfrom typing import overload\n\n\nclass TasksManagerInterface(ABC):\n"
        "    @overload\n    async def get(self, ctx, key: int) -> int: ...\n\n"
        "    @overload\n    async def get(self, ctx, key: str) -> str: ...\n\n"
        "    @abstractmethod\n    async def get(self, ctx, key): ...\n"
    )
    code, _, _ = run(tmp_path, "CON-02", {f"{OM}/tasks/manager.py": iface})
    assert code == 0
    code, found, _ = run(tmp_path, "CON-02", {f"{OM}/tasks/manager.py": iface.replace("    @abstractmethod\n", "")})
    assert (code, [line for _, _, line in found]) == (1, [12])


def test_a_stubbed_manager_memory_impl_is_con_04(tmp_path):
    memory = "class TasksManagerMemoryImpl:\n    async def get_task(self, ctx, task_id):\n        raise NotImplementedError\n"
    code, found, _ = run(tmp_path, "CON-04", {f"{OM}/tasks/impl/memory.py": memory})
    assert code == 1
    assert found == [("CON-04", f"{OM}/tasks/impl/memory.py", 3)]


@pytest.mark.parametrize("annotation", ["Callable[[], float]", "list[int]", "dict[str, timedelta]"])
def test_a_container_of_numbers_is_not_a_tunable_for_con_07(tmp_path, annotation):
    impl = f"class TasksManagerImpl:\n    def __init__(self, storage: S, clock: {annotation}) -> None:\n        pass\n"
    code, _, _ = run(tmp_path, "CON-07", {f"{OM}/tasks/impl/manager.py": impl})
    assert code == 0
    code, _, _ = run(tmp_path, "CON-07", {f"{OM}/tasks/impl/manager.py": impl.replace(annotation, "float | None")})
    assert code == 1


def test_a_root_behind_a_shared_base_is_still_a_root_for_con_06_and_con_20(tmp_path):
    root = (
        "class InfraBaseImpl(InfraInterface):\n    pass\n\n\n"
        "class InfraLocalImpl(InfraBaseImpl):\n"
        "    def __init__(self) -> None:\n        self._cache = CacheMemoryImpl()\n\n"
        "    @cached_property\n    def get_topics(self):\n        return self._topics\n"
    )
    code, found, _ = run(tmp_path, "CON-06", {f"{INFRA}/impl/local.py": root})
    assert (code, found) == (0, [])
    code, found, messages = run(tmp_path, "CON-20", {f"{INFRA}/impl/local.py": root})
    assert (code, [line for _, _, line in found]) == (1, [10])
    assert "caches on first use" in messages[0]
