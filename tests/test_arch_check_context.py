"""checkers/src/arch_check/rules/context.py: the context group, a pass and a fail per rule."""

import pytest

pytest.importorskip("tomllib")

from arch_check_fixtures import PYPROJECT, check, check_json, rules_found, write_project

OM = "om/src/acme/om"
INFRA = "infra/src/acme/infra"
API = "services/api/src/acme/services/api"
WORKER = "workers/maintenance/src/acme/workers/maintenance"
STAGES = f"{OM}/opcontext.py"

OPCONTEXT = """from typing import Protocol
from uuid import UUID

from acme.om.base import Platform


class SecurityContext(Platform):
    user_id: UUID
    org_id: UUID
    credential_id: UUID


class RequestContext(Platform):
    request_id: UUID
    app: str
    caused_by_request_id: UUID | None = None


class IdentityContext(RequestContext):
    identity_id: UUID


class OpContext(RequestContext):
    security: SecurityContext


class OperatorContext(IdentityContext):
    permissions: frozenset[str]


class TenantScope(Protocol):
    @property
    def org_id(self) -> UUID: ...


class ActorScope(TenantScope, Protocol):
    \"\"\"No actor without a tenant.\"\"\"

    @property
    def user_id(self) -> UUID: ...


def build_context(rctx: RequestContext) -> OpContext:
    return OpContext(request_id=rctx.request_id, app=rctx.app, security=SecurityContext())


def stamp(ctx: ActorScope) -> None: ...
"""


def run(tmp_path, rule, files, pyproject=PYPROJECT):
    write_project(tmp_path, {STAGES: OPCONTEXT, **files}, pyproject=pyproject)
    code, report = check_json(tmp_path, "--rule", rule)
    return code, rules_found(report), [f["message"] for f in report["findings"]]


def test_the_base_tree_is_clean(tmp_path):
    write_project(tmp_path)
    code, out, _ = check(tmp_path, "--group", "context")
    assert code == 0, out


def test_the_stage_module_alone_is_clean(tmp_path):
    write_project(tmp_path, {STAGES: OPCONTEXT})
    code, out, _ = check(tmp_path, "--group", "context")
    assert code == 0, out


# --- CTX-01

MANAGER = """from abc import ABC, abstractmethod


class TasksManagerInterface(ABC):
    @abstractmethod
    async def get_task(self, ctx: OpContext, task_id: UUID) -> Task: ...

    @abstractmethod
    async def enqueue_relayed(self, org_id: UUID, row: OutboxRow) -> None: ...
"""


def test_the_context_first_passes_ctx_01(tmp_path):
    service = "class RealtimeServiceInterface(ABC):\n    def subscribe(self, ctx: ActorScope) -> None: ...\n"
    code, _, _ = run(tmp_path, "CTX-01", {f"{OM}/tasks/manager.py": MANAGER, f"{API}/services/realtime.py": service})
    assert code == 0


def test_a_late_context_or_a_manager_scope_is_ctx_01(tmp_path):
    manager = MANAGER.replace("ctx: OpContext, task_id: UUID", "task_id: UUID, ctx: OpContext").replace(
        "org_id: UUID, row", "ctx: ActorScope, row"
    )
    code, found, messages = run(tmp_path, "CTX-01", {f"{OM}/tasks/manager.py": manager})
    assert code == 1
    assert [line for _, _, line in found] == [6, 9]
    assert "takes OpContext in position 2" in messages[0]
    assert "takes a scope" in messages[1]


# --- CTX-02


def test_a_stage_module_of_ids_passes_ctx_02(tmp_path):
    code, _, _ = run(tmp_path, "CTX-02", {f"{OM}/tenancy/types/user.py": "class User:\n    pass\n"})
    assert code == 0


