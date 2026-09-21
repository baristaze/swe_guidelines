"""The object model: the base chain, the mixins, immutability, ids, namespaces, pure rules.

Most rules here read the OM base chain: the root class of
`<pkg>.om.base` (a direct `BaseModel` subclass, `Platform` in the
guideline) and every class whose bases reach it, resolved through
imports (see `_om_util`). The mixins are the classes of the base module
on the chain: `Identifiable`, `Named`, `Created`, `Trackable`,
`SoftDeletable`, and any new trait added beside them.

"Above storage" code is `<pkg>.om`, `<pkg>.services`, and
`<pkg>.workers`. Clients, apps, and ops are left out: an idempotency key
or a request id minted there is not an entity id.
"""

from __future__ import annotations

import ast
import contextlib
import re
from collections.abc import Iterator

import tomllib

from arch_check.model import Violation
from arch_check.project import Project, SourceFile, decorator_names, dotted, is_under, last, methods, parameters
from arch_check.registry import rule
from arch_check.rules._om_util import (
    MIXIN_FIELDS,
    MIXIN_ORDER,
    VALIDATORS,
    ClassInfo,
    Index,
    config_settings,
    constant,
    field_name,
    fields,
    index,
    namespaces,
)


def above_storage(project: Project) -> tuple[str, ...]:
    return (project.sub("om"), project.sub("services"), project.sub("workers"))


def ancestors(idx: Index, info: ClassInfo) -> list[ClassInfo]:
    """Every project class a class inherits from, nearest first, each once."""
    out: list[ClassInfo] = []
    seen: set[tuple[str, str]] = set()
    todo = [k for _, k in idx.bases(info)]
    while todo:
        key = todo.pop(0)
        if key is None or key in seen or key not in idx.classes:
            continue
        seen.add(key)
        parent = idx.classes[key]
        out.append(parent)
        todo.extend(k for _, k in idx.bases(parent))
    return out


def all_fields(idx: Index, info: ClassInfo) -> set[str]:
    """The field names a class declares or inherits from project classes."""
    names = {field_name(f) for f in fields(info.node)}
    for parent in ancestors(idx, info):
        names.update(field_name(f) for f in fields(parent.node))
    return names


# --- OM-01


def distribution_dir(project: Project, file: SourceFile) -> str:
    """The directory of the distribution a file ships in: `om/src/acme/om/x.py` gives `om`."""
    parts = file.rel.split("/")
    depth = len(file.module.split(".")) + 1
    src = parts[: len(parts) - depth]
    if src and src[-1] == "src":
        src = src[:-1]
    return "/".join(src)


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(data: dict[str, object]) -> set[str]:
    project = data.get("project")
    deps = project.get("dependencies", []) if isinstance(project, dict) else []
    out: set[str] = set()
    for dep in deps if isinstance(deps, list) else []:
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(dep))
        if m:
            out.add(normalized(m.group(1)))
    return out


def pyproject(project: Project, directory: str) -> tuple[str, dict[str, object] | None]:
    rel = f"{directory}/pyproject.toml" if directory else "pyproject.toml"
    text = project.read(rel)
    if text is None:
        return rel, None
    try:
        return rel, tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return rel, None


@rule(
    "OM-01",
    coverage="partial",
    summary="The OM ships as its own distribution, and every distribution that imports it depends on it.",
)
def one_object_model(project: Project) -> Iterator[Violation]:
    """The OM package is a distribution of its own, with a `pyproject.toml`
    naming it. A distribution whose code imports `<pkg>.om` lists it in its
    dependencies, so the OM is never importable only by installing a service.
    A copy of an entity declared outside the OM is judged."""
    om = project.sub("om")
    om_files = project.modules_under(om)
    if not om_files:
        return
    om_dir = distribution_dir(project, om_files[0])
    rel, data = pyproject(project, om_dir)
    project_table = data.get("project") if data else None
    om_name = project_table.get("name") if isinstance(project_table, dict) else None
    if not isinstance(om_name, str):
        yield Violation(rel, 1, 1, f"{om} ships in no distribution of its own: {rel} names no [project]")
        return
    by_dir: dict[str, list[SourceFile]] = {}
    for f in project.python_files:
        by_dir.setdefault(distribution_dir(project, f), []).append(f)
    for directory, files in sorted(by_dir.items()):
        if directory == om_dir:
            continue
        uses = next(
            (i for f in files for i in project.imports(f) if any(is_under(t, om) for t in i.targets())),
            None,
        )
        if uses is None:
            continue
        rel, data = pyproject(project, directory)
        if data is None or normalized(om_name) in requirement_names(data):
            continue
        yield Violation(rel, 1, 1, f"code under {directory}/ imports {om}, and {rel} does not depend on {om_name}")


