"""The async rules: infra capabilities, topics, the work queue, workers, and secrets.

Each rule decides the mechanical part of one lens of `lenses/async.md`.
The infra root is `InfraInterface` in `<pkg>.infra.root`; a capability
is a package `<pkg>.infra.<cap>` whose `__init__.py` declares its
`*Interface`, the way Infrastructure lays them out. A rule whose
capability or namespace the project does not have reports nothing.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from collections.abc import Iterator

from arch_check.model import Violation
from arch_check.project import Project, SourceFile, base_names, classes, dotted, is_under, last, methods, parameters
from arch_check.registry import rule
from arch_check.rules._storage_util import (
    class_value,
    column_call,
    in_storage,
    index_calls,
    is_true,
    kwarg,
    manager_impl_files,
    module_value,
    names_in,
    own_columns,
    string_args,
    tables,
)


def package_classes(project: Project, module: str) -> tuple[SourceFile | None, list[ast.ClassDef]]:
    """A module's file and its top-level classes; (None, []) when the project has no such module."""
    file = project.module(module)
    tree = project.tree(file) if file else None
    if file is None or tree is None:
        return None, []
    return file, [n for n in tree.body if isinstance(n, ast.ClassDef)]


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


def capabilities(project: Project) -> list[tuple[SourceFile, str, list[ast.ClassDef]]]:
    """Each capability package of infra: its file, its name, and the `*Interface` classes it declares."""
    infra = project.sub("infra")
    out = []
    for f in project.modules_under(infra):
        parts = f.module[len(infra) + 1 :].split(".") if f.module != infra else []
        if f.is_package and len(parts) == 1 and parts[0] != "impl":
            _, found = package_classes(project, f.module)
            interfaces = [c for c in found if c.name.endswith("Interface")]
            if interfaces:
                out.append((f, parts[0], interfaces))
    return out


def module_globs(module: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(module, p) for p in patterns)


# --- ASY-01

CLIENTS = [
    "boto3.client",
    "boto3.resource",
    "boto3.Session",
    "aioboto3.Session",
    "redis.Redis",
    "redis.from_url",
    "redis.asyncio.Redis",
    "redis.asyncio.from_url",
    "Redis",
    "Redis.from_url",
    "valkey.Valkey",
    "valkey.from_url",
    "valkey.asyncio.Valkey",
    "Valkey",
    "Valkey.from_url",
    "httpx.AsyncClient",
    "httpx.Client",
    "aiohttp.ClientSession",
]


@rule(
    "ASY-01",
    coverage="partial",
    summary="No module-level infra client or impl, and no manager constructs an infra impl.",
)
def no_ambient_infra(project: Project) -> Iterator[Violation]:
    """Reads `clients` under `[tool.arch-check.options.ASY-01]`: the dotted constructors of infra clients, as called."""
    clients = project.option("ASY-01", "clients", CLIENTS, {"clients"})
    for file, tree in project.trees():
        for node in tree.body:
            value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
            if isinstance(value, ast.Await):
                value = value.value
            if not isinstance(value, ast.Call):
                continue
            called = dotted(value.func) or ""
            if called in clients or (last(called) or "").endswith("Impl"):
                yield Violation.at(file.rel, node, f"a module-level {called}(); an infra handle arrives through a constructor")
    infra = project.sub("infra")
    for file in manager_impl_files(project):
        impl = project.tree(file)
        if impl is None:
            continue
        from_infra = {n for imp in project.imports(file) if is_under(imp.module, infra) for n in imp.names}
        for call in ast.walk(impl):
            if isinstance(call, ast.Call):
                name = last(dotted(call.func)) or ""
                if name.endswith("Impl") and name in from_infra:
                    yield Violation.at(file.rel, call, f"a manager constructs {name}; the container hands it in")


# --- ASY-02

BOOT = ["*.container", "*.settings", "*.main"]


