"""The contracts group: interfaces, impls, injection, roots, and the router shape.

Each rule reads class names the guideline fixes: an interface ends in
`Interface`, an impl in `Impl`, a manager interface in
`ManagerInterface`, a storage one in `StorageInterface`, a service one
in `ServiceInterface`. The roots are the classes that implement
`StorageInterface`, `InfraInterface`, or `ServicesInterface`, and the
business root is `build_managers` in `<pkg>.om.root`. The import
direction (CON-10, CON-12) lives in `imports.py`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from arch_check.model import Violation
from arch_check.project import Function, Project, SourceFile, base_names, classes, decorator_names, dotted, is_under, last
from arch_check.registry import rule
from arch_check.rules._contracts_util import (
    HTTP_VERBS,
    arguments,
    body_without_docstring,
    called_name,
    calls,
    classes_named,
    declared_fields,
    head_name,
    in_manager_impl,
    init_of,
    interface_methods,
    is_ellipsis_body,
    names_in,
    namespace_of,
    service_part,
    stage_classes,
    stage_module,
    union_members,
)

ROOT_INTERFACES = ["StorageInterface", "InfraInterface", "ServicesInterface"]
"""The roots of storage, infra, and services, by the names the guideline gives them."""

EMPTY_LITERALS = (ast.List, ast.Dict, ast.Tuple, ast.Set)


def bases(cls: ast.ClassDef) -> list[str]:
    """The last segment of every base name: `abc.ABC` gives `ABC`."""
    return [last(b) or b for b in base_names(cls)]


def roots(project: Project, rule_id: str) -> list[str]:
    return project.option(rule_id, "roots", list(ROOT_INTERFACES), {"roots"})


def is_root(cls: ast.ClassDef, names: list[str]) -> bool:
    return cls.name in names or any(b in names for b in bases(cls))


# --- CON-01


@rule(
    "CON-01",
    coverage="partial",
    summary="Every *Impl subclasses an interface; manager and storage interface operations are async.",
)
def every_layer_has_an_interface(project: Project) -> Iterator[Violation]:
    """Every class named `*Impl` has a base named `*Interface` or `*Impl`.

    Every public method of a class named `*ManagerInterface` or
    `*StorageInterface` is `async`, except on the storage root
    `StorageInterface`, whose getters are plain. Option
    `[tool.arch-check.options.CON-01]`: `sync_methods`, a list of
    `Class.method` that may stay synchronous (default none).
    """
    allowed: set[str] = set(project.option("CON-01", "sync_methods", [], {"sync_methods"}))
    for file, cls in classes_named(project, "Impl"):
        if not any(b.endswith(("Interface", "Impl")) for b in bases(cls)):
            yield Violation.at(file.rel, cls, f"{cls.name} subclasses no *Interface; an impl implements an interface")
    for suffix in ("ManagerInterface", "StorageInterface"):
        for file, cls in classes_named(project, suffix):
            if cls.name == "StorageInterface":
                continue
            for fn in interface_methods(cls):
                if isinstance(fn, ast.FunctionDef) and f"{cls.name}.{fn.name}" not in allowed:
                    yield Violation.at(file.rel, fn, f"{cls.name}.{fn.name} is synchronous; an operation is an async method")


# --- CON-02


@rule(
    "CON-02",
    coverage="partial",
    summary="Every *Interface is an ABC whose public methods are @abstractmethod with empty bodies.",
)
def interfaces_are_abstract(project: Project) -> Iterator[Violation]:
    """Every class named `*Interface` has `ABC`, `metaclass=ABCMeta`, or another
    `*Interface` among its bases, and each public method it declares is
    `@abstractmethod` with a body of an optional docstring and `...`.
    Whether an impl adds public methods its callers use is judged."""
    for file, cls in classes_named(project, "Interface"):
        meta = next((last(dotted(k.value)) for k in cls.keywords if k.arg == "metaclass"), None)
        if not any(b == "ABC" or b.endswith("Interface") for b in bases(cls)) and meta != "ABCMeta":
            yield Violation.at(file.rel, cls, f"{cls.name} is not an ABC; an interface is an abstract class")
        for fn in interface_methods(cls):
            if "abstractmethod" not in {last(d) for d in decorator_names(fn)}:
                yield Violation.at(file.rel, fn, f"{cls.name}.{fn.name} is not @abstractmethod")
            elif not is_ellipsis_body(fn):
                yield Violation.at(file.rel, fn, f"{cls.name}.{fn.name} has a body; an interface method is `...`")


# --- CON-03


def implementers(project: Project) -> dict[str, list[str]]:
    """Interface name to the names of the classes that subclass it, directly or through another impl."""
    direct: dict[str, list[str]] = {}
    for _, tree in project.trees():
        for cls in classes(tree):
            for b in bases(cls):
                direct.setdefault(b, []).append(cls.name)
    out: dict[str, list[str]] = {}
    for name in direct:
        seen: list[str] = []
        todo = list(direct[name])
        while todo:
            sub = todo.pop()
            if sub not in seen:
                seen.append(sub)
                todo.extend(direct.get(sub, []))
        out[name] = [s for s in seen if not s.endswith("Interface")]
    return out


@rule(
    "CON-03",
    coverage="partial",
    summary="An impl's name ends in Impl; every storage and infra interface has at least two impls.",
)
def impls_are_named_and_paired(project: Project) -> Iterator[Violation]:
    """A class that subclasses a `*Interface` and is not itself an
    interface is named `...Impl`. Every `*StorageInterface` under the OM,
    and every `*Interface` an infra capability package or the infra root
    declares, has at least two impls across the tree. Whether a name puts
    the technology last, or a technology name leaks into a signature, is
    judged."""
    for file, cls in classes_named(project, ""):
        if cls.name.endswith("Interface"):
            continue
        if any(b.endswith("Interface") for b in bases(cls)) and not cls.name.endswith("Impl"):
            yield Violation.at(file.rel, cls, f"{cls.name} implements an interface; its name ends in Impl")
    impls = implementers(project)
    infra = project.sub("infra")
    for file, cls in classes_named(project, "Interface"):
        storage = cls.name.endswith("StorageInterface") and is_under(file.module, project.sub("om"))
        capability = is_under(file.module, infra) and (
            (file.is_package and file.module.count(".") == infra.count(".") + 1) or file.module == f"{infra}.root"
        )
        if (storage or capability) and len(impls.get(cls.name, [])) < 2:
            found = ", ".join(sorted(impls.get(cls.name, []))) or "none"
            yield Violation.at(
                file.rel, cls, f"{cls.name} has fewer than two impls ({found}); it must run without its technology"
            )


# --- CON-04


def is_memory_module(project: Project, file: SourceFile) -> bool:
    name = file.module.rpartition(".")[2]
    if is_under(file.module, project.sub("om")):
        return ".storage.impl." in f".{file.module}." and name.startswith("memory")
    if is_under(file.module, project.sub("infra")):
        return name.startswith(("memory", "local"))
    return False


def raises_not_implemented(node: ast.Raise) -> bool:
    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return last(dotted(exc)) == "NotImplementedError"


def returns_empty(fn: Function) -> bool:
    rest = body_without_docstring(fn.body)
    if len(rest) != 1 or not isinstance(rest[0], ast.Return):
        return False
    value = rest[0].value
    if isinstance(value, EMPTY_LITERALS):
        return not getattr(value, "elts", None) and not getattr(value, "keys", None)
    return isinstance(value, ast.Call) and called_name(value) in {"list", "dict", "tuple", "set"} and not value.args


@rule(
    "CON-04",
    coverage="partial",
    summary="No memory impl raises NotImplementedError or answers a public method with an empty literal.",
)
def memory_impls_are_whole(project: Project) -> Iterator[Violation]:
    """In a memory impl (`<pkg>.om...storage.impl.memory*`, and
    `<pkg>.infra...memory*` or `local*`), nothing raises
    `NotImplementedError`, and no public method's whole body is `return`
    of an empty list, dict, tuple, or set. Parity with the relational impl
    and a contract case per unique key are judged or run as tests."""
    for file, tree in project.trees(project.sub("om"), project.sub("infra")):
        if not is_memory_module(project, file):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and raises_not_implemented(node):
                yield Violation.at(file.rel, node, "a memory impl raises NotImplementedError; it is a full implementation")
        for cls in classes(tree):
            for fn in interface_methods(cls):
                if returns_empty(fn):
                    yield Violation.at(
                        file.rel, fn, f"{cls.name}.{fn.name} only returns an empty value; a memory impl is not a stub"
                    )


# --- CON-06


@rule(
    "CON-06",
    coverage="partial",
    summary="Impl constructors are typed by interface; impls build no impl; interfaces take no manager or storage.",
)
def dependencies_are_injected(project: Project) -> Iterator[Violation]:
    """Every parameter of an `*Impl` constructor is annotated, and never
    with `Any` or a name ending in `Impl`. No `*Impl` other than a root
    constructs an `*Impl`. No `*Interface` method takes a parameter
    annotated with a `*ManagerInterface` or a `*StorageInterface`.
    Option `[tool.arch-check.options.CON-06]`: `roots`, the interfaces
    whose impls wire others (default `StorageInterface`,
    `InfraInterface`, `ServicesInterface`). A locator reached at call
    time is judged."""
    root_names = roots(project, "CON-06")
    for file, cls in classes_named(project, "Impl"):
        init = init_of(cls)
        if init is not None:
            for arg in arguments(init):
                if arg.annotation is None:
                    yield Violation.at(file.rel, arg, f"{cls.name}.__init__ takes `{arg.arg}` untyped; type it by interface")
                    continue
                spelled = names_in(arg.annotation)
                bad = sorted({n for n in spelled if n.endswith("Impl")} | {"Any"} & set(union_members(arg.annotation)))
                if bad:
                    yield Violation.at(
                        file.rel, arg, f"{cls.name}.__init__ types `{arg.arg}` as {bad[0]}; a dependency is typed by interface"
                    )
        if is_root(cls, root_names):
            continue
        for call in calls(cls):
            name = called_name(call)
            if name and name.endswith("Impl"):
                yield Violation.at(file.rel, call, f"{cls.name} constructs {name}; a root wires impls, an impl receives them")
    for file, cls in classes_named(project, "Interface"):
        for fn in interface_methods(cls):
            for arg in arguments(fn):
                bad = sorted(n for n in names_in(arg.annotation) if n.endswith(("ManagerInterface", "StorageInterface")))
                if bad:
                    yield Violation.at(
                        file.rel, arg, f"{cls.name}.{fn.name} takes a {bad[0]}; a peer is the impl's constructor dependency"
                    )


# --- CON-07

LOOSE_TUNABLES = frozenset({"int", "float", "timedelta", "Decimal"})


@rule(
    "CON-07",
    coverage="partial",
    summary="No *ManagerImpl constructor takes a loose number or duration.",
)
def tunables_arrive_as_options(project: Project) -> Iterator[Violation]:
    """No parameter of a `*ManagerImpl` constructor is annotated `int`,
    `float`, `timedelta`, or `Decimal`: tunables arrive as one options
    object. Whether a module constant differs between deployments, and
    whether the options object is frozen, is judged."""
    for file, cls in classes_named(project, "ManagerImpl"):
        init = init_of(cls)
        for arg in arguments(init) if init else []:
            loose = sorted(names_in(arg.annotation) & LOOSE_TUNABLES)
            if loose:
                yield Violation.at(
                    file.rel,
                    arg,
                    f"{cls.name}.__init__ takes `{arg.arg}: {loose[0]}`; tunables arrive as a frozen options object",
                )


# --- CON-08


def private_targets(node: ast.AST) -> Iterator[ast.Attribute]:
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AugAssign | ast.AnnAssign):
        targets = [node.target]
    while targets:
        t = targets.pop()
        if isinstance(t, ast.Tuple | ast.List):
            targets.extend(t.elts)
        elif (
            isinstance(t, ast.Attribute)
            and t.attr.startswith("_")
            and not t.attr.startswith("__")
            and not (isinstance(t.value, ast.Name) and t.value.id in {"self", "cls"})
        ):
            yield t


@rule(
    "CON-08",
    coverage="partial",
    summary="Nothing assigns another object's private attribute; no two namespaces import each other's impls.",
)
def cycles_are_broken_above(project: Project) -> Iterator[Violation]:
    """No assignment writes an underscore attribute of anything but `self`
    or `cls`. No two OM namespaces import each other's `impl` packages.
    A dependency made optional to dodge a cycle is judged."""
    for file, tree in project.trees():
        for node in ast.walk(tree):
            for t in private_targets(node):
                owner = dotted(t.value) or "another object"
                yield Violation.at(file.rel, t, f"assigns {owner}.{t.attr}; wiring is the constructor, not a private attribute")
    om = project.sub("om")
    edges: dict[tuple[str, str], tuple[SourceFile, ast.AST]] = {}
    for file in project.modules_under(om):
        if not in_manager_impl(project, file.module):
            continue
        ns = namespace_of(project, file.module)
        for imp in project.imports(file):
            for target in imp.targets():
                other = namespace_of(project, target)
                if other and other != ns and in_manager_impl(project, target):
                    edges.setdefault((ns or "", other), (file, imp.node))
    for (a, b), (file, node) in sorted(edges.items(), key=lambda kv: kv[0]):
        if (b, a) in edges:
            yield Violation.at(file.rel, node, f"the {a} impl imports the {b} impl and the {b} impl imports it back")


# --- CON-09

MUTABLE_RETURNS = frozenset({"dict", "Dict", "Mapping", "MutableMapping", "list", "List", "Any", "None", "object"})


def dataclass_frozen(cls: ast.ClassDef) -> bool | None:
    """True or False for a `@dataclass`, by its `frozen` keyword; None when it is no dataclass."""
    for d in cls.decorator_list:
        if last(dotted(d)) != "dataclass":
            continue
        if isinstance(d, ast.Call):
            for k in d.keywords:
                if k.arg == "frozen":
                    return isinstance(k.value, ast.Constant) and k.value.value is True
        return False
    return None


@rule(
    "CON-09",
    coverage="partial",
    summary="build_managers returns one frozen object of interface fields; root getters return interfaces.",
)
def roots_wire_at_boot(project: Project) -> Iterator[Violation]:
    """`build_managers` in `<pkg>.om.root` returns a class, not a dict or a
    list; a dataclass there is `frozen=True`, and no field of it is typed
    with an `*Impl`. Every `get_*` of a root (a class that is or
    implements a name in `roots`) returns no `*Impl`. Option
    `[tool.arch-check.options.CON-09]`: `roots` (default
    `StorageInterface`, `InfraInterface`, `ServicesInterface`). Where
    impls are built is CON-06."""
    root_names = roots(project, "CON-09")
    file = project.module(f"{project.sub('om')}.root")
    tree = project.tree(file) if file else None
    if file is not None and tree is not None:
        local = {c.name: c for c in classes(tree)}
        for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            if fn.name != "build_managers":
                continue
            name = head_name(fn.returns)
            if name is None or name in MUTABLE_RETURNS:
                yield Violation.at(
                    file.rel, fn, "build_managers returns no frozen object; it returns one with a field per manager"
                )
                continue
            cls = local.get(name)
            if cls is None:
                continue
            if dataclass_frozen(cls) is False or (dataclass_frozen(cls) is None and not cls.bases):
                yield Violation.at(file.rel, cls, f"{cls.name} is mutable; the business root returns a frozen object")
            for node in cls.body:
                if isinstance(node, ast.AnnAssign) and any(n.endswith("Impl") for n in names_in(node.annotation)):
                    yield Violation.at(file.rel, node, f"{cls.name} holds an impl type; a manager field is its interface")
    for file, tree in project.trees():
        for cls in classes(tree):
            if not is_root(cls, root_names):
                continue
            for fn in interface_methods(cls):
                if fn.name.startswith("get_") and any(n.endswith("Impl") for n in names_in(fn.returns)):
                    yield Violation.at(
                        file.rel, fn, f"{cls.name}.{fn.name} returns an impl type; a root getter returns an interface"
                    )


# --- CON-11

VENDORS = [
    "sqlalchemy",
    "asyncpg",
    "psycopg",
    "psycopg2",
    "redis",
    "valkey",
    "boto3",
    "botocore",
    "aiobotocore",
    "httpx",
    "sentry_sdk",
]


def declares_interface(tree: ast.Module) -> bool:
    return any(isinstance(n, ast.ClassDef) and n.name.endswith("Interface") for n in tree.body)


@rule(
    "CON-11",
    coverage="partial",
    summary="No interface module and no manager impl imports a vendor library.",
)
def no_technology_leaks(project: Project) -> Iterator[Violation]:
    """A module that declares an `*Interface`, and every module under
    `<pkg>.om.<ns>.impl`, imports nothing from a vendor library. Option
    `[tool.arch-check.options.CON-11]`: `vendors`, the top-level
    packages that count (default sqlalchemy, asyncpg, psycopg, psycopg2,
    redis, valkey, boto3, botocore, aiobotocore, httpx, sentry_sdk). A
    caller branching on the configured backend is judged."""
    vendors = set(project.option("CON-11", "vendors", list(VENDORS), {"vendors"}))
    for file, tree in project.trees():
        if not (declares_interface(tree) or in_manager_impl(project, file.module)):
            continue
        for imp in project.imports(file):
            top = imp.module.partition(".")[0]
            if top in vendors:
                yield Violation.at(file.rel, imp.node, f"{file.module} imports {imp.module}; a technology stays behind its impl")


# --- CON-14


@rule(
    "CON-14",
    coverage="partial",
    summary="Every *ServiceInterface has an impl in its own process.",
)
def every_service_has_its_impl(project: Project) -> Iterator[Violation]:
    """Every class named `*ServiceInterface` under `<pkg>.services.<process>`
    has a subclass in the same process, the in-process impl every router
    calls through. What a service impl decides is judged."""
    services = project.sub("services")
    by_process: dict[str, set[str]] = {}
    for file, tree in project.trees(services):
        process = ".".join(file.module.split(".")[: services.count(".") + 2])
        for cls in classes(tree):
            by_process.setdefault(process, set()).update(bases(cls))
    for file, cls in classes_named(project, "ServiceInterface", services):
        process = ".".join(file.module.split(".")[: services.count(".") + 2])
        if cls.name not in by_process.get(process, set()):
            yield Violation.at(file.rel, cls, f"{cls.name} has no in-process impl in {process}")


# --- CON-15


def route_verb(fn: Function) -> str | None:
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Attribute) and target.attr in HTTP_VERBS:
            return target.attr
    return None


def one_awaited_call(fn: Function) -> bool:
    rest = body_without_docstring(fn.body)
    if len(rest) != 1 or not isinstance(rest[0], ast.Return):
        return False
    value = rest[0].value
    return isinstance(value, ast.Await) and isinstance(value.value, ast.Call) and isinstance(value.value.func, ast.Attribute)


@rule(
    "CON-15",
    coverage="partial",
    summary="A route function is one `return await <service>.<operation>(...)` and takes no manager or storage.",
)
def routers_bind_only(project: Project) -> Iterator[Violation]:
    """Every function under `<pkg>.services.<process>.routers` decorated
    with an HTTP verb (`@router.get(...)`) has a body of an optional
    docstring and one `return await <name>.<operation>(...)`, and no
    parameter typed with a `*ManagerInterface` or `*StorageInterface`.
    Whether the impl behind it translates correctly is judged."""
    for file, tree in project.trees(project.sub("services")):
        if service_part(project, file.module) != "routers":
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or route_verb(node) is None:
                continue
            if not one_awaited_call(node):
                yield Violation.at(file.rel, node, f"route {node.name} does more than `return await` one service call")
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                bad = sorted(n for n in names_in(arg.annotation) if n.endswith(("ManagerInterface", "StorageInterface")))
                if bad:
                    yield Violation.at(file.rel, arg, f"route {node.name} takes a {bad[0]}; a router calls its service impl")


# --- CON-16


def process_packages(project: Project) -> dict[str, list[SourceFile]]:
    """Each process package, `<pkg>.services.<name>` and `<pkg>.workers.<name>`, with its files."""
    out: dict[str, list[SourceFile]] = {}
    for layer in ("services", "workers"):
        prefix = project.sub(layer)
        depth = prefix.count(".") + 2
        for file in project.modules_under(prefix):
            parts = file.module.split(".")
            if len(parts) >= depth:
                out.setdefault(".".join(parts[:depth]), []).append(file)
    return out


def has_lifecycle(cls: ast.ClassDef) -> bool:
    names = {m.name for m in cls.body if isinstance(m, ast.AsyncFunctionDef)}
    return {"start", "close"} <= names


@rule(
    "CON-16",
    coverage="partial",
    summary="Every service and worker process with a main module has a container class with async start and close.",
)
def every_process_has_a_container(project: Project) -> Iterator[Violation]:
    """Each process package, `<pkg>.services.<name>` and
    `<pkg>.workers.<name>`, that has a `main` module (its entry point)
    has a `container` module that defines a class with `async def start`
    and `async def close`. The boot order, the reverse teardown, and the
    test assembly are judged."""
    for process, files in sorted(process_packages(project).items()):
        where = next((f for f in files if f.module == f"{process}.main"), None)
        if where is None:
            continue
        container = project.module(f"{process}.container")
        tree = project.tree(container) if container else None
        if container is None:
            yield Violation.at(where.rel, None, f"{process} has no container module; every process boots through one")
        elif tree is not None and not any(has_lifecycle(c) for c in classes(tree)):
            yield Violation.at(container.rel, None, f"{container.module} has no class with async start() and close()")


# --- CON-18

REQUEST_STATE = frozenset({"request_id", "actor_id", "user_id", "org_id", "tenant_id", "ctx", "context"})


@rule(
    "CON-18",
    coverage="partial",
    summary="No stage or scope member is a manager, storage, or container; no impl constructor takes request state.",
)
def constructors_take_structure(project: Project) -> Iterator[Violation]:
    """No field or property of a class in the stage module
    (`<pkg>.om.opcontext`) is typed with an `*Interface`, an `*Impl`, or
    a name holding `Manager`, `Storage`, or `Container`. No `*Impl`
    constructor has a parameter named `request_id`, `actor_id`,
    `user_id`, `org_id`, `tenant_id`, `ctx`, or `context`. A locator
    reached from an operation is judged."""
    file = stage_module(project)
    for cls in stage_classes(project).values():
        for name, node in declared_fields(cls).items():
            annotation = node.annotation if isinstance(node, ast.AnnAssign) else getattr(node, "returns", None)
            bad = sorted(
                n
                for n in names_in(annotation)
                if n.endswith(("Interface", "Impl")) or any(w in n for w in ("Manager", "Storage", "Container"))
            )
            if bad and file is not None:
                yield Violation.at(file.rel, node, f"{cls.name}.{name} is a {bad[0]}; a context carries state, not services")
    for f, cls in classes_named(project, "Impl"):
        init = init_of(cls)
        for arg in arguments(init) if init else []:
            if arg.arg in REQUEST_STATE:
                yield Violation.at(f.rel, arg, f"{cls.name}.__init__ takes `{arg.arg}`; operation state travels on the context")


# --- CON-20

CACHING_DECORATORS = frozenset({"property", "cached_property", "cache", "lru_cache"})


def returns_held(fn: Function) -> bool:
    """Whether a getter's body is `return self._x` or `return self._x[<key>]`, an optional docstring before it."""
    rest = body_without_docstring(fn.body)
    if len(rest) != 1 or not isinstance(rest[0], ast.Return):
        return False
    value = rest[0].value
    if isinstance(value, ast.Subscript):
        value = value.value
    return isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "self"


