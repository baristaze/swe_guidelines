"""checkers/src/arch_check/rules/network.py: the gateway's edge, errors, idempotency, the prefix, wire types, the document."""

import pytest

pytest.importorskip("tomllib")

from arch_check_fixtures import check, check_json, rules_found, write_project

SVC = "services/api/src/acme/services/api"
ROUTER = f"{SVC}/routers/orders.py"
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
        (
            "from fastapi import Depends\nfrom fastapi.security import HTTPBearer\n\n"
            "bearer = HTTPBearer()\n\ndef f(t=Depends(bearer)):\n    ...\n"
        ),
        "from fastapi.security import OAuth2PasswordBearer as O\n\nscheme = O(tokenUrl='t')\n",
    ],
)
def test_net_06_a_router_that_reads_a_header_fails(tmp_path, source):
    code, where, _ = found(tmp_path, "NET-06", {ROUTER: source})
    assert code == 1
    assert [p for _, p, _ in where] == [ROUTER]


def test_net_06_the_gateway_may_read_headers(tmp_path):
    source = "from fastapi import Request\n\n\ndef f(request: Request):\n    return request.headers['authorization']\n"
    code, where, _ = found(tmp_path / "impl", "NET-06", {f"{SVC}/impl/auth.py": source})
    assert (code, where) == (1, [("NET-06", f"{SVC}/impl/auth.py", 5)])
    code, _, _ = found(tmp_path / "gateway", "NET-06", {f"{SVC}/gateway/auth.py": source})
    assert code == 0


def test_net_06_an_outbound_credential_and_a_response_header_pass(tmp_path):
    impl = (
        "async def call(client, token):\n"
        '    client.headers["Authorization"] = f"Bearer {token}"\n'
        '    return await client.get("/x", headers={"Authorization": token})\n'
    )
    router = 'async def create(ctx: Ctx, response: Response):\n    response.headers["Location"] = "/orders/1"\n'
    code, _, _ = found(tmp_path, "NET-06", {f"{SVC}/impl/orders.py": impl, ROUTER: router})
    assert code == 0


def test_net_06_an_outbound_clients_request_hook_passes(tmp_path):
    impl = "import httpx\n\nasync def log(request: httpx.Request):\n    return request.headers.get('x-request-id')\n"
    code, _, _ = found(tmp_path, "NET-06", {f"{SVC}/impl/orders.py": impl})
    assert code == 0


def test_net_06_a_request_header_read_in_service_code_fails(tmp_path):
    impl = "from starlette.requests import Request\n\ndef who(req: Request):\n    return req.headers.get('x-org')\n"
    code, where, _ = found(tmp_path, "NET-06", {f"{SVC}/impl/orders.py": impl})
    assert (code, where) == (1, [("NET-06", f"{SVC}/impl/orders.py", 4)])


def test_net_06_cors_origins_from_settings_pass_and_a_literal_fails(tmp_path):
    app = f"{SVC}/app.py"
    code, _, _ = found(tmp_path, "NET-06", {app: "app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins)\n"})
    assert code == 0
    code, where, _ = found(tmp_path, "NET-06", {app: 'app.add_middleware(CORSMiddleware, allow_origins=["*"])\n'})
    assert (code, where) == (1, [("NET-06", app, 1)])
    code, _, _ = found(tmp_path, "NET-06", {app: "app.add_middleware(CORSMiddleware, allow_origins=[settings.portal])\n"})
    assert code == 0
    code, where, _ = found(tmp_path, "NET-06", {app: 'app.add_middleware(CORSMiddleware, allow_origin_regex="https://.*")\n'})
    assert (code, where) == (1, [("NET-06", app, 1)])
    code, _, _ = found(
        tmp_path, "NET-06", {app: "app.add_middleware(CORSMiddleware, allow_origin_regex=settings.origin_regex)\n"}
    )
    assert code == 0
    code, where, _ = found(tmp_path, "NET-06", {app: 'app.add_middleware(CORSMiddleware, allow_origins=["https://a"])\n'})
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
    rel = f"{SVC}/impl/orders.py"
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


def test_net_07_one_module_per_service_and_the_gateways_handlers_pass(tmp_path):
    code, _, _ = found(
        tmp_path,
        "NET-07",
        {
            f"{SVC}/gateway/errors.py": "@app.exception_handler(E)\nasync def h(r, e): ...\n",
            f"{SVC}/app.py": "from acme.gateway.errors import platform\n\napp.add_exception_handler(P, platform)\n",
            "services/admin/src/acme/services/admin/app.py": "app.add_exception_handler(F, g)\n",
        },
    )
    assert code == 0


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