# --- OM-02


@rule(
    "OM-02",
    coverage="partial",
    summary="org_id is a field of an OM type only on the entities a reader with no tenant takes, and those carry it.",
)
def org_id_only_where_no_tenant(project: Project) -> Iterator[Violation]:
    """A class on the chain under a namespace's `types/` declares `org_id`
    only when a reader with no tenant takes it, and each such class carries
    it. Whether a field exists only for a response or a column is judged.

    Option `[tool.arch-check.options.OM-02]`: `tenantless`, the class names
    of those entities (default `["OutboxRow", "Event"]`, the guideline's).
    """
    tenantless = set(project.option("OM-02", "tenantless", ["OutboxRow", "Event"], {"tenantless"}))
    idx = index(project)
    for info in idx.chain(project.sub("om")):
        in_types = "types" in info.file.module.removeprefix(project.sub("om")).split(".")
        declared = next((f for f in fields(info.node) if field_name(f) == "org_id"), None)
        if info.node.name in tenantless:
            if "org_id" not in all_fields(idx, info):
                yield Violation.at(info.file.rel, info.node, f"{info.node.name} is read with no tenant and declares no org_id")
        elif declared is not None and in_types:
            yield Violation.at(
                info.file.rel,
                declared,
                f"{info.node.name}.org_id: a tenant entity carries no org_id; tenancy is a storage concern",
            )


# --- OM-03


def module_functions(tree: ast.Module) -> set[str]:
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def module_assign(tree: ast.Module, name: str) -> ast.Assign | ast.AnnAssign | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node
    return None


PROVENANCE = frozenset({"created_at", "created_by", "deleted_at", "deleted_by"})
OUTBOX_FIELDS = ("actor_id", "request_id", "traceparent", "app")
CLOCK_CALLS = frozenset({"datetime.now", "datetime.utcnow", "datetime.datetime.now", "datetime.datetime.utcnow"})


def string_set(node: ast.expr | None) -> frozenset[str] | None:
    """The strings of `frozenset({...})`, `frozenset([...])`, or a set literal; None for anything else."""
    if isinstance(node, ast.Call) and dotted(node.func) == "frozenset" and len(node.args) == 1 and not node.keywords:
        node = node.args[0]
    if isinstance(node, ast.Set | ast.List | ast.Tuple):
        values = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if len(values) == len(node.elts):
            return frozenset(values)
    return None


