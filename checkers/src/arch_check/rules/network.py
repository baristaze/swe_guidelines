"""The network lenses a parser can decide: the gateway's edge, errors, idempotency, the prefix, wire types, the document.

Each rule reads the service distributions, `<pkg>.services.<svc>`,
and within one the parts the guideline names: `routers`, `services`
(the service interfaces), `impl`, `types`, and `gateway`. What a route
does at run time, and every judgment about shape, stays with the
review.
"""

from __future__ import annotations

import ast
import contextlib
import re
from collections.abc import Iterator

from arch_check.model import Violation
from arch_check.project import Project, SourceFile, base_names, classes, dotted, is_under, last
from arch_check.registry import rule
from arch_check.rules._storage_util import config_value, is_true
from arch_check.rules._text_util import (
    Target,
    call_name,
    const_str,
    imported_names,
    is_file,
    kwarg,
    make_targets,
    resolved,
    subdirs,
    walk,
)

VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"})
OPERATIONAL = ("/healthz", "/readyz", "/metrics")
VERSIONED = re.compile(r"^/v\d")


def service_part(project: Project, file: SourceFile) -> tuple[str, str] | None:
    """(service, part) for a module `<pkg>.services.<svc>.<part>...`; None for anything else."""
    prefix = project.sub("services") + "."
    if not file.module.startswith(prefix):
        return None
    parts = file.module[len(prefix) :].split(".")
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def service_files(project: Project, *parts: str) -> list[SourceFile]:
    """The modules under `<pkg>.services.<svc>.<part>` for any service and any of `parts`."""
    out = []
    for f in project.modules_under(project.sub("services")):
        found = service_part(project, f)
        if found is not None and found[1] in parts:
            out.append(f)
    return out


def routes(tree: ast.Module) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call, str]]:
    """(function, decorator call, verb) for every function decorated `@<router>.<verb>(...)`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in VERBS:
                    yield node, d, d.func.attr


def dotted_names_in(node: ast.AST | None) -> list[str]:
    """The dotted names an expression mentions, outermost first, a string annotation parsed like any other.
    (`_storage_util.names_in` reduces a dotted name to its last part; this one keeps it whole.)"""
    if node is None:
        return []
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute | ast.Name):
            name = dotted(n)
            if name:
                out.append(name)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            with contextlib.suppress(SyntaxError):
                out += dotted_names_in(ast.parse(n.value, mode="eval"))
    return out


# --- NET-06


def is_fastapi_header(name: str | None) -> bool:
    return bool(name) and is_under(name or "", "fastapi") and last(name) == "Header"


SECURITY_SCHEMES = frozenset(
    {
        "HTTPBearer",
        "HTTPBasic",
        "HTTPDigest",
        "OAuth2PasswordBearer",
        "OAuth2AuthorizationCodeBearer",
        "APIKeyHeader",
        "APIKeyCookie",
    }
)
"""FastAPI's security schemes: each reads the Authorization header, a key header, or a cookie."""


def is_fastapi_security(name: str | None) -> bool:
    return bool(name) and is_under(name or "", "fastapi") and last(name) in SECURITY_SCHEMES


REQUEST_TYPES = frozenset({"Request", "HTTPConnection", "WebSocket"})
CLIENT_LIBRARIES = ("httpx", "requests", "aiohttp", "urllib3")
"""Outbound clients whose `Request` is what this process sends, never what it received."""


def request_params(fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, names: dict[str, str]) -> set[str]:
    """The parameters of one function that hold an inbound request: annotated `Request`, `HTTPConnection`, or
    `WebSocket` (not an outbound client's `Request`, read through the module's imports), or an unannotated
    `request`."""
    out: set[str] = set()
    for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
        written = dotted(a.annotation) if a.annotation is not None else None
        full = resolved(written, names) or written or ""
        outbound = any(is_under(full, lib) for lib in CLIENT_LIBRARIES)
        if (last(written) in REQUEST_TYPES and not outbound) or (a.annotation is None and a.arg == "request"):
            out.add(a.arg)
    return out


def header_reads(tree: ast.AST, names: dict[str, str]) -> Iterator[ast.Attribute]:
    """Every `.headers` read on a request parameter, judged in the function that has the parameter.

    A name is a request only where a function takes it (a closure inside
    that function included), so a `request` of one function never taints
    an unrelated `request` of another.
    """
    stores = written(tree)
    seen: set[int] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        inbound = request_params(fn, names)
        if not inbound:
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "headers"
                and dotted(node.value) in inbound
                and id(node) not in stores
                and id(node) not in seen
            ):
                seen.add(id(node))
                yield node


