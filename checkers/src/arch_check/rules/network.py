"""The network lenses a parser can decide: the gateway's edge, errors, idempotency, the prefix, wire types, the document.

Each rule reads the service distributions, `<pkg>.services.<svc>`,
and within one the parts the guideline names: `routers`, `services`
(the service interfaces), `impl`, `types`, and `gateway`. What a route
does at run time, and every judgment about shape, stays with the
review.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator

from arch_check.model import Violation
from arch_check.project import Project, SourceFile, base_names, classes, dotted, is_under, keywords, last
from arch_check.registry import rule
from arch_check.rules._text_util import (
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


def names_in(node: ast.AST | None) -> list[str]:
    """The dotted names an expression mentions, outermost first."""
    if node is None:
        return []
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute | ast.Name):
            name = dotted(n)
            if name:
                out.append(name)
    return out


# --- NET-06


def is_fastapi_header(name: str | None) -> bool:
    return bool(name) and is_under(name or "", "fastapi") and last(name) == "Header"


@rule(
    "NET-06",
    coverage="partial",
    summary="Routers, service code, and the OM read no header or token; CORS origins are never a literal.",
)
def no_headers_below_the_gateway(project: Project) -> Iterator[Violation]:
    """Nothing below the gateway parses headers, and the allowed origins come from settings.

    In `<pkg>.services.<svc>.{routers,services,impl}` and in `<pkg>.om`:
    no `.headers` read, no FastAPI `Header(...)`, and no string that is
    `authorization`. Anywhere under `<pkg>.services` and `<pkg>.gateway`,
    `CORSMiddleware` never gets `allow_origins` as a literal list or
    `"*"`. A second path to the internet is judged.
    """
    below = service_files(project, "routers", "services", "impl") + project.modules_under(project.sub("om"))
    for file in below:
        tree = project.tree(file)
        if tree is None:
            continue
        names = imported_names(project, file)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "headers":
                yield Violation.at(file.rel, node, "reads `.headers`; the gateway parses headers, nothing below it does")
            elif isinstance(node, ast.Call) and is_fastapi_header(call_name(node, names)):
                yield Violation.at(file.rel, node, "takes a `Header(...)` parameter; the gateway parses headers")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() == "authorization":
                yield Violation.at(file.rel, node, "names the Authorization header; only the gateway reads a token")
    for file in project.modules_under(project.sub("services"), project.sub("gateway")):
        tree = project.tree(file)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args or last(dotted(node.args[0])) != "CORSMiddleware":
                continue
            origins = kwarg(node, "allow_origins")
            if isinstance(origins, ast.List | ast.Tuple | ast.Set) or const_str(origins) is not None:
                yield Violation.at(
                    file.rel, origins, "allow_origins is a literal; the browser apps' origins are read from settings"
                )


# --- NET-07


def http_error_status(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value >= 400
    name = last(dotted(node))
    return bool(name and re.match(r"^HTTP_[45]\d\d_", name))


@rule(
    "NET-07",
    coverage="partial",
    summary="Routers and service code raise no HTTP exception and set no error status; one module registers handlers.",
)
def one_error_handler(project: Project) -> Iterator[Violation]:
    """Routers never set error statuses, and one module maps exceptions to responses.

    In `<pkg>.services.<svc>.{routers,services,impl}`: no `raise` of an
    `HTTPException`, and no response built with a 4xx or 5xx
    `status_code`. Across `<pkg>.services` and `<pkg>.gateway`, at most
    one module registers exception handlers (`@app.exception_handler`
    or `add_exception_handler`). The envelope's shape and its request
    id are the project's tests.
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
            elif isinstance(node, ast.Call) and last(dotted(node.func)) in {"JSONResponse", "Response", "PlainTextResponse"}:
                if http_error_status(kwarg(node, "status_code")):
                    yield Violation.at(file.rel, node, "builds an error response by hand; one handler at the gateway does")
    registering: list[tuple[SourceFile, ast.AST]] = []
    for file in project.modules_under(project.sub("services"), project.sub("gateway")):
        tree = project.tree(file)
        if tree is None:
            continue
        for node in ast.walk(tree):
            hit = (isinstance(node, ast.Call) and last(dotted(node.func)) in {"exception_handler", "add_exception_handler"}) and (
                isinstance(node.func, ast.Attribute)
            )
            if hit:
                registering.append((file, node))
                break
    if len(registering) > 1:
        first = registering[0][0]
        for file, node in registering[1:]:
            yield Violation.at(file.rel, node, f"registers exception handlers, and so does {first.rel}; one module maps them")