def test_an_entity_in_the_context_or_an_import_above_the_base_is_ctx_02(tmp_path):
    stages = OPCONTEXT.replace(
        "from acme.om.base import Platform\n", "from acme.om.base import Platform\nfrom acme.om.tenancy.types.user import User\n"
    ).replace("    identity_id: UUID\n", "    identity_id: UUID\n    user: User\n")
    files = {STAGES: stages, f"{OM}/tenancy/types/user.py": "class User:\n    pass\n"}
    code, found, messages = run(tmp_path, "CTX-02", files)
    assert code == 1
    assert [line for _, _, line in found] == [5, 22]
    assert "imports acme.om.tenancy.types.user" in messages[0]
    assert "holds the entity User" in messages[1]


def test_the_base_imported_as_a_module_passes_ctx_02(tmp_path):
    stages = OPCONTEXT.replace(
        "from acme.om.base import Platform\n", "from acme.om import base, exceptions\n\nPlatform = base.Platform\n"
    )
    code, _, _ = run(tmp_path, "CTX-02", {STAGES: stages})
    assert code == 0


def test_a_namespace_imported_beside_the_base_is_ctx_02(tmp_path):
    stages = OPCONTEXT.replace(
        "from acme.om.base import Platform\n", "from acme.om import base, orders\n\nPlatform = base.Platform\n"
    )
    code, found, messages = run(tmp_path, "CTX-02", {STAGES: stages})
    assert code == 1
    assert found == [("CTX-02", STAGES, 4)]
    assert "imports acme.om;" in messages[0]


def test_a_request_stage_without_its_ids_is_ctx_02(tmp_path):
    stages = OPCONTEXT.replace("    app: str\n", "").replace("    credential_id: UUID\n", "")
    code, _, messages = run(tmp_path, "CTX-02", {STAGES: stages})
    assert code == 1
    assert sorted(messages) == ["RequestContext declares no app", "no context class declares credential_id"]


# --- CTX-05


def test_the_edge_minting_the_request_stage_passes_ctx_05(tmp_path):
    files = {
        f"{API}/gateway/auth.py": "def rctx():\n    return RequestContext(request_id=new_id(), app=app)\n",
        f"{WORKER}/loop.py": "def mint():\n    return RequestContext(request_id=new_id(), app=app)\n",
    }
    code, _, _ = run(tmp_path, "CTX-05", files)
    assert code == 0


def test_a_stage_minted_below_the_edge_is_ctx_05(tmp_path):
    files = {
        f"{OM}/tasks/impl/manager.py": "def f():\n    return RequestContext.model_validate({})\n",
        f"{API}/routers/tasks.py": "def g():\n    return opcontext.OpContext(security=s)\n",
    }
    code, found, messages = run(tmp_path, "CTX-05", files)
    assert code == 1
    assert [p for _, p, _ in found] == [f"{OM}/tasks/impl/manager.py", f"{API}/routers/tasks.py"]
    assert "constructs RequestContext" in messages[0]
    assert "constructs OpContext" in messages[1]


# --- CTX-06


def test_a_context_passed_on_unchanged_passes_ctx_06(tmp_path):
    src = (
        "def caused_by(rctx: RequestContext, item) -> RequestContext:\n"
        "    return rctx.model_copy(update={'caused_by_request_id': item.request_id})\n\n"
        "async def get(ctx: OpContext, override: bool = False):\n    return await other(ctx, override=override)\n"
    )
    code, _, _ = run(tmp_path, "CTX-06", {f"{OM}/tasks/impl/manager.py": src})
    assert code == 0


def test_a_copied_or_mutated_context_is_ctx_06(tmp_path):
    src = (
        "def narrow(ctx: OpContext):\n    wider = ctx.model_copy(update={'role': 'owner'})\n    ctx.trace_id = 'x'\n\n"
        "def with_role(ctx: OpContext, role) -> OpContext:\n    return build(ctx, role)\n"
    )
    code, found, _ = run(tmp_path, "CTX-06", {f"{API}/impl/tasks.py": src})
    assert code == 1
    assert [line for _, _, line in found] == [2, 3, 5]


