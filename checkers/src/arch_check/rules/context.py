"""The context group: stages, scopes, the first parameter, and tenant keys.

The stages are the classes the guideline names, `RequestContext`,
`IdentityContext`, `OpContext`, and `OperatorContext`, declared with the
scopes (`*Scope`) in the stage module `<pkg>.om.opcontext`. A rule that
reads the stage module finds nothing to judge in a project without one.
The operations are the public methods of `*ManagerInterface`,
`*ServiceInterface`, and `*HandlerInterface` classes, and the storage
signatures those of `*StorageInterface` classes.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from arch_check.config import glob_match
from arch_check.model import Violation
from arch_check.project import Function, Project, SourceFile, base_names, classes, dotted, is_under, last, methods
from arch_check.registry import rule
from arch_check.rules._contracts_util import (
    REQUEST_STAGE,
    STAGES,
    arguments,
    body_without_docstring,
    called_name,
    calls,
    classes_named,
    declared_fields,
    enclosing,
    has_docstring,
    head_name,
    in_om_storage,
    interface_methods,
    names_in,
    service_part,
    stage_classes,
    stage_module,
    union_members,
)

OPERATION_INTERFACES = ("ManagerInterface", "ServiceInterface", "HandlerInterface")
LIFECYCLE = frozenset({"describe", "start", "close", "healthcheck"})
CLASS_BUILDERS = frozenset({"model_validate", "model_construct", "model_copy"})
ABOVE_REQUEST = tuple(s for s in STAGES if s != REQUEST_STAGE)


def bases(cls: ast.ClassDef) -> list[str]:
    return [last(b) or b for b in base_names(cls)]


def scope_names(project: Project) -> set[str]:
    """The scopes: the classes of the stage module whose name ends in `Scope`."""
    return {name for name in stage_classes(project) if name.endswith("Scope")}


def stage_of(arg: ast.arg) -> str | None:
    """The stage a parameter is typed with, alone or in a union with None."""
    members = [m for m in union_members(arg.annotation) if m != "None"]
    return members[0] if len(members) == 1 and members[0] in STAGES else None


def operations(project: Project) -> Iterator[tuple[SourceFile, ast.ClassDef, Function]]:
    for suffix in OPERATION_INTERFACES:
        for file, cls in classes_named(project, suffix):
            for fn in interface_methods(cls):
                yield file, cls, fn


def constructed(call: ast.Call, names: set[str]) -> str | None:
    """What a call constructs when it is one of `names`: `X(...)`, `m.X(...)`, `X.model_validate(...)`."""
    func = call.func
    if isinstance(func, ast.Name) and func.id in names:
        return func.id
    if isinstance(func, ast.Attribute):
        if func.attr in names:
            return func.attr
        if func.attr in CLASS_BUILDERS and last(dotted(func.value)) in names:
            return last(dotted(func.value))
    return None


# --- CTX-01


@rule(
    "CTX-01",
    coverage="partial",
    summary="A stage or scope parameter of an operation comes first; no manager operation takes a scope.",
)
def context_comes_first(project: Project) -> Iterator[Violation]:
    """On every public method of a `*ManagerInterface`, `*ServiceInterface`,
    or `*HandlerInterface`, a parameter typed with a stage or a scope is
    the first one after `self`, and a `*ManagerInterface` method never
    takes a scope first. A method that takes no context at all is
    CTX-16's; identity fetched from a request object is judged."""
    scopes = scope_names(project)
    contexts = set(STAGES) | scopes
    for file, cls, fn in operations(project):
        args = arguments(fn)
        for i, arg in enumerate(args):
            typed = [m for m in union_members(arg.annotation) if m in contexts]
            if typed and i > 0:
                yield Violation.at(
                    file.rel, arg, f"{cls.name}.{fn.name} takes {typed[0]} in position {i + 1}; the context is first"
                )
        if cls.name.endswith("ManagerInterface") and args and head_name(args[0].annotation) in scopes:
            yield Violation.at(file.rel, args[0], f"{cls.name}.{fn.name} takes a scope; a manager operation takes OpContext")


# --- CTX-02