@rule(
    "CON-20",
    coverage="partial",
    summary="A root's getters return what the constructor built: `return self._x`, no lazy build, no caching decorator.",
)
def roots_build_once(project: Project) -> Iterator[Violation]:
    """In every class that implements a name in `roots`, each `get_*`
    method carries no `property`, `cached_property`, `cache`, or
    `lru_cache` decorator, and its body is `return self._x` (or
    `self._x[key]`). Option `[tool.arch-check.options.CON-20]`: `roots`
    (default `StorageInterface`, `InfraInterface`, `ServicesInterface`).
    The build-once test is the project's."""
    root_names = roots(project, "CON-20")
    for file, tree in project.trees():
        for cls in classes(tree):
            if cls.name in root_names or not any(b in root_names for b in bases(cls)):
                continue
            for fn in interface_methods(cls):
                if not fn.name.startswith("get_"):
                    continue
                if CACHING_DECORATORS & {last(d) for d in decorator_names(fn)}:
                    yield Violation.at(
                        file.rel, fn, f"{cls.name}.{fn.name} caches on first use; a root builds every member at boot"
                    )
                elif not returns_held(fn):
                    yield Violation.at(file.rel, fn, f"{cls.name}.{fn.name} does more than return a member the constructor built")


# --- CON-23


