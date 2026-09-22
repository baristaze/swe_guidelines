"""The async rules: infra capabilities, topics, the work queue, workers, and secrets.

Each rule decides the mechanical part of one lens of `lenses/async.md`.
The infra root is the class `InfraInterface`, wherever under
`<pkg>.infra` it lives; a capability is a package `<pkg>.infra.<cap>`
that declares an `*Interface`, the way Infrastructure lays them out. A
capability's names (`CacheScope`, `Buckets`, `Topics`) are read from its
`__init__.py` and what that re-exports, then from any module of the
package. A rule whose
capability or namespace the project does not have reports nothing.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from collections.abc import Collection, Iterator

from arch_check.model import Violation
from arch_check.project import Import, Project, SourceFile, base_names, classes, dotted, is_under, last, methods, parameters
from arch_check.registry import rule
from arch_check.rules._storage_util import (
    call_name,
    column_call,
    config_value,
    has_tablename,
    in_storage,
    index_calls,
    is_true,
    kwarg,
    manager_impl_files,
    module_value,
    names_in,
    own_columns,
    string_args,
    table_args,
    tables,
)

Declaration = tuple[SourceFile, ast.ClassDef | ast.expr]
"""Where a name is declared: its file, and the class or the value a top-level assignment gives it."""


def own_declarations(file: SourceFile, tree: ast.Module) -> dict[str, Declaration]:
    """The top-level classes and assigned names a module defines itself."""
    out: dict[str, Declaration] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out.setdefault(node.name, (file, node))
        elif isinstance(node, ast.Assign) and node.value is not None:
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, (file, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            out.setdefault(node.target.id, (file, node.value))
    return out


def exported(project: Project, module: str, memo: dict[str, dict[str, Declaration]] | None = None) -> dict[str, Declaration]:
    """What a module declares or re-exports: its own names, then each `from x import y` of the project, followed.

    Each module is read once per call, so imports that reach a module by many paths cost one read of it.
    A module met again while it is still being read, through an import cycle, gives nothing.
    """
    memo = {} if memo is None else memo
    if module in memo:
        return memo[module]
    file = project.module(module)
    tree = project.tree(file) if file else None
    if file is None or tree is None:
        return {}
    memo[module] = {}
    out = own_declarations(file, tree)
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        source = project.resolve(file, node.level, node.module) if node.level else node.module or ""
        if project.module(source) is None:
            continue
        found = exported(project, source, memo)
        for a in node.names:
            if a.name == "*":
                for name, decl in found.items():
                    out.setdefault(name, decl)
            elif a.name in found:
                out.setdefault(a.asname or a.name, found[a.name])
    memo[module] = out
    return out


def package_declarations(project: Project, package: str) -> tuple[SourceFile | None, dict[str, Declaration]]:
    """A package's `__init__` file and every name it declares, wherever in the package the definition lives.

    The guideline never says which file of a capability declares its
    enum or its interface. So the package's own names and re-exports
    come first, then every top-level definition of its other modules.
    (None, {}) when the project has no such package.
    """
    file = project.module(package)
    if file is None or project.tree(file) is None:
        return None, {}
    out = exported(project, package)
    for f, tree in project.trees(package):
        if f.module != package:
            for name, decl in own_declarations(f, tree).items():
                out.setdefault(name, decl)
    return file, out


def declared_class(decls: dict[str, Declaration], name: str) -> tuple[SourceFile, ast.ClassDef] | None:
    found = decls.get(name)
    if found is not None and isinstance(found[1], ast.ClassDef):
        return found[0], found[1]
    return None


def declared_interfaces(decls: dict[str, Declaration]) -> list[tuple[SourceFile, ast.ClassDef]]:
    return [(f, n) for f, n in decls.values() if isinstance(n, ast.ClassDef) and n.name.endswith("Interface")]


def class_index(project: Project, *prefixes: str) -> dict[str, list[tuple[SourceFile, ast.ClassDef]]]:
    """Every class under `prefixes`, by name."""
    out: dict[str, list[tuple[SourceFile, ast.ClassDef]]] = {}
    for file, tree in project.trees(*prefixes):
        for cls in classes(tree):
            out.setdefault(cls.name, []).append((file, cls))
    return out


def ancestors(name: str, index: dict[str, list[tuple[SourceFile, ast.ClassDef]]]) -> set[str]:
    """Every base name a class reaches through the classes `index` knows, by last name."""
    seen: set[str] = set()
    todo = [name]
    while todo:
        for _, cls in index.get(todo.pop(), []):
            for b in base_names(cls):
                n = last(b) or ""
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
    return seen


def is_enum(cls: ast.ClassDef) -> bool:
    return any((last(b) or "").endswith("Enum") for b in base_names(cls))


def capabilities(project: Project) -> list[tuple[SourceFile, str, list[tuple[SourceFile, ast.ClassDef]]]]:
    """Each capability package of infra: its `__init__` file, its name, and the `*Interface` classes it declares."""
    infra = project.sub("infra")
    out = []
    for f in project.modules_under(infra):
        parts = f.module[len(infra) + 1 :].split(".") if f.module != infra else []
        if f.is_package and len(parts) == 1 and parts[0] != "impl":
            _, decls = package_declarations(project, f.module)
            interfaces = declared_interfaces(decls)
            if interfaces:
                out.append((f, parts[0], interfaces))
    return out


def module_globs(module: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(module, p) for p in patterns)


# --- ASY-01


def infra_names(project: Project, file: SourceFile) -> set[str]:
    """The local names a module binds to something under `<pkg>.infra`: `from acme.infra.cache import memory`
    binds `memory`, `import acme.infra.cache.memory as m` binds `m`."""
    infra = project.sub("infra")
    out: set[str] = set()
    for imp in project.imports(file):
        if isinstance(imp.node, ast.ImportFrom):
            if is_under(imp.module, infra):
                out.update(a.asname or a.name for a in imp.node.names)
        else:
            out.update(a.asname for a in imp.node.names if a.asname and a.name == imp.module and is_under(a.name, infra))
    return out


def import_time(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """The statements a module runs at import: its top level and the blocks of a top-level `if`, `try`, `with`,
    or loop, never a function or a class body."""
    for node in body:
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for block in ("body", "orelse", "finalbody"):
            inner = getattr(node, block, None)
            if isinstance(inner, list):
                yield from import_time([n for n in inner if isinstance(n, ast.stmt)])
        for handler in getattr(node, "handlers", []) or []:
            if isinstance(handler, ast.ExceptHandler):
                yield from import_time(handler.body)


CLIENTS = [
    "boto3.client",
    "boto3.resource",
    "boto3.Session",
    "boto3.session.Session",
    "aioboto3.Session",
    "aioboto3.session.Session",
    "redis.Redis",
    "redis.Redis.from_url",
    "redis.from_url",
    "redis.asyncio.Redis",
    "redis.asyncio.Redis.from_url",
    "redis.asyncio.from_url",
    "Redis",
    "Redis.from_url",
    "valkey.Valkey",
    "valkey.Valkey.from_url",
    "valkey.from_url",
    "valkey.asyncio.Valkey",
    "valkey.asyncio.Valkey.from_url",
    "Valkey",
    "Valkey.from_url",
]
"""The constructors of cache, bucket, topic, queue, and secret clients. An HTTP client is none of them."""


@rule(
    "ASY-01",
    options=("clients",),
    coverage="partial",
    summary="No module-level infra client or impl, and no manager constructs an infra impl.",
)
def no_ambient_infra(project: Project) -> Iterator[Violation]:
    """Reads `clients` under `[tool.arch-check.options.ASY-01]`: the dotted constructors of infra clients, as called.

    Module level is what runs at import: the top level, the blocks of a top-level `if`, `try`, `with`, or loop,
    and every function default. A module-level `*Impl()` counts only when the name comes from `<pkg>.infra`:
    `CacheMemoryImpl()` imported from `acme.infra.cache.memory` is an infra handle, an `OrdersManagerImpl()` is
    not.
    """
    clients = project.option("ASY-01", "clients", CLIENTS, {"clients"})
    infra = project.sub("infra")

    def infra_impl(file: SourceFile, called: str) -> bool:
        """Whether a call builds an infra `*Impl`: by a name, or through a module, imported from infra."""
        head = called.split(".")[0]
        return (last(called) or "").endswith("Impl") and (head in infra_names(project, file) or is_under(called, infra))

    for file, tree in project.trees():
        bound = bindings(tree)

        def builds(value: ast.AST | None, file: SourceFile = file, bound: dict[str, str] = bound) -> str | None:
            """The constructor a value calls when it builds an infra client or impl, else None."""
            if isinstance(value, ast.Await):
                value = value.value
            if not isinstance(value, ast.Call):
                return None
            called = dotted(value.func) or ""
            # `import boto3 as b3; b3.client(...)` is `boto3.client(...)`, read through the import
            through = resolved(value.func, bound)
            return called if called in clients or through in clients or infra_impl(file, called) else None

        for node in import_time(tree.body):
            called = builds(node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None)
            if called:
                yield Violation.at(file.rel, node, f"a module-level {called}(); an infra handle arrives through a constructor")
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue
            for default in [*fn.args.defaults, *fn.args.kw_defaults]:
                called = builds(default)
                if called and default is not None:
                    yield Violation.at(
                        file.rel,
                        default,
                        f"a default of {called}() is built at import; an infra handle arrives through a constructor",
                    )
    for file in manager_impl_files(project):
        impl = project.tree(file)
        if impl is None:
            continue
        for call in ast.walk(impl):
            if isinstance(call, ast.Call) and infra_impl(file, dotted(call.func) or ""):
                name = last(dotted(call.func))
                yield Violation.at(file.rel, call, f"a manager constructs {name}; the container hands it in")


# --- ASY-02

BOOT = ["*.container", "*.settings", "*.main"]


def impl_classes(project: Project) -> set[str]:
    """The names of the infra classes that implement a capability interface: subclasses of one, not interfaces."""
    index = class_index(project, project.sub("infra"))
    names = {i.name for _, _, interfaces in capabilities(project) for _, i in interfaces}
    return {n for n in index if not n.endswith("Interface") and ancestors(n, index) & names}


@rule(
    "ASY-02",
    options=("boot",),
    coverage="partial",
    summary="InfraInterface has a getter per capability, start and close; only boot modules import an infra impl.",
)
def one_infra_root(project: Project) -> Iterator[Violation]:
    """Reads `boot` under `[tool.arch-check.options.ASY-02]`: module globs allowed to import an infra impl
    (`*.container`, `*.settings`, `*.main`).

    An infra impl is a module under `<pkg>.infra.impl`, or a class that subclasses a capability interface (and
    a module that defines one). `InfraInterface` is found wherever under `<pkg>.infra` it is declared.
    """
    boot = project.option("ASY-02", "boot", BOOT, {"boot"})
    infra = project.sub("infra")
    caps = capabilities(project)
    root = next(iter(class_index(project, infra).get("InfraInterface", [])), None)
    if root is not None:
        file, iface = root
        declared = {m.name: m for m in methods(iface)}
        returned = {r for m in declared.values() for r in names_in(m.returns)}
        for _, cap, interfaces in caps:
            for _, i in interfaces:
                if i.name not in returned:
                    yield Violation.at(file.rel, iface, f"InfraInterface has no getter returning {i.name} ({cap})")
        for name in ("start", "close"):
            if name not in declared:
                yield Violation.at(file.rel, iface, f"InfraInterface declares no {name}()")
    impls = impl_classes(project)
    impl_root = f"{infra}.impl"

    def defines_impl(module: str) -> bool:
        target = project.module(module)
        tree = project.tree(target) if target else None
        return tree is not None and any(isinstance(n, ast.ClassDef) and n.name in impls for n in tree.body)

    def picks_impl(imp: Import) -> str | None:
        if not is_under(imp.module, infra):
            return None
        if is_under(imp.module, impl_root) or (not imp.names and defines_impl(imp.module)):
            return imp.module
        for n in imp.names:
            sub = f"{imp.module}.{n}"
            if project.module(sub) is not None:
                if is_under(sub, impl_root) or defines_impl(sub):
                    return sub
            elif n in impls:
                return imp.module
        return None

    for file in project.python_files:
        if is_under(file.module, infra) or module_globs(file.module, boot):
            continue
        for imp in project.imports(file):
            t = picks_impl(imp)
            if t is not None:
                yield Violation.at(file.rel, imp.node, f"{file.module} imports {t}; only boot modules pick an infra impl")


# --- ASY-03


@rule(
    "ASY-03",
    coverage="partial",
    summary="Every infra impl defines or inherits describe().",
)
def impls_describe_themselves(project: Project) -> Iterator[Violation]:
    infra = project.sub("infra")
    index = class_index(project, infra)
    iface_names = {i.name for _, _, interfaces in capabilities(project) for _, i in interfaces}

    def defines(name: str, seen: set[str]) -> bool:
        for _, cls in index.get(name, []):
            if any(m.name == "describe" for m in methods(cls)):
                return True
            for b in base_names(cls):
                n = last(b) or ""
                if n not in seen and n not in iface_names:
                    seen.add(n)
                    if defines(n, seen):
                        return True
        return False

    for file, tree in project.trees(infra):
        for cls in classes(tree):
            if cls.name.endswith("Interface") or "ABC" in {last(b) for b in base_names(cls)}:
                continue
            if not (ancestors(cls.name, index) & iface_names):
                continue
            if not defines(cls.name, {cls.name}):
                yield Violation.at(file.rel, cls, f"{cls.name} has no describe(); the boot inventory line reads it")


# --- ASY-04


@rule(
    "ASY-04",
    coverage="partial",
    summary="CacheScope is an enum; no get_cache call outside infra passes a free string, and no manager calls it.",
)
def caches_are_scoped(project: Project) -> Iterator[Violation]:
    infra = project.sub("infra")
    file, decls = package_declarations(project, f"{infra}.cache")
    if file is not None and declared_interfaces(decls):
        scope = declared_class(decls, "CacheScope")
        if scope is None:
            yield Violation.at(file.rel, None, f"{infra}.cache declares no CacheScope")
        elif not is_enum(scope[1]):
            yield Violation.at(scope[0].rel, scope[1], "CacheScope is not an Enum; a scope is a fixed member")
    managers = {f.rel for f in manager_impl_files(project)}
    for f, tree in project.trees():
        if is_under(f.module, infra):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and last(dotted(node.func)) == "get_cache"):
                continue
            if f.rel in managers:
                yield Violation.at(f.rel, node, "a manager calls get_cache; it receives its cache already scoped")
                continue
            arg = node.args[0] if node.args else kwarg(node, "scope")
            if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)) or isinstance(arg, ast.JoinedStr):
                yield Violation.at(f.rel, node, "get_cache takes a CacheScope member, never a free string")


# --- ASY-07

CACHE_NAMES = frozenset({"CacheInterface", "CacheScope", "get_cache"})


@rule(
    "ASY-07",
    coverage="partial",
    summary="No storage module, and no storage impl class anywhere, imports, names, or takes a cache.",
)
def storage_never_caches(project: Project) -> Iterator[Violation]:
    cache = f"{project.sub('infra')}.cache"

    def uses(node: ast.AST) -> str | None:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id in CACHE_NAMES:
                return n.id
            if isinstance(n, ast.Attribute) and n.attr in CACHE_NAMES:
                return n.attr
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in CACHE_NAMES:
                return n.value
        return None

    for file, tree in project.trees():
        storage = in_storage(project, file.module)
        if storage:
            for imp in project.imports(file):
                if any(is_under(t, cache) for t in imp.targets()) or CACHE_NAMES & set(imp.names):
                    yield Violation.at(file.rel, imp.node, f"{file.module} imports the cache; caching is a manager decision")
            hit = uses(tree)
            if hit:
                node = next(
                    n
                    for n in ast.walk(tree)
                    if (isinstance(n, ast.Name) and n.id == hit)
                    or (isinstance(n, ast.Attribute) and n.attr == hit)
                    or (isinstance(n, ast.Constant) and n.value == hit)
                )
                yield Violation.at(file.rel, node, f"{file.module} names {hit}; storage talks to its database only")
            continue
        for cls in classes(tree):
            if any((last(b) or "").endswith("StorageInterface") for b in base_names(cls)):
                hit = uses(cls)
                if hit:
                    yield Violation.at(
                        file.rel, cls, f"{cls.name} is a storage impl and names {hit}; caching is a manager decision"
                    )


# --- ASY-08

CLOUD_SDKS = ("boto3", "aioboto3", "botocore", "aiobotocore", "google.cloud", "azure", "minio")
"""The SDKs of a cloud object store: an impl whose module imports one is not the local impl."""
CLOUD_NAMES = ("S3", "Gcs", "GCS", "Azure", "Aws", "AWS", "Cloud", "Minio")


@rule(
    "ASY-08",
    coverage="partial",
    summary="Buckets is an enum, every bucket parameter is typed with it, none is a string literal, and a local impl exists.",
)
def buckets_are_an_enum(project: Project) -> Iterator[Violation]:
    """A local impl is any subclass of the bucket interface that neither imports a cloud SDK nor names a cloud
    store: `BucketsLocalImpl` and `BucketsFilesystemImpl` both count, `BucketsS3Impl` does not."""
    infra = project.sub("infra")
    file, decls = package_declarations(project, f"{infra}.buckets")
    if file is None or not declared_interfaces(decls):
        return  # a package that declares no interface yet is judged, as ASY-04 leaves an empty cache package
    enum = declared_class(decls, "Buckets")
    if enum is None or not is_enum(enum[1]):
        where, bad = enum if enum is not None else (file, None)
        yield Violation.at(where.rel, bad, "Buckets is not an Enum in the buckets package; a bucket is a fixed member")
    interfaces = declared_interfaces(decls)
    for where, i in interfaces:
        for fn in methods(i):
            for p in parameters(fn):
                if p.name == "bucket" and last(p.annotation) != "Buckets":
                    yield Violation.at(where.rel, fn, f"{i.name}.{fn.name} takes bucket as {p.annotation}; it takes Buckets")
    index = class_index(project, infra)
    names = {i.name for _, i in interfaces}

    def local(name: str) -> bool:
        if name.endswith("Interface") or any(c in name for c in CLOUD_NAMES):
            return False
        return any(not any(is_under(imp.module, sdk) for imp in project.imports(f) for sdk in CLOUD_SDKS) for f, _ in index[name])

    if interfaces and not any(ancestors(n, index) & names and local(n) for n in index):
        where, first = interfaces[0]
        yield Violation.at(where.rel, first, f"no local impl subclasses {first.name}; tests run without the cloud")
    for f, tree in project.trees():
        if is_under(f.module, infra):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                value = kwarg(node, "bucket")
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    yield Violation.at(f.rel, value, "a bucket named by a string; name it by a Buckets member")


# --- ASY-09


@rule(
    "ASY-09",
    coverage="partial",
    summary="Topics is an enum mapped to TopicPayload subclasses; the payload base, publish and subscribe have the fixed shapes.",
)
def topics_are_fixed(project: Project) -> Iterator[Violation]:
    infra = project.sub("infra")
    file, decls = package_declarations(project, f"{infra}.topics")
    if file is None or not declared_interfaces(decls):
        return  # a package that declares no interface yet is judged, as ASY-04 leaves an empty cache package
    topics = declared_class(decls, "Topics")
    if topics is None or not is_enum(topics[1]):
        where, bad = topics if topics is not None else (file, None)
        yield Violation.at(where.rel, bad, "Topics is not an Enum; a topic is a fixed member")
    found = declared_class(decls, "TopicPayload")
    if found is None:
        yield Violation.at(file.rel, None, "the topics package declares no TopicPayload")
    else:
        where, base = found
        if [last(b) for b in base_names(base)] != ["BaseModel"]:
            yield Violation.at(where.rel, base, "TopicPayload extends pydantic's BaseModel directly, never the OM root")
        if not is_true(config_value(base, "frozen")):
            yield Violation.at(where.rel, base, "TopicPayload is not frozen=True")
        extra = config_value(base, "extra")
        if not (isinstance(extra, ast.Constant) and extra.value == "ignore"):
            yield Violation.at(
                where.rel, base, 'TopicPayload does not set extra="ignore"; an old consumer must read a new payload'
            )
        fields = own_columns(base)
        for name in ("idempotency_key", "produced_at"):
            if name not in fields:
                yield Violation.at(where.rel, base, f"TopicPayload declares no {name}")
    where, payloads = decls.get("TOPIC_PAYLOADS", (file, None))
    index = class_index(project)
    if not isinstance(payloads, ast.Dict):
        yield Violation.at(where.rel, payloads, "TOPIC_PAYLOADS is not a dict literal in the topics package")
    else:
        for k, v in zip(payloads.keys, payloads.values, strict=True):
            if k is None:
                continue  # a `**spread` of another map carries no key to judge
            if not (isinstance(k, ast.Attribute) and last(dotted(k.value)) == "Topics"):
                yield Violation.at(where.rel, k, "a TOPIC_PAYLOADS key is not a Topics member")
            name = last(dotted(v)) or ""
            if name != "TopicPayload" and "TopicPayload" not in ancestors(name, index):
                yield Violation.at(where.rel, v, f"{name} does not extend TopicPayload")
    iface = next(((f, c) for f, c in declared_interfaces(decls) if {"publish", "subscribe"} & {m.name for m in methods(c)}), None)
    if iface is not None:
        where, cls = iface
        by_method = {m.name: m for m in methods(cls)}
        publish = by_method.get("publish")
        if publish is not None and not (isinstance(publish.returns, ast.Constant) and publish.returns.value is None):
            yield Violation.at(where.rel, publish, "publish is not annotated `-> None`; it returns None")
        subscribe = by_method.get("subscribe")
        if subscribe is not None:
            if "consumer" not in {p.name for p in parameters(subscribe)}:
                yield Violation.at(where.rel, subscribe, "subscribe takes no consumer name")
            if "Callable" not in names_in(subscribe.returns):
                yield Violation.at(where.rel, subscribe, "subscribe returns no unsubscribe callable")
    for f, t in project.trees():
        if is_under(f.module, infra):
            continue
        for node in ast.walk(t):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "publish":
                topic = node.args[0] if node.args else kwarg(node, "topic")
                if isinstance(topic, ast.Constant) and isinstance(topic.value, str):
                    yield Violation.at(f.rel, topic, "a topic published by its string name; publish a Topics member")


# --- ASY-15

SPAWNS = frozenset(
    {
        "asyncio.create_task",
        "asyncio.ensure_future",
        "threading.Thread",
        "threading.Timer",
        "multiprocessing.Process",
        "multiprocessing.context.Process",
        "os.fork",
        "fastapi.BackgroundTasks",
        "fastapi.background.BackgroundTasks",
        "starlette.background.BackgroundTasks",
        "starlette.background.BackgroundTask",
    }
)
"""What starts work in the background, as the defining module spells it. A task group is not here: it
cannot outlive the `async with` that holds it."""
LOOP_CALLS = frozenset({"call_later", "call_at"})
LOOPS = frozenset({"asyncio.get_running_loop", "asyncio.get_event_loop", "asyncio.new_event_loop"})
LOOP_SPAWNS = frozenset({"create_task", "run_in_executor"})
"""What a loop starts in the background: `loop.create_task` outlives the request as `asyncio.create_task` does."""
EXECUTORS = frozenset(
    {
        "concurrent.futures.ThreadPoolExecutor",
        "concurrent.futures.ProcessPoolExecutor",
        "multiprocessing.Pool",
        "multiprocessing.pool.Pool",
        "multiprocessing.pool.ThreadPool",
    }
)
EXECUTOR_SPAWNS = frozenset({"submit", "map", "apply_async", "map_async"})
"""An executor held past a `with` runs what it is given after the request returns; one a `with` holds waits for it."""
SCHEDULERS = ("apscheduler", "schedule", "sched", "rq_scheduler", "aiocron", "crontab")
"""Scheduler libraries. A job queue such as celery or rq is not one."""
EDGE = ["*.realtime", "*.realtime.*"]


def bindings(tree: ast.Module) -> dict[str, str]:
    """Each name an import binds in a module, to the dotted name it stands for."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                else:
                    out.setdefault(a.name.split(".")[0], a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def resolved(node: ast.AST, bound: dict[str, str]) -> str | None:
    """A dotted name with its head replaced by what the import bound it to."""
    name = dotted(node)
    if name is None:
        return None
    head, _, rest = name.partition(".")
    if head not in bound:
        return None
    return f"{bound[head]}.{rest}" if rest else bound[head]


def held(tree: ast.Module, bound: dict[str, str]) -> tuple[set[str], set[str]]:
    """The names a module binds by assignment to an event loop and to an executor, in any scope.

    A name a `with` binds is not here: the `with` waits for the executor's work before it returns.
    """
    loops: set[str] = set()
    executors: set[str] = set()
    for node in ast.walk(tree):
        value: ast.expr | None = None
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            value, targets = node.value, node.targets
        elif isinstance(node, ast.AnnAssign):
            value, targets = node.value, [node.target]
        if not isinstance(value, ast.Call):
            continue
        made = resolved(value.func, bound)
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        names |= {t.attr for t in targets if isinstance(t, ast.Attribute)}  # `self._loop = ...`
        if made in LOOPS:
            loops |= names
        elif made in EXECUTORS:
            executors |= names
    return loops, executors


def unannotated(node: ast.expr, bound: dict[str, str]) -> ast.expr:
    """The type an `Annotated[X, ...]` annotation carries, X; any other annotation as it is."""
    if isinstance(node, ast.Subscript) and last(resolved(node.value, bound) or "") == "Annotated":
        inner = node.slice
        if isinstance(inner, ast.Tuple) and inner.elts:
            return inner.elts[0]
        return inner
    return node


def annotated_types(node: ast.expr, bound: dict[str, str]) -> Iterator[ast.expr]:
    """Every type an annotation may carry: through `Annotated[...]`, a union (`X | None`,
    `Optional[X]`, `Union[X, Y]`), and a string annotation, which is parsed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            parsed = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return
        yield from annotated_types(parsed, bound)
        return
    node = unannotated(node, bound)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        yield from annotated_types(node.left, bound)
        yield from annotated_types(node.right, bound)
        return
    if isinstance(node, ast.Subscript) and last(resolved(node.value, bound) or "") in ("Optional", "Union"):
        inner = node.slice
        for element in inner.elts if isinstance(inner, ast.Tuple) else [inner]:
            yield from annotated_types(element, bound)
        return
    yield node


def receiver(node: ast.expr, bound: dict[str, str], loops: set[str], executors: set[str]) -> str | None:
    """`loop` or `executor` when a method is called on one: made inline, or a name or attribute bound to one."""
    if isinstance(node, ast.Call):
        made = resolved(node.func, bound)
        return "loop" if made in LOOPS else "executor" if made in EXECUTORS else None
    name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
    return "loop" if name in loops else "executor" if name in executors else None


@rule(
    "ASY-15",
    options=("edge",),
    coverage="partial",
    summary="No web service or gateway module outside the socket edge starts a task, a thread, a timer, or a scheduler.",
)
def services_spawn_nothing(project: Project) -> Iterator[Violation]:
    """Every module under `<pkg>.services` or `<pkg>.gateway` is read: the gateway runs in every web service.
    A worker is where background work belongs, so it is not read.

    Reads `edge` under `[tool.arch-check.options.ASY-15]`: module globs of the socket edge, where a
    task that forwards to a held socket may run (`*.realtime`, `*.realtime.*`).

    The guideline names no module for the socket edge, so the default is a guess: a project whose edge
    lives elsewhere names it here.

    A spawn is `asyncio.create_task` and its kin, a thread or a timer, a background-task parameter,
    `create_task` or `run_in_executor` on a loop from `get_running_loop()` or `get_event_loop()`, and
    `submit` or `map` on an executor no `with` holds. A task group's `create_task` is not one.
    """
    edge = project.option("ASY-15", "edge", EDGE, {"edge"})
    # the gateway runs inside every web service, in the service's own package or in its own distribution
    for file in project.modules_under(project.sub("services"), project.sub("gateway")):
        if module_globs(file.module, edge):
            continue
        tree = project.tree(file)
        if tree is None:
            continue
        for imp in project.imports(file):
            if any(is_under(imp.module, s) for s in SCHEDULERS):
                yield Violation.at(file.rel, imp.node, f"a web service imports {imp.module}; recurring work is a worker")
        bound = bindings(tree)
        loops, executors = held(tree, bound)
        awaited = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Await)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = resolved(node.func, bound)
                attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
                on = receiver(node.func.value, bound, loops, executors) if isinstance(node.func, ast.Attribute) else None
                # `await loop.run_in_executor(...)` finishes before the response, as `asyncio.to_thread` does
                spawned = on == "loop" and attr in LOOP_SPAWNS and not (attr == "run_in_executor" and id(node) in awaited)
                if spawned or (on == "executor" and attr in EXECUTOR_SPAWNS):
                    yield Violation.at(
                        file.rel, node, f"a web service calls {on}.{attr}(); work that outlives a request is a worker"
                    )
                elif name in SPAWNS or attr in LOOP_CALLS:
                    what = name or attr
                    yield Violation.at(file.rel, node, f"a web service calls {what}(); work that outlives a request is a worker")
            elif isinstance(node, ast.arg) and node.annotation is not None:
                for kind in annotated_types(node.annotation, bound):
                    name = resolved(kind, bound)
                    if name in SPAWNS:
                        yield Violation.at(
                            file.rel, node, f"a web service takes {name}; work that outlives a request is a worker"
                        )
                        break


# --- ASY-16

WORK_FIELDS = (
    "kind",
    "target_id",
    "idempotency_key",
    "lane",
    "status",
    "available_at",
    "claimed_by",
    "claim_token",
    "lease_expires_at",
    "attempts",
)


@rule(
    "ASY-16",
    options=("namespace", "item"),
    coverage="partial",
    summary="WorkItem has the queue's fields, WORK_PAYLOADS is a dict literal, the work table has a unique idempotency_key.",
)
def work_item_shape(project: Project) -> Iterator[Violation]:
    """Reads `[tool.arch-check.options.ASY-16]`: `namespace`, the work queue's OM namespace (`work`), and
    `item`, the work item's class name (`WorkItem`)."""
    keys = {"namespace", "item"}
    ns = project.option("ASY-16", "namespace", "work", keys)
    item = project.option("ASY-16", "item", "WorkItem", keys)
    root = f"{project.sub('om')}.{ns}"
    if not project.modules_under(root):
        return
    found = False
    payloads = False
    for file, tree in project.trees(root):
        payloads = payloads or isinstance(module_value(tree, "WORK_PAYLOADS"), ast.Dict)
        for cls in classes(tree):
            if cls.name != item or has_tablename(cls):
                continue
            found = True
            fields = own_columns(cls)
            for name in WORK_FIELDS:
                if name not in fields:
                    yield Violation.at(file.rel, cls, f"{item} declares no {name}")
    first = project.modules_under(root)[0]
    if not found:
        yield Violation.at(first.rel, None, f"{root} declares no {item}")
    if not payloads:
        yield Violation.at(first.rel, None, f"{root} declares no WORK_PAYLOADS dict literal")
    for t in tables(project, f"{root}.storage.tables"):
        if "idempotency_key" not in own_columns(t.node):
            continue
        unique = any(string_args(i) == ["idempotency_key"] and is_true(kwarg(i, "unique")) for i in index_calls(t.node))
        constraint = any(
            isinstance(a, ast.Call)
            and call_name(a) == "UniqueConstraint"
            and [x.value for x in a.args if isinstance(x, ast.Constant)] == ["idempotency_key"]
            for a in table_args(t.node)
        )
        decl = own_columns(t.node)["idempotency_key"]
        call = column_call(decl) if isinstance(decl, ast.stmt) else None
        if not (unique or constraint or (call is not None and is_true(kwarg(call, "unique")))):
            yield Violation.at(t.file.rel, t.node, f"{t.node.name} has no unique index on idempotency_key")


# --- ASY-19

TF_SCHEDULE = re.compile(r'resource\s+"(aws_scheduler_schedule|aws_cloudwatch_event_rule)"\s+"[^"]*"\s*\{')


def block_end(text: str, start: int) -> int:
    """The offset just past the `}` that closes an HCL block opened before `start`.

    A brace inside a string (`"fires at } midnight"`) or a comment (`#`,
    `//`, `/* */`) is not counted.
    """
    depth, i, n = 1, start, len(text)
    while i < n and depth:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] not in '"\n':
                i += 2 if text[i] == "\\" else 1
        elif c == "#" or text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 1
        else:
            depth += {"{": 1, "}": -1}.get(c, 0)
        i += 1
    return i