def written(tree: ast.AST) -> set[int]:
    """The ids of `.headers` nodes a statement writes into: `response.headers["Location"] = ...`."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store | ast.Del):
            out.add(id(node.value))
    return out


def literal_origins(node: ast.expr | None) -> bool:
    """Whether `allow_origins` is `"*"`, a string, or a non-empty list whose every element is a string constant."""
    if const_str(node) is not None:
        return True
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return bool(node.elts) and all(const_str(e) is not None for e in node.elts)
    return False


@rule(
    "NET-06",
    coverage="partial",
    summary="Routers, service code, and the OM read no inbound header; routers name no token; CORS origins are never literal.",
)
def no_headers_below_the_gateway(project: Project) -> Iterator[Violation]:
    """Nothing below the gateway parses headers, and the allowed origins come from settings.

    In `<pkg>.services.<svc>.{routers,services,impl}` and in `<pkg>.om`:
    no read of `.headers` on an inbound request (a parameter annotated
    `Request`, `HTTPConnection`, or `WebSocket`, or an unannotated
    `request`), and no FastAPI `Header(...)`. In
    `<pkg>.services.<svc>.routers`, no string that is `authorization`, and
    below the gateway no FastAPI security scheme (`HTTPBearer`,
    `OAuth2PasswordBearer`, `APIKeyHeader`, and the rest), each of which
    reads a header.
    An outbound client's headers, and a header written on a response,
    are not a read. Anywhere under `<pkg>.services` and
    `<pkg>.gateway`, `CORSMiddleware` never gets `allow_origins` as
    `"*"`, a string, or a list of string literals, nor a literal
    `allow_origin_regex`. A second path to the
    internet is judged.
    """
    routers = {f.rel for f in service_files(project, "routers")}
    below = service_files(project, "routers", "services", "impl") + project.modules_under(project.sub("om"))
    for file in below:
        tree = project.tree(file)
        if tree is None:
            continue
        names = imported_names(project, file)
        for read in header_reads(tree, imported_names(project, file)):
            yield Violation.at(file.rel, read, "reads `.headers`; the gateway parses headers, nothing below it does")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and is_fastapi_header(call_name(node, names)):
                yield Violation.at(file.rel, node, "takes a `Header(...)` parameter; the gateway parses headers")
            elif isinstance(node, ast.Call) and is_fastapi_security(call_name(node, names)):
                yield Violation.at(
                    file.rel,
                    node,
                    f"builds `{last(call_name(node, names) or '')}`, which reads a header; the gateway reads the credential",
                )
            elif (
                file.rel in routers
                and isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.lower() == "authorization"
            ):
                yield Violation.at(file.rel, node, "names the Authorization header; only the gateway reads a token")
    for file in project.modules_under(project.sub("services"), project.sub("gateway")):
        tree = project.tree(file)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args or last(dotted(node.args[0])) != "CORSMiddleware":
                continue
            origins = kwarg(node, "allow_origins")
            if literal_origins(origins):
                yield Violation.at(
                    file.rel, origins, "allow_origins is a literal; the browser apps' origins are read from settings"
                )
            pattern = kwarg(node, "allow_origin_regex")
            if const_str(pattern) is not None:
                yield Violation.at(
                    file.rel, pattern, "allow_origin_regex is a literal; the browser apps' origins are read from settings"
                )


# --- NET-07


RESPONSES = frozenset({"JSONResponse", "Response", "PlainTextResponse", "HTMLResponse", "ORJSONResponse"})
"""The response classes; each takes the body first and the status second, by position or by keyword."""


def http_error_status(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value >= 400
    name = last(dotted(node))
    return bool(name and re.match(r"^HTTP_[45]\d\d_", name))


@rule(
    "NET-07",
    coverage="partial",
    summary="Routers and service code raise no HTTP exception and set no error status; one module per service maps errors.",
)
def one_error_handler(project: Project) -> Iterator[Violation]:
    """Routers never set error statuses, and one module maps exceptions to responses.

    In `<pkg>.services.<svc>.{routers,services,impl}`: no `raise` of an
    `HTTPException`, no response built with a 4xx or 5xx status (by
    keyword or as the second argument), and no 4xx or 5xx assigned to a
    `status_code` attribute. Within each service, `<pkg>.services.<svc>`, and
    within the shared `<pkg>.gateway`, at most one module registers
    exception handlers of its own (`@app.exception_handler`, or
    `add_exception_handler` with a handler not imported from a gateway
    module). A service that registers the gateway's handlers is
    registering the one mapping, not a second. The envelope's shape and
    its request id are the project's tests.
    """
    for file in service_files(project, "routers", "services", "impl"):
        tree = project.tree(file)
        if tree is None:
            continue
        names = imported_names(project, file)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                target = resolved(dotted(node.exc), names)
                if last(target) in {"HTTPException", "StarletteHTTPException"}:
                    yield Violation.at(file.rel, node, "raises an HTTP exception; one handler at the gateway sets statuses")
            elif isinstance(node, ast.Call) and last(dotted(node.func)) in RESPONSES:
                status = kwarg(node, "status_code") or (node.args[1] if len(node.args) > 1 else None)
                if http_error_status(status):
                    yield Violation.at(file.rel, node, "builds an error response by hand; one handler at the gateway does")
            elif isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Attribute) and t.attr == "status_code" for t in targets) and http_error_status(
                    node.value
                ):
                    yield Violation.at(file.rel, node, "sets an error status by hand; one handler at the gateway does")
    registering: dict[str, list[tuple[SourceFile, ast.AST]]] = {}
    for file in project.modules_under(project.sub("services"), project.sub("gateway")):
        tree = project.tree(file)
        if tree is None:
            continue
        services = project.sub("services") + "."
        group = file.module[len(services) :].split(".")[0] if file.module.startswith(services) else "gateway"
        names = imported_names(project, file)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr == "add_exception_handler" and from_a_gateway(project, node, names):
                continue
            if node.func.attr in {"exception_handler", "add_exception_handler"}:
                registering.setdefault(group, []).append((file, node))
                break
    for found_in in registering.values():
        first = found_in[0][0]
        for file, node in found_in[1:]:
            yield Violation.at(file.rel, node, f"registers exception handlers, and so does {first.rel}; one module maps them")


def from_a_gateway(project: Project, call: ast.Call, names: dict[str, str]) -> bool:
    """Whether `add_exception_handler(E, handler)` registers a handler imported from a gateway module."""
    handler = call.args[1] if len(call.args) >= 2 else kwarg(call, "handler")
    full = resolved(dotted(handler), names) if handler is not None else None
    return bool(full) and (is_under(full or "", project.sub("gateway")) or ".gateway." in f".{full}.")


# --- NET-09


def creates(call: ast.Call) -> bool:
    """Whether a route decorator answers 201 or 202: a literal, `status.HTTP_201_CREATED`, or `HTTPStatus.CREATED`."""
    status = kwarg(call, "status_code")
    if isinstance(status, ast.Constant):
        return status.value in (201, 202)
    name = last(dotted(status))
    return bool(name and (re.match(r"^HTTP_20[12]_", name) or name in {"CREATED", "ACCEPTED"}))


def names_post(call: ast.Call) -> bool:
    """Whether a route's `methods=` names POST."""
    methods = kwarg(call, "methods")
    return isinstance(methods, ast.List | ast.Tuple | ast.Set) and any(
        isinstance(m, ast.Constant) and str(m.value).upper() == "POST" for m in methods.elts
    )