# --- CTX-07


def test_a_context_variable_in_the_log_module_passes_ctx_07(tmp_path):
    src = "from contextvars import ContextVar\n\nrequest_id_var = ContextVar('request_id', default=None)\n"
    code, _, _ = run(tmp_path, "CTX-07", {f"{INFRA}/observability.py": src})
    assert code == 0


def test_a_context_variable_or_a_thread_local_elsewhere_is_ctx_07(tmp_path):
    files = {
        f"{OM}/tasks/impl/manager.py": "import contextvars\n\ncurrent_org = contextvars.ContextVar('org')\n",
        f"{API}/gateway/auth.py": "from threading import local\n\nstate = local()\n",
    }
    code, found, _ = run(tmp_path, "CTX-07", files)
    assert code == 1
    assert [p for _, p, _ in found] == [f"{OM}/tasks/impl/manager.py", f"{API}/gateway/auth.py"]


def test_the_gateway_log_module_holds_the_context_variable_under_ctx_07(tmp_path):
    src = "from contextvars import ContextVar\n\nrequest_id_var = ContextVar('request_id', default=None)\n"
    files = {"gateway/src/acme/gateway/observability.py": src, "gateway/src/acme/gateway/auth.py": src}
    code, found, _ = run(tmp_path, "CTX-07", files)
    assert (code, [p for _, p, _ in found]) == (1, ["gateway/src/acme/gateway/auth.py"])


def test_the_log_module_is_an_option_of_ctx_07(tmp_path):
    pyproject = PYPROJECT + '\n[tool.arch-check.options.CTX-07]\nmodules = ["infra.logs"]\n'
    src = "from contextvars import ContextVar\n\nv = ContextVar('v')\n"
    code, found, _ = run(tmp_path, "CTX-07", {f"{INFRA}/logs.py": src, f"{INFRA}/observability.py": src}, pyproject=pyproject)
    assert (code, [p for _, p, _ in found]) == (1, [f"{INFRA}/observability.py"])


# --- CTX-08


def test_a_manager_requiring_a_permission_passes_ctx_08(tmp_path):
    src = "async def create(self, ctx):\n    ctx.require(Permission.WRITE)\n"
    storage = "async def write_membership(self, org_id, membership):\n    row.role = membership.role\n"
    code, _, _ = run(tmp_path, "CTX-08", {f"{OM}/tasks/impl/manager.py": src, f"{OM}/tenancy/storage/impl/memory.py": storage})
    assert code == 0


def test_a_router_or_storage_reading_a_permission_is_ctx_08(tmp_path):
    files = {
        f"{API}/routers/tasks.py": "async def create(ctx):\n    ctx.require(Permission.WRITE)\n",
        f"{OM}/tasks/storage/impl/memory.py": "def f(ctx):\n    return ctx.has(opcontext.OperatorPermission.READ)\n",
    }
    code, found, _ = run(tmp_path, "CTX-08", files)
    assert code == 1
    assert [p for _, p, _ in found] == [f"{OM}/tasks/storage/impl/memory.py", f"{API}/routers/tasks.py"]


# --- CTX-10

STORAGE = """from abc import ABC, abstractmethod


class TasksStorageInterface(ABC):
    @abstractmethod
    async def read_task(self, org_id: UUID, task_id: UUID) -> Task | None: ...

    @abstractmethod
    async def read_keys(self, org_id: UUID, after: UUID | None, user_id: UUID | None = None) -> list[Key]: ...

    @abstractmethod
    async def count_since(self, since: datetime) -> int:
        \"\"\"Cross-tenant: the platform size.\"\"\"
        ...
"""


def test_tenant_first_passes_ctx_10(tmp_path):
    code, _, _ = run(tmp_path, "CTX-10", {f"{OM}/tasks/storage/__init__.py": STORAGE, f"{OM}/tasks/manager.py": MANAGER})
    assert code == 0


