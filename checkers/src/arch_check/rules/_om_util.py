"""What the `om` rules share: the OM base chain, found by reading source.

The root of the object model is a class of `<pkg>.om.base` that
subclasses pydantic's `BaseModel` directly (`Platform` in the
guideline). A class is on the chain when one of its bases resolves to
the root or to a class on the chain. A base name resolves the way
Python binds it: a class of the same module, or a name an import binds,
followed through re-exports (`from .order import Order` in a package
`__init__`) to the module that defines it. A base that resolves to
nothing in the project (pydantic, the standard library) is not on the
chain.

The index is built once per project and shared by every rule.
"""

from __future__ import annotations

import ast
import weakref
from collections.abc import Iterator
from dataclasses import dataclass

from arch_check.project import Project, SourceFile, dotted, is_under, last

Key = tuple[str, str]
"""A class by (module, name)."""

MIXIN_ORDER: dict[str, int] = {"Identifiable": 0, "Named": 1, "Created": 2, "Trackable": 2, "SoftDeletable": 3}
"""The guideline's mixins and their place in a base list: identity, label, lifecycle, then cross-cutting."""

MIXIN_FIELDS: dict[str, frozenset[str]] = {
    "Identifiable": frozenset({"id"}),
    "Named": frozenset({"name"}),
    "Created": frozenset({"created_at"}),
    "Trackable": frozenset({"updated_at", "created_by", "updated_by"}),
    "SoftDeletable": frozenset({"deleted_at", "deleted_by"}),
}
"""The fields each mixin declares in its own body; `Trackable` adds its own to `Created`'s."""

VALIDATORS = frozenset({"field_validator", "model_validator", "field_serializer", "model_serializer", "validator"})
"""Pydantic decorators that shape a field rather than share code."""


def nested_classes(tree: ast.Module) -> Iterator[tuple[str, ast.ClassDef]]:
    """Every class of a module with its qualified name: a class under a top-level `if` or `try` is named alone,
    one in a class body or a function after it (`Views.Summary`, `build.Row`)."""
    todo: list[tuple[ast.AST, str]] = [(tree, "")]
    while todo:
        node, scope = todo.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qualname = f"{scope}.{child.name}" if scope else child.name
                yield qualname, child
                todo.append((child, qualname))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                todo.append((child, f"{scope}.{child.name}" if scope else child.name))
            else:
                todo.append((child, scope))


@dataclass(frozen=True)
class ClassInfo:
    key: Key
    file: SourceFile
    node: ast.ClassDef


