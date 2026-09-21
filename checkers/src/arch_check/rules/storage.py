"""The storage rules: what a storage operation may do, how tables are declared, roles, and migrations.

Each rule decides the mechanical part of one lens of `lenses/storage.md`
and reads the project's current state: the table classes, the storage
interfaces, the role and scope maps. Migration SQL is history, since an
applied file is never edited (STO-24). So a rule reads it only for a
property every file must hold (its name, its wrapper, the roles it
names), or replays a role's chain in stamp order and judges what the
chain leaves in place, never a state a later migration replaced.
"""

from __future__ import annotations

import ast
import itertools
import re
from collections.abc import Iterator

from arch_check.model import Violation
from arch_check.project import Project, SourceFile, base_names, classes, dotted, is_under, keywords, last, parameters
from arch_check.registry import rule
from arch_check.rules._storage_util import (
    MIXIN_RANK,
    ROLE_MAP,
    SCOPE_MAP,
    SQL_DIR,
    VERSIONS_DIR,
    WRAPPER_FILE,
    call_name,
    class_value,
    column_call,
    column_calls_of,
    composed,
    enum_values,
    ident,
    in_storage_impl,
    in_tables,
    index_calls,
    is_docstring,
    is_true,
    kwarg,
    line_of,
    manager_impl_files,
    mixin_columns,
    mixins,
    module_value,
    names_in,
    namespace_of,
    negative_int,
    om_tables,
    own_columns,
    public_methods,
    role_names,
    role_value,
    roles_of,
    sql_code,
    sql_files,
    storage_interfaces,
    string_args,
    string_constants,
    table_args,
    table_map,
    tables,
    up_chain,
)

# --- STO-01

INTEGRITY_ERRORS = frozenset(
    {"IntegrityError", "UniqueViolation", "UniqueViolationError", "ForeignKeyViolation", "ForeignKeyViolationError"}
)


@rule(
    "STO-01",
    coverage="partial",
    summary="No relationship, backref, cascade or ondelete in a table module; no manager catches an integrity error.",
)
def no_database_relationships(project: Project) -> Iterator[Violation]:
    for file, tree in project.trees(project.sub("om")):
        if not in_tables(project, file.module):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if name in ("relationship", "backref"):
                yield Violation.at(file.rel, node, f"{name}() in a table module; the code reads related rows by id itself")
            for k in node.keywords:
                if k.arg in ("cascade", "ondelete"):
                    yield Violation.at(
                        file.rel, k.value, f"`{k.arg}=` in a table module; no code relies on the database to cascade"
                    )
    for file in manager_impl_files(project):
        impl = project.tree(file)
        if impl is None:
            continue
        for node in ast.walk(impl):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                caught = names_in(node.type) & INTEGRITY_ERRORS
                if caught:
                    yield Violation.at(
                        file.rel,
                        node,
                        f"a manager catches {sorted(caught)[0]}; an existence check is a read, never a failed insert",
                    )


# --- STO-02

TRANSACTION_CALLS = frozenset({"commit", "rollback", "begin", "begin_nested"})


@rule(
    "STO-02",
    coverage="partial",
    summary="No bare commit(), rollback(), begin() or begin_nested() in a manager impl, a service or a worker.",
)
def no_transaction_above_storage(project: Project) -> Iterator[Violation]:
    files = [*manager_impl_files(project), *project.modules_under(project.sub("services"), project.sub("workers"))]
    for file in files:
        tree = project.tree(file)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in TRANSACTION_CALLS
                and not node.args
                and not node.keywords
            ):
                yield Violation.at(
                    file.rel,
                    node,
                    f".{node.func.attr}() above storage; a storage operation opens and commits its own transaction",
                )


# --- STO-03

LOCKING = re.compile(r"\bFOR\s+(?:NO\s+KEY\s+)?(?:UPDATE|SHARE)\b|\bSKIP\s+LOCKED\b")
HANDLE_SOURCES = ("sqlalchemy", "asyncpg", "psycopg", "psycopg2", "asyncio", "threading", "multiprocessing")
"""The packages a lock, a session, or a transaction handle is imported from."""


def handles(project: Project, file: SourceFile) -> set[str]:
    """The names a module imports from a database driver or a locking library."""
    return {
        n
        for imp in project.imports(file)
        if any(is_under(imp.module, p) for p in HANDLE_SOURCES)
        for n in (imp.names or (imp.module.split(".")[0],))
    }


@rule(
    "STO-03",
    coverage="partial",
    summary="Row locks appear only in storage impls; no storage interface signature names a driver or lock type.",
)
def locking_stays_in_one_method(project: Project) -> Iterator[Violation]:
    for file, tree in project.trees():
        if in_storage_impl(project, file.module):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "with_for_update":
                yield Violation.at(file.rel, node, "with_for_update() outside a storage impl; a lock lives inside one method")
        for const in string_constants(tree):
            m = LOCKING.search(str(const.value))
            if m:
                yield Violation.at(file.rel, const, f"`{m.group(0)}` outside a storage impl; a lock lives inside one method")
    for file, cls in storage_interfaces(project):
        imported = handles(project, file)
        for fn in public_methods(cls):
            for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
                hit = names_in(arg.annotation) & imported
                if hit:
                    yield Violation.at(
                        file.rel,
                        arg,
                        f"{cls.name}.{fn.name} takes {sorted(hit)[0]}; a storage signature exposes no lock or session",
                    )