@rule(
    "ASY-02",
    coverage="partial",
    summary="InfraInterface has a getter per capability, start and close; only boot modules import an infra impl.",
)
def one_infra_root(project: Project) -> Iterator[Violation]:
    """Reads `boot` under `[tool.arch-check.options.ASY-02]`: module globs allowed to import an infra impl
    (`*.container`, `*.settings`, `*.main`)."""
    boot = project.option("ASY-02", "boot", BOOT, {"boot"})
    infra = project.sub("infra")
    root, found = package_classes(project, f"{infra}.root")
    iface = next((c for c in found if c.name == "InfraInterface"), None)
    if root is not None:
        if iface is None:
            yield Violation.at(root.rel, None, f"{infra}.root declares no InfraInterface")
        else:
            declared = {m.name: m for m in methods(iface)}
            returned = {r for m in declared.values() for r in names_in(m.returns)}
            for _, cap, interfaces in capabilities(project):
                for i in interfaces:
                    if i.name not in returned:
                        yield Violation.at(root.rel, iface, f"InfraInterface has no getter returning {i.name} ({cap})")
            for name in ("start", "close"):
                if name not in declared:
                    yield Violation.at(root.rel, iface, f"InfraInterface declares no {name}()")
    caps = {cap for _, cap, _ in capabilities(project)}
    for file in project.python_files:
        if is_under(file.module, infra) or module_globs(file.module, boot):
            continue
        for imp in project.imports(file):
            for t in imp.targets():
                target = project.module(t)
                if target is None or not is_under(t, infra):
                    continue
                parts = t[len(infra) + 1 :].split(".")
                impl = parts[0] == "impl" or (parts[0] in caps and len(parts) > 1)
                if impl:
                    yield Violation.at(file.rel, imp.node, f"{file.module} imports {t}; only boot modules pick an infra impl")
                    break


# --- ASY-03


@rule(
    "ASY-03",
    coverage="partial",
    summary="Every capability interface declares describe() abstract, and every infra impl defines or inherits one.",
)
def impls_describe_themselves(project: Project) -> Iterator[Violation]:
    infra = project.sub("infra")
    index = class_index(project, infra)
    iface_names: set[str] = set()
    for file, _, interfaces in capabilities(project):
        for i in interfaces:
            iface_names.add(i.name)
            describe = next((m for m in methods(i) if m.name == "describe"), None)
            if describe is None or "abstractmethod" not in {last(dotted(d)) for d in describe.decorator_list}:
                yield Violation.at(file.rel, i, f"{i.name} declares no abstract describe()")

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
    summary="CacheScope is an enum; every get_cache call outside infra passes a CacheScope member, and no manager calls it.",
)
def caches_are_scoped(project: Project) -> Iterator[Violation]:
    infra = project.sub("infra")
    file, found = package_classes(project, f"{infra}.cache")
    scope = next((c for c in found if c.name == "CacheScope"), None)
    if not any(c.name.endswith("Interface") for c in found):
        file = None
    if file is not None and scope is None:
        yield Violation.at(file.rel, None, f"{infra}.cache declares no CacheScope")
    elif file is not None and scope is not None and not is_enum(scope):
        yield Violation.at(file.rel, scope, "CacheScope is not an Enum; a scope is a fixed member")
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
            if not (isinstance(arg, ast.Attribute) and last(dotted(arg.value)) == "CacheScope"):
                yield Violation.at(f.rel, node, "get_cache takes a CacheScope member, never a string or a variable")


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
                if not isinstance(node, ast.alias):
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


@rule(
    "ASY-08",
    coverage="partial",
    summary="Buckets is an enum, every bucket parameter is typed with it, none is a string literal, and a local impl exists.",
)
def buckets_are_an_enum(project: Project) -> Iterator[Violation]:
    infra = project.sub("infra")
    file, found = package_classes(project, f"{infra}.buckets")
    if file is None:
        return
    enum = next((c for c in found if c.name == "Buckets"), None)
    if enum is None or not is_enum(enum):
        yield Violation.at(file.rel, enum, "Buckets is not an Enum in the buckets package; a bucket is a fixed member")
    interfaces = [c for c in found if c.name.endswith("Interface")]
    for i in interfaces:
        for fn in methods(i):
            for p in parameters(fn):
                if p.name == "bucket" and last(p.annotation) != "Buckets":
                    yield Violation.at(file.rel, fn, f"{i.name}.{fn.name} takes bucket as {p.annotation}; it takes Buckets")
    index = class_index(project, infra)
    names = {i.name for i in interfaces}
    local = [n for n in index if (ancestors(n, index) & names) and ("Local" in n or "Memory" in n)]
    if interfaces and not local:
        yield Violation.at(file.rel, interfaces[0], f"no local impl subclasses {interfaces[0].name}; tests run without the cloud")
    for f, tree in project.trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                value = kwarg(node, "bucket")
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    yield Violation.at(f.rel, value, "a bucket named by a string; name it by a Buckets member")


# --- ASY-09


