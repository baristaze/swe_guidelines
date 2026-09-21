"""checkers/src/arch_check/rules/network.py: the gateway's edge, errors, idempotency, the prefix, wire types, the document."""

import pytest

pytest.importorskip("tomllib")

from arch_check_fixtures import check, check_json, rules_found, write_project

SVC = "services/api/src/acme/services/api"
ROUTER = f"{SVC}/routers/tasks.py"
TYPES = f"{SVC}/types/common.py"


def found(tmp_path, rule, files):
    write_project(tmp_path, files)
    code, report = check_json(tmp_path, "--rule", rule)
    return code, rules_found(report), [f["message"] for f in report["findings"]]


# --- NET-06


def test_net_06_a_router_that_reads_no_header_passes(tmp_path):
    code, _, _ = found(tmp_path, "NET-06", {ROUTER: "async def f(ctx: Ctx):\n    return await x.op(ctx)\n"})
    assert code == 0


@pytest.mark.parametrize(
    "source",
    [
        "def f(request):\n    return request.headers\n",
        "from fastapi import Header\n\ndef f(token: str = Header()):\n    ...\n",
        'def f(r):\n    return r.get("Authorization")\n',
    ],
)
def test_net_06_a_router_that_reads_a_header_fails(tmp_path, source):
    code, where, _ = found(tmp_path, "NET-06", {ROUTER: source})
    assert code == 1
    assert [p for _, p, _ in where] == [ROUTER]


def test_net_06_the_gateway_may_read_headers(tmp_path):
    code, _, _ = found(tmp_path, "NET-06", {f"{SVC}/gateway/auth.py": "def f(r):\n    return r.headers['authorization']\n"})
    assert code == 0


def test_net_06_cors_origins_from_settings_pass_and_a_literal_fails(tmp_path):
    app = f"{SVC}/app.py"
    code, _, _ = found(tmp_path, "NET-06", {app: "app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins)\n"})
    assert code == 0
    code, where, _ = found(tmp_path, "NET-06", {app: 'app.add_middleware(CORSMiddleware, allow_origins=["*"])\n'})
    assert (code, where) == (1, [("NET-06", app, 1)])


# --- NET-07


def test_net_07_one_handler_module_and_plain_routers_pass(tmp_path):
    code, _, _ = found(
        tmp_path,
        "NET-07",
        {
            f"{SVC}/gateway/errors.py": "def install(app):\n    app.add_exception_handler(E, h)\n",
            ROUTER: "async def f(ctx):\n    return await x.op(ctx)\n",
        },
    )
    assert code == 0


def test_net_07_a_router_raising_an_http_exception_fails(tmp_path):
    source = "from fastapi import HTTPException\n\ndef f():\n    raise HTTPException(404)\n"
    code, where, _ = found(tmp_path, "NET-07", {ROUTER: source})
    assert (code, where) == (1, [("NET-07", ROUTER, 4)])


def test_net_07_an_error_response_built_in_service_code_fails(tmp_path):
    rel = f"{SVC}/impl/tasks.py"
    code, where, _ = found(tmp_path, "NET-07", {rel: "def f():\n    return JSONResponse({}, status_code=409)\n"})
    assert (code, where) == (1, [("NET-07", rel, 2)])


def test_net_07_two_modules_registering_handlers_fail(tmp_path):
    code, where, msgs = found(
        tmp_path,
        "NET-07",
        {
            f"{SVC}/gateway/errors.py": "@app.exception_handler(E)\nasync def h(r, e): ...\n",
            f"{SVC}/app.py": "app.add_exception_handler(F, g)\n",
        },
    )
    assert code == 1
    assert [p for _, p, _ in where] == [f"{SVC}/gateway/errors.py"]
    assert "and so does" in msgs[0]


# --- NET-09

IDEM = f"{SVC}/gateway/idempotency.py"


def test_net_09_a_creating_post_with_the_key_passes(tmp_path):
    source = (
        "from acme.services.api.gateway.idempotency import Idem\n\n"
        '@router.post("", status_code=201)\nasync def create(ctx: Ctx, idem: Idem): ...\n\n'
        '@router.post("/login")\nasync def login(ctx: Ctx): ...\n'
    )
    code, _, _ = found(tmp_path, "NET-09", {IDEM: "Idem = 1\n", ROUTER: source})
    assert code == 0


def test_net_09_a_creating_post_without_the_key_fails(tmp_path):
    source = '@router.post("", status_code=status.HTTP_202_ACCEPTED)\nasync def submit(ctx: Ctx): ...\n'
    code, where, msgs = found(tmp_path, "NET-09", {ROUTER: source})
    assert (code, where) == (1, [("NET-09", ROUTER, 2)])
    assert msgs[0].startswith("submit answers 201 or 202")