# --- STO-05

DB_OBJECT = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?(?P<kind>TRIGGER|FUNCTION|PROCEDURE|RULE)\s+(?P<name>[\w.\"]+)",
    re.IGNORECASE,
)
DROP_OBJECT = re.compile(
    r"\bDROP\s+(?P<kind>TRIGGER|FUNCTION|PROCEDURE|RULE)\s+(?:IF\s+EXISTS\s+)?(?P<name>[\w.\"]+)",
    re.IGNORECASE,
)
DB_TIMESTAMPS = frozenset({"created_at", "updated_at", "deleted_at"})
DB_SET = ("server_default", "onupdate", "server_onupdate")


@rule(
    "STO-05",
    coverage="partial",
    summary="The migration chain leaves no trigger, function, procedure or rule; no computed column or database-set timestamp.",
)
def no_triggers_or_functions(project: Project) -> Iterator[Violation]:
    sql_dir = project.option("STO-05", "sql_dir", SQL_DIR, {"sql_dir"})
    for chain in up_chain(project, sql_dir).values():
        alive: dict[tuple[str, str], tuple[str, int, str]] = {}
        for f in chain:
            code = sql_code(project.read(f.rel) or "")
            events = [(m.start(), "create", m) for m in DB_OBJECT.finditer(code)]
            events += [(m.start(), "drop", m) for m in DROP_OBJECT.finditer(code)]
            for offset, what, m in sorted(events, key=lambda e: e[0]):
                key = (m.group("kind").upper(), ident(m.group("name")).rpartition(".")[2])
                if what == "create":
                    alive[key] = (f.rel, line_of(code, offset), m.group("name"))
                else:
                    alive.pop(key, None)
        for (kind, _), (rel, line, name) in sorted(alive.items(), key=lambda e: (e[1][0], e[1][1])):
            yield Violation(rel, line, 1, f"CREATE {kind} {name} is left in the schema; logic happens in the code")
    known = mixins(project)
    targets = [(t.file, t.node) for t in om_tables(project)] + [(m.file, m.node) for m in known.values()]
    for file, cls in targets:
        for node in ast.walk(cls):
            if isinstance(node, ast.Call) and call_name(node) == "Computed":
                yield Violation.at(file.rel, node, f"{cls.name} declares a computed column; the code computes the value")
        for name, decl in own_columns(cls).items():
            if name not in DB_TIMESTAMPS:
                continue
            for call in column_calls_of(decl):
                for setter in DB_SET:
                    if kwarg(call, setter) is not None:
                        yield Violation.at(
                            file.rel, call, f"{cls.name}.{name} has `{setter}=`; the manager's copy sets it, not the database"
                        )


# --- STO-06

ID_DEFAULTS = ("server_default", "default", "server_onupdate")


@rule(
    "STO-06",
    coverage="partial",
    summary="The id column has no database default, sequence or identity; no create or write returns a UUID.",
)
def ids_come_from_above(project: Project) -> Iterator[Violation]:
    known = mixins(project)
    targets = [(t.file, t.node) for t in om_tables(project)] + [(m.file, m.node) for m in known.values()]
    for file, cls in targets:
        decl = own_columns(cls).get("id")
        if decl is None:
            continue
        for call in column_calls_of(decl):
            bad = [k for k in ID_DEFAULTS if kwarg(call, k) is not None]
            auto = kwarg(call, "autoincrement")
            if auto is not None and not (isinstance(auto, ast.Constant) and auto.value is False):
                bad.append("autoincrement")
            bad += [n for n in ("Identity", "Sequence") if any(call_name(a) == n for a in call.args)]
            for b in bad:
                yield Violation.at(file.rel, call, f"{cls.name}.id has {b}; an id is minted above storage with new_id()")
    for file, cls in storage_interfaces(project):
        for fn in public_methods(cls):
            if fn.name.split("_")[0] in ("create", "write", "insert") and fn.returns is not None:
                ret = ast.unparse(fn.returns).replace(" ", "")
                if ret in ("UUID", "uuid.UUID", "UUID|None", "Optional[UUID]"):
                    yield Violation.at(
                        file.rel, fn, f"{cls.name}.{fn.name} returns a UUID; the caller already holds the id it passed"
                    )


# --- STO-08

DRIVERS = ["sqlalchemy", "asyncpg", "psycopg", "psycopg2", "sqlmodel", "alembic", "pymongo", "motor", "aiosqlite", "sqlite3"]


@rule(
    "STO-08",
    coverage="partial",
    summary="Types, storage interfaces, manager interfaces and manager impls import no ORM or database driver.",
)
def storage_interfaces_are_technology_free(project: Project) -> Iterator[Violation]:
    """Reads `packages` under `[tool.arch-check.options.STO-08]`: the ORM and driver packages, a list of names."""
    packages = project.option("STO-08", "packages", DRIVERS, {"packages"})
    om = project.sub("om")
    for file in project.modules_under(om):
        ns = namespace_of(project, file.module)
        root = f"{om}.{ns}" if ns else None
        judged = file.module == f"{om}.storage.root" or (
            root is not None
            and (
                file.module in (root, f"{root}.storage", f"{root}.manager")
                or is_under(file.module, f"{root}.types")
                or is_under(file.module, f"{root}.impl")
            )
        )
        if not judged:
            continue
        for imp in project.imports(file):
            hit = next((p for p in packages if is_under(imp.module, p)), None)
            if hit is not None:
                yield Violation.at(
                    file.rel, imp.node, f"{file.module} imports {imp.module}; only a storage impl names the technology"
                )


# --- STO-09


@rule(
    "STO-09",
    coverage="full",
    summary="Each namespace storage has its interface, impl/ and tables/; tables live only in storage/tables.",
)
def storage_namespace_shape(project: Project) -> Iterator[Violation]:
    om = project.sub("om")
    for t in tables(project):
        if not in_tables(project, t.file.module):
            yield Violation.at(t.file.rel, t.node, f"table class {t.node.name} lives outside storage/tables/")
    for file, tree in project.trees(om):
        parts = file.module.split(".")
        if "impl" in parts or "types" in parts:
            for cls in classes(tree):
                if cls.name.endswith("StorageInterface"):
                    yield Violation.at(
                        file.rel, cls, f"{cls.name} is defined in {file.module}; the interface is storage/__init__.py"
                    )
    for file in project.modules_under(om):
        ns = namespace_of(project, file.module)
        if ns is None or file.module != f"{om}.{ns}.storage":
            continue
        package = project.tree(file)
        if package is None:
            continue
        defined = any(c.name.endswith("StorageInterface") for c in classes(package))
        exported = any(n.endswith("StorageInterface") for i in project.imports(file) for n in i.names)
        if not (defined or exported):
            yield Violation.at(file.rel, None, f"{file.module} defines no *StorageInterface; the interface lives here")
        for sub in ("impl", "tables"):
            if not project.modules_under(f"{file.module}.{sub}"):
                yield Violation.at(file.rel, None, f"{file.module} has no {sub}/ package")


# --- STO-10

STORAGE_ROOT_IMPL = re.compile(r"^Storage[A-Z]\w*Impl$")


@rule(
    "STO-10",
    coverage="partial",
    summary="StorageInterface has a getter per namespace storage, healthcheck and close; two Storage<Tech>Impl roots define all.",
)
def one_storage_root(project: Project) -> Iterator[Violation]:
    root = project.module(f"{project.sub('om')}.storage.root")
    tree = project.tree(root) if root else None
    if root is None or tree is None:
        return
    iface = next((c for c in classes(tree) if c.name == "StorageInterface"), None)
    if iface is None:
        yield Violation.at(root.rel, None, "the storage root module declares no StorageInterface")
        return
    declared = {m.name: m for m in public_methods(iface)}
    returned = {last(dotted(m.returns)) for m in declared.values()}
    for file, cls in storage_interfaces(project):
        if namespace_of(project, file.module) and file.module.endswith(".storage") and cls.name not in returned:
            yield Violation.at(root.rel, iface, f"StorageInterface has no getter returning {cls.name}")
    for name in ("healthcheck", "close"):
        if name not in declared:
            yield Violation.at(root.rel, iface, f"StorageInterface declares no {name}()")
    impls: list[tuple[SourceFile, ast.ClassDef]] = []
    for file, t in project.trees(project.sub("om")):
        impls += [(file, c) for c in classes(t) if "StorageInterface" in {last(b) for b in base_names(c)}]
    for file, cls in impls:
        if not STORAGE_ROOT_IMPL.match(cls.name):
            yield Violation.at(file.rel, cls, f"storage root {cls.name} is not named Storage<Tech>Impl")
        own = {m.name for m in public_methods(cls)}
        for name in sorted(set(declared) - own):
            yield Violation.at(file.rel, cls, f"{cls.name} does not define {name}()")
    if impls and (len(impls) < 2 or "StorageMemoryImpl" not in {c.name for _, c in impls}):
        yield Violation.at(root.rel, iface, "StorageInterface needs two roots, one of them StorageMemoryImpl")


# --- STO-11


@rule(
    "STO-11",
    coverage="partial",
    summary="Table mixins in house order with the base last; no mixin column redeclared; no org_id on a global table.",
)
def table_mixins(project: Project) -> Iterator[Violation]:
    """Reads `base` under `[tool.arch-check.options.STO-11]`: the declarative base's class name, `Base` by default."""
    base = project.option("STO-11", "base", "Base", {"base"})
    known = mixins(project)
    for t in om_tables(project):
        cls = t.node
        bases = [last(b) or "" for b in base_names(cls)]
        if base in bases and bases[-1] != base:
            yield Violation.at(t.file.rel, cls, f"{cls.name} lists {base} before its mixins; the base goes last")
        ranked = [(b, MIXIN_RANK[b]) for b in bases if b in MIXIN_RANK]
        for (a, ra), (b, rb) in itertools.pairwise(ranked):
            if rb < ra:
                yield Violation.at(
                    t.file.rel, cls, f"{cls.name} lists {a} before {b}; the house order is identity, name, lifecycle, soft delete"
                )
                break
        inherited = mixin_columns(cls, known)
        for name, decl in own_columns(cls).items():
            if name in inherited:
                yield Violation.at(t.file.rel, decl, f"{cls.name} redeclares {name}, which {inherited[name]} brings")
        global_ids = [m.name for m in composed(cls, known) if "id" in m.columns and "org_id" not in m.columns]
        if global_ids and "org_id" not in inherited and "org_id" in own_columns(cls):
            yield Violation.at(t.file.rel, own_columns(cls)["org_id"], f"{cls.name} composes {global_ids[0]} and declares org_id")


# --- STO-12


@rule(
    "STO-12",
    coverage="partial",
    summary="No storage impl method returns a table class, no interface signature names one, and no table class is frozen.",
)
def rows_never_leave(project: Project) -> Iterator[Violation]:
    found = tables(project, project.sub("om"))
    names = {t.node.name for t in found}
    for t in found:
        if is_true(keywords(t.node).get("frozen")):
            yield Violation.at(t.file.rel, t.node, f"table class {t.node.name} is frozen; rows are mutable by design")
    if not names:
        return
    for file, tree in project.trees(project.sub("om")):
        impl = in_storage_impl(project, file.module)
        for cls in classes(tree):
            interface = cls.name.endswith("Interface")
            if not (impl or interface):
                continue
            for fn in public_methods(cls):
                hit = names_in(fn.returns) & names
                if hit:
                    yield Violation.at(
                        file.rel, fn, f"{cls.name}.{fn.name} returns {sorted(hit)[0]}; a row never leaves the impl"
                    )
                if interface:
                    for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
                        hit = names_in(arg.annotation) & names
                        if hit:
                            yield Violation.at(
                                file.rel, arg, f"{cls.name}.{fn.name} takes {sorted(hit)[0]}; an interface names no row"
                            )


# --- STO-13


@rule(
    "STO-13",
    coverage="partial",
    summary="Every mixin column has a negative sort_order in house-order bands; a concrete table sets none.",
)
def column_order(project: Project) -> Iterator[Violation]:
    known = mixins(project)
    bands: dict[int, list[tuple[int, str, SourceFile, ast.AST]]] = {}
    for m in known.values():
        for name, decl in m.own.items():
            calls = column_calls_of(decl)
            orders = [negative_int(kwarg(c, "sort_order")) for c in calls]
            order = next((o for o in orders if o is not None), None)
            if order is None:
                yield Violation.at(m.file.rel, decl, f"{m.name}.{name} has no negative sort_order; mixin columns lead the table")
            elif m.name in MIXIN_RANK:
                bands.setdefault(MIXIN_RANK[m.name], []).append((order, f"{m.name}.{name}", m.file, decl))
    ranks = sorted(bands)
    for r, s in itertools.pairwise(ranks):
        top = max(bands[r], key=lambda e: e[0])
        for order, where, file, decl in bands[s]:
            if order <= top[0]:
                yield Violation.at(
                    file.rel, decl, f"{where} sorts at {order}, before {top[1]} at {top[0]}; bands follow the house order"
                )
    for t in om_tables(project):
        for node in ast.walk(t.node):
            if (
                isinstance(node, ast.Call)
                and call_name(node) in ("mapped_column", "Column")
                and negative_int(kwarg(node, "sort_order")) is not None
            ):
                yield Violation.at(t.file.rel, node, f"{t.node.name} sets a negative sort_order; only mixin columns lead")


# --- STO-14

DESC_ID = re.compile(r"\bid\s+DESC\b", re.IGNORECASE)


def org_id_indexed(cls: ast.ClassDef, known: dict) -> bool | None:
    """Whether the identity mixin a table composes indexes `org_id` on its own; None when it cannot be read."""
    for m in composed(cls, known):
        decl = m.columns.get("org_id")
        if decl is None:
            continue
        calls = column_calls_of(decl)
        if not calls:
            return False
        index = kwarg(calls[0], "index")
        if index is None or (isinstance(index, ast.Constant) and index.value is False):
            return False
        if is_true(index):
            return True
        if isinstance(index, ast.Attribute):
            own = class_value(cls, index.attr)
            if own is not None:
                return not (isinstance(own, ast.Constant) and own.value is False)
            owner = next((x for x in composed(cls, known) if class_value(x.node, index.attr) is not None), None)
            default = class_value(owner.node, index.attr) if owner else None
            return is_true(default) if default is not None else None
        return None
    return None


@rule(
    "STO-14",
    coverage="partial",
    summary="No single org_id index beside a compound index it leads, and no descending index on id.",
)
def index_rules(project: Project) -> Iterator[Violation]:
    known = mixins(project)
    for t in om_tables(project):
        indexes = index_calls(t.node)
        compound = [i for i in indexes if len(string_args(i)) > 1 and string_args(i)[0] == "org_id"]
        single = [i for i in indexes if string_args(i) == ["org_id"]]
        if compound and (single or org_id_indexed(t.node, known)):
            yield Violation.at(t.file.rel, t.node, f"{t.node.name} indexes org_id alone and leads a compound index with it")
        for i in indexes:
            for node in ast.walk(i):
                desc = (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "desc"
                    and last(dotted(node.func.value)) == "id"
                ) or (isinstance(node, ast.Constant) and isinstance(node.value, str) and DESC_ID.search(node.value))
                if desc:
                    yield Violation.at(t.file.rel, i, f"{t.node.name} declares a descending index on id; v7 ids scan backwards")
                    break


# --- STO-17


@rule(
    "STO-17",
    coverage="partial",
    summary="The role map and the table classes match; no table declares a schema; no foreign key crosses a role.",
)
def one_role_per_table(project: Project) -> Iterator[Violation]:
    """Reads `map` under `[tool.arch-check.options.STO-17]`: the name of the table-to-role map, `TABLE_ROLES` by default."""
    name = project.option("STO-17", "map", ROLE_MAP, {"map"})
    found = om_tables(project)
    for t in found:
        for arg in table_args(t.node):
            if isinstance(arg, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "schema" for k in arg.keys):
                yield Violation.at(t.file.rel, arg, f"{t.node.name} declares its schema; the role map derives it")
    read = roles_of(project, name)
    if read is None:
        return
    rmap, roles = read
    names = {t.name for t in found}
    keys = {e.table for e in rmap.entries}
    for t in found:
        if t.name and t.name not in keys:
            yield Violation.at(t.file.rel, t.node, f"table {t.name} is missing from {name}")
    for e in rmap.entries:
        if e.table not in names:
            yield Violation.at(rmap.file.rel, e.key, f"{name} names {e.table}, which no table class declares")
    by_class = {t.node.name: t.name for t in found}
    values = set(roles.values())
    for t in found:
        mine = roles.get(t.name)
        if mine is None:
            continue
        for node in ast.walk(t.node):
            if not (isinstance(node, ast.Call) and call_name(node) == "ForeignKey" and node.args):
                continue
            target = node.args[0]
            other: str | None = None
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                parts = target.value.split(".")
                if len(parts) >= 2:
                    other = roles.get(parts[-2])
                if other is None and len(parts) == 3 and parts[0] in values:
                    other = parts[0]
            elif isinstance(target, ast.Attribute):
                owner = last(dotted(target.value))
                other = roles.get(by_class.get(owner or "", ""))
            if other is not None and other != mine:
                yield Violation.at(t.file.rel, node, f"{t.node.name} has a foreign key into role {other}; it is in {mine}")


# --- STO-18


def wrapper_call(fn: ast.FunctionDef) -> ast.Call | None:
    body = [s for s in fn.body if not is_docstring(s)]
    if len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Call):
        return body[0].value
    return None