# --- NET-09


def creates(call: ast.Call) -> bool:
    status = kwarg(call, "status_code")
    if isinstance(status, ast.Constant):
        return status.value in (201, 202)
    name = last(dotted(status))
    return bool(name and re.match(r"^HTTP_20[12]_", name))


@rule(
    "NET-09",
    coverage="partial",
    summary="Every POST answering 201 or 202 takes the gateway's idempotency dependency.",
)
def creating_posts_take_a_key(project: Project) -> Iterator[Violation]:
    """Every creating `POST` in a router takes the idempotency dependency.

    A function in `<pkg>.services.<svc>.routers` decorated
    `@<router>.post(..., status_code=201 or 202)` has a parameter
    annotated with, or a `dependencies=` entry naming, something
    imported from the gateway's idempotency module. Whether a `POST`
    that answers 200 writes a row, and what the store keeps, are judged.

    Option `[tool.arch-check.options.NET-09]`:
    `module` (default `"gateway.idempotency"`), the dotted tail of the
    module the dependency is imported from.
    """
    tail = project.option("NET-09", "module", "gateway.idempotency", {"module"})
    for file in service_files(project, "routers"):
        tree = project.tree(file)
        if tree is None:
            continue
        idem = {
            local
            for local, full in imported_names(project, file).items()
            if full.rpartition(".")[0] == tail or full.rpartition(".")[0].endswith("." + tail)
        }
        for fn, call, verb in routes(tree):
            if verb != "post" or not creates(call):
                continue
            mentioned = {n.split(".")[0] for a in [*fn.args.args, *fn.args.kwonlyargs] for n in names_in(a.annotation)}
            mentioned |= {n.split(".")[0] for n in names_in(kwarg(call, "dependencies"))}
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


def config_value(cls: ast.ClassDef, key: str) -> ast.expr | None:
    """A model config key set on a class: `model_config = ConfigDict(key=...)`, a dict literal, or a class keyword."""
    kw = keywords(cls).get(key)
    if kw is not None:
        return kw
    for stmt in cls.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target, value = stmt.target, stmt.value
        if not (isinstance(target, ast.Name) and target.id == "model_config") or value is None:
            continue
        if isinstance(value, ast.Call):
            found = kwarg(value, key)
            if found is not None:
                return found
        elif isinstance(value, ast.Dict):
            for k, v in zip(value.keys, value.values, strict=True):
                if const_str(k) == key:
                    return v
    return None


def is_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


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
            shown = names_in(kwarg(call, "response_model")) + names_in(fn.returns)
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
    coverage="partial",
    summary="The Makefile has the openapi target, and a CI workflow runs it and fails on a diff.",
)
def openapi_is_diffed(project: Project) -> Iterator[Violation]:
    """The OpenAPI document is regenerated by a make target and diffed in CI.

    When the tree has a service with routers: the `Makefile` has the
    target, and some `.github/workflows/*.yml` file runs `make <target>`
    and `git diff --exit-code`. Whether the document is written by hand
    anywhere is judged.

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
    run = re.compile(rf"\bmake\b[^\n#]*\b{re.escape(target)}\b")
    for rel in project.files(".github/workflows/*.yml", ".github/workflows/*.yaml"):
        text = project.read(rel) or ""
        if run.search(text) and "git diff --exit-code" in text:
            return
    yield Violation(
        "Makefile", 1, 1, f"no CI workflow runs `make {target}` and `git diff --exit-code`; the document is diffed in CI"
    )


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