@rule(
    "ASY-09",
    coverage="full",
    summary="Topics is an enum mapped to TopicPayload subclasses; the payload base, publish and subscribe have the fixed shapes.",
)
def topics_are_fixed(project: Project) -> Iterator[Violation]:
    infra = project.sub("infra")
    file, found = package_classes(project, f"{infra}.topics")
    if file is None:
        return
    tree = project.tree(file)
    assert tree is not None
    by_name = {c.name: c for c in found}
    topics = by_name.get("Topics")
    if topics is None or not is_enum(topics):
        yield Violation.at(file.rel, topics, "Topics is not an Enum; a topic is a fixed member")
    base = by_name.get("TopicPayload")
    if base is None:
        yield Violation.at(file.rel, None, "the topics package declares no TopicPayload")
    else:
        if [last(b) for b in base_names(base)] != ["BaseModel"]:
            yield Violation.at(file.rel, base, "TopicPayload extends pydantic's BaseModel directly, never the OM root")
        config = class_value(base, "model_config")
        settings = {k.arg: k.value for k in config.keywords if k.arg} if isinstance(config, ast.Call) else {}
        if not is_true(settings.get("frozen")):
            yield Violation.at(file.rel, base, "TopicPayload is not frozen=True")
        extra = settings.get("extra")
        if not (isinstance(extra, ast.Constant) and extra.value == "ignore"):
            yield Violation.at(
                file.rel, base, 'TopicPayload does not set extra="ignore"; an old consumer must read a new payload'
            )
        fields = own_columns(base)
        for name in ("idempotency_key", "produced_at"):
            if name not in fields:
                yield Violation.at(file.rel, base, f"TopicPayload declares no {name}")
    payloads = module_value(tree, "TOPIC_PAYLOADS")
    index = class_index(project)
    if not isinstance(payloads, ast.Dict):
        yield Violation.at(file.rel, payloads, "TOPIC_PAYLOADS is not a dict literal in the topics package")
    else:
        for k, v in zip(payloads.keys, payloads.values, strict=True):
            if not (isinstance(k, ast.Attribute) and last(dotted(k.value)) == "Topics"):
                yield Violation.at(file.rel, k, "a TOPIC_PAYLOADS key is not a Topics member")
            name = last(dotted(v)) or ""
            if name != "TopicPayload" and "TopicPayload" not in ancestors(name, index):
                yield Violation.at(file.rel, v, f"{name} does not extend TopicPayload")
    iface = next(
        (c for c in found if c.name.endswith("Interface") and {"publish", "subscribe"} & {m.name for m in methods(c)}), None
    )
    if iface is not None:
        by_method = {m.name: m for m in methods(iface)}
        publish = by_method.get("publish")
        if publish is not None and not (isinstance(publish.returns, ast.Constant) and publish.returns.value is None):
            yield Violation.at(file.rel, publish, "publish returns something; it returns None")
        subscribe = by_method.get("subscribe")
        if subscribe is not None:
            if "consumer" not in {p.name for p in parameters(subscribe)}:
                yield Violation.at(file.rel, subscribe, "subscribe takes no consumer name")
            if "Callable" not in names_in(subscribe.returns):
                yield Violation.at(file.rel, subscribe, "subscribe returns no unsubscribe callable")
    for f, t in project.trees():
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
        "asyncio.TaskGroup",
        "anyio.create_task_group",
        "threading.Thread",
        "threading.Timer",
        "fastapi.BackgroundTasks",
        "starlette.background.BackgroundTasks",
        "starlette.background.BackgroundTask",
    }
)
"""What starts work in the background, as the defining module spells it."""
LOOP_CALLS = frozenset({"call_later", "call_at"})
SCHEDULERS = ("apscheduler", "schedule", "celery", "rq", "rq_scheduler", "aiocron", "crontab")
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