@rule(
    "STO-18",
    coverage="partial",
    summary="Migrations are named up and down SQL pairs per role, each wrapper one run_sql call, each file naming only its role.",
)
def migration_layout(project: Project) -> Iterator[Violation]:
    """Reads `[tool.arch-check.options.STO-18]`: `sql_dir` and `versions_dir`, the two migration folders
    (`om/migrations/sql` and `om/migrations/versions`); `runner`, the function a wrapper calls (`run_sql`);
    and `map`, the table-to-role map (`TABLE_ROLES`)."""
    keys = {"sql_dir", "versions_dir", "runner", "map"}
    sql_dir = project.option("STO-18", "sql_dir", SQL_DIR, keys)
    versions_dir = project.option("STO-18", "versions_dir", VERSIONS_DIR, keys)
    runner = project.option("STO-18", "runner", "run_sql", keys)
    name = project.option("STO-18", "map", ROLE_MAP, keys)
    for rel in project.files("services/**/migrations/**/*", "workers/**/migrations/**/*"):
        yield Violation(rel, 1, 1, "a migration outside the OM; migrations live with the OM")
    roles = role_names(project, name)
    good, bad = sql_files(project, sql_dir)
    for rel in bad:
        yield Violation(rel, 1, 1, "a migration file not named <YYYYMMDDHHMM>_<slug>.up.sql or .down.sql")
    pairs: dict[tuple[str, str], set[str]] = {}
    for f in good:
        pairs.setdefault((f.role, f"{f.stamp}_{f.slug}"), set()).add(f.direction)
    for (role, stem), dirs in sorted(pairs.items()):
        for missing in sorted({"up", "down"} - dirs):
            have = next(iter(dirs))
            yield Violation(f"{sql_dir}/{role}/{stem}.{have}.sql", 1, 1, f"{stem} has no .{missing}.sql")
        if not project.read(f"{versions_dir}/{role}/{stem}.py"):
            yield Violation(
                f"{sql_dir}/{role}/{stem}.{min(dirs)}.sql", 1, 1, f"{stem} has no wrapper under {versions_dir}/{role}/"
            )
    if roles:
        seen_roles = {rel.split("/")[-2] for rel in project.files(f"{sql_dir}/*/*", f"{versions_dir}/*/*.py")}
        for role in sorted(seen_roles - roles):
            yield Violation(f"{sql_dir}/{role}", 1, 1, f"{role} is not a role of {name}")
        pattern = re.compile(r"\b(" + "|".join(re.escape(r) for r in sorted(roles)) + r")\s*\.\s*\"?([A-Za-z_]\w*)")
        for f in good:
            code = sql_code(project.read(f.rel) or "")
            for ref in pattern.finditer(code):
                if ref.group(1) != f.role:
                    yield Violation(
                        f.rel,
                        line_of(code, ref.start()),
                        1,
                        f"{ref.group(1)}.{ref.group(2)} is in role {ref.group(1)}; this file is {f.role}'s",
                    )
    enums = enum_values(project)
    for rel in project.files(f"{versions_dir}/*/*.py"):
        role, fname = rel.split("/")[-2:]
        if fname.startswith("_"):
            continue
        m = WRAPPER_FILE.match(fname)
        if not m:
            yield Violation(rel, 1, 1, "a wrapper not named <YYYYMMDDHHMM>_<slug>.py")
            continue
        try:
            tree = ast.parse(project.read(rel) or "", filename=rel)
        except SyntaxError as e:
            yield Violation(rel, e.lineno or 1, 1, f"the wrapper does not parse: {e.msg}")
            continue
        stem = fname[:-3]
        fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for fn_name, direction in (("upgrade", "up"), ("downgrade", "down")):
            fn = fns.get(fn_name)
            if fn is None:
                yield Violation(rel, 1, 1, f"the wrapper has no {fn_name}()")
                continue
            call = wrapper_call(fn)
            if call is None or call_name(call) != runner:
                yield Violation.at(rel, fn, f"{fn_name}() is not one {runner}() call; the SQL file holds the migration")
                continue
            want = f"{stem}.{direction}.sql"
            if len(call.args) != 2:
                yield Violation.at(rel, call, f"{runner}() takes the role and {want}")
                continue
            named = role_value(call.args[0], enums)
            if named is not None and named != role:
                yield Violation.at(rel, call, f"{fn_name}() runs role {named}; the wrapper is in {role}/")
            arg = call.args[1]
            if not (isinstance(arg, ast.Constant) and arg.value == want):
                yield Violation.at(rel, call, f"{fn_name}() runs {ast.unparse(arg)}; it runs {want}")