@rule(
    "OM-03",
    coverage="partial",
    summary="The root holds no field, each mixin declares exactly its fields, no class redeclares a mixin's field, "
    "the base helpers and PROVENANCE_FIELDS live in the base module, and OutboxRow carries its provenance.",
)
def mixins_declare_exactly_their_fields(project: Project) -> Iterator[Violation]:
    """In `<pkg>.om.base`: the root declares no field; `Identifiable`,
    `Named`, `Created`, `Trackable`, and `SoftDeletable` each declare
    exactly the fields the guideline lists; `new_id`, `utcnow`, and
    `PROVENANCE_FIELDS` (the four provenance fields, as a literal) are
    defined there and `PROVENANCE_FIELDS` nowhere else. No class on the
    chain declares a field a mixin it composes declares. `OutboxRow`
    declares `actor_id`, `request_id`, `traceparent`, and `app`, and has no
    `created_by`; it and `IdempotencyMarker` are each declared once. Above
    storage, no code calls `datetime.now` or `datetime.utcnow` outside the
    base module. Whether a new trait belongs in a new mixin is judged."""
    idx = index(project)
    base = project.module(idx.base_module)
    base_tree = project.tree(base) if base else None
    if base is None or base_tree is None:
        return
    for key in sorted(idx.roots):
        info = idx.classes[key]
        for f in fields(info.node):
            yield Violation.at(base.rel, f, f"the root {info.node.name} declares {field_name(f)}; the root holds no fields")
    for name, expected in MIXIN_FIELDS.items():
        found = idx.classes.get((idx.base_module, name))
        if found is None:
            continue
        own = {field_name(f) for f in fields(found.node)}
        if own != expected:
            extra = ", ".join(sorted(own - expected)) or "none"
            missing = ", ".join(sorted(expected - own)) or "none"
            yield Violation.at(
                base.rel, found.node, f"{name} declares {extra} beyond its fields and lacks {missing}; a new trait is a new mixin"
            )
    defined = module_functions(base_tree)
    for helper in ("new_id", "utcnow"):
        if helper not in defined:
            yield Violation(base.rel, 1, 1, f"{idx.base_module} defines no {helper}(); the base helpers live in the base module")
    prov = module_assign(base_tree, "PROVENANCE_FIELDS")
    if prov is None:
        yield Violation(base.rel, 1, 1, f"{idx.base_module} defines no PROVENANCE_FIELDS")
    elif string_set(prov.value) != PROVENANCE:
        yield Violation.at(
            base.rel, prov, "PROVENANCE_FIELDS names other fields than created_at, created_by, deleted_at, deleted_by"
        )
    for file, tree in project.trees(*above_storage(project)):
        if file.module == idx.base_module:
            continue
        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.Assign):
                target = next((t for t in node.targets if isinstance(t, ast.Name) and t.id == "PROVENANCE_FIELDS"), None)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target = node.target if node.target.id == "PROVENANCE_FIELDS" else None
            if target is not None:
                yield Violation.at(file.rel, node, "PROVENANCE_FIELDS is declared again; it lives in the base module only")
            if isinstance(node, ast.Call) and dotted(node.func) in CLOCK_CALLS:
                yield Violation.at(
                    file.rel, node, f"{dotted(node.func)}() in place of utcnow(); the clock is the base module's helper"
                )
    for info in idx.chain():
        if info.file.module == idx.base_module:
            continue
        inherited: dict[str, str] = {}
        for parent in ancestors(idx, info):
            if parent.file.module == idx.base_module:
                for f in fields(parent.node):
                    inherited.setdefault(field_name(f), parent.node.name)
        for f in fields(info.node):
            name = field_name(f)
            if name in inherited:
                yield Violation.at(
                    info.file.rel, f, f"{info.node.name} redeclares {name}, which {inherited[name]} already declares"
                )
    seen: dict[str, ClassInfo] = {}
    for info in idx.chain(project.sub("om")):
        if info.node.name not in ("OutboxRow", "IdempotencyMarker"):
            continue
        if info.node.name in seen:
            first = seen[info.node.name]
            yield Violation.at(
                info.file.rel, info.node, f"{info.node.name} is declared again; it is declared once, in {first.file.module}"
            )
            continue
        seen[info.node.name] = info
        if info.node.name == "OutboxRow":
            names = all_fields(idx, info)
            lacking = [f for f in OUTBOX_FIELDS if f not in names]
            if lacking:
                yield Violation.at(
                    info.file.rel, info.node, f"OutboxRow lacks {', '.join(lacking)}; it carries the provenance of its write"
                )
            if "created_by" in names:
                yield Violation.at(info.file.rel, info.node, "OutboxRow has a created_by; no person stands behind the row")


# --- OM-04


@rule(
    "OM-04",
    coverage="full",
    summary="Mixins are composed in the order identity, label, lifecycle, cross-cutting.",
)
def mixin_order(project: Project) -> Iterator[Violation]:
    """In every base list on the chain, the mixins of the base module come
    in this order: `Identifiable`, `Named`, `Created` or `Trackable`,
    `SoftDeletable`, then any other mixin (a cross-cutting trait). Bases
    that are not mixins are not ordered."""
    idx = index(project)
    for info in idx.chain():
        placed = [(MIXIN_ORDER.get(key[1], 4), key[1]) for _, key in idx.bases(info) if key is not None and idx.is_mixin(key)]
        if [p for p, _ in placed] != sorted(p for p, _ in placed):
            names = ", ".join(n for _, n in placed)
            yield Violation.at(
                info.file.rel,
                info.node,
                f"{info.node.name}({names}): mixins go identity, label, lifecycle, then cross-cutting traits",
            )


# --- OM-05


