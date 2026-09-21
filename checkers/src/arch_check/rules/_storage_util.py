"""What the `storage` and `async` rules share: table classes, mixins, the role map, and migration files.

A table class is a class with a `__tablename__` assignment in its body.
A table mixin is a class whose name ends in `Mixin`, defined in a
`tables` module of the OM; its columns are read from its body and its
mixin bases. The role map and the scope map are module-level dict
literals of the OM, found by name. Migration files are read as text
and as history: a rule replays a role's `up` files in stamp order when
it asks what the chain leaves in place, so a state a later migration
replaced is never reported.
"""

from __future__ import annotations

import ast
import contextlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from arch_check.project import Project, SourceFile, base_names, classes, dotted, is_under, last, methods

SQL_DIR = "om/migrations/sql"
"""Where the SQL files of each role live (The Storage Layer, Migrations)."""
VERSIONS_DIR = "om/migrations/versions"
"""Where the wrapper of each migration lives, one folder per role."""
ROLE_MAP = "TABLE_ROLES"
"""The module-level name of the table-to-role map."""
SCOPE_MAP = "TABLE_SCOPES"
"""The module-level name of the table-to-scope map."""

MIXIN_RANK: dict[str, int] = {
    "IdentifiableMixin": 0,
    "GlobalIdentifiableMixin": 0,
    "FeedIdentifiableMixin": 0,
    "NamedMixin": 1,
    "CreatedMixin": 2,
    "TrackableMixin": 2,
    "SoftDeletableMixin": 3,
}
"""The guideline's table mixins and their place in a base list: identity, name, lifecycle, soft delete."""

COLUMN_CALLS = frozenset({"mapped_column", "Column"})

SQL_FILE = re.compile(r"^(?P<stamp>\d{12}[a-z]?)_(?P<slug>[a-z0-9_]+)\.(?P<dir>up|down)\.sql$")
WRAPPER_FILE = re.compile(r"^(?P<stamp>\d{12}[a-z]?)_(?P<slug>[a-z0-9_]+)\.py$")


# --- names


def namespace_of(project: Project, module: str) -> str | None:
    """The OM namespace a module sits in: `acme.om.tasks.impl` gives `tasks`; the shared `storage` package gives None."""
    om = project.sub("om")
    if not is_under(module, om) or module == om:
        return None
    ns = module[len(om) + 1 :].split(".")[0]
    return None if ns == "storage" else ns


def segments(project: Project, module: str) -> list[str]:
    """The parts of a module below `<pkg>.om`, or empty when it is not in the OM."""
    om = project.sub("om")
    return module[len(om) + 1 :].split(".") if is_under(module, om) and module != om else []


def in_storage_impl(project: Project, module: str) -> bool:
    """Whether a module is a storage impl of the OM: `...storage.impl` or below it."""
    parts = segments(project, module)
    return any(parts[i] == "storage" and parts[i + 1] == "impl" for i in range(len(parts) - 1))


def in_storage(project: Project, module: str) -> bool:
    """Whether a module is part of the OM's storage layer: any `storage` package or below it."""
    return "storage" in segments(project, module)


def in_tables(project: Project, module: str) -> bool:
    parts = segments(project, module)
    return any(parts[i] == "storage" and parts[i + 1] == "tables" for i in range(len(parts) - 1))


def manager_impl_files(project: Project) -> list[SourceFile]:
    """The business-layer impls: `<pkg>.om.<ns>.impl` and below, for every namespace but the shared storage."""
    out = []
    for f in project.modules_under(project.sub("om")):
        ns = namespace_of(project, f.module)
        if ns is not None and is_under(f.module, f"{project.sub('om')}.{ns}.impl"):
            out.append(f)
    return out


def names_in(annotation: ast.AST | None) -> set[str]:
    """Every identifier an annotation spells, dotted names reduced to their last part, string annotations parsed."""
    if annotation is None:
        return set()
    out: set[str] = set()
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            with contextlib.suppress(SyntaxError):
                out |= names_in(ast.parse(node.value, mode="eval"))
    return out


def is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def string_constants(tree: ast.AST) -> Iterator[ast.Constant]:
    """Every string constant of a tree that is not a docstring."""
    docs = {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
    }
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n