def test_a_user_as_the_target_after_a_narrower_id_passes_ctx_10(tmp_path):
    storage = STORAGE.replace("after: UUID | None, user_id: UUID | None = None", "team_id: UUID, user_id: UUID").replace(
        "read_keys", "add_team_member"
    )
    code, _, _ = run(tmp_path, "CTX-10", {f"{OM}/tasks/storage/__init__.py": storage})
    assert code == 0


def test_a_late_tenant_and_a_tenant_beside_op_context_are_ctx_10(tmp_path):
    storage = STORAGE.replace("org_id: UUID, task_id: UUID", "task_id: UUID, org_id: UUID")
    manager = MANAGER.replace("task_id: UUID) -> Task", "org_id: UUID) -> Task")
    code, found, messages = run(
        tmp_path, "CTX-10", {f"{OM}/tasks/storage/__init__.py": storage, f"{OM}/tasks/manager.py": manager}
    )
    assert code == 1
    assert [(p.rpartition("/")[2], line) for _, p, line in found] == [("manager.py", 6), ("__init__.py", 6)]
    assert "org_id beside OpContext" in messages[0]
    assert "org_id in position 2" in messages[1]


# --- CTX-12


def test_a_documented_tenantless_method_passes_ctx_12(tmp_path):
    code, _, _ = run(tmp_path, "CTX-12", {f"{OM}/tasks/storage/__init__.py": STORAGE})
    assert code == 0


def test_an_undocumented_tenantless_method_is_ctx_12(tmp_path):
    storage = STORAGE.replace('        """Cross-tenant: the platform size."""\n', "")
    code, found, _ = run(tmp_path, "CTX-12", {f"{OM}/tasks/storage/__init__.py": storage})
    assert (code, found) == (1, [("CTX-12", f"{OM}/tasks/storage/__init__.py", 12)])


def test_a_global_interface_documented_on_the_class_passes_ctx_12(tmp_path):
    storage = (
        "from abc import ABC, abstractmethod\n\n\nclass CatalogStorageInterface(ABC):\n"
        '    """Global: the catalog is platform-owned reference data, shared by every tenant."""\n\n'
        "    @abstractmethod\n    async def read_product(self, sku: str) -> Product | None: ...\n\n"
        "    @abstractmethod\n    async def list_products(self) -> list[Product]: ...\n"
    )
    code, _, _ = run(tmp_path, "CTX-12", {f"{OM}/catalog/storage/__init__.py": storage})
    assert code == 0


def test_the_tenantless_list_is_held_both_ways_by_ctx_12(tmp_path):
    pyproject = PYPROJECT + '\n[tool.arch-check.options.CTX-12]\ntenantless = ["TasksStorageInterface.count_gone"]\n'
    code, found, messages = run(tmp_path, "CTX-12", {f"{OM}/tasks/storage/__init__.py": STORAGE}, pyproject=pyproject)
    assert code == 1
    assert [p for _, p, _ in found] == [f"{OM}/tasks/storage/__init__.py", "pyproject.toml"]
    assert "not on the tenantless list" in messages[0]
    assert "count_gone, which is no tenant-less method" in messages[1]


def test_a_listed_tenantless_method_passes_ctx_12(tmp_path):
    pyproject = PYPROJECT + '\n[tool.arch-check.options.CTX-12]\ntenantless = ["TasksStorageInterface.count_since"]\n'
    code, _, _ = run(tmp_path, "CTX-12", {f"{OM}/tasks/storage/__init__.py": STORAGE}, pyproject=pyproject)
    assert code == 0


# --- CTX-14

CACHE = """class CacheInterface(ABC):
    @abstractmethod
    async def get(self, org_id: UUID, key: str) -> bytes | None: ...

    @abstractmethod
    def describe(self) -> str: ...
"""


def test_a_tenant_keyed_cache_passes_ctx_14(tmp_path):
    code, _, _ = run(tmp_path, "CTX-14", {f"{INFRA}/cache/__init__.py": CACHE})
    assert code == 0