@rule(
    "CTX-02",
    coverage="partial",
    summary="The stage module imports only the base and exceptions and holds no entity; the request stage holds its ids.",
)
def context_carries_ids(project: Project) -> Iterator[Violation]:
    """The stage module imports nothing of the product but
    `<pkg>.om.base` and `<pkg>.om.exceptions`. No field of a class there
    is typed with a class defined under `<pkg>.om.<ns>.types`.
    `RequestContext` declares `request_id` and `app`, and a class of the
    module declares `credential_id`. Identity passed beside the context
    is judged."""
    file = stage_module(project)
    tree = project.tree(file) if file else None
    if file is None or tree is None:
        return
    allowed = (f"{project.sub('om')}.base", f"{project.sub('om')}.exceptions")
    for imp in project.imports(file):
        if is_under(imp.module, project.package) and not any(is_under(imp.module, a) for a in allowed):
            yield Violation.at(file.rel, imp.node, f"the stage module imports {imp.module}; it reaches nothing above the base")
    entities: set[str] = set()
    for f, t in project.trees(project.sub("om")):
        if ".types" in f".{f.module}" and "types" in f.module.split(".")[project.sub("om").count(".") + 2 :]:
            entities.update(c.name for c in classes(t))
    stages = stage_classes(project)
    for cls in stages.values():
        for name, node in declared_fields(cls).items():
            annotation = node.annotation if isinstance(node, ast.AnnAssign) else getattr(node, "returns", None)
            hit = sorted(names_in(annotation) & entities)
            if hit:
                yield Violation.at(file.rel, node, f"{cls.name}.{name} holds the entity {hit[0]}; a context carries ids")
    request = stages.get(REQUEST_STAGE)
    if request is not None:
        missing = [f for f in ("request_id", "app") if f not in declared_fields(request)]
        if missing:
            yield Violation.at(file.rel, request, f"{REQUEST_STAGE} declares no {' or '.join(missing)}")
    if stages and not any("credential_id" in declared_fields(c) for c in stages.values()):
        yield Violation.at(file.rel, None, "no context class declares credential_id")


# --- CTX-05


@rule(
    "CTX-05",
    coverage="partial",
    summary="No OM, infra, router, or service module constructs the request stage; no router or service builds a stage.",
)
def request_stage_at_the_edge(project: Project) -> Iterator[Violation]:
    """No module under `<pkg>.om` or `<pkg>.infra` constructs
    `RequestContext`, and no module under
    `<pkg>.services.<process>.routers`, `.services`, or `.impl`
    constructs any stage. A call to the class, or to its
    `model_validate`, `model_construct`, or `model_copy`, is a
    construction. Stages above the request stage are CTX-26."""
    below = {project.sub("om"), project.sub("infra")}
    for file, tree in project.trees():
        network = service_part(project, file.module) in {"routers", "services", "impl"}
        lower = any(is_under(file.module, p) for p in below)
        if not (network or lower):
            continue
        for call in calls(tree):
            name = constructed(call, set(STAGES) if network else {REQUEST_STAGE})
            if name is not None:
                yield Violation.at(file.rel, call, f"{file.module} constructs {name}; the request stage is minted at the edge")


# --- CTX-06


def stage_bindings(fn: Function) -> dict[str, str]:
    """Names bound in a function to a stage above the request stage: its parameters and annotated locals."""
    out: dict[str, str] = {}
    for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
        stage = stage_of(arg)
        if stage in ABOVE_REQUEST:
            out[arg.arg] = stage
    for node in ast.walk(fn):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            members = [m for m in union_members(node.annotation) if m != "None"]
            if len(members) == 1 and members[0] in ABOVE_REQUEST:
                out[node.target.id] = members[0]
    return out


def returns_stage(fn: Function) -> bool:
    return any(m in STAGES for m in union_members(fn.returns))


@rule(
    "CTX-06",
    coverage="partial",
    summary="No stage above the request stage is copied or assigned; no with_/override helper returns a stage.",
)
def context_is_immutable(project: Project) -> Iterator[Violation]:
    """In a function under `<pkg>.om`, `<pkg>.services`, or
    `<pkg>.workers`, a name typed with `IdentityContext`, `OpContext`, or
    `OperatorContext` is never `.model_copy(...)`-ed and never has an
    attribute assigned. No function named `with_*` or `override*` returns
    a stage outside the stage module. Narrowing passed as an argument is
    judged."""
    module = stage_module(project)
    prefixes = (project.sub("om"), project.sub("services"), project.sub("workers"))
    for file, tree in project.trees(*prefixes):
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            if fn.name.startswith(("with_", "override")) and returns_stage(fn) and file != module:
                yield Violation.at(file.rel, fn, f"{fn.name} returns a stage; narrowing is an argument, not a new context")
            bound = stage_bindings(fn)
            if not bound:
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "model_copy"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in bound
                ):
                    name = node.func.value.id
                    yield Violation.at(file.rel, node, f"copies {name}, a {bound[name]}; a context flows unchanged")
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AugAssign) else []
                )
                for t in targets:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in bound:
                        yield Violation.at(file.rel, t, f"assigns {t.value.id}.{t.attr}; a context is immutable")