@rule(
    "ASY-19",
    coverage="partial",
    summary="No scheduler library in the source and no scheduled rule or schedule in the Terraform.",
)
def no_scheduler(project: Project) -> Iterator[Violation]:
    for file, _ in project.trees():
        for imp in project.imports(file):
            if any(is_under(imp.module, s) for s in SCHEDULERS):
                yield Violation.at(
                    file.rel, imp.node, f"{file.module} imports {imp.module}; maintenance is a sweep, not a scheduler"
                )
    for rel in project.files("**/*.tf"):
        if ".terraform" in rel.split("/"):
            continue
        text = project.read(rel) or ""
        for m in TF_SCHEDULE.finditer(text):
            body = text[m.end() : block_end(text, m.end())]
            if m.group(1) == "aws_scheduler_schedule" or re.search(r"^\s*schedule_expression\s*=", body, re.MULTILINE):
                line = text.count("\n", 0, m.start()) + 1
                yield Violation(rel, line, 1, f"a scheduled {m.group(1)}; maintenance is a sweep every worker runs")


# --- ASY-28

READS = frozenset({"read_text", "read_bytes"})
PRIVATE = frozenset({"S_IRWXG", "S_IRWXO", "S_IRGRP", "S_IROTH"})
OWNER_ONLY = frozenset({0o600, 0o400})
"""The modes an owner-only secrets file has, compared for equality."""
WRITE_FLAGS = frozenset({"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"})