def posts(tree: ast.Module) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]]:
    """Every `@<router>.post(...)`, every `@<router>.api_route(..., methods=[..., "POST", ...])`,
    and every `<router>.add_api_route(path, endpoint, methods=[..., "POST", ...])` whose
    endpoint is a function of the module."""
    for fn, call, verb in routes(tree):
        if verb == "post" or (verb == "api_route" and names_post(call)):
            yield fn, call
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_api_route"):
            continue
        if not names_post(node):
            continue
        endpoint = node.args[1] if len(node.args) > 1 else kwarg(node, "endpoint")
        handler = functions.get(endpoint.id) if isinstance(endpoint, ast.Name) else None
        if handler is not None:
            yield handler, node


@rule(
    "NET-09",
    options=("module",),
    coverage="partial",
    summary="Every POST answering 201 or 202 takes the gateway's idempotency dependency.",
)
def creating_posts_take_a_key(project: Project) -> Iterator[Violation]:
    """Every creating `POST` in a router takes the idempotency dependency.

    A function in `<pkg>.services.<svc>.routers` decorated
    `@<router>.post(..., status_code=201 or 202)`, or registered with
    `<router>.add_api_route(..., methods=["POST"], status_code=201 or 202)`,
    takes something
    imported from the gateway's idempotency module: in a parameter's
    annotation or default (`key = Depends(idempotency_key)`), in the
    route's `dependencies=`, in the `dependencies=` of the
    `APIRouter(...)` it is declared on, or in the `dependencies=` of an
    `include_router(...)` in the same service that mounts its module's
    router. Whether a `POST` that answers 200 writes a row, and what
    the store keeps, are judged.

    Option `[tool.arch-check.options.NET-09]`:
    `module` (default `"gateway.idempotency"`), the dotted tail of the
    module the dependency is imported from.
    """
    tail = project.option("NET-09", "module", "gateway.idempotency", {"module"})

    def from_module(full: str) -> bool:
        # a name imported from the module, or the module itself (`from acme.gateway import idempotency`)
        return any(m == tail or m.endswith("." + tail) for m in (full, full.rpartition(".")[0]))

    def aliases(file: SourceFile) -> list[tuple[str, ast.expr]]:
        """The top-level names a file assigns, each with its value: `X = ...`, `X: T = ...`, `type X = ...`."""
        tree = project.tree(file)
        out: list[tuple[str, ast.expr]] = []
        for node in tree.body if tree is not None else []:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                out.append((node.targets[0].id, node.value))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                out.append((node.target.id, node.value))
            elif type(node).__name__ == "TypeAlias":  # `type IdemKey = ...`, Python 3.12 and later
                out.append((node.name.id, node.value))  # type: ignore[attr-defined]
        return out

    def carriers() -> dict[str, set[str]]:
        """Each file's names that carry the dependency, by path: imported from the module, an alias of one
        (`IdemKey = Annotated[str, Depends(idem)]`), or imported from another module of the project where it carries it.

        One fixed point over the whole project. A pass only adds names, so the loop ends: an import
        cycle, a module that imports from itself, or an alias that names itself adds nothing new.
        """
        files = project.python_files
        names = {f.rel: imported_names(project, f) for f in files}
        assigned = {f.rel: aliases(f) for f in files}
        out = {f.rel: {local for local, full in names[f.rel].items() if from_module(full)} for f in files}
        grew = True
        while grew:
            grew = False
            for f in files:
                known = out[f.rel]
                for local, full in names[f.rel].items():
                    owner, _, attr = full.rpartition(".")
                    source = project.module(owner)
                    if local not in known and source is not None and attr in out.get(source.rel, ()):
                        known.add(local)
                        grew = True
                for name, value in assigned[f.rel]:
                    if name not in known and heads(value) & known:
                        known.add(name)
                        grew = True
        return out

    def heads(node: ast.expr | None) -> set[str]:
        return {n.split(".")[0] for n in dotted_names_in(node)}

    carrying = carriers()

    def idem_names(file: SourceFile) -> set[str]:
        return carrying.get(file.rel, set())

    mounted: set[str] = set()  # routers modules, and their router names, mounted with the dependency
    for file in project.modules_under(project.sub("services")):
        tree = project.tree(file)
        if tree is None:
            continue
        idem, names = idem_names(file), imported_names(project, file)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and last(dotted(node.func)) == "include_router"
                and heads(kwarg(node, "dependencies")) & idem
            ):
                mounted_router = node.args[0] if node.args else kwarg(node, "router")
                full = resolved(dotted(mounted_router), names) if mounted_router is not None else None
                if full:
                    mounted.update({full, full.rpartition(".")[0]})
    for file in service_files(project, "routers"):
        tree = project.tree(file)
        if tree is None:
            continue
        idem = idem_names(file)
        covered = {  # router variables declared with the dependency, annotated or not
            t.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign | ast.AnnAssign)
            and isinstance(node.value, ast.Call)
            and last(dotted(node.value.func)) == "APIRouter"
            and heads(kwarg(node.value, "dependencies")) & idem
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(t, ast.Name)
        }
        for fn, call in posts(tree):
            if not creates(call):
                continue
            router = dotted(call.func.value) if isinstance(call.func, ast.Attribute) else None
            if router in covered or file.module in mounted or f"{file.module}.{router}" in mounted:
                continue
            args = fn.args
            mentioned = {h for a in [*args.posonlyargs, *args.args, *args.kwonlyargs] for h in heads(a.annotation)}
            mentioned |= {h for d in [*args.defaults, *args.kw_defaults] if d is not None for h in heads(d)}
            mentioned |= heads(kwarg(call, "dependencies"))
            if not mentioned & idem:
                yield Violation.at(file.rel, fn, f"{fn.name} answers 201 or 202 and takes no idempotency key from {tail}")