@rule(
    "OM-05",
    coverage="partial",
    summary="The root and the mixins carry no methods, and no entity inherits from another OM class.",
)
def mixins_carry_no_behavior(project: Project) -> Iterator[Violation]:
    """The root and every mixin of the base module define no method other
    than a pydantic validator or serializer. No class on the chain under a
    namespace's `types/` has a base that is a chain class outside the base
    module: an entity never inherits from an entity. Whether an operation
    exercises each mixin an entity composes is judged."""
    idx = index(project)
    for info in idx.chain(idx.base_module):
        for fn in methods(info.node):
            if not set(map(last, decorator_names(fn))) & VALIDATORS:
                yield Violation.at(info.file.rel, fn, f"{info.node.name}.{fn.name}: a mixin is a promise and carries no behavior")
    om = project.sub("om")
    for info in idx.chain(om):
        if "types" not in info.file.module.removeprefix(om).split("."):
            continue
        for node, key in idx.bases(info):
            if key is not None and key[0] != idx.base_module and idx.on_chain(key):
                yield Violation.at(
                    info.file.rel, node, f"{info.node.name} inherits from {key[1]}; inheritance in the OM never shares code"
                )


# --- OM-07


@rule(
    "OM-07",
    coverage="full",
    summary='The OM root sets extra="forbid" and no class on the chain relaxes it.',
)
def root_forbids_extras(project: Project) -> Iterator[Violation]:
    """The root's model config sets `extra="forbid"`, and no class on the
    chain sets `extra` to anything else, in `model_config` or as a class
    keyword."""
    idx = index(project)
    for key in sorted(idx.roots):
        info = idx.classes[key]
        if not any(k == "extra" and constant(v) == "forbid" for _, k, v in config_settings(info.node)):
            yield Violation.at(info.file.rel, info.node, f'the root {info.node.name} does not set extra="forbid"')
    for info in idx.chain():
        for node, k, v in config_settings(info.node):
            if k == "extra" and constant(v) != "forbid":
                yield Violation.at(
                    info.file.rel, node, f"{info.node.name} sets extra={ast.unparse(v)}; the chain forbids unknown fields"
                )


# --- OM-09

FILTER_NAMES = frozenset({"filter", "filters", "where", "order_by", "group_by", "sql", "criteria"})
LOOSE = frozenset({"dict", "Dict", "Mapping", "MutableMapping", "Any", "str", "object"})


@rule(
    "OM-09",
    coverage="partial",
    summary="Manager and storage interfaces take no **kwargs and no filter as a dict, Any, or string.",
)
def filters_are_typed(project: Project) -> Iterator[Violation]:
    """On every method of a `*ManagerInterface` or `*StorageInterface` class
    under `<pkg>.om`: no `**kwargs`, and no parameter named `filter`,
    `filters`, `where`, `order_by`, `group_by`, `sql`, or `criteria` that is
    unannotated or annotated with a dict, a `Mapping`, `Any`, `object`, or
    `str`. Whether one filter type travels through both interfaces is
    judged."""
    for file, tree in project.trees(project.sub("om")):
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            if not cls.name.endswith(("ManagerInterface", "StorageInterface")):
                continue
            for fn in methods(cls):
                for p in parameters(fn):
                    where = f"{cls.name}.{fn.name}"
                    if p.kind == "varkw":
                        yield Violation.at(file.rel, fn, f"{where} takes **{p.name}; a filter is a typed value object")
                    elif p.name in FILTER_NAMES:
                        names = set(re.findall(r"[A-Za-z_]\w*", p.annotation or ""))
                        if p.annotation is None or names & LOOSE:
                            shown = p.annotation or "no annotation"
                            yield Violation.at(file.rel, fn, f"{where}: {p.name} is {shown}; a filter is a typed value object")


# --- OM-10 and OM-11


def frozen_false(idx: Index, info: ClassInfo) -> Iterator[ast.AST]:
    for node, k, v in config_settings(info.node):
        if k == "frozen" and constant(v) is not True:
            yield node