class Index:
    """Every class of the project by its qualified name, how each module binds names, and the OM chain."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.classes: dict[Key, ClassInfo] = {}
        self.bindings: dict[str, dict[str, str]] = {}
        # A module to the modules it star-imports from.
        self.stars: dict[str, list[str]] = {}
        for file, tree in project.trees():
            names: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.asname:
                            names[a.asname] = a.name
                        else:
                            head = a.name.split(".")[0]
                            names.setdefault(head, head)
                elif isinstance(node, ast.ImportFrom):
                    base = project.resolve(file, node.level, node.module) if node.level else node.module or ""
                    for a in node.names:
                        if a.name != "*":
                            names[a.asname or a.name] = f"{base}.{a.name}" if base else a.name
                        elif base:
                            self.stars.setdefault(file.module, []).append(base)
            self.bindings[file.module] = names
            for qualname, cls in sorted(nested_classes(tree), key=lambda e: (e[1].lineno, e[1].col_offset)):
                key = (file.module, qualname)
                self.classes.setdefault(key, ClassInfo(key, file, cls))
        self.base_module = project.sub("om.base")
        self.roots: set[Key] = {
            info.key
            for info in self.classes.values()
            if info.file.module == self.base_module
            and any(last(self.qualified(info.file.module, dotted(b))) == "BaseModel" for b in info.node.bases)
        }
        self._chain: dict[Key, bool] = {}

    # --- names

    def qualified(self, module: str, name: str | None) -> str | None:
        """A dotted name written in `module` with its head read through the module's imports.

        `dt.now` after `from datetime import datetime as dt` gives
        `datetime.datetime.now`; `u.uuid4` after `import uuid as u` gives
        `uuid.uuid4`. A head no import binds is returned as written, so
        `slot.end_time.time` stays itself and never reads as `time.time`.
        """
        if not name:
            return None
        head, _, rest = name.partition(".")
        bound = self.bindings.get(module, {}).get(head, head)
        return f"{bound}.{rest}" if rest else bound

    def lookup(self, module: str, name: str, depth: int = 0) -> Key | None:
        """The class `name` means inside `module`, followed through imports; None when it is not a project class."""
        if depth > 20:
            return None
        if (module, name) in self.classes:
            return (module, name)
        target = self.bindings.get(module, {}).get(name)
        if target is None:
            # `from acme.om.base import *` binds every public name of the
            # base; a class reached that way is still on the chain.
            if name.startswith("_"):
                return None
            for source in self.stars.get(module, []):
                found = self.lookup(source, name, depth + 1)
                if found is not None:
                    return found
            return None
        return self.lookup_dotted(target, depth + 1)

    def lookup_dotted(self, full: str, depth: int = 0) -> Key | None:
        """The class an absolute dotted name means (`acme.om.orders.Order`), or None."""
        mod, _, name = full.rpartition(".")
        if not mod or self.project.module(mod) is None:
            return None
        return self.lookup(mod, name, depth)

    def resolve(self, module: str, name: str | None) -> Key | None:
        """The class a dotted name written in `module` means: `Order`, `types.Order`, `acme.om.base.Platform`."""
        if not name:
            return None
        head, _, rest = name.partition(".")
        if not rest:
            return self.lookup(module, head)
        bound = self.bindings.get(module, {}).get(head)
        if bound is None:
            return None
        return self.lookup_dotted(f"{bound}.{rest}")

    def bases(self, info: ClassInfo) -> list[tuple[ast.expr, Key | None]]:
        """Each base with the class it means: a sibling in the enclosing class body first, then the module's."""
        return [(b, self.resolve_in(info, dotted(b))) for b in info.node.bases]

    def resolve_in(self, info: ClassInfo, name: str | None) -> Key | None:
        """The class a base name means in the body a class is defined in: `class Outer: class A; class B(A)`."""
        module, qualname = info.key
        scope = qualname.rpartition(".")[0]
        while name and scope:
            if (module, f"{scope}.{name}") in self.classes:
                return (module, f"{scope}.{name}")
            scope = scope.rpartition(".")[0]
        return self.resolve(module, name)

    # --- the chain

    def on_chain(self, key: Key | None) -> bool:
        """Whether a class is the root or reaches it through its bases."""
        if key is None or key not in self.classes:
            return False
        if key in self.roots:
            return True
        if key in self._chain:
            return self._chain[key]
        self._chain[key] = False  # a cycle is not the chain
        found = any(self.on_chain(k) for _, k in self.bases(self.classes[key]))
        self._chain[key] = found
        return found

    def chain(self, *prefixes: str) -> Iterator[ClassInfo]:
        """Every class on the chain, the root included, defined under `prefixes` (all when none), in path order."""
        for info in sorted(self.classes.values(), key=lambda i: (i.file.rel, i.node.lineno)):
            if prefixes and not any(is_under(info.file.module, p) for p in prefixes):
                continue
            if self.on_chain(info.key):
                yield info

    def is_mixin(self, key: Key | None) -> bool:
        """A class of the base module on the chain that is not the root."""
        return key is not None and key[0] == self.base_module and key not in self.roots and self.on_chain(key)

    def ancestors(self, key: Key, seen: set[Key] | None = None) -> set[Key]:
        """A class and every project class its bases reach, itself included."""
        seen = set() if seen is None else seen
        if key in seen or key not in self.classes:
            return seen
        seen.add(key)
        for _, base in self.bases(self.classes[key]):
            if base is not None:
                self.ancestors(base, seen)
        return seen

    def lineage(self, module: str, node: ast.expr | None) -> set[Key]:
        """Every chain class an annotation names, quoted parts included, with all of their ancestors."""
        if node is None:
            return set()
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return set()
        out: set[Key] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name | ast.Attribute):
                key = self.resolve(module, dotted(sub))
                if key is not None and self.on_chain(key):
                    out |= self.ancestors(key)
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub is not node:
                out |= self.lineage(module, sub)
        return out

    def annotation_on_chain(self, module: str, node: ast.expr | None) -> bool:
        """Whether an annotation names a chain class anywhere in it: `Order`, `Order | None`, `"Order"`."""
        if node is None:
            return False
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name | ast.Attribute) and self.on_chain(self.resolve(module, dotted(sub))):
                return True
            if (
                isinstance(sub, ast.Constant)
                and isinstance(sub.value, str)
                and sub is not node
                and self.annotation_on_chain(module, sub)
            ):
                return True
        return False