@rule(
    "ASY-15",
    coverage="partial",
    summary="No web service module outside the socket edge starts a task, a thread, a timer, or a scheduler.",
)
def services_spawn_nothing(project: Project) -> Iterator[Violation]:
    """Reads `edge` under `[tool.arch-check.options.ASY-15]`: module globs of the socket edge, where a
    task that forwards to a held socket may run (`*.realtime`, `*.realtime.*`)."""
    edge = project.option("ASY-15", "edge", EDGE, {"edge"})
    for file in project.modules_under(project.sub("services")):
        if module_globs(file.module, edge):
            continue
        tree = project.tree(file)
        if tree is None:
            continue
        for imp in project.imports(file):
            if any(is_under(imp.module, s) for s in SCHEDULERS):
                yield Violation.at(file.rel, imp.node, f"a web service imports {imp.module}; recurring work is a worker")
        bound = bindings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = resolved(node.func, bound)
                attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
                if name in SPAWNS or attr in LOOP_CALLS:
                    what = name or attr
                    yield Violation.at(file.rel, node, f"a web service calls {what}(); work that outlives a request is a worker")
            elif isinstance(node, ast.arg) and node.annotation is not None:
                name = resolved(node.annotation, bound)
                if name in SPAWNS:
                    yield Violation.at(file.rel, node, f"a web service takes {name}; work that outlives a request is a worker")


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
    types = f"{root}.types"
    found = False
    payloads = False
    for file, tree in project.trees(types):
        payloads = payloads or isinstance(module_value(tree, "WORK_PAYLOADS"), ast.Dict)
        for cls in classes(tree):
            if cls.name != item:
                continue
            found = True
            fields = own_columns(cls)
            for name in WORK_FIELDS:
                if name not in fields:
                    yield Violation.at(file.rel, cls, f"{item} declares no {name}")
    first = next(iter(project.modules_under(types)), None) or project.modules_under(root)[0]
    if not found:
        yield Violation.at(first.rel, None, f"{types} declares no {item}")
    if not payloads:
        yield Violation.at(first.rel, None, f"{types} declares no WORK_PAYLOADS dict literal")
    for t in tables(project, f"{root}.storage.tables"):
        if "idempotency_key" not in own_columns(t.node):
            continue
        unique = any(string_args(i) == ["idempotency_key"] and is_true(kwarg(i, "unique")) for i in index_calls(t.node))
        decl = own_columns(t.node)["idempotency_key"]
        call = column_call(decl) if isinstance(decl, ast.stmt) else None
        if not unique and not (call is not None and is_true(kwarg(call, "unique"))):
            yield Violation.at(t.file.rel, t.node, f"{t.node.name} has no unique index on idempotency_key")


# --- ASY-19

TF_SCHEDULE = re.compile(r'resource\s+"(aws_scheduler_schedule|aws_cloudwatch_event_rule)"\s+"[^"]*"\s*\{')


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
            depth, i = 1, m.end()
            while i < len(text) and depth:
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            body = text[m.end() : i]
            if m.group(1) == "aws_scheduler_schedule" or re.search(r"^\s*schedule_expression\s*=", body, re.MULTILINE):
                line = text.count("\n", 0, m.start()) + 1
                yield Violation(rel, line, 1, f"a scheduled {m.group(1)}; maintenance is a sweep every worker runs")


# --- ASY-28

READS = frozenset({"read_text", "read_bytes"})
PRIVATE = frozenset({"S_IRWXG", "S_IRWXO", "S_IRGRP", "S_IROTH"})


@rule(
    "ASY-28",
    coverage="partial",
    summary="Every file the secrets package reads is checked owner-only (mode & 0o077) first, in the same function.",
)
def secrets_files_are_private(project: Project) -> Iterator[Violation]:
    for file, tree in project.trees(f"{project.sub('infra')}.secrets"):
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]:
            body = [n for s in fn.body for n in ast.walk(s)]
            reads = [
                n
                for n in body
                if isinstance(n, ast.Call)
                and (
                    (isinstance(n.func, ast.Attribute) and n.func.attr in READS)
                    or (dotted(n.func) or "") in ("open", "io.open", "os.open")
                )
            ]
            if not reads:
                continue
            stats = [
                n.lineno for n in body if isinstance(n, ast.Call) and (last(dotted(n.func)) or "") in ("stat", "lstat", "fstat")
            ]
            mask = any(
                (isinstance(n, ast.Constant) and n.value == 0o077 and not isinstance(n.value, bool))
                or (isinstance(n, ast.Name | ast.Attribute) and (last(dotted(n)) or "") in PRIVATE)
                for n in body
            )
            first = min(r.lineno for r in reads)
            if not (mask and stats and min(stats) <= first):
                yield Violation.at(file.rel, reads[0], f"{fn.name} reads a file with no owner-only mode check before it")


# --- ASY-29


@rule(
    "ASY-29",
    coverage="partial",
    summary="WorkItem and OutboxRow declare request_id and traceparent, and neither declares trace_id.",
)
def work_carries_its_request(project: Project) -> Iterator[Violation]:
    """Reads `classes` under `[tool.arch-check.options.ASY-29]`: the class names that carry the request
    (`WorkItem`, `OutboxRow`)."""
    names = project.option("ASY-29", "classes", ["WorkItem", "OutboxRow"], {"classes"})
    for file, tree in project.trees(project.sub("om")):
        if ".types" not in file.module:
            continue
        for cls in classes(tree):
            if cls.name not in names:
                continue
            fields = own_columns(cls)
            for name in ("request_id", "traceparent"):
                if name not in fields:
                    yield Violation.at(file.rel, cls, f"{cls.name} declares no {name}")
            if "trace_id" in fields:
                yield Violation.at(file.rel, fields["trace_id"], f"{cls.name} carries trace_id; it carries the traceparent")