def module_value(tree: ast.Module, name: str) -> ast.expr | None:
    """The value a top-level `name = ...` or `name: T = ...` assigns, else None."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    return None


def class_value(cls: ast.ClassDef, name: str) -> ast.expr | None:
    """The value a class body assigns to `name`, else None."""
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    return None


def call_name(node: ast.AST) -> str | None:
    """The last part of what a call calls: `sa.orm.relationship(...)` gives `relationship`."""
    return last(dotted(node.func)) if isinstance(node, ast.Call) else None


def kwarg(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def is_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def negative_int(node: ast.AST | None) -> int | None:
    """The value of a literal like `-1000`, else None."""
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
        and not isinstance(node.operand.value, bool)
        and node.operand.value > 0
    ):
        return -node.operand.value
    return None


# --- table classes and mixins


@dataclass(frozen=True)
class Table:
    """A table class: a class whose body assigns `__tablename__`."""

    file: SourceFile
    node: ast.ClassDef
    name: str


def tablename(cls: ast.ClassDef) -> str | None:
    value = class_value(cls, "__tablename__")
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def has_tablename(cls: ast.ClassDef) -> bool:
    return any(
        (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in n.targets))
        or (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "__tablename__")
        for n in cls.body
    )


def tables(project: Project, *prefixes: str) -> list[Table]:
    """Every table class under `prefixes` (the whole source when none), in path order."""
    out = []
    for file, tree in project.trees(*prefixes):
        for cls in classes(tree):
            if has_tablename(cls):
                out.append(Table(file, cls, tablename(cls) or ""))
    return out


def om_tables(project: Project) -> list[Table]:
    return tables(project, project.sub("om"))


def column_call(node: ast.stmt) -> ast.Call | None:
    """The `mapped_column(...)` or `Column(...)` a class-body assignment makes, else None."""
    value = node.value if isinstance(node, ast.AnnAssign | ast.Assign) else None
    if isinstance(value, ast.Call) and call_name(value) in COLUMN_CALLS:
        return value
    return None


def own_columns(cls: ast.ClassDef) -> dict[str, ast.AST]:
    """The columns a class body declares: annotated attributes, `x = mapped_column(...)`, and `declared_attr` functions.

    Dunder names and `ClassVar` annotations are not columns.
    """
    out: dict[str, ast.AST] = {}
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if name.startswith("__") or "ClassVar" in names_in(node.annotation):
                continue
            out[name] = node
        elif isinstance(node, ast.Assign) and column_call(node) is not None:
            for t in node.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("__"):
                    out[t.id] = node
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            last(dotted(d)) == "declared_attr" for d in node.decorator_list
        ):
            out[node.name] = node
    return out


def column_calls_of(node: ast.AST) -> list[ast.Call]:
    """The column constructor calls a column declaration makes; a `declared_attr` function's are those in its body."""
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and call_name(n) in COLUMN_CALLS]


@dataclass
class Mixin:
    name: str
    file: SourceFile
    node: ast.ClassDef
    own: dict[str, ast.AST]
    columns: dict[str, ast.AST] = field(default_factory=dict)
    """Its columns and those of its mixin bases, name to the declaring node."""


def mixins(project: Project) -> dict[str, Mixin]:
    """The table mixins of the OM by name: classes named `*Mixin` in a `storage.tables` module."""
    found: dict[str, Mixin] = {}
    for file, tree in project.trees(project.sub("om")):
        if not in_tables(project, file.module):
            continue
        for cls in classes(tree):
            if cls.name.endswith("Mixin") and cls.name not in found:
                found[cls.name] = Mixin(cls.name, file, cls, own_columns(cls))

    def collect(name: str, seen: frozenset[str]) -> dict[str, ast.AST]:
        m = found[name]
        out: dict[str, ast.AST] = {}
        for base in base_names(m.node):
            b = last(base) or ""
            if b in found and b not in seen:
                out.update(collect(b, seen | {b}))
        out.update(m.own)
        return out

    for name, m in found.items():
        m.columns = collect(name, frozenset({name}))
    return found


def composed(cls: ast.ClassDef, known: dict[str, Mixin]) -> list[Mixin]:
    """The table mixins a class lists as bases, in order."""
    return [known[b] for b in (last(n) or "" for n in base_names(cls)) if b in known]


def mixin_columns(cls: ast.ClassDef, known: dict[str, Mixin]) -> dict[str, str]:
    """Each column a class inherits from its mixins, to the mixin that brings it."""
    out: dict[str, str] = {}
    for m in composed(cls, known):
        for col in m.columns:
            out.setdefault(col, m.name)
    return out


def table_args(cls: ast.ClassDef) -> list[ast.expr]:
    """The entries of `__table_args__`: the items of its tuple, or the dict itself."""
    value = class_value(cls, "__table_args__")
    if isinstance(value, ast.Tuple | ast.List):
        return list(value.elts)
    if value is not None:
        return [value]
    return []


def index_calls(cls: ast.ClassDef) -> list[ast.Call]:
    return [a for a in table_args(cls) if isinstance(a, ast.Call) and call_name(a) == "Index"]


def string_args(call: ast.Call) -> list[str]:
    """The positional string arguments of a call after the first (an `Index`'s column names)."""
    return [a.value for a in call.args[1:] if isinstance(a, ast.Constant) and isinstance(a.value, str)]