def chain_params(idx: Index, module: str, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = fn.args
    every = [*a.posonlyargs, *a.args, *a.kwonlyargs]
    return {p.arg for p in every if p.arg not in ("self", "cls") and idx.annotation_on_chain(module, p.annotation)}


def has_dump(node: ast.AST, dumped: set[str]) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "model_dump":
            return True
        if isinstance(sub, ast.Name) and sub.id in dumped:
            return True
    return False


@rule(
    "OM-10",
    coverage="partial",
    summary="No class on the chain unfreezes, no model_copy takes a dump, no model_validate takes an entity, "
    "and no code assigns to an entity's attribute.",
)
def entities_are_immutable(project: Project) -> Iterator[Violation]:
    """No class on the chain sets `frozen` to anything but `True`. Above
    storage: no `model_copy(update=...)` whose update holds a
    `model_dump()` call or a name bound to one in the same function; no
    `model_validate(x)` where `x` is a parameter typed with a chain class;
    no assignment to an attribute of such a parameter, and no
    `object.__setattr__` on one. An in-place change through a method of a
    nested value is judged."""
    idx = index(project)
    for info in idx.chain():
        for node in frozen_false(idx, info):
            yield Violation.at(info.file.rel, node, f"{info.node.name} unfreezes itself; an entity is a frozen snapshot")
    for file, tree in project.trees(*above_storage(project)):
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            entities = chain_params(idx, file.module, fn)
            dumped: set[str] = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and has_dump(node.value, set()):
                    dumped.update(t.id for t in node.targets if isinstance(t, ast.Name))
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    attr = node.func.attr
                    update = next((k.value for k in node.keywords if k.arg == "update"), None)
                    if attr == "model_copy" and update is not None and has_dump(update, dumped):
                        yield Violation.at(
                            file.rel, node, "model_copy(update=...) fed a dump; rebuild it with model_validate so it validates"
                        )
                    first = node.args[0] if node.args else None
                    if attr == "model_validate" and isinstance(first, ast.Name) and first.id in entities:
                        yield Violation.at(
                            file.rel, node, f"model_validate({first.id}) on an entity it already is; validate a dict"
                        )
                    if dotted(node.func) == "object.__setattr__" and isinstance(first, ast.Name) and first.id in entities:
                        yield Violation.at(file.rel, node, f"object.__setattr__ on {first.id}; an entity is never mutated")
                targets: list[ast.expr] = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AugAssign | ast.AnnAssign):
                    targets = [node.target]
                for t in targets:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in entities:
                        yield Violation.at(
                            file.rel, t, f"{t.value.id}.{t.attr} = ...: an entity is never mutated; copy it and write the copy"
                        )


@rule(
    "OM-11",
    coverage="partial",
    summary="The OM root is frozen and no class on the chain unfreezes.",
)
def chain_is_frozen(project: Project) -> Iterator[Violation]:
    """The root's model config sets `frozen=True`, and no class on the
    chain sets `frozen` to anything else. The ORM row classes are not on
    the chain. An entity built by wrapping a live row is judged."""
    idx = index(project)
    for key in sorted(idx.roots):
        info = idx.classes[key]
        if not any(k == "frozen" and constant(v) is True for _, k, v in config_settings(info.node)):
            yield Violation.at(info.file.rel, info.node, f"the root {info.node.name} does not set frozen=True")
    for info in idx.chain():
        for node in frozen_false(idx, info):
            yield Violation.at(info.file.rel, node, f"{info.node.name} is declared mutable; the whole chain is frozen")


# --- OM-12

OTHER_FACTORIES = frozenset({"uuid1", "uuid3", "uuid4", "uuid5"})
ID_PACKAGES = frozenset({"uuid6", "uuid_extensions", "uuid_utils", "ulid", "shortuuid", "nanoid"})


@rule(
    "OM-12",
    coverage="partial",
    summary="Above storage, no id factory but new_id(): no uuid1/3/4/5, and uuid7 only in the base module.",
)
def ids_are_minted_by_new_id(project: Project) -> Iterator[Violation]:
    """Under `<pkg>.om`, `<pkg>.services`, and `<pkg>.workers`: no import
    or call of `uuid1`, `uuid3`, `uuid4`, or `uuid5`, no import of a
    third-party id package, and `uuid7` only in the base module, behind
    `new_id()`. An entity built without an id, and an id minted in a
    storage impl through `new_id()`, are judged."""
    idx = index(project)
    for file, tree in project.trees(*above_storage(project)):
        in_base = file.module == idx.base_module
        for imp in project.imports(file):
            if imp.module.split(".")[0] in ID_PACKAGES:
                yield Violation.at(file.rel, imp.node, f"{file.module} imports {imp.module}; every id comes from new_id()")
            if imp.module == "uuid":
                for name in imp.names:
                    if name in OTHER_FACTORIES or (name == "uuid7" and not in_base):
                        yield Violation.at(file.rel, imp.node, f"{file.module} imports uuid.{name}; every id comes from new_id()")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = dotted(node.func)
                if called in {f"uuid.{f}" for f in OTHER_FACTORIES} or (called == "uuid.uuid7" and not in_base):
                    yield Violation.at(file.rel, node, f"{called}() mints an id; every id comes from new_id()")


# --- OM-13