@rule(
    "CON-23",
    coverage="partial",
    summary="The OM holds no breaker; a breaker's bounds are never numeric literals.",
)
def breakers_are_infrastructure(project: Project) -> Iterator[Violation]:
    """No module under `<pkg>.om` imports a module named `*breaker*` or
    calls a name holding `Breaker`. Every call that constructs a
    `*Breaker*` passes no numeric literal, so its failure bound and
    cool-down come from settings. What it answers while open is judged."""
    om = project.sub("om")
    for file, tree in project.trees():
        in_om = is_under(file.module, om)
        if in_om:
            for imp in project.imports(file):
                if any("breaker" in part.lower() for t in imp.targets() for part in t.split(".")):
                    yield Violation.at(file.rel, imp.node, f"{file.module} imports a breaker; a breaker is infrastructure")
        for call in calls(tree):
            name = called_name(call)
            if not name or "Breaker" not in name:
                continue
            if in_om:
                yield Violation.at(file.rel, call, f"{file.module} builds {name}; a manager never holds a breaker")
                continue
            values = [*call.args, *(k.value for k in call.keywords)]
            if any(
                isinstance(v, ast.Constant) and isinstance(v.value, int | float) and not isinstance(v.value, bool) for v in values
            ):
                yield Violation.at(file.rel, call, f"{name} gets a literal bound; its threshold and cool-down come from settings")