_INDEXES: weakref.WeakKeyDictionary[Project, Index] = weakref.WeakKeyDictionary()


def index(project: Project) -> Index:
    """The project's chain index, built on first use."""
    found = _INDEXES.get(project)
    if found is None:
        found = _INDEXES[project] = Index(project)
    return found


# --- ast helpers the om rules share


def fields(cls: ast.ClassDef) -> list[ast.AnnAssign]:
    """The fields a class body declares: annotated names, `ClassVar` and `model_config` left out."""
    out = []
    for node in cls.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id == "model_config" or "ClassVar" in ast.unparse(node.annotation):
            continue
        out.append(node)
    return out


def field_name(node: ast.AnnAssign) -> str:
    assert isinstance(node.target, ast.Name)
    return node.target.id


def config_settings(cls: ast.ClassDef) -> list[tuple[ast.AST, str, ast.expr]]:
    """(node, key, value) of every model setting a class makes.

    `model_config = ConfigDict(...)` or a dict, the two joined with `|`
    (`Base.model_config | {"extra": "allow"}`), a nested `class Config:`
    whose assignments are settings, and class keywords.
    """
    out: list[tuple[ast.AST, str, ast.expr]] = []
    for kw in cls.keywords:
        if kw.arg and kw.arg != "metaclass":
            out.append((kw, kw.arg, kw.value))
    for node in cls.body:
        if isinstance(node, ast.ClassDef) and node.name == "Config":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and stmt.value is not None:
                    out.extend((t, t.id, stmt.value) for t in stmt.targets if isinstance(t, ast.Name))
                elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None and isinstance(stmt.target, ast.Name):
                    out.append((stmt.target, stmt.target.id, stmt.value))
            continue
        if not is_model_config(node):
            continue
        assert isinstance(node, ast.Assign | ast.AnnAssign)
        if node.value is not None:
            out.extend(_settings_in(node.value))
    return out


def _settings_in(value: ast.expr) -> list[tuple[ast.AST, str, ast.expr]]:
    """The settings one `model_config` value spells, through `|` on either side."""
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
        return _settings_in(value.left) + _settings_in(value.right)
    if isinstance(value, ast.Call):
        return [(k, k.arg, k.value) for k in value.keywords if k.arg]
    if isinstance(value, ast.Dict):
        return [
            (k, k.value, v)
            for k, v in zip(value.keys, value.values, strict=True)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
    return []


def is_model_config(node: ast.stmt) -> bool:
    """Whether a statement of a class body assigns `model_config`."""
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == "model_config" for t in node.targets)
    return isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "model_config"


def constant(node: ast.expr) -> object:
    """The value of a literal, an enum member's name (`Extra.forbid` gives `forbid`), else a marker no literal equals."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    return ...


def namespaces(project: Project, not_namespaces: list[str]) -> list[tuple[str, str]]:
    """(name, directory relative to the root) of every direct subpackage of `<pkg>.om` but the ones named."""
    om = project.sub("om")
    out = []
    for f in project.python_files:
        if f.is_package and f.module.rpartition(".")[0] == om:
            name = f.module.rpartition(".")[2]
            if name not in not_namespaces:
                out.append((name, f.rel.rpartition("/")[0]))
    return out