ZERO = re.compile(r"^(urn:uuid:)?\{?0{8}-?0{4}-?0{4}-?0{4}-?0{12}\}?$", re.IGNORECASE)


def all_zero_uuid(node: ast.Call) -> bool:
    """`UUID(int=0)`, `UUID("00000000-0000-0000-0000-000000000000")`, `UUID(hex=...)` of zeros, `UUID(bytes=bytes(16))`."""
    if last(dotted(node.func)) != "UUID":
        return False
    values = [*node.args[:1], *(k.value for k in node.keywords if k.arg in ("hex", "int", "bytes"))]
    for v in values:
        if isinstance(v, ast.Constant):
            if v.value == 0 and not isinstance(v.value, bool):
                return True
            if isinstance(v.value, str) and ZERO.match(v.value.strip()):
                return True
            if isinstance(v.value, bytes) and v.value == bytes(16):
                return True
        if isinstance(v, ast.Call) and dotted(v.func) == "bytes" and len(v.args) == 1:
            arg = v.args[0]
            if isinstance(arg, ast.Constant) and arg.value == 16:
                return True
    return False


def optional(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    text = ast.unparse(annotation)
    return "None" in re.findall(r"\w+", text) or "Optional" in text


def is_empty_uuid(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if last(dotted(node)) == "EMPTY_UUID" and not isinstance(node, ast.Call):
        return True
    if isinstance(node, ast.Call) and last(dotted(node.func)) == "Field":
        default = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "default"), None)
        return is_empty_uuid(default)
    return False


@rule(
    "OM-13",
    coverage="partial",
    summary="EMPTY_UUID is defined once, no other all-zero UUID is minted above storage, "
    "and no optional reference defaults to it.",
)
def empty_uuid_defined_once(project: Project) -> Iterator[Violation]:
    """The base module defines `EMPTY_UUID = UUID(int=0)`. Nowhere else
    under `<pkg>.om`, `<pkg>.services`, or `<pkg>.workers` assigns
    `EMPTY_UUID` or builds an all-zero `UUID(...)`; infra keeps its own
    system scope. No field or parameter annotated optional defaults to
    `EMPTY_UUID`. Which writes are signed with it is judged."""
    idx = index(project)
    base = project.module(idx.base_module)
    base_tree = project.tree(base) if base else None
    if base is not None and base_tree is not None:
        found = module_assign(base_tree, "EMPTY_UUID")
        if found is None or not (isinstance(found.value, ast.Call) and all_zero_uuid(found.value)):
            yield Violation(base.rel, getattr(found, "lineno", 1), 1, f"{idx.base_module} defines no EMPTY_UUID = UUID(int=0)")
    for file, tree in project.trees(*above_storage(project)):
        in_base = file.module == idx.base_module
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not in_base and any(isinstance(t, ast.Name) and t.id == "EMPTY_UUID" for t in targets):
                    yield Violation.at(file.rel, node, "EMPTY_UUID is defined again; the base module defines it once")
                    continue
                if isinstance(node, ast.AnnAssign) and optional(node.annotation) and is_empty_uuid(node.value):
                    yield Violation.at(file.rel, node, "an optional reference defaults to EMPTY_UUID; absence is None")
            if isinstance(node, ast.Call) and all_zero_uuid(node):
                assigned_here = in_base and any(
                    isinstance(n, ast.Assign)
                    and n.value is node
                    and any(isinstance(t, ast.Name) and t.id == "EMPTY_UUID" for t in n.targets)
                    for n in tree.body
                )
                if not assigned_here:
                    yield Violation.at(file.rel, node, "an all-zero UUID is a second sentinel; use EMPTY_UUID")
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                a = node.args
                positional = [*a.posonlyargs, *a.args]
                pairs = list(zip(positional[len(positional) - len(a.defaults) :], a.defaults, strict=True))
                pairs += [(p, d) for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is not None]
                for p, d in pairs:
                    if optional(p.annotation) and is_empty_uuid(d):
                        yield Violation.at(
                            file.rel, p, f"{node.name}({p.arg}=EMPTY_UUID): an optional reference defaults to None"
                        )


# --- OM-14


