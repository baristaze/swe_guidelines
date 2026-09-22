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

from arch_check.project import Project, SourceFile, base_names, classes, dotted, is_under, keywords, last, methods
from arch_check.rules._text_util import kwarg

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
    "IdentityScopedMixin": 0,
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
    """The OM namespace a module sits in: `acme.om.orders.impl` gives `orders`; the shared `storage` package gives None."""
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


def is_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


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
                if isinstance(k, ast.Constant) and k.value == key:
                    return v
    return None


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


TABLE_DUNDERS = frozenset({"__tablename__", "__table__"})


def has_tablename(cls: ast.ClassDef) -> bool:
    """A mapped class: its body assigns `__tablename__` or `__table__`, or computes one (`@declared_attr`)."""
    return any(
        (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in TABLE_DUNDERS for t in n.targets))
        or (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id in TABLE_DUNDERS)
        or (isinstance(n, ast.FunctionDef) and n.name in TABLE_DUNDERS)
        for n in cls.body
    )


def core_tables(tree: ast.Module) -> list[ast.Call]:
    """Every `Table(...)` or `sqlalchemy.Table(...)` call at the top level of a module: a Core table."""
    out = []
    for stmt in tree.body:
        value = stmt.value if isinstance(stmt, ast.Assign | ast.AnnAssign) else None
        if isinstance(value, ast.Call) and call_name(value) == "Table":
            out.append(value)
    return out


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


def storage_root(project: Project) -> tuple[SourceFile, ast.ClassDef] | None:
    """The class `StorageInterface` under `<pkg>.om.storage`: in `root.py`, in the package's `__init__.py`, or in
    any module below it, the conventional `root.py` first."""
    base = f"{project.sub('om')}.storage"
    files = sorted(project.modules_under(base), key=lambda f: f.module != f"{base}.root")
    for file in files:
        tree = project.tree(file)
        found = next((c for c in classes(tree) if c.name == "StorageInterface"), None) if tree else None
        if found is not None:
            return file, found
    return None


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
    """Every enum-like class of the OM: class name to member name to its string value.

    A member is a string literal, or `auto()` in a `StrEnum`, whose value
    is the member's name in lower case. `auto()` in any other enum is not
    a string the checker can know, so that member is left out.
    """
    out: dict[str, dict[str, str]] = {}
    for _, tree in project.trees(project.sub("om")):
        for cls in classes(tree):
            bases = [last(b) or "" for b in base_names(cls)]
            if not any(b.endswith("Enum") for b in bases):
                continue
            lowers = "StrEnum" in bases
            members: dict[str, str] = {}
            for node in cls.body:
                if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    members[node.targets[0].id] = node.value.value
                elif lowers and isinstance(node.value, ast.Call) and last(dotted(node.value.func)) == "auto":
                    members[node.targets[0].id] = node.targets[0].id.lower()
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


DOLLAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")
"""The opening of a dollar-quoted body: `$$` or `$tag$`."""


def blank(text: str) -> str:
    return "".join(ch if ch == "\n" else " " for ch in text)