def test_net_09_a_creating_post_added_with_add_api_route_is_read(tmp_path):
    bare = 'async def submit(ctx: Ctx): ...\n\nrouter.add_api_route("", submit, methods=["POST"], status_code=201)\n'
    code, where, _ = found(tmp_path, "NET-09", {ROUTER: bare})
    assert (code, where) == (1, [("NET-09", ROUTER, 1)])
    keyed = (
        "from acme.services.api.gateway.idempotency import Idem\n\n"
        "async def submit(ctx: Ctx, idem: Idem): ...\n\n"
        'router.add_api_route("", submit, methods=["POST"], status_code=201)\n'
    )
    code, _, _ = found(tmp_path / "keyed", "NET-09", {IDEM: "Idem = 1\n", ROUTER: keyed})
    assert code == 0


def test_net_09_the_module_is_an_option(tmp_path):
    pyproject = '[tool.arch-check]\npackage = "acme"\n\n[tool.arch-check.options.NET-09]\nmodule = "edge.keys"\n'
    source = "from acme.edge.keys import Key\n\n@router.post('', status_code=201)\nasync def create(k: Key): ...\n"
    write_project(tmp_path, {ROUTER: source}, pyproject=pyproject)
    code, _, _ = check(tmp_path, "--rule", "NET-09")
    assert code == 0


@pytest.mark.parametrize(
    "source",
    [
        "from acme.services.api.gateway.idempotency import key\n\n"
        "@router.post('', status_code=201)\nasync def create(ctx: Ctx, k: str = Depends(key)): ...\n",
        "from acme.services.api.gateway.idempotency import key\n\n"
        "router = APIRouter(dependencies=[Depends(key)])\n\n"
        "@router.post('', status_code=201)\nasync def create(ctx: Ctx): ...\n",
    ],
)
def test_net_09_a_key_as_a_default_or_on_the_router_passes(tmp_path, source):
    code, _, _ = found(tmp_path, "NET-09", {IDEM: "key = 1\n", ROUTER: source})
    assert code == 0


@pytest.mark.parametrize(
    "source",
    [
        "from http import HTTPStatus\n\n@router.post('', status_code=HTTPStatus.CREATED)\nasync def create(ctx: Ctx): ...\n",
        "@router.api_route('', methods=['POST'], status_code=201)\nasync def create(ctx: Ctx): ...\n",
    ],
)
def test_net_09_a_creating_post_spelled_another_way_without_the_key_fails(tmp_path, source):
    code, where, _ = found(tmp_path, "NET-09", {ROUTER: source})
    assert code == 1
    assert [p for _, p, _ in where] == [ROUTER]


def test_net_09_a_key_on_an_annotated_router_or_a_keyword_mount_passes(tmp_path):
    app = (
        "from acme.services.api.gateway.idempotency import key\nfrom acme.services.api.routers import orders\n\n"
        "app.include_router(router=orders.router, dependencies=[Depends(key)])\n"
    )
    source = (
        "from acme.services.api.gateway.idempotency import key\n\n"
        "router: APIRouter = APIRouter(dependencies=[Depends(key)])\n\n"
        "@router.post('', status_code=201)\nasync def create(ctx: Ctx): ...\n"
    )
    other = "@router.post('', status_code=201)\nasync def create(ctx: Ctx): ...\n"
    files = {IDEM: "key = 1\n", f"{SVC}/app.py": app, ROUTER: source, f"{SVC}/routers/carts.py": other}
    code, where, _ = found(tmp_path, "NET-09", files)
    assert (code, where) == (1, [("NET-09", f"{SVC}/routers/carts.py", 2)])


def test_net_09_a_key_where_the_router_is_mounted_passes(tmp_path):
    app = (
        "from acme.services.api.gateway.idempotency import key\nfrom acme.services.api.routers import orders\n\n"
        "app.include_router(orders.router, dependencies=[Depends(key)])\n"
    )
    source = "@router.post('', status_code=201)\nasync def create(ctx: Ctx): ...\n"
    code, _, _ = found(tmp_path, "NET-09", {IDEM: "key = 1\n", f"{SVC}/app.py": app, ROUTER: source})
    assert code == 0


# --- NET-10


def test_net_10_router_paths_without_the_prefix_pass(tmp_path):
    source = 'router = APIRouter(prefix="/orders")\n\n@router.get("/{id}")\nasync def get(): ...\n'
    code, _, _ = found(tmp_path, "NET-10", {ROUTER: source})
    assert code == 0


@pytest.mark.parametrize("path", ["/v1/orders", "/healthz", "/metrics"])
def test_net_10_a_versioned_or_operational_router_path_fails(tmp_path, path):
    source = f'router = APIRouter(prefix="/orders")\n\n@router.get("{path}")\nasync def get(): ...\n'
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
            f"{SVC}/types/orders.py": "class OrderView(View):\n    id: int\n",
            ROUTER: "from acme.services.api.types.orders import OrderView\n\n"
            '@router.get("", response_model=list[OrderView])\nasync def f(limit: int = 50): ...\n',
        },
    )
    assert code == 0