def is_path(node: ast.expr, paths: Collection[str]) -> bool:
    """Whether an expression is a file path: a `Path(...)`, a name typed `Path`, or a name that says path or file."""
    if isinstance(node, ast.Call):
        return last(dotted(node.func)) in ("Path", "PurePath")
    name = dotted(node) or ""
    return name in paths or any(w in (last(name) or "").lower() for w in ("path", "file"))


def reads_a_file(call: ast.Call, paths: Collection[str] = ()) -> bool:
    """A call that opens a file to read it: `read_text`, `read_bytes`, or an `open` (the builtin, or
    `.open()` on a path, `paths` being the names typed `Path`) in a read mode or a mode that is not a literal."""
    if isinstance(call.func, ast.Attribute) and call.func.attr in READS:
        return True
    name = dotted(call.func) or ""
    path_open = (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "open"
        and name not in ("io.open", "os.open")
        and is_path(call.func.value, paths)
    )
    if name in ("open", "io.open") or path_open:
        # `open(path, mode)` takes the mode second; `path.open(mode)` takes it first
        position = 0 if path_open else 1
        mode = call.args[position] if len(call.args) > position else kwarg(call, "mode")
        if mode is None:
            return True
        if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
            return True  # a mode the checker cannot read may read
        return not set("wax+") & set(mode.value)
    if name == "os.open":
        flags = call.args[1] if len(call.args) > 1 else kwarg(call, "flags")
        return flags is None or not WRITE_FLAGS & {last(dotted(n)) for n in ast.walk(flags)}
    return False