# --- STO-20


def single_row(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name | ast.Attribute):
        return last(dotted(node)) == "OutboxRow"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return single_row(node.left) or single_row(node.right)
    if isinstance(node, ast.Subscript) and last(dotted(node.value)) == "Optional":
        return single_row(node.slice)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return single_row(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return False
    return False


@rule(
    "STO-20",
    coverage="partial",
    summary="No storage interface parameter takes a single OutboxRow; outbox rows travel as a tuple.",
)
def outbox_rows_are_a_tuple(project: Project) -> Iterator[Violation]:
    for file, cls in storage_interfaces(project):
        for fn in public_methods(cls):
            for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
                if single_row(arg.annotation):
                    yield Violation.at(
                        file.rel, arg, f"{cls.name}.{fn.name} takes one OutboxRow; take outbox_rows: tuple[OutboxRow, ...]"
                    )


# --- STO-23


@rule(
    "STO-23",
    coverage="partial",
    summary="Each wrapper's revision is its file's stamp, unique in its role, and each role is one linear chain.",
)
def one_chain_per_role(project: Project) -> Iterator[Violation]:
    """Reads `versions_dir` under `[tool.arch-check.options.STO-23]`: the wrappers' folder, `om/migrations/versions`."""
    versions_dir = project.option("STO-23", "versions_dir", VERSIONS_DIR, {"versions_dir"})
    chains: dict[str, dict[str, tuple[str, object, ast.AST | None]]] = {}
    for rel in project.files(f"{versions_dir}/*/*.py"):
        role, fname = rel.split("/")[-2:]
        m = WRAPPER_FILE.match(fname)
        if not m:
            continue
        try:
            tree = ast.parse(project.read(rel) or "", filename=rel)
        except SyntaxError:
            continue
        rev_node = module_value(tree, "revision")
        down_node = module_value(tree, "down_revision")
        revision = rev_node.value if isinstance(rev_node, ast.Constant) and isinstance(rev_node.value, str) else None
        if revision is None or revision != m.group("stamp"):
            yield Violation.at(rel, rev_node, f"revision is {revision!r}; it is the file's stamp {m.group('stamp')!r}")
            continue
        down: object = down_node.value if isinstance(down_node, ast.Constant) else ast.unparse(down_node) if down_node else None
        if isinstance(down_node, ast.Tuple | ast.List):
            yield Violation.at(rel, down_node, "down_revision names two parents; each role is one linear chain")
        chain = chains.setdefault(role, {})
        if revision in chain:
            yield Violation(rel, 1, 1, f"revision {revision} is already {chain[revision][0]}")
            continue
        chain[revision] = (rel, down, down_node)
    for role, chain in sorted(chains.items()):
        parents: dict[object, str] = {}
        for revision, (rel, down, node) in sorted(chain.items()):
            if down is not None and down not in chain:
                yield Violation.at(rel, node, f"down_revision {down!r} is no revision of role {role}")
            if down in parents:
                yield Violation.at(rel, node, f"{revision} and {parents[down]} both follow {down!r}; the chain has two heads")
            else:
                parents[down] = revision
        roots = [r for r, (_, d, _) in chain.items() if d is None]
        if len(roots) > 1:
            rel = chain[sorted(roots)[1]][0]
            yield Violation(rel, 1, 1, f"role {role} has {len(roots)} first migrations; it has one chain")


# --- STO-26


def living_only(where: ast.AST | None) -> bool:
    if where is None:
        return False
    text = ast.unparse(where)
    return "deleted_at" in text and bool(re.search(r"IS\s+NULL|is_\(None\)|== *None|is None", text, re.IGNORECASE))


@rule(
    "STO-26",
    coverage="partial",
    summary="Every unique index on a soft-deletable table is partial on deleted_at IS NULL.",
)
def unique_among_the_living(project: Project) -> Iterator[Violation]:
    known = mixins(project)
    for t in om_tables(project):
        if "deleted_at" not in mixin_columns(t.node, known):
            continue
        for arg in table_args(t.node):
            if not isinstance(arg, ast.Call):
                continue
            name = call_name(arg)
            unique = name == "UniqueConstraint" or (name == "Index" and is_true(kwarg(arg, "unique")))
            if unique and not living_only(kwarg(arg, "postgresql_where") or kwarg(arg, "sqlite_where")):
                yield Violation.at(
                    t.file.rel,
                    arg,
                    f"{t.node.name} is soft-deletable and this unique key holds the dead too; add WHERE deleted_at IS NULL",
                )
        for col, decl in own_columns(t.node).items():
            if isinstance(decl, ast.stmt):
                call = column_call(decl)
                if call is not None and is_true(kwarg(call, "unique")):
                    yield Violation.at(
                        t.file.rel,
                        call,
                        f"{t.node.name}.{col} is unique=True on a soft-deletable table; use a partial unique index",
                    )


# --- STO-27

ENGINE_CALLS = frozenset({"create_async_engine", "create_engine"})


@rule(
    "STO-27",
    coverage="partial",
    summary="Every engine a storage impl builds passes pool_size and pool_timeout, neither a literal.",
)
def pools_declare_their_bounds(project: Project) -> Iterator[Violation]:
    for file, tree in project.trees(project.sub("om")):
        if not in_storage_impl(project, file.module):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and call_name(node) in ENGINE_CALLS):
                continue
            if any(k.arg is None for k in node.keywords):
                continue
            for key in ("pool_size", "pool_timeout"):
                value = kwarg(node, key)
                if value is None:
                    yield Violation.at(file.rel, node, f"the engine is built without {key}; the pool declares it from settings")
                elif isinstance(value, ast.Constant):
                    yield Violation.at(file.rel, value, f"{key} is hard-coded; it comes from settings")