def test_a_cache_call_without_the_tenant_first_is_ctx_14(tmp_path):
    cache = CACHE.replace("org_id: UUID, key: str", "key: str, org_id: UUID")
    code, found, _ = run(tmp_path, "CTX-14", {f"{INFRA}/cache/__init__.py": cache})
    assert (code, found) == (1, [("CTX-14", f"{INFRA}/cache/__init__.py", 3)])


# --- CTX-15

TOPICS = """class TopicPayload(BaseModel):
    org_id: UUID


class TaskPayload(TopicPayload):
    task_id: UUID


class ChangedPayload(TaskPayload):
    pass


TOPIC_PAYLOADS: dict[Topics, type[TopicPayload]] = {Topics.TASK: TaskPayload, Topics.CHANGED: ChangedPayload}
"""


def test_payloads_extending_the_base_pass_ctx_15(tmp_path):
    code, _, _ = run(tmp_path, "CTX-15", {f"{INFRA}/topics/__init__.py": TOPICS})
    assert code == 0


def test_a_payload_without_the_tenant_is_ctx_15(tmp_path):
    topics = TOPICS.replace("    org_id: UUID\n", "    pass\n").replace(
        "class ChangedPayload(TaskPayload)", "class ChangedPayload(BaseModel)"
    )
    code, found, messages = run(tmp_path, "CTX-15", {f"{INFRA}/topics/__init__.py": topics})
    assert code == 1
    assert [line for _, _, line in found] == [1, 13]
    assert "declares no org_id" in messages[0]
    assert "ChangedPayload does not extend TopicPayload" in messages[1]


# --- CTX-20


def test_a_tenantless_operator_stage_passes_ctx_20(tmp_path):
    code, _, _ = run(tmp_path, "CTX-20", {f"{OM}/tenancy/impl.py": "def f(ctx: OperatorContext) -> None: ...\n"})
    assert code == 0


def test_a_log_helper_reading_either_stage_passes_ctx_20(tmp_path):
    helper = "def log_fields(ctx: OpContext | OperatorContext) -> dict[str, str]: ...\n"
    code, _, _ = run(tmp_path, "CTX-20", {"gateway/src/acme/gateway/observability.py": helper})
    assert code == 0


def test_an_operator_stage_with_a_tenant_or_a_union_is_ctx_20(tmp_path):
    stages = OPCONTEXT.replace(
        "class OperatorContext(IdentityContext):\n", "class OperatorContext(RequestContext):\n    org_id: UUID\n"
    )
    impl = (
        "class OrdersManagerImpl(OrdersManagerInterface):\n"
        "    async def cancel_order(self, ctx: OpContext | OperatorContext, order_id: UUID) -> None: ...\n"
    )
    files = {STAGES: stages, f"{OM}/orders/impl.py": impl}
    code, found, messages = run(tmp_path, "CTX-20", files)
    assert code == 1
    assert [p for _, p, _ in found] == [STAGES, STAGES, f"{OM}/orders/impl.py"]
    assert sorted(m.partition(";")[0] for m in messages) == [
        "OperatorContext declares org_id",
        "OperatorContext does not refine IdentityContext",
        "OrdersManagerImpl.cancel_order accepts either OpContext or OperatorContext",
    ]


# --- CTX-21


def test_the_stage_chain_passes_ctx_21(tmp_path):
    code, _, _ = run(tmp_path, "CTX-21", {})
    assert code == 0


def test_a_broken_stage_chain_is_ctx_21(tmp_path):
    stages = OPCONTEXT.replace("class OpContext(RequestContext):", "class OpContext(IdentityContext):").replace(
        "class IdentityContext(RequestContext):", "class IdentityContext(Protocol):"
    )
    code, _, messages = run(tmp_path, "CTX-21", {STAGES: stages})
    assert code == 1
    assert sorted(messages) == [
        "IdentityContext does not subclass RequestContext",
        "IdentityContext is a Protocol; a stage is a concrete frozen type",
        "OpContext does not subclass RequestContext",
        "OpContext subclasses IdentityContext; it does not refine it",
    ]