def checks_mode(nodes: list[ast.AST]) -> bool:
    """Whether nodes stat a file and test its mode against a group-or-other mask or an owner-only mode."""
    stats = any(isinstance(n, ast.Call) and (last(dotted(n.func)) or "") in ("stat", "lstat", "fstat") for n in nodes)

    def number(n: ast.AST, values: frozenset[int]) -> bool:
        return isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool) and n.value in values

    mask = any(
        number(n, frozenset({0o077}))
        or (isinstance(n, ast.Name | ast.Attribute) and (last(dotted(n)) or "") in PRIVATE)
        or (isinstance(n, ast.Compare) and any(number(c, OWNER_ONLY) for c in [n.left, *n.comparators]))
        for n in nodes
    )
    return stats and mask


CONTENT_READS = frozenset({"read", "readline", "readlines", "read_text", "read_bytes"})
"""A call that reads what a file holds: on a handle, or on a path."""
NOT_READS = frozenset({"stat", "lstat", "fstat", "fileno", "close", "closing"})
"""A call that takes a handle and reads none of its content."""


def is_open(call: ast.Call, paths: Collection[str]) -> bool:
    """A call that opens a file for reading and returns a handle, not its content."""
    return reads_a_file(call, paths) and not (isinstance(call.func, ast.Attribute) and call.func.attr in READS)