# --- storage interfaces


def storage_interfaces(project: Project) -> Iterator[tuple[SourceFile, ast.ClassDef]]:
    """Every class of the OM whose name ends in `StorageInterface`, the root `StorageInterface` left out."""
    for file, tree in project.trees(project.sub("om")):
        for cls in classes(tree):
            if cls.name.endswith("StorageInterface") and cls.name != "StorageInterface":
                yield file, cls


def public_methods(cls: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [m for m in methods(cls) if not m.name.startswith("_")]


# --- the role map and the scope map


@dataclass(frozen=True)
class MapEntry:
    table: str
    key: ast.expr
    value: ast.expr


@dataclass(frozen=True)
class TableMap:
    """A module-level dict literal keyed by table name."""

    file: SourceFile
    tree: ast.Module
    node: ast.Dict
    entries: tuple[MapEntry, ...]


def table_map(project: Project, name: str) -> TableMap | None:
    """The first module of the OM that assigns `name` a dict literal, keyed by string."""
    for file, tree in project.trees(project.sub("om")):
        value = module_value(tree, name)
        if isinstance(value, ast.Dict):
            entries = tuple(
                MapEntry(k.value, k, v)
                for k, v in zip(value.keys, value.values, strict=True)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            )
            return TableMap(file, tree, value, entries)
    return None


def enum_values(project: Project) -> dict[str, dict[str, str]]:
    """Every enum-like class of the OM: class name to member name to its string value."""
    out: dict[str, dict[str, str]] = {}
    for _, tree in project.trees(project.sub("om")):
        for cls in classes(tree):
            if not any((last(b) or "").endswith("Enum") for b in base_names(cls)):
                continue
            members: dict[str, str] = {}
            for node in cls.body:
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    members[node.targets[0].id] = node.value.value
            out.setdefault(cls.name, members)
    return out


def role_value(node: ast.AST, enums: dict[str, dict[str, str]]) -> str | None:
    """The role a map value names: a string, or an enum member (`DatabaseRole.CORE`) read through its class."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute):
        owner = last(dotted(node.value))
        if owner in enums and node.attr in enums[owner]:
            return enums[owner][node.attr]
    return None


def roles_of(project: Project, name: str = ROLE_MAP) -> tuple[TableMap, dict[str, str]] | None:
    """The role map and each table's role, where the role can be read."""
    found = table_map(project, name)
    if found is None:
        return None
    enums = enum_values(project)
    roles = {}
    for e in found.entries:
        r = role_value(e.value, enums)
        if r is not None:
            roles[e.table] = r
    return found, roles


def role_names(project: Project, name: str = ROLE_MAP) -> set[str]:
    """Every role the project declares: the map's values, and every member of an enum the map uses."""
    read = roles_of(project, name)
    if read is None:
        return set()
    found, roles = read
    out = set(roles.values())
    enums = enum_values(project)
    for e in found.entries:
        if isinstance(e.value, ast.Attribute):
            out |= set(enums.get(last(dotted(e.value.value)) or "", {}).values())
    return out


# --- SQL


def sql_code(text: str) -> str:
    """SQL with comments and string literals blanked out, line breaks kept so offsets keep their lines."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and text.startswith("''", j):
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
            j = min(j + 1, n)
            out.append("'" + "".join(ch if ch == "\n" else " " for ch in text[i + 1 : j - 1]) + "'")
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def ident(name: str) -> str:
    """An SQL identifier as the database folds it: quotes dropped, lower case."""
    return name.replace('"', "").lower()


@dataclass(frozen=True)
class SqlFile:
    rel: str
    role: str
    stamp: str
    slug: str
    direction: str


def sql_files(project: Project, sql_dir: str) -> tuple[list[SqlFile], list[str]]:
    """The migration SQL files named `<stamp>_<slug>.<up|down>.sql` under `<sql_dir>/<role>/`, and every other file there."""
    good: list[SqlFile] = []
    bad: list[str] = []
    for rel in project.files(f"{sql_dir}/*/*"):
        role, name = rel.split("/")[-2:]
        m = SQL_FILE.match(name)
        if m:
            good.append(SqlFile(rel, role, m.group("stamp"), m.group("slug"), m.group("dir")))
        else:
            bad.append(rel)
    good.sort(key=lambda f: (f.role, f.stamp, f.slug, f.direction))
    return good, bad


def up_chain(project: Project, sql_dir: str) -> dict[str, list[SqlFile]]:
    """Each role's `up` files in stamp order: the order the chain applies them."""
    files, _ = sql_files(project, sql_dir)
    out: dict[str, list[SqlFile]] = {}
    for f in files:
        if f.direction == "up":
            out.setdefault(f.role, []).append(f)
    return out