# --- NET-10


@rule(
    "NET-10",
    coverage="partial",
    summary="No router path or prefix repeats the version prefix or serves /healthz, /readyz, or /metrics.",
)
def operational_endpoints_outside_the_prefix(project: Project) -> Iterator[Violation]:
    """The version prefix is applied once, where routers are mounted, and the operational endpoints sit outside it.

    In `<pkg>.services.<svc>.routers`, no route path and no
    `APIRouter(prefix=...)` starts with `/v<digit>`, and none is
    `/healthz`, `/readyz`, or `/metrics`. What each endpoint does, and
    the load balancer's rule for `/metrics`, are judged.
    """
    for file in service_files(project, "routers"):
        tree = project.tree(file)
        if tree is None:
            continue
        paths: list[tuple[ast.AST, str]] = []
        for _, call, _ in routes(tree):
            path = const_str(call.args[0]) if call.args else const_str(kwarg(call, "path"))
            if path is not None:
                paths.append((call, path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and last(dotted(node.func)) == "APIRouter":
                prefix = const_str(kwarg(node, "prefix"))
                if prefix is not None:
                    paths.append((node, prefix))
        for node, path in paths:
            if VERSIONED.match(path):
                yield Violation.at(file.rel, node, f"{path!r} repeats the version prefix; it is applied once, at mount")
            elif any(path == p or path.startswith(p + "/") for p in OPERATIONAL):
                yield Violation.at(file.rel, node, f"{path!r} is operational; it lives on the app, outside the prefix")


# --- NET-13


@rule(
    "NET-13",
    coverage="partial",
    summary="Wire types build on a frozen View and a forbidding RequestBody; no route returns an OM entity or pages by offset.",
)
def wire_types_are_curated(project: Project) -> Iterator[Violation]:
    """Wire types are hand-written on the two bases, and entities never cross the wire.

    In `<pkg>.services.<svc>.types`: the class `View` sets
    `frozen=True`, the class `RequestBody` sets `extra="forbid"`, no
    other class builds on `BaseModel` directly, and no class sets
    `frozen=False`. In `<pkg>.services.<svc>.routers`: no
    `response_model` and no return annotation names anything imported
    from `<pkg>.om`, and no route takes a parameter named `offset`.
    Names, the clamp, and the cursor are judged.
    """
    for file in service_files(project, "types"):
        tree = project.tree(file)
        if tree is None:
            continue
        for cls in classes(tree):
            bases = [last(b) for b in base_names(cls)]
            if cls.name == "View" and not is_true(config_value(cls, "frozen")):
                yield Violation.at(file.rel, cls, "View is not frozen=True; a view is immutable")
            elif cls.name == "RequestBody" and const_str(config_value(cls, "extra")) != "forbid":
                yield Violation.at(file.rel, cls, 'RequestBody does not set extra="forbid"; a request refuses unknown keys')
            elif cls.name not in {"View", "RequestBody"} and "BaseModel" in bases:
                yield Violation.at(file.rel, cls, f"{cls.name} builds on BaseModel; a wire type builds on View or RequestBody")
            frozen = config_value(cls, "frozen")
            if isinstance(frozen, ast.Constant) and frozen.value is False:
                yield Violation.at(file.rel, cls, f"{cls.name} sets frozen=False; a wire type is immutable")
    om = project.sub("om")
    for file in service_files(project, "routers"):
        tree = project.tree(file)
        if tree is None:
            continue
        names = imported_names(project, file)
        for fn, call, _ in routes(tree):
            shown = dotted_names_in(kwarg(call, "response_model")) + dotted_names_in(fn.returns)
            for name in shown:
                full = resolved(name, names)
                if full and is_under(full, om):
                    yield Violation.at(file.rel, fn, f"{fn.name} returns {full}; an OM entity never goes on the wire")
                    break
            for a in [*fn.args.args, *fn.args.kwonlyargs]:
                if a.arg == "offset":
                    yield Violation.at(file.rel, a, f"{fn.name} pages by offset; a list pages by an opaque cursor")


# --- NET-14


@rule(
    "NET-14",
    options=("target",),
    coverage="partial",
    summary="The Makefile has the openapi target, and a CI workflow runs it and fails on a diff.",
)
def openapi_is_diffed(project: Project) -> Iterator[Violation]:
    """The OpenAPI document is regenerated by a make target and diffed in CI.

    When the tree has a service with routers: the `Makefile` has the
    target, and some `.github/workflows/*.yml` file runs `make` on it,
    directly or through a target that reaches it (a prerequisite, or a
    `make` or `$(MAKE)` line in a recipe), and runs `git diff` with
    `--exit-code` or `--quiet`, in the workflow or in a recipe it
    reaches. Whether the document is written by hand anywhere is
    judged.

    Option `[tool.arch-check.options.NET-14]`: `target` (default
    `"openapi"`), the make target that writes the document.
    """
    target = project.option("NET-14", "target", "openapi", {"target"})
    if not service_files(project, "routers"):
        return
    if not is_file(project, "Makefile"):
        yield Violation("Makefile", 1, 1, f"no Makefile; `make {target}` regenerates the OpenAPI document")
        return
    if target not in make_targets(project):
        yield Violation("Makefile", 1, 1, f"no `{target}` target; it regenerates the committed OpenAPI document")
        return
    targets = make_targets(project)
    for rel in project.files(".github/workflows/*.yml", ".github/workflows/*.yaml"):
        text = uncommented(project.read(rel) or "")
        reached = reachable(targets, made(text))
        recipes = [line for name in reached for line in targets[name].recipe if not line.lstrip().startswith("#")]
        if target in reached and any(DIFF.search(line) for line in [*text.splitlines(), *recipes]):
            return
    yield Violation(
        "Makefile", 1, 1, f"no CI workflow runs `make {target}` and a failing `git diff`; the document is diffed in CI"
    )


DIFF = re.compile(r"\bgit\s+diff\b[^\n#]*\s--(exit-code|quiet)\b")
MAKE = re.compile(r"(?:\bmake|\$\(MAKE\)|\$\{MAKE\})((?:\s+[^\s&|;#)]+)+)")


def uncommented(text: str) -> str:
    """The text without its comment lines: a step commented out runs nothing."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def made(text: str) -> set[str]:
    """The targets a text runs with `make` or `$(MAKE)`, flags and variable assignments left out."""
    out: set[str] = set()
    for m in MAKE.finditer(text):
        out.update(w for w in m.group(1).split() if not w.startswith("-") and "=" not in w)
    return out


def reachable(targets: dict[str, Target], start: set[str]) -> set[str]:
    """The Makefile targets `start` reaches through prerequisites and `make` lines in recipes, `start` included."""
    seen: set[str] = set()
    stack = [t for t in start if t in targets]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        rule_ = targets[name]
        nxt = set(rule_.prerequisites) | made("\n".join(rule_.recipe))
        stack.extend(t for t in nxt if t in targets and t not in seen)
    return seen


# --- NET-29


@rule(
    "NET-29",
    coverage="partial",
    summary="No table class and no migrations folder under services or workers.",
)
def no_data_tier_per_service(project: Project) -> Iterator[Violation]:
    """The schema belongs to the OM's roles, never to a service.

    No class with `__tablename__` under `<pkg>.services` or
    `<pkg>.workers`, and no `migrations/` folder or `alembic.ini` in
    any `services/*` or `workers/*` folder. Which database each
    service's settings name is judged.
    """
    for file, tree in project.trees(project.sub("services"), project.sub("workers")):
        for cls in classes(tree):
            for stmt in cls.body:
                targets = (
                    stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
                )
                if any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in targets):
                    yield Violation.at(file.rel, cls, f"{cls.name} is a table in a service; tables live in the OM")
    for role in ("services", "workers"):
        for folder in subdirs(project, role):
            seen: set[str] = set()
            for rel in walk(project, folder):
                parts = rel.split("/")
                where = "/".join(parts[: parts.index("migrations") + 1]) if "migrations" in parts[:-1] else None
                if parts[-1] == "alembic.ini":
                    where = rel
                if where is not None and where not in seen:
                    seen.add(where)
                    yield Violation(where, 1, 1, "a migration in a service; the schema timeline stays with the OM")