def test_net_09_the_module_is_an_option(tmp_path):
    pyproject = '[tool.arch-check]\npackage = "acme"\n\n[tool.arch-check.options.NET-09]\nmodule = "edge.keys"\n'
    source = "from acme.edge.keys import Key\n\n@router.post('', status_code=201)\nasync def create(k: Key): ...\n"
    write_project(tmp_path, {ROUTER: source}, pyproject=pyproject)
    code, _, _ = check(tmp_path, "--rule", "NET-09")
    assert code == 0


# --- NET-10


def test_net_10_router_paths_without_the_prefix_pass(tmp_path):
    source = 'router = APIRouter(prefix="/tasks")\n\n@router.get("/{id}")\nasync def get(): ...\n'
    code, _, _ = found(tmp_path, "NET-10", {ROUTER: source})
    assert code == 0


@pytest.mark.parametrize("path", ["/v1/tasks", "/healthz", "/metrics"])
def test_net_10_a_versioned_or_operational_router_path_fails(tmp_path, path):
    source = f'router = APIRouter(prefix="/tasks")\n\n@router.get("{path}")\nasync def get(): ...\n'
    code, where, _ = found(tmp_path, "NET-10", {ROUTER: source})
    assert (code, where) == (1, [("NET-10", ROUTER, 3)])


# --- NET-13

BASES = (
    "class View(BaseModel):\n    model_config = ConfigDict(frozen=True, from_attributes=True)\n\n"
    'class RequestBody(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n'
)


def test_net_13_views_on_the_bases_pass(tmp_path):
    code, _, _ = found(
        tmp_path,
        "NET-13",
        {
            TYPES: BASES,
            f"{SVC}/types/tasks.py": "class TaskView(View):\n    id: int\n",
            ROUTER: "from acme.services.api.types.tasks import TaskView\n\n"
            '@router.get("", response_model=list[TaskView])\nasync def f(limit: int = 50): ...\n',
        },
    )
    assert code == 0


def test_net_13_a_mutable_view_base_and_a_bare_model_fail(tmp_path):
    code, where, _ = found(
        tmp_path,
        "NET-13",
        {
            TYPES: 'class View(BaseModel):\n    pass\n\nclass RequestBody(BaseModel):\n    model_config = {"extra": "forbid"}\n',
            f"{SVC}/types/tasks.py": "class TaskView(BaseModel):\n    id: int\n",
        },
    )
    assert code == 1
    assert where == [("NET-13", TYPES, 1), ("NET-13", f"{SVC}/types/tasks.py", 1)]


def test_net_13_an_om_entity_on_the_wire_or_an_offset_fails(tmp_path):
    source = (
        "from acme.om.tasks.types import Task\n\n"
        '@router.get("", response_model=list[Task])\nasync def f(): ...\n\n'
        '@router.get("/page")\nasync def g(offset: int = 0): ...\n'
    )
    code, where, _ = found(tmp_path, "NET-13", {ROUTER: source})
    assert code == 1
    assert [line for _, _, line in where] == [4, 7]


# --- NET-14

WORKFLOW = ".github/workflows/ci.yml"


def test_net_14_a_target_and_a_diff_in_ci_pass(tmp_path):
    code, _, _ = found(
        tmp_path,
        "NET-14",
        {
            ROUTER: "",
            "Makefile": "openapi: ## Emit the document\n\tuv run acme-api openapi\n",
            WORKFLOW: "jobs:\n  check:\n    steps:\n      - run: make openapi && git diff --exit-code\n",
        },
    )
    assert code == 0


def test_net_14_no_diff_in_ci_fails(tmp_path):
    code, where, msgs = found(
        tmp_path, "NET-14", {ROUTER: "", "Makefile": "openapi:\n\techo\n", WORKFLOW: "steps:\n  - run: make check\n"}
    )
    assert (code, where) == (1, [("NET-14", "Makefile", 1)])
    assert "git diff --exit-code" in msgs[0]


def test_net_14_a_tree_with_no_router_is_not_judged(tmp_path):
    code, _, _ = found(tmp_path, "NET-14", {})
    assert code == 0


# --- NET-29


def test_net_29_a_service_without_tables_passes(tmp_path):
    code, _, _ = found(tmp_path, "NET-29", {f"{SVC}/impl/tasks.py": "class TasksImpl:\n    pass\n"})
    assert code == 0


def test_net_29_a_table_or_a_migration_in_a_service_fails(tmp_path):
    code, where, _ = found(
        tmp_path,
        "NET-29",
        {
            f"{SVC}/tables.py": 'class Note(Base):\n    __tablename__ = "notes"\n',
            "services/api/migrations/env.py": "",
        },
    )
    assert code == 1
    assert sorted(p for _, p, _ in where) == ["services/api/migrations", f"{SVC}/tables.py"]