# --- STO-28

RLS = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?P<table>[\w.\"]+)\s+"
    r"(?P<what>ENABLE|DISABLE|NO\s+FORCE|FORCE)\s+ROW\s+LEVEL\s+SECURITY",
    re.IGNORECASE,
)
CREATE_POLICY = re.compile(r"\bCREATE\s+POLICY\s+(?P<name>[\w\"]+)\s+ON\s+(?:ONLY\s+)?(?P<table>[\w.\"]+)", re.IGNORECASE)
DROP_POLICY = re.compile(
    r"\bDROP\s+POLICY\s+(?:IF\s+EXISTS\s+)?(?P<name>[\w\"]+)\s+ON\s+(?:ONLY\s+)?(?P<table>[\w.\"]+)", re.IGNORECASE
)
DROP_TABLE = re.compile(r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<table>[\w.\"]+)", re.IGNORECASE)


def is_system(value: ast.AST) -> bool:
    for node in ast.walk(value):
        if isinstance(node, ast.Attribute) and node.attr.lower() == "system":
            return True
        if isinstance(node, ast.Constant) and node.value == "system":
            return True
    return False


@rule(
    "STO-28",
    coverage="partial",
    summary="The scope map matches the role map; the chain leaves every tenant table with RLS enabled, forced and a policy.",
)
def scopes_match_policies(project: Project) -> Iterator[Violation]:
    """Reads `[tool.arch-check.options.STO-28]`: `scopes` and `roles`, the names of the two maps
    (`TABLE_SCOPES` and `TABLE_ROLES`), and `sql_dir`, the SQL folder (`om/migrations/sql`)."""
    keys = {"scopes", "roles", "sql_dir"}
    scopes_name = project.option("STO-28", "scopes", SCOPE_MAP, keys)
    roles_name = project.option("STO-28", "roles", ROLE_MAP, keys)
    sql_dir = project.option("STO-28", "sql_dir", SQL_DIR, keys)
    smap = table_map(project, scopes_name)
    read = roles_of(project, roles_name)
    if smap is None or read is None:
        return
    rmap, roles = read
    rkeys = {e.table for e in rmap.entries}
    skeys = {e.table for e in smap.entries}
    for e in smap.entries:
        if e.table not in rkeys:
            yield Violation.at(smap.file.rel, e.key, f"{scopes_name} names {e.table}, which {roles_name} does not")
    for e in rmap.entries:
        if e.table not in skeys:
            yield Violation.at(rmap.file.rel, e.key, f"{e.table} has no scope in {scopes_name}")
    chains = up_chain(project, sql_dir)
    if not chains:
        return
    enabled: dict[str, bool] = {}
    forced: dict[str, bool] = {}
    policies: dict[str, set[str]] = {}
    for chain in chains.values():
        for f in chain:
            code = sql_code(project.read(f.rel) or "")
            events: list[tuple[int, str, re.Match[str]]] = []
            for kind, pattern in (("rls", RLS), ("create", CREATE_POLICY), ("drop", DROP_POLICY), ("table", DROP_TABLE)):
                events += [(m.start(), kind, m) for m in pattern.finditer(code)]
            for _, kind, m in sorted(events, key=lambda e: e[0]):
                table = ident(m.group("table"))
                if kind == "rls":
                    what = re.sub(r"\s+", " ", m.group("what").upper())
                    if what in ("ENABLE", "DISABLE"):
                        enabled[table] = what == "ENABLE"
                    else:
                        forced[table] = what == "FORCE"
                elif kind == "create":
                    policies.setdefault(table, set()).add(ident(m.group("name")))
                elif kind == "drop":
                    policies.get(table, set()).discard(ident(m.group("name")))
                else:
                    enabled.pop(table, None)
                    forced.pop(table, None)
                    policies.pop(table, None)
    for e in smap.entries:
        role = roles.get(e.table)
        if role is None or role not in chains:
            continue
        qualified = f"{role}.{e.table}"
        if is_system(e.value):
            if enabled.get(qualified):
                yield Violation.at(
                    smap.file.rel, e.key, f"{qualified} is a system table and the chain enables row-level security on it"
                )
            continue
        missing = [
            what
            for what, ok in (
                ("ENABLE ROW LEVEL SECURITY", enabled.get(qualified)),
                ("FORCE ROW LEVEL SECURITY", forced.get(qualified)),
                ("a policy", policies.get(qualified)),
            )
            if not ok
        ]
        if missing:
            yield Violation.at(
                smap.file.rel, e.key, f"{qualified} is a tenant table and the chain leaves it without {', '.join(missing)}"
            )


# --- STO-29

LIST_TYPES = frozenset({"list", "List", "Sequence", "tuple", "Tuple"})


def returns_many(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    node = fn.returns
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return False
    if not isinstance(node, ast.Subscript) or last(dotted(node.value)) not in LIST_TYPES:
        return False
    if last(dotted(node.value)) in ("tuple", "Tuple"):
        s = node.slice
        return (
            isinstance(s, ast.Tuple) and len(s.elts) == 2 and isinstance(s.elts[1], ast.Constant) and s.elts[1].value is Ellipsis
        )
    return True


@rule(
    "STO-29",
    coverage="partial",
    summary="Every storage interface method and bucket listing that returns a list takes a limit.",
)
def list_reads_take_a_limit(project: Project) -> Iterator[Violation]:
    found = list(storage_interfaces(project))
    for file, tree in project.trees(project.sub("infra.buckets")):
        found += [(file, c) for c in classes(tree) if c.name.endswith("Interface")]
    for file, cls in found:
        for fn in public_methods(cls):
            judged = cls.name.endswith("StorageInterface") or fn.name == "list"
            if judged and returns_many(fn) and "limit" not in {p.name for p in parameters(fn)}:
                yield Violation.at(file.rel, fn, f"{cls.name}.{fn.name} returns a list and takes no limit")