def handle_names(body: list[ast.AST], call: ast.Call) -> set[str]:
    """The names an open's handle is bound to: `with open(p) as h`, `h = open(p)`, `fd = os.open(p, ...)`."""
    out: set[str] = set()
    for n in body:
        if isinstance(n, ast.withitem) and n.context_expr is call and isinstance(n.optional_vars, ast.Name):
            out.add(n.optional_vars.id)
        elif isinstance(n, ast.Assign) and n.value is call:
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return out


def first_read(body: list[ast.AST], paths: Collection[str]) -> int | None:
    """The line of the first read of a file's content in a function, or None when it reads no file.

    `read_text` and `read_bytes` read at once. An open reads when its handle
    is read: `.read()`, `os.read(fd, n)`, the handle passed to a call
    (`json.load(h)`), or iterated. A handle the function never reads, or
    one it hands on unnamed, is read at the open, since the checker cannot
    follow it. A `stat` of the open handle is not a read, so the race-free
    check (`os.fstat(h.fileno())` before `h.read()`) comes before it.
    """
    lines: list[int] = []
    for call in (n for n in body if isinstance(n, ast.Call)):
        if isinstance(call.func, ast.Attribute) and call.func.attr in READS:
            lines.append(call.lineno)
            continue
        if not is_open(call, paths):
            continue
        names = handle_names(body, call)
        uses: list[int] = []
        for n in body:
            if isinstance(n, ast.Call) and n is not call:
                func = n.func
                on_handle = isinstance(func, ast.Attribute) and (
                    func.value is call or (isinstance(func.value, ast.Name) and func.value.id in names)
                )
                reads_content = on_handle and isinstance(func, ast.Attribute) and func.attr in CONTENT_READS
                passed = (last(dotted(func)) or "") not in NOT_READS and any(
                    isinstance(a, ast.Name) and a.id in names for a in [*n.args, *(k.value for k in n.keywords)]
                )
                if reads_content or passed:
                    uses.append(n.lineno)
            elif isinstance(n, ast.For | ast.AsyncFor | ast.comprehension):
                if isinstance(n.iter, ast.Name) and n.iter.id in names:
                    uses.append(n.iter.lineno)
        lines.append(min(uses) if uses else call.lineno)
    return min(lines) if lines else None