# --- CTX-07


@rule(
    "CTX-07",
    coverage="partial",
    summary="A ContextVar lives only in the log module; nothing uses a thread local.",
)
def no_ambient_state(project: Project) -> Iterator[Violation]:
    """`ContextVar(...)` is called only in the log module, and nothing
    calls `threading.local()`. Option `[tool.arch-check.options.CTX-07]`:
    `modules`, the modules below the package allowed to hold a context
    variable (default `infra.observability`). What a context variable is
    read for is judged."""
    allowed = {f"{project.package}.{m}" for m in project.option("CTX-07", "modules", ["infra.observability"], {"modules"})}
    for file, tree in project.trees():
        threading_local = any(
            isinstance(n, ast.ImportFrom) and n.module == "threading" and any(a.name == "local" for a in n.names)
            for n in ast.walk(tree)
        )
        for call in calls(tree):
            name = dotted(call.func)
            if last(name) == "ContextVar" and file.module not in allowed:
                yield Violation.at(file.rel, call, f"{file.module} creates a ContextVar; ambient state flows through the context")
            elif name == "threading.local" or (name == "local" and threading_local):
                yield Violation.at(file.rel, call, f"{file.module} uses a thread local; ambient state flows through the context")


# --- CTX-08


@rule(
    "CTX-08",
    coverage="partial",
    summary="No storage module or router reads a permission.",
)
def authorization_in_managers(project: Project) -> Iterator[Violation]:
    """No OM storage module and no module under
    `<pkg>.services.<process>.routers` reads a member of a `*Permission`
    enum (`Permission.WRITE`). That every mutating manager operation
    starts with its permission check is judged."""
    for file, tree in project.trees():
        if not (in_om_storage(project, file.module) or service_part(project, file.module) == "routers"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and (last(dotted(node.value)) or "").endswith("Permission"):
                yield Violation.at(file.rel, node, f"{file.module} reads {dotted(node)}; authorization is the manager's decision")


# --- CTX-10


def required(fn: Function) -> set[str]:
    """The parameters of a function that have no default."""
    a = fn.args
    positional = [*a.posonlyargs, *a.args]
    out = {p.arg for p in positional[: len(positional) - len(a.defaults)]}
    return out | {p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is None}


@rule(
    "CTX-10",
    coverage="partial",
    summary="Storage takes org_id first and user_id right after; an OpContext operation takes no org_id.",
)
def tenant_first(project: Project) -> Iterator[Violation]:
    """On a `*StorageInterface` method that takes `org_id`, it is the first
    parameter, and a required `user_id` beside it comes second. No operation of a
    `*ManagerInterface` or `*ServiceInterface` that takes `OpContext`
    first also takes `org_id`. A tenant-less storage method is CTX-12's;
    what counts as a filter is judged."""
    for file, cls in classes_named(project, "StorageInterface"):
        for fn in interface_methods(cls):
            names = [a.arg for a in arguments(fn)]
            if "org_id" in names and names[0] != "org_id":
                yield Violation.at(file.rel, fn, f"{cls.name}.{fn.name} takes org_id in position {names.index('org_id') + 1}")
            elif names[:1] == ["org_id"] and "user_id" in required(fn) and names[1] != "user_id":
                yield Violation.at(file.rel, fn, f"{cls.name}.{fn.name} takes user_id after a narrower id; it follows org_id")
    for file, cls, fn in operations(project):
        args = arguments(fn)
        if args and stage_of(args[0]) == "OpContext" and "org_id" in [a.arg for a in args]:
            yield Violation.at(file.rel, fn, f"{cls.name}.{fn.name} takes org_id beside OpContext, which carries it")


# --- CTX-12


@rule(
    "CTX-12",
    coverage="partial",
    summary="Every tenant-less storage method has a docstring, and is on the project's list when it keeps one.",
)
def tenantless_is_enumerated(project: Project) -> Iterator[Violation]:
    """A `*StorageInterface` method whose first parameter is not `org_id`
    carries a docstring that says why. Option
    `[tool.arch-check.options.CTX-12]`: `tenantless`, the `Class.method`
    names of those methods; when set, a method not on it and an entry
    that names no such method are findings, so the list is the
    enumerating test. Whether a sweep returns its tenant is judged."""
    listed: list[str] = project.option("CTX-12", "tenantless", [], {"tenantless"})
    seen: set[str] = set()
    for file, cls in classes_named(project, "StorageInterface"):
        if cls.name == "StorageInterface":
            continue
        for fn in interface_methods(cls):
            if [a.arg for a in arguments(fn)][:1] == ["org_id"]:
                continue
            key = f"{cls.name}.{fn.name}"
            seen.add(key)
            if not has_docstring(fn):
                yield Violation.at(file.rel, fn, f"{key} takes no tenant and has no docstring saying why")
            if listed and key not in listed:
                yield Violation.at(file.rel, fn, f"{key} takes no tenant and is not on the tenantless list")
    for key in sorted(set(listed) - seen):
        yield Violation.at(
            "pyproject.toml", None, f"[tool.arch-check.options.CTX-12] lists {key}, which is no tenant-less method"
        )


# --- CTX-14


@rule(
    "CTX-14",
    coverage="partial",
    summary="Every cache and bucket operation takes org_id first.",
)
def cache_and_buckets_take_the_tenant(project: Project) -> Iterator[Violation]:
    """Every public method of `CacheInterface` and `BucketsInterface`,
    the lifecycle ones (`describe`, `start`, `close`, `healthcheck`)
    left out, takes `org_id` first. Option
    `[tool.arch-check.options.CTX-14]`: `interfaces` (default
    `CacheInterface`, `BucketsInterface`). The key layout inside an impl
    is judged."""
    names = set(project.option("CTX-14", "interfaces", ["CacheInterface", "BucketsInterface"], {"interfaces"}))
    for file, tree in project.trees(project.sub("infra")):
        for cls in classes(tree):
            if cls.name not in names:
                continue
            for fn in interface_methods(cls):
                if fn.name not in LIFECYCLE and [a.arg for a in arguments(fn)][:1] != ["org_id"]:
                    yield Violation.at(file.rel, fn, f"{cls.name}.{fn.name} does not take org_id first")


# --- CTX-15


@rule(
    "CTX-15",
    coverage="partial",
    summary="TopicPayload declares org_id and every payload in TOPIC_PAYLOADS extends it.",
)
def payloads_carry_the_tenant(project: Project) -> Iterator[Violation]:
    """`TopicPayload` under `<pkg>.infra` declares `org_id`, and every
    value of the `TOPIC_PAYLOADS` mapping is a class that extends it. The
    tenant comparison in the socket is judged."""
    parents: dict[str, set[str]] = {}
    for _, tree in project.trees():
        for cls in classes(tree):
            parents.setdefault(cls.name, set()).update(bases(cls))

    def extends(name: str, target: str) -> bool:
        todo, seen = [name], set()
        while todo:
            n = todo.pop()
            if n == target:
                return True
            if n not in seen:
                seen.add(n)
                todo.extend(parents.get(n, ()))
        return False

    for file, tree in project.trees(project.sub("infra")):
        for cls in classes(tree):
            if cls.name == "TopicPayload" and "org_id" not in declared_fields(cls):
                yield Violation.at(file.rel, cls, "TopicPayload declares no org_id; every payload carries the tenant")
        for node in ast.walk(tree):
            target: ast.expr | None = node.target if isinstance(node, ast.AnnAssign) else None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
            value = getattr(node, "value", None) if target is not None else None
            if not (isinstance(target, ast.Name) and target.id == "TOPIC_PAYLOADS" and isinstance(value, ast.Dict)):
                continue
            for v in value.values:
                name = last(dotted(v))
                if name and not extends(name, "TopicPayload"):
                    yield Violation.at(file.rel, v, f"{name} does not extend TopicPayload, so it carries no tenant")


# --- CTX-20


@rule(
    "CTX-20",
    coverage="partial",
    summary="OperatorContext refines IdentityContext with no org_id; no parameter accepts both it and OpContext.",
)
def operator_plane_has_its_own_context(project: Project) -> Iterator[Violation]:
    """`OperatorContext` has `IdentityContext` among its bases, and neither
    it nor a stage it refines declares `org_id`. No parameter anywhere
    is typed as a union of `OpContext` and `OperatorContext`. The
    allowlist gate is judged."""
    file = stage_module(project)
    stages = stage_classes(project)
    operator = stages.get("OperatorContext")
    if file is not None and operator is not None:
        if "IdentityContext" not in bases(operator):
            yield Violation.at(file.rel, operator, "OperatorContext does not refine IdentityContext")
        chain, todo = [], ["OperatorContext"]
        while todo:
            name = todo.pop()
            if name in stages and name not in chain:
                chain.append(name)
                todo.extend(bases(stages[name]))
        for name in chain:
            if "org_id" in declared_fields(stages[name]):
                yield Violation.at(file.rel, stages[name], f"{name} declares org_id; the operator plane has no tenant")
    for f, tree in project.trees():
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
                if {"OpContext", "OperatorContext"} <= set(union_members(arg.annotation)):
                    yield Violation.at(f.rel, arg, f"{fn.name} accepts either OpContext or OperatorContext; it takes one")


# --- CTX-21


@rule(
    "CTX-21",
    coverage="partial",
    summary="Stages are classes on the chain: Identity and Op refine Request, Operator refines Identity, Op not Identity.",
)
def stage_hierarchy(project: Project) -> Iterator[Violation]:
    """In the stage module, no stage is a `Protocol`; `IdentityContext` and
    `OpContext` subclass `RequestContext`; `OperatorContext` subclasses
    `IdentityContext`; `OpContext` does not subclass `IdentityContext`.
    A stage the module does not declare is not judged. Re-checking a
    credential inside an operation is judged."""
    file = stage_module(project)
    stages = stage_classes(project)
    if file is None:
        return
    expected = {"IdentityContext": REQUEST_STAGE, "OpContext": REQUEST_STAGE, "OperatorContext": "IdentityContext"}
    for name in STAGES:
        cls = stages.get(name)
        if cls is None:
            continue
        if "Protocol" in bases(cls):
            yield Violation.at(file.rel, cls, f"{name} is a Protocol; a stage is a concrete frozen type")
        parent = expected.get(name)
        if parent and parent in stages and parent not in bases(cls):
            yield Violation.at(file.rel, cls, f"{name} does not subclass {parent}")
        if name == "OpContext" and "IdentityContext" in bases(cls):
            yield Violation.at(file.rel, cls, "OpContext subclasses IdentityContext; it does not refine it")


# --- CTX-22


def annotation_names(tree: ast.Module) -> set[str]:
    """Every name spelled in an annotation of a module, and in a class's bases."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg | ast.AnnAssign):
            found |= names_in(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found |= names_in(node.returns)
        elif isinstance(node, ast.ClassDef):
            found |= {last(b) or b for b in base_names(node)}
    return found


def is_property_member(node: ast.stmt) -> bool:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return True
    if isinstance(node, ast.Pass):
        return True
    return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
        last(dotted(d)) == "property" for d in node.decorator_list
    )


@rule(
    "CTX-22",
    coverage="partial",
    summary="Every scope is a Protocol of read-only properties that something declares or builds on.",
)
def scopes_are_protocols(project: Project) -> Iterator[Violation]:
    """Every class named `*Scope` in the stage module has `Protocol` among
    its bases and a body of properties only, and its name appears in an
    annotation or a base list somewhere other than its own definition.
    Whether a helper declares the narrowest scope is judged."""
    file = stage_module(project)
    if file is None:
        return
    scopes = {n: c for n, c in stage_classes(project).items() if n.endswith("Scope")}
    used: set[str] = set()
    for f, tree in project.trees():
        if f == file:
            for cls in classes(tree):
                used |= {last(b) or b for b in base_names(cls)}
                for fn in methods(cls):
                    for arg in fn.args.args:
                        used |= names_in(arg.annotation)
                    used |= names_in(fn.returns)
            for node in tree.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    used |= annotation_names(ast.Module(body=[node], type_ignores=[]))
        else:
            used |= annotation_names(tree)
    for name, cls in scopes.items():
        if "Protocol" not in bases(cls):
            yield Violation.at(file.rel, cls, f"{name} is not a Protocol; a scope is satisfied structurally")
        extra = [n for n in body_without_docstring(cls.body) if not is_property_member(n)]
        if extra:
            yield Violation.at(file.rel, extra[0], f"{name} holds more than read-only properties")
        if name not in used:
            yield Violation.at(file.rel, cls, f"{name} is declared by no consumer and built on by no scope")


# --- CTX-24


@rule(
    "CTX-24",
    coverage="partial",
    summary="An operation taking OperatorContext never calls outbox_row or builds an OpContext.",
)
def operator_writes_own_provenance(project: Project) -> Iterator[Violation]:
    """No function whose first parameter (after `self`) is typed
    `OperatorContext` calls `outbox_row(...)` or constructs `OpContext`.
    Who the operator row names is judged or run as a test."""
    for file, tree in project.trees():
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            args = [*fn.args.posonlyargs, *fn.args.args]
            if args and args[0].arg in {"self", "cls"}:
                args = args[1:]
            if not args or stage_of(args[0]) != "OperatorContext":
                continue
            for call in calls(fn):
                name = called_name(call)
                if name == "outbox_row" or constructed(call, {"OpContext"}):
                    yield Violation.at(
                        file.rel, call, f"{fn.name} takes OperatorContext and calls {name}; the operator plane stamps its own"
                    )


# --- CTX-26

SITE_STAGES = ["IdentityContext", "OpContext", "OperatorContext", "SecurityContext"]
DEFAULT_SITES = ["**/om/tenancy/impl/**", "**/om/opcontext.py"]


def site_matches(entry: str, rel: str, qualname: str) -> bool:
    path, _, where = entry.partition("::")
    return glob_match(path, rel) and (not where or where == qualname)


@rule(
    "CTX-26",
    coverage="full",
    summary="Every site that constructs a stage above the request stage is on the allowed list, and every entry is used.",
)
def stage_sites_are_enumerated(project: Project) -> Iterator[Violation]:
    """A call that constructs `IdentityContext`, `OpContext`,
    `OperatorContext`, or `SecurityContext` (the class, or its
    `model_validate`, `model_construct`, or `model_copy`), or calls a
    configured builder, is allowed only at a listed site. The checker is
    the enumerating test. Options `[tool.arch-check.options.CTX-26]`:
    `sites`, each a path glob or `glob::Qualname.method` (default the
    tenancy manager's impl and the stage module); `stages`, the names
    that count (default the four above); `builders`, functions that
    assemble a stage (default none). A configured site that constructs
    nothing is itself a finding."""
    keys = {"sites", "stages", "builders"}
    configured = "sites" in project.config.options.get("CTX-26", {})
    sites = project.option("CTX-26", "sites", list(DEFAULT_SITES), keys)
    names = set(project.option("CTX-26", "stages", list(SITE_STAGES), keys))
    names |= set(project.option("CTX-26", "builders", [], keys))
    used: set[str] = set()
    for file, tree in project.trees():
        scope = enclosing(tree)
        for call in calls(tree):
            name = constructed(call, names)
            if name is None:
                continue
            where = scope.get(call, "<module>")
            hits = [s for s in sites if site_matches(s, file.rel, where)]
            used.update(hits)
            if not hits:
                yield Violation.at(
                    file.rel, call, f"{where} constructs {name}; only a listed transition builds a stage above the request stage"
                )
    if configured:
        for entry in sites:
            if entry not in used:
                yield Violation.at("pyproject.toml", None, f"[tool.arch-check.options.CTX-26] site {entry} constructs no stage")


# --- CTX-29


@rule(
    "CTX-29",
    coverage="partial",
    summary="RequestContext carries request_id and caused_by_request_id; a worker mint never reuses the item's id.",
)
def handoff_names_its_cause(project: Project) -> Iterator[Violation]:
    """`RequestContext` in the stage module declares `request_id` and
    `caused_by_request_id`. Under `<pkg>.workers`, no call that
    constructs `RequestContext` passes `request_id=<x>.request_id`: the
    run mints its own id and names the cause in `caused_by_request_id`.
    Log lines carrying both are DEL-39."""
    file = stage_module(project)
    request = stage_classes(project).get(REQUEST_STAGE)
    if file is not None and request is not None:
        missing = [f for f in ("request_id", "caused_by_request_id") if f not in declared_fields(request)]
        if missing:
            yield Violation.at(file.rel, request, f"{REQUEST_STAGE} declares no {' or '.join(missing)}")
    for f, tree in project.trees(project.sub("workers")):
        for call in calls(tree):
            if constructed(call, {REQUEST_STAGE}) is None:
                continue
            for k in call.keywords:
                if k.arg == "request_id" and isinstance(k.value, ast.Attribute) and k.value.attr == "request_id":
                    yield Violation.at(
                        f.rel, k.value, "the worker's request stage reuses a request_id; it mints its own and names the cause"
                    )