def sql_code(text: str) -> str:
    """SQL with comments and string literals blanked out, line breaks kept so offsets keep their lines.

    A double-quoted identifier (`"it's"`, `"a--b"`) is kept as written,
    `""` inside it included, so a quote or a dash in a name opens no
    string and no comment. A `'...'` string is blanked, `''` inside it included; an `E'...'`
    string also skips a backslash and the character after it. A
    dollar-quoted body (`$$...$$`, `$tag$...$tag$`) is a function or
    `DO` body: its own strings and comments are blanked the same way,
    and its code is kept, so a statement it runs is still read.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        word_before = i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_$")
        if c == '"':
            j = i + 1
            while j < n and (text[j] != '"' or text.startswith('""', j)):
                j += 2 if text.startswith('""', j) else 1
            j = min(j + 1, n)
            out.append(text[i:j])
            i = j
        elif text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(blank(text[i:j]))
            i = j
        elif c == "$" and not word_before and (m := DOLLAR.match(text, i)):
            tag = m.group(0)
            j = text.find(tag, m.end())
            j = n if j < 0 else j
            out.append(tag + sql_code(text[m.end() : j]) + (tag if j < n else ""))
            i = j + len(tag) if j < n else n
        elif c == "'" or (c in "eE" and text.startswith("'", i + 1) and not word_before):
            escapes = c != "'"
            start = i + 1 if escapes else i
            if escapes:
                out.append(c)
            j = start + 1
            while j < n:
                if escapes and text[j] == "\\":
                    j += 2
                    continue
                if text.startswith("''", j):
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
            j = min(j + 1, n)
            out.append("'" + blank(text[start + 1 : j - 1]) + "'")
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


NAME = r'(?:"[^"\n]+"|[\w$]+)(?:\s*\.\s*(?:"[^"\n]+"|[\w$]+))*'
"""An SQL name, maybe schema-qualified, each part bare or double-quoted (`"tenant fence"`)."""


def statement_tail(code: str, start: int) -> str:
    """The rest of the statement from `start`: up to the next `;`, or the end."""
    end = code.find(";", start)
    return code[start : end if end >= 0 else len(code)]


def name_list(text: str) -> list[str]:
    """The names of a comma-separated list, each item's `(args)` and trailing words dropped.

    `a(int), "b c"(), d CASCADE` gives `a`, `"b c"`, `d`.
    """
    items: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            items.append("".join(current))
            current = []
            continue
        current.append(ch)
    items.append("".join(current))
    out = []
    for item in items:
        m = re.match(r"\s*(" + NAME + ")", item)
        if m:
            out.append(m.group(1))
    return out


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def ident(name: str) -> str:
    """An SQL identifier as the database folds it: quotes and the space around a dot dropped, lower case."""
    return re.sub(r"\s*\.\s*", ".", name.strip()).replace('"', "").lower()


@dataclass(frozen=True)
class SqlFile:
    rel: str
    role: str
    stamp: str
    slug: str
    direction: str


def sql_files(project: Project, sql_dir: str) -> tuple[list[SqlFile], list[str]]:
    """The migration SQL files named `<stamp>_<slug>.<up|down>.sql` under `<sql_dir>/<role>/`, and every other
    `.sql` file there. A dotfile (`.gitkeep`) or a file of another kind is neither."""
    good: list[SqlFile] = []
    bad: list[str] = []
    for rel in project.files(f"{sql_dir}/*/*"):
        role, name = rel.split("/")[-2:]
        if name.startswith(".") or not name.endswith(".sql"):
            continue
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


# --- the tables a chain leaves: columns, keys, and unique indexes

CREATE_TABLE = re.compile(
    r"\bCREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:(?:TEMP|TEMPORARY|UNLOGGED)\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<table>" + NAME + r")\s*\(",
    re.IGNORECASE,
)
ALTER_TABLE_ANY = re.compile(r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?P<table>" + NAME + ")", re.IGNORECASE)
CREATE_INDEX = re.compile(
    r"\bCREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:(?P<name>" + NAME + r")\s+)?ON\s+(?:ONLY\s+)?(?P<table>" + NAME + ")",
    re.IGNORECASE,
)
RENAME_TABLE = re.compile(r"\s*RENAME\s+TO\s+(?P<new>" + NAME + r")\s*$", re.IGNORECASE)
"""The action of `ALTER TABLE <old> RENAME TO <new>`, read on the rest of the statement."""
ALTER_INDEX_RENAME = re.compile(
    r"\bALTER\s+INDEX\s+(?:IF\s+EXISTS\s+)?(?P<old>" + NAME + r")\s+RENAME\s+TO\s+(?P<new>" + NAME + ")", re.IGNORECASE
)
DROP_INDEX = re.compile(r"\bDROP\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?", re.IGNORECASE)
DROP_TABLES = re.compile(r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?", re.IGNORECASE)
SERIAL = frozenset({"serial", "bigserial", "smallserial", "serial2", "serial4", "serial8"})
IDENTITY = re.compile(r"\bGENERATED\b[^,]*?\bAS\s+IDENTITY\b", re.IGNORECASE)
TABLE_CONSTRAINT = re.compile(r"(?:PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY|EXCLUDE)\b", re.IGNORECASE)


@dataclass
class Where:
    """Where a migration declared something: the file, the line, and what it declared."""

    rel: str
    line: int
    what: str


@dataclass
class SqlColumn:
    default: Where | None = None
    """A database default, a serial type included, that the chain leaves on the column."""
    identity: Where | None = None


@dataclass
class UniqueKey:
    name: str
    at: Where
    living: bool
    """Whether it is partial on `deleted_at IS NULL`."""


@dataclass
class SqlTable:
    columns: dict[str, SqlColumn] = field(default_factory=dict)
    primary: tuple[str, set[str]] | None = None
    """The primary key's constraint name and its columns."""
    uniques: dict[str, UniqueKey] = field(default_factory=dict)
    """Each unique constraint and unique index, by name."""