# --- CTX-22


def test_protocol_scopes_in_use_pass_ctx_22(tmp_path):
    code, _, _ = run(tmp_path, "CTX-22", {})
    assert code == 0


def test_an_abc_scope_with_a_method_nobody_uses_is_ctx_22(tmp_path):
    stages = OPCONTEXT + "\n\nclass CredentialScope(ABC):\n    def check(self) -> bool:\n        return True\n"
    code, _, messages = run(tmp_path, "CTX-22", {STAGES: stages})
    assert code == 1
    assert sorted(m.partition(" ")[2] for m in messages) == [
        "holds more than read-only properties",
        "is declared by no consumer and built on by no scope",
        "is not a Protocol; a scope is satisfied structurally",
    ]


# --- CTX-24


def test_an_operator_write_through_its_own_helper_passes_ctx_24(tmp_path):
    src = (
        "class TenancyOperatorManagerImpl:\n    async def create_org(self, ctx: OperatorContext, org):\n"
        "        row = operator_row(ctx, 'org.created', org.id)\n"
    )
    code, _, _ = run(tmp_path, "CTX-24", {f"{OM}/tenancy/impl/operator.py": src})
    assert code == 0


def test_an_operator_write_through_outbox_row_is_ctx_24(tmp_path):
    src = (
        "class TenancyOperatorManagerImpl:\n    async def create_org(self, ctx: OperatorContext, org):\n"
        "        row = outbox_row(OpContext(security=s), 'org.created', org.id)\n"
    )
    code, found, _ = run(tmp_path, "CTX-24", {f"{OM}/tenancy/impl/operator.py": src})
    assert code == 1
    assert len(found) == 2


# --- CTX-26

TRANSITIONS = f"{OM}/tenancy/impl/manager.py"
SIGN_IN = (
    "class TenancyManagerImpl:\n    async def authenticate(self, rctx):\n        return build_context(rctx)\n\n"
    "    async def login(self, rctx):\n        return IdentityContext(identity_id=i)\n"
)


def test_stages_built_by_the_transitions_pass_ctx_26(tmp_path):
    code, _, _ = run(tmp_path, "CTX-26", {TRANSITIONS: SIGN_IN})
    assert code == 0


def test_a_stage_built_anywhere_else_is_ctx_26(tmp_path):
    files = {
        f"{API}/gateway/auth.py": "def principal():\n    return OpContext.model_validate(raw)\n",
        f"{WORKER}/handler.py": "class H:\n    def run(self):\n        return opcontext.SecurityContext(user_id=u)\n",
    }
    code, found, messages = run(tmp_path, "CTX-26", files)
    assert code == 1
    assert [p for _, p, _ in found] == [f"{API}/gateway/auth.py", f"{WORKER}/handler.py"]
    assert messages[0].startswith("principal constructs OpContext;")
    assert messages[1].startswith("H.run constructs SecurityContext;")


def test_listed_sites_and_builders_are_enumerated_both_ways_by_ctx_26(tmp_path):
    pyproject = PYPROJECT + (
        "\n[tool.arch-check.options.CTX-26]\n"
        'builders = ["build_context"]\n'
        f'sites = ["{STAGES}::build_context", "{TRANSITIONS}::TenancyManagerImpl.login",'
        f' "{TRANSITIONS}::TenancyManagerImpl.gone"]\n'
    )
    code, found, messages = run(tmp_path, "CTX-26", {TRANSITIONS: SIGN_IN}, pyproject=pyproject)
    assert code == 1
    assert [(p, line) for _, p, line in found] == [(TRANSITIONS, 3), ("pyproject.toml", 1)]
    assert messages[0].startswith("TenancyManagerImpl.authenticate constructs build_context;")
    assert "TenancyManagerImpl.gone constructs no stage" in messages[1]