def test_net_13_a_mutable_view_base_and_a_bare_model_fail(tmp_path):
    code, where, _ = found(
        tmp_path,
        "NET-13",
        {
            TYPES: 'class View(BaseModel):\n    pass\n\nclass RequestBody(BaseModel):\n    model_config = {"extra": "forbid"}\n',
            f"{SVC}/types/orders.py": "class OrderView(BaseModel):\n    id: int\n",
        },
    )
    assert code == 1
    assert where == [("NET-13", TYPES, 1), ("NET-13", f"{SVC}/types/orders.py", 1)]


def test_net_13_an_om_entity_on_the_wire_or_an_offset_fails(tmp_path):
    source = (
        "from acme.om.orders.types import Order\n\n"
        '@router.get("", response_model=list[Order])\nasync def f(): ...\n\n'
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
    assert "failing `git diff`" in msgs[0]


def test_net_14_a_commented_out_step_runs_nothing(tmp_path):
    code, where, _ = found(
        tmp_path,
        "NET-14",
        {
            ROUTER: "",
            "Makefile": "openapi:\n\techo\n",
            WORKFLOW: (
                "jobs:\n  check:\n    steps:\n      # - run: make openapi && git diff --exit-code\n      - run: make check\n"
            ),
        },
    )
    assert (code, where) == (1, [("NET-14", "Makefile", 1)])


def test_net_14_a_tree_with_no_router_is_not_judged(tmp_path):
    code, _, _ = found(tmp_path, "NET-14", {})
    assert code == 0


def test_net_14_a_make_target_that_reaches_the_diff_passes(tmp_path):
    makefile = (
        "check: openapi\n\t$(MAKE) lint\n\tgit diff --quiet -- apps/portal/openapi.json\n\nopenapi:\n\techo\n\nlint:\n\ttrue\n"
    )
    code, _, _ = found(tmp_path, "NET-14", {ROUTER: "", "Makefile": makefile, WORKFLOW: "steps:\n  - run: make check\n"})
    assert code == 0


def test_net_14_a_diff_that_does_not_fail_fails(tmp_path):
    workflow = "steps:\n  - run: make openapi && git diff --stat\n"
    code, where, _ = found(tmp_path, "NET-14", {ROUTER: "", "Makefile": "openapi:\n\techo\n", WORKFLOW: workflow})
    assert (code, where) == (1, [("NET-14", "Makefile", 1)])


# --- NET-29


def test_net_29_a_service_without_tables_passes(tmp_path):
    code, _, _ = found(tmp_path, "NET-29", {f"{SVC}/impl/orders.py": "class OrdersImpl:\n    pass\n"})
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


# --- review fixes


def test_net_06_a_request_is_a_request_only_in_the_function_that_takes_it(tmp_path):
    source = (
        "def f(request: Request):\n    return request.url\n\n\ndef g(request: OutboundRequest):\n    return request.headers\n"
    )
    code, where, _ = found(tmp_path, "NET-06", {ROUTER: source})
    assert (code, where) == (0, [])
    source = source.replace("return request.url", "return request.headers")
    code, where, _ = found(tmp_path, "NET-06", {ROUTER: source})
    assert (code, where) == (1, [("NET-06", ROUTER, 2)])


@pytest.mark.parametrize(
    "source",
    [
        "def f():\n    return JSONResponse({'e': 1}, 404)\n",
        "def f(response):\n    response.status_code = 409\n",
        "def f(response):\n    response.status_code = status.HTTP_409_CONFLICT\n",
    ],
)
def test_net_07_a_positional_or_assigned_error_status_fails(tmp_path, source):
    code, where, _ = found(tmp_path, "NET-07", {ROUTER: source})
    assert (code, [p for _, p, _ in where]) == (1, [ROUTER])


def test_net_07_a_success_status_set_by_hand_passes(tmp_path):
    source = "def f(response):\n    response.status_code = 201\n    return JSONResponse({}, 200)\n"
    code, _, _ = found(tmp_path, "NET-07", {ROUTER: source})
    assert code == 0


def test_net_09_the_idempotency_module_imported_whole_passes(tmp_path):
    source = (
        "from fastapi import Depends\nfrom acme.services.api.gateway import idempotency\n\n"
        '@router.post("", status_code=201)\nasync def create(ctx: Ctx, key=Depends(idempotency.idempotency_key)): ...\n'
    )
    code, where, _ = found(tmp_path, "NET-09", {IDEM: "def idempotency_key(): ...\n", ROUTER: source})
    assert (code, where) == (0, [])


def test_net_14_a_missing_target_is_reported_once(tmp_path):
    code, where, msgs = found(tmp_path, "NET-14", {ROUTER: "", "Makefile": "check:\n\techo\n"})
    assert (code, where) == (1, [("NET-14", "Makefile", 1)])
    assert "no `openapi` target" in msgs[0]