def closing_paren(code: str, start: int) -> int:
    """The offset of the `)` that closes the `(` at `start`, or the end of the code."""
    depth = 0
    for i in range(start, len(code)):
        if code[i] == "(":
            depth += 1
        elif code[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(code)


def split_items(code: str, start: int, end: int) -> list[tuple[int, str]]:
    """The comma-separated items of `code[start:end]` at paren depth zero, each with its offset, stripped."""
    items: list[tuple[int, str]] = []
    depth, begin = 0, start
    for i in range(start, end + 1):
        c = code[i] if i < end else ","
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            text = code[begin:i]
            stripped = text.lstrip()
            if stripped.strip():
                items.append((begin + len(text) - len(stripped), stripped.rstrip()))
            begin = i + 1
    return items


def paren_names(text: str) -> list[str]:
    """The names inside the first `(...)` of `text`: `PRIMARY KEY (org_id, id)` gives `org_id`, `id`."""
    m = re.search(r"\(([^)]*)\)", text)
    return [ident(n) for n in name_list(m.group(1))] if m else []


class SqlTables:
    """The tables a role's chain leaves: each column's default and identity, the primary key, and every unique
    constraint and unique index with whether it is partial on the living. A name the chain gives no constraint or
    index is the one the database would, `<table>_pkey`, `<table>_<columns>_key`, and `<table>_<columns>_idx`, so a
    later `DROP` finds it. A table, an index, or a constraint renamed keeps what the chain knows of it.
    """

    def __init__(self) -> None:
        self.tables: dict[str, SqlTable] = {}

    def replay(self, rel: str, code: str) -> None:
        events: list[tuple[int, str, re.Match[str]]] = []
        for kind, pattern in (
            ("create", CREATE_TABLE),
            ("alter", ALTER_TABLE_ANY),
            ("index", CREATE_INDEX),
            ("rename_index", ALTER_INDEX_RENAME),
            ("drop_index", DROP_INDEX),
            ("drop_table", DROP_TABLES),
        ):
            events += [(m.start(), kind, m) for m in pattern.finditer(code)]
        for _, kind, m in sorted(events, key=lambda e: e[0]):
            if kind == "create":
                self.create(rel, code, m)
            elif kind == "alter":
                name = ident(m.group("table"))
                table = self.tables.get(name)
                if table is None:
                    continue
                new = renamed(name, statement_tail(code, m.end()))
                if new is not None:
                    self.tables[new] = self.tables.pop(name)
                else:
                    self.alter(rel, code, table, name, m.end())
            elif kind == "rename_index":
                old, new = (ident(m.group(g)).rpartition(".")[2] for g in ("old", "new"))
                for t in self.tables.values():
                    if old in t.uniques:
                        key = t.uniques.pop(old)
                        t.uniques[new] = UniqueKey(new, key.at, key.living)
            elif kind == "index":
                self.index(rel, code, m)
            elif kind == "drop_index":
                gone = {ident(n).rpartition(".")[2] for n in name_list(statement_tail(code, m.end()))}
                for t in self.tables.values():
                    for name in gone & set(t.uniques):
                        del t.uniques[name]
            else:
                for name in name_list(statement_tail(code, m.end())):
                    self.tables.pop(ident(name), None)

    def create(self, rel: str, code: str, m: re.Match[str]) -> None:
        name = ident(m.group("table"))
        table = SqlTable()
        self.tables[name] = table
        open_at = m.end() - 1
        for offset, item in split_items(code, open_at + 1, closing_paren(code, open_at)):
            self.item(rel, code, table, name, offset, item)

    def item(self, rel: str, code: str, table: SqlTable, name: str, offset: int, item: str) -> None:
        """One item of a `CREATE TABLE` list or of an `ADD`: a column or a table constraint."""
        where = Where(rel, line_of(code, offset), "")
        constraint = None
        named = re.match(r"CONSTRAINT\s+(" + NAME + r")\s+", item, re.IGNORECASE)
        if named:
            constraint = ident(named.group(1))
            item = item[named.end() :]
        if TABLE_CONSTRAINT.match(item):
            self.constraint(table, name, constraint, item, where)
            return
        col = re.match(r"(" + NAME + r")\s*(.*)", item, re.DOTALL)
        if col is None:
            return
        column = ident(col.group(1))
        rest = col.group(2)
        state = SqlColumn()
        table.columns[column] = state
        word = re.match(r"[\w$]+", rest)
        kind = word.group(0).lower() if word else ""
        if kind in SERIAL:
            state.default = Where(where.rel, where.line, f"type {kind}")
        elif re.search(r"\bDEFAULT\b", rest, re.IGNORECASE):
            state.default = Where(where.rel, where.line, "a DEFAULT")
        if IDENTITY.search(rest):
            state.identity = Where(where.rel, where.line, "an identity")
        if re.search(r"\bPRIMARY\s+KEY\b", rest, re.IGNORECASE):
            table.primary = (constraint or f"{short_name(name)}_pkey", {column})
        if re.search(r"\bUNIQUE\b", rest, re.IGNORECASE):
            key = constraint or f"{short_name(name)}_{column}_key"
            table.uniques[key] = UniqueKey(key, where, living=False)

    def constraint(self, table: SqlTable, name: str, constraint: str | None, item: str, where: Where) -> None:
        cols = paren_names(item)
        if re.match(r"PRIMARY\s+KEY\b", item, re.IGNORECASE):
            table.primary = (constraint or f"{short_name(name)}_pkey", set(cols))
        elif re.match(r"UNIQUE\b", item, re.IGNORECASE):
            key = constraint or f"{short_name(name)}_{'_'.join(cols)}_key"
            table.uniques[key] = UniqueKey(key, where, living=False)

    def alter(self, rel: str, code: str, table: SqlTable, name: str, start: int) -> None:
        end = code.find(";", start)
        end = len(code) if end < 0 else end
        for offset, action in split_items(code, start, end):
            where = Where(rel, line_of(code, offset), "")
            if m := re.match(r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?", action, re.IGNORECASE):
                # a column or a table constraint, the way a CREATE TABLE list holds them
                self.item(rel, code, table, name, offset + m.end(), action[m.end() :])
            elif m := re.match(r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?(" + NAME + ")", action, re.IGNORECASE):
                gone = ident(m.group(1))
                table.uniques.pop(gone, None)
                if table.primary and table.primary[0] == gone:
                    table.primary = None
            elif m := re.match(r"DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(" + NAME + ")", action, re.IGNORECASE):
                table.columns.pop(ident(m.group(1)), None)
            elif m := re.match(r"RENAME\s+CONSTRAINT\s+(" + NAME + r")\s+TO\s+(" + NAME + ")", action, re.IGNORECASE):
                old, new = ident(m.group(1)), ident(m.group(2))
                if old in table.uniques:
                    key = table.uniques.pop(old)
                    table.uniques[new] = UniqueKey(new, key.at, key.living)
                if table.primary and table.primary[0] == old:
                    table.primary = (new, table.primary[1])
            elif m := re.match(r"RENAME\s+(?:COLUMN\s+)?(" + NAME + r")\s+TO\s+(" + NAME + ")", action, re.IGNORECASE):
                old, new = ident(m.group(1)), ident(m.group(2))
                if old in table.columns:
                    table.columns[new] = table.columns.pop(old)
            elif m := re.match(r"ALTER\s+(?:COLUMN\s+)?(" + NAME + r")\s+(.*)", action, re.IGNORECASE | re.DOTALL):
                column = table.columns.setdefault(ident(m.group(1)), SqlColumn())
                change = m.group(2)
                if re.match(r"SET\s+DEFAULT\b", change, re.IGNORECASE):
                    column.default = Where(where.rel, where.line, "a DEFAULT")
                elif re.match(r"DROP\s+DEFAULT\b", change, re.IGNORECASE):
                    column.default = None
                elif re.match(r"ADD\s+GENERATED\b", change, re.IGNORECASE):
                    column.identity = Where(where.rel, where.line, "an identity")
                elif re.match(r"DROP\s+IDENTITY\b", change, re.IGNORECASE):
                    column.identity = None

    def index(self, rel: str, code: str, m: re.Match[str]) -> None:
        table = self.tables.get(ident(m.group("table")))
        if table is None or not m.group("unique"):
            return
        tail = statement_tail(code, m.end())
        where = re.search(r"\bWHERE\b(.*)", tail, re.IGNORECASE | re.DOTALL)
        at = Where(rel, line_of(code, m.start()), "")
        if m.group("name"):
            name = ident(m.group("name")).rpartition(".")[2]
        else:
            name = "_".join([short_name(m.group("table")), *index_columns(tail), "idx"])
        table.uniques[name] = UniqueKey(name, at, living=bool(where and living(where.group(1))))


def renamed(table: str, tail: str) -> str | None:
    """The name `ALTER TABLE <table> RENAME TO <new>` leaves: the new name in the table's own schema, else None."""
    m = RENAME_TABLE.match(tail)
    if m is None:
        return None
    schema, _, _ = table.rpartition(".")
    new = ident(m.group("new")).rpartition(".")[2]
    return f"{schema}.{new}" if schema else new


def index_columns(tail: str) -> list[str]:
    """The column part of the name Postgres gives an unnamed index, one word per key.

    A column is its name, a function call is the function's name
    (`lower(email)` gives `lower`), and any other expression is `expr`,
    so `ON t (org_id, lower(email))` is named `t_org_id_lower_idx`.
    """
    open_at = tail.find("(")
    if open_at < 0:
        return []
    out: list[str] = []
    for _, item in split_items(tail, open_at + 1, closing_paren(tail, open_at)):
        while item.startswith("(") and closing_paren(item, 0) == len(item) - 1:
            item = item[1:-1].strip()
        m = re.match(NAME, item)
        out.append(ident(m.group(0)).rpartition(".")[2] if m else "expr")
    return out


def living(predicate: str) -> bool:
    """Whether an index predicate holds only rows with `deleted_at IS NULL`.

    It does when the predicate is that test, or an `AND` of terms one of
    which is. An `OR` at the top, or a `NOT` before the test, lets a dead
    row in.
    """
    text = predicate.strip()
    while text.startswith("(") and closing_paren(text, 0) == len(text) - 1:
        text = text[1:-1].strip()
    if re.fullmatch(r"(?:" + NAME + r"\s*\.\s*)?deleted_at\s+IS\s+NULL", text, re.IGNORECASE):
        return True
    if len(top_level(text, "OR")) > 1:
        return False
    terms = top_level(text, "AND")
    return len(terms) > 1 and any(living(t) for t in terms)


def top_level(text: str, word: str) -> list[str]:
    """`text` split on `word` (`AND`, `OR`) where it stands outside every parenthesis; one item when it never does."""
    parts: list[str] = []
    depth, begin = 0, 0
    for m in re.finditer(r"[()]|\b" + word + r"\b", text, re.IGNORECASE):
        if m.group(0) == "(":
            depth += 1
        elif m.group(0) == ")":
            depth -= 1
        elif depth == 0:
            parts.append(text[begin : m.start()])
            begin = m.end()
    return [*parts, text[begin:]] if parts else [text]


def short_name(name: str) -> str:
    """A table's name without its schema, as the database folds it."""
    return ident(name).rpartition(".")[2]


def sql_tables(project: Project, sql_dir: str) -> dict[str, SqlTable]:
    """The tables every role's chain leaves, by their schema-qualified name."""
    out: dict[str, SqlTable] = {}
    for chain in up_chain(project, sql_dir).values():
        state = SqlTables()
        for f in chain:
            state.replay(f.rel, sql_code(project.read(f.rel) or ""))
        out.update(state.tables)
    return out