# --- CTX-29


def test_a_worker_minting_its_own_request_id_passes_ctx_29(tmp_path):
    src = "def mint(item):\n    return RequestContext(request_id=new_id(), app=APP, caused_by_request_id=item.request_id)\n"
    code, _, _ = run(tmp_path, "CTX-29", {f"{WORKER}/loop.py": src})
    assert code == 0


def test_a_worker_reusing_the_items_request_id_or_a_stage_without_the_cause_is_ctx_29(tmp_path):
    stages = OPCONTEXT.replace("    caused_by_request_id: UUID | None = None\n", "")
    src = "def mint(item):\n    return RequestContext(request_id=item.request_id, app=APP)\n"
    code, found, messages = run(tmp_path, "CTX-29", {STAGES: stages, f"{WORKER}/loop.py": src})
    assert code == 1
    assert [p for _, p, _ in found] == [STAGES, f"{WORKER}/loop.py"]
    assert messages[0] == "RequestContext declares no caused_by_request_id"


# --- review fixes


def test_a_stage_module_class_named_like_an_entity_passes_ctx_02(tmp_path):
    stages = OPCONTEXT.replace(
        "class SecurityContext(Platform):\n", "class Role(str):\n    pass\n\n\nclass SecurityContext(Platform):\n"
    ).replace("    credential_id: UUID\n", "    credential_id: UUID\n    role: Role\n")
    files = {STAGES: stages, f"{OM}/tenancy/types/role.py": "class Role:\n    pass\n"}
    code, found, _ = run(tmp_path, "CTX-02", files)
    assert (code, found) == (0, [])


def test_a_nested_function_sees_only_what_it_does_not_rebind_for_ctx_06(tmp_path):
    src = (
        "def outer(ctx: OpContext):\n"
        "    def shadow(ctx):\n        return ctx.model_copy()\n\n"
        "    def other(item):\n        item.x = 1\n\n"
        "    return shadow, other\n"
    )
    code, found, _ = run(tmp_path, "CTX-06", {f"{OM}/tasks/impl/manager.py": src})
    assert (code, found) == (0, [])
    closure = "def outer(ctx: OpContext):\n    def inner():\n        return ctx.model_copy()\n\n    return inner\n"
    code, found, _ = run(tmp_path, "CTX-06", {f"{OM}/tasks/impl/manager.py": closure})
    assert (code, [line for _, _, line in found]) == (1, [3])


def test_an_empty_tenantless_list_still_enumerates_for_ctx_12(tmp_path):
    pyproject = PYPROJECT + "\n[tool.arch-check.options.CTX-12]\ntenantless = []\n"
    code, found, messages = run(tmp_path, "CTX-12", {f"{OM}/tasks/storage/__init__.py": STORAGE}, pyproject=pyproject)
    assert (code, [p for _, p, _ in found]) == (1, [f"{OM}/tasks/storage/__init__.py"])
    assert "not on the tenantless list" in messages[0]


def test_a_closure_inside_a_listed_method_is_part_of_the_site_for_ctx_26(tmp_path):
    source = (
        "class TenancyManagerImpl:\n    async def login(self, rctx):\n"
        "        def make():\n            return IdentityContext(identity_id=i)\n\n        return make()\n"
    )
    pyproject = (
        PYPROJECT + f'\n[tool.arch-check.options.CTX-26]\nsites = ["{STAGES}", "{TRANSITIONS}::TenancyManagerImpl.login"]\n'
    )
    code, found, _ = run(tmp_path, "CTX-26", {TRANSITIONS: source}, pyproject=pyproject)
    assert (code, found) == (0, [])
    renamed = source.replace("async def login", "async def log")
    code, found, _ = run(tmp_path, "CTX-26", {TRANSITIONS: renamed}, pyproject=pyproject)
    assert code == 1