@rule(
    "OM-14",
    coverage="partial",
    summary="Each OM namespace has its entry module, types/, impl/, and storage/, re-exports its interface, "
    "and keeps no entity in impl/.",
)
def namespace_shape(project: Project) -> Iterator[Violation]:
    """Every direct subpackage of `<pkg>.om` but the storage root is a
    namespace. Each has its entry module (`manager.py`), `types/`,
    `impl/`, and `storage/`; its `__init__.py` imports a class named
    `*Interface` from the entry module; and no entity (a chain class that
    composes `Identifiable`) is defined under its `impl/`. The names of the
    interfaces and whether a manager takes each entity are judged.

    Options `[tool.arch-check.options.OM-14]`: `entry`, a table of
    namespace to entry module for a namespace whose interface is not a
    manager, over the default `{outbox = "relay"}` (the guideline's
    relay);
    `not_namespaces`, the subpackages of the OM that are not namespaces
    (default `["storage"]`, the storage root).
    """
    keys = {"entry", "not_namespaces"}
    entry = {"outbox": "relay", **project.option("OM-14", "entry", {}, keys)}
    skip = project.option("OM-14", "not_namespaces", ["storage"], keys)
    idx = index(project)
    om = project.sub("om")
    for name, directory in namespaces(project, skip):
        ns = f"{om}.{name}"
        module = entry.get(name, "manager")
        if not isinstance(module, str):
            continue
        init = project.module(ns)
        for part, is_pkg in ((module, False), ("types", True), ("impl", True), ("storage", True)):
            if project.module(f"{ns}.{part}") is None:
                shown = f"{part}/" if is_pkg else f"{part}.py"
                yield Violation(
                    f"{directory}/__init__.py", 1, 1, f"namespace {name} has no {shown}; every namespace has one shape"
                )
        if init is not None and project.module(f"{ns}.{module}") is not None:
            reexports = any(
                imp.module == f"{ns}.{module}" and any(n.endswith("Interface") for n in imp.names)
                for imp in project.imports(init)
            )
            if not reexports:
                yield Violation(
                    init.rel, 1, 1, f"{ns} does not re-export its interface from .{module}; consumers import it by a short path"
                )
        for info in idx.chain(f"{ns}.impl"):
            if any(a.key == (idx.base_module, "Identifiable") for a in ancestors(idx, info)):
                yield Violation.at(info.file.rel, info.node, f"entity {info.node.name} lives in impl/; entities live in types/")


# --- OM-15

CLOCK_CALLS_IN_RULES = (
    "datetime.now",
    "datetime.utcnow",
    "datetime.today",
    "date.today",
    "time.time",
    "time.time_ns",
    "time.monotonic",
    "time.perf_counter",
    "os.getenv",
)


@rule(
    "OM-15",
    coverage="partial",
    summary="A namespace's rules module reads no storage, infra, settings, clock, or environment, and awaits nothing.",
)
def rules_are_pure(project: Project) -> Iterator[Violation]:
    """Every `<pkg>.om.<ns>.rules` module imports nothing from a storage
    package of the OM, from `<pkg>.infra`, from a settings module, or from
    `os` or `time`, and not `utcnow`; calls no `utcnow()`, `now()`,
    `today()`, or `time()`; reads no `os.environ`; and defines no `async
    def`. It may import `datetime` for its types. Duplicated arithmetic
    in impls is judged."""
    om = project.sub("om")
    infra = project.sub("infra")
    for file in project.modules_under(om):
        parts = file.module.split(".")
        if parts[-1] != "rules" or file.module.rpartition(".")[0].rpartition(".")[0] != om:
            continue
        tree = project.tree(file)
        if tree is None:
            continue
        for imp in project.imports(file):
            for target in imp.targets():
                segs = target.split(".")
                bad = (
                    (is_under(target, om) and "storage" in segs)
                    or is_under(target, infra)
                    or any("settings" in s for s in segs)
                    or segs[0] in ("os", "time")
                    or (target.endswith(".utcnow"))
                )
                if bad:
                    yield Violation.at(
                        file.rel, imp.node, f"{file.module} imports {target}; a rule reads no storage, settings, or clock"
                    )
                    break
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                yield Violation.at(file.rel, node, f"async def {node.name}: a rule is a plain function over values")
            elif isinstance(node, ast.Call):
                name = dotted(node.func) or ""
                if last(name) in ("utcnow", "getenv") or name.endswith(CLOCK_CALLS_IN_RULES):
                    yield Violation.at(file.rel, node, f"{name}() in a rule; take the time or the setting as an argument")
            elif isinstance(node, ast.Attribute) and dotted(node) == "os.environ":
                yield Violation.at(file.rel, node, "os.environ in a rule; take the setting as an argument")