@rule(
    "ASY-28",
    coverage="partial",
    summary="Every file the secrets package reads is checked owner-only first, in the same function or a helper it calls.",
)
def secrets_files_are_private(project: Project) -> Iterator[Violation]:
    """The check is a `stat` and a mode test (`mode & 0o077`, `S_IRWXG`, or `== 0o600`) before the first read
    of the file's content, in the reading function itself or in a function of the same module it calls first.
    Opening the file is not yet the read: an `fstat` of the open handle before reading it is the check."""
    for file, tree in project.trees(f"{project.sub('infra')}.secrets"):
        fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        helpers = {fn.name for fn in fns if checks_mode([n for s in fn.body for n in ast.walk(s)])}
        for fn in fns:
            body = [n for s in fn.body for n in ast.walk(s)]
            paths = {a.arg for a in [*fn.args.args, *fn.args.kwonlyargs] if last(dotted(a.annotation)) == "Path"}
            first = first_read(body, paths)
            if first is None:
                continue
            reads = [n for n in body if isinstance(n, ast.Call) and reads_a_file(n, paths)]
            before = [n for n in body if getattr(n, "lineno", first + 1) <= first]
            helped = any(isinstance(n, ast.Call) and last(dotted(n.func)) in helpers and n.lineno <= first for n in body)
            if not (checks_mode(before) or helped):
                yield Violation.at(file.rel, reads[0], f"{fn.name} reads a file with no owner-only mode check before it")


# --- ASY-29


@rule(
    "ASY-29",
    options=("classes",),
    coverage="partial",
    summary="WorkItem and OutboxRow declare request_id and traceparent, and neither declares trace_id.",
)
def work_carries_its_request(project: Project) -> Iterator[Violation]:
    """Reads `classes` under `[tool.arch-check.options.ASY-29]`: the class names that carry the request
    (`WorkItem`, `OutboxRow`). A class of that name is read wherever the OM declares it, not only under a
    `types` package."""
    names = project.option("ASY-29", "classes", ["WorkItem", "OutboxRow"], {"classes"})
    for file, tree in project.trees(project.sub("om")):
        for cls in classes(tree):
            if cls.name not in names:
                continue
            fields = own_columns(cls)
            for name in ("request_id", "traceparent"):
                if name not in fields:
                    yield Violation.at(file.rel, cls, f"{cls.name} declares no {name}")
            if "trace_id" in fields:
                yield Violation.at(file.rel, fields["trace_id"], f"{cls.name} carries trace_id; it carries the traceparent")