# --- OM-16

IDENTITY_CLASSES = frozenset({"User", "Org", "Organization", "Membership", "Credential", "ApiKey", "AuditEntry", "AuditRecord"})


@rule(
    "OM-16",
    coverage="partial",
    summary="Tenancy is a namespace of the OM, and no identity or audit class lives in the base module or a utils module.",
)
def tenancy_is_a_namespace(project: Project) -> Iterator[Violation]:
    """`<pkg>.om` has a tenancy namespace, and neither the base module nor
    any `util*` or `helper*` module of the OM defines `User`, `Org`,
    `Organization`, `Membership`, `Credential`, `ApiKey`, `AuditEntry`, or
    `AuditRecord`. Where audit lives, and whether it has a storage
    interface, is judged.

    Option `[tool.arch-check.options.OM-16]`: `namespace`, the name of the
    tenancy namespace (default `"tenancy"`).
    """
    name = project.option("OM-16", "namespace", "tenancy", {"namespace"})
    om = project.sub("om")
    base = project.module(f"{om}.base")
    if base is None:
        return
    if project.module(f"{om}.{name}") is None:
        yield Violation(base.rel, 1, 1, f"{om} has no {name} namespace; tenancy is a swimlane of its own")
    for file, tree in project.trees(om):
        segs = file.module.removeprefix(om).split(".")
        if file.module != base.module and not any(s.startswith(("util", "helper")) for s in segs):
            continue
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef) and cls.name in IDENTITY_CLASSES:
                yield Violation.at(file.rel, cls, f"{cls.name} in {file.module}; identity and audit live in their namespace")


# --- OM-17

MUTABLE = frozenset(
    {
        "list",
        "List",
        "dict",
        "Dict",
        "set",
        "Set",
        "Mapping",
        "MutableMapping",
        "MutableSequence",
        "MutableSet",
        "defaultdict",
        "DefaultDict",
        "OrderedDict",
        "Counter",
        "deque",
        "bytearray",
    }
)


def mutable_names(node: ast.expr) -> list[str]:
    """The mutable container names an annotation spells, quoted parts included."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return []
    found = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name | ast.Attribute):
            name = last(dotted(sub))
            if name in MUTABLE:
                found.append(name)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub is not node:
            with contextlib.suppress(SyntaxError):
                found.extend(mutable_names(ast.parse(sub.value, mode="eval").body))
    return found


def validated_default(value: ast.expr) -> bool:
    if isinstance(value, ast.Constant) and value.value is None:
        return True
    if isinstance(value, ast.Call) and last(dotted(value.func)) == "Field":
        default = value.args[0] if value.args else next((k.value for k in value.keywords if k.arg == "default"), None)
        if (
            isinstance(default, ast.Constant)
            and default.value is None
            and not any(k.arg == "default_factory" for k in value.keywords)
        ):
            return True
        return any(
            k.arg == "validate_default" and isinstance(k.value, ast.Constant) and k.value.value is True for k in value.keywords
        )
    return False


@rule(
    "OM-17",
    coverage="partial",
    summary="No field on the chain is a list, dict, set, or bare Mapping, and a FrozenMapping default is validated.",
)
def fields_are_frozen(project: Project) -> Iterator[Violation]:
    """No field of a class on the chain is annotated with `list`, `dict`,
    `set`, a bare `Mapping` or `MutableMapping`, or another mutable
    container, at any depth of the annotation; a tuple, a frozen model, or
    `FrozenMapping` stands in. A `FrozenMapping` field with a default other
    than `None` is `Field(..., validate_default=True)`. Whether the
    validator behind `FrozenMapping` descends is a runtime test."""
    idx = index(project)
    for info in idx.chain():
        if info.key in idx.roots:
            continue
        for f in fields(info.node):
            bad = mutable_names(f.annotation)
            if bad:
                yield Violation.at(
                    info.file.rel,
                    f,
                    f"{info.node.name}.{field_name(f)} is {ast.unparse(f.annotation)}; "
                    f"a field is a tuple, a frozen model, or FrozenMapping",
                )
            elif f.value is not None and "FrozenMapping" in ast.unparse(f.annotation) and not validated_default(f.value):
                yield Violation.at(
                    info.file.rel,
                    f,
                    f"{info.node.name}.{field_name(f)}: a FrozenMapping default needs validate_default=True, "
                    f"or it stays a plain dict",
                )
