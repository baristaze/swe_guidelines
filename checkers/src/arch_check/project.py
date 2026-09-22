"""The project under check, as a parsed tree a rule reads.

`Project` finds the Python files under the configured source roots,
names each one's module the way the import system would, and parses a
file with `ast` the first time a rule asks for it. It also reads any
other file of the repository (a Dockerfile, a workflow, a migration).
Nothing here imports the code it reads.

The module-level functions are the `ast` helpers rules share: dotted
names, classes and their bases, decorators, and function signatures.
"""

from __future__ import annotations

import ast
import contextlib
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TypeVar

from arch_check.config import Config, ConfigError, glob_match, relative_glob

T = TypeVar("T")

SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox"}
)
"""Directory names never walked into, at any depth."""

Function = ast.FunctionDef | ast.AsyncFunctionDef

MAX_DEPTH = 2500
"""The deepest syntax tree the rules walk. A rule walks a tree by
recursion (`ast.unparse`, a `NodeVisitor`), and the runner raises the
recursion limit to cover this depth; a file that nests deeper is a
`PARSE` finding, never a rule that fails on it."""


@dataclass(frozen=True)
class SourceFile:
    """A Python file under a source root.

    `rel` is its path from the repository root with `/` separators;
    `module` is the dotted name it imports as; `is_package` is true for
    an `__init__.py`.
    """

    path: Path
    rel: str
    module: str
    is_package: bool


@dataclass(frozen=True)
class Import:
    """One imported name, resolved to an absolute module path.

    `import a.b` gives module `a.b` and no names; `from a import b, c`
    gives module `a` and names `("b", "c")`. A relative import is
    resolved against the file's own package.
    """

    module: str
    names: tuple[str, ...]
    node: ast.Import | ast.ImportFrom

    @property
    def line(self) -> int:
        return self.node.lineno

    def targets(self) -> tuple[str, ...]:
        """Every module this import may bind: the module, and `module.name` for each name.

        A name after `from a import` may be a submodule or an attribute;
        both spellings are returned, so `from acme import services` is
        seen as an import of `acme.services`.
        """
        return (self.module, *(f"{self.module}.{n}" for n in self.names if n != "*"))


class Project:
    """The repository at `config.root`, read through `config`."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.root = config.root
        self.package = config.package
        self.parse_errors: dict[str, tuple[int, str]] = {}
        self._trees: dict[str, ast.Module | None] = {}
        self._imports: dict[str, list[Import]] = {}
        self._bound: dict[str, dict[str, str]] = {}
        self._owners: dict[int, SourceFile] = {}
        self.read_paths: set[str] = set()
        """Every file `read` returned, so the runner reads the inline ignores of the files the rules read."""

    # --- options

    def option(self, rule: str, key: str, default: T, allowed: Collection[str]) -> T:
        """The project's value of `key` under `[tool.arch-check.options.<rule>]`, else `default`.

        `allowed` is every key the rule reads; any other key under the rule's
        table is a `ConfigError`, so a misspelt option never silently falls
        back to the default. The value must have the default's type, and a
        table's entries are each a name or a non-empty list of names.
        """
        table = self.config.options.get(rule, {})
        unknown = sorted(set(table) - set(allowed))
        if unknown:
            raise ConfigError(f"[tool.arch-check.options.{rule}]: unknown key(s) {', '.join(unknown)}")
        if key not in table:
            return default
        value = table[key]
        if not isinstance(value, type(default)) or (isinstance(value, list) and not all(isinstance(v, str) for v in value)):
            raise ConfigError(f"[tool.arch-check.options.{rule}] `{key}` must be a {type(default).__name__}")
        if isinstance(value, dict):
            for name, item in value.items():
                if not names(item):
                    raise ConfigError(
                        f"[tool.arch-check.options.{rule}] `{key}`: {name} must be a name or a non-empty list of names"
                    )
        return value

    # --- names

    def sub(self, name: str) -> str:
        """A subpackage of the product: `sub("om")` is `acme.om`."""
        return f"{self.package}.{name}"

    def rel(self, path: Path) -> str:
        """A path under the root as the root spells it, `/`-separated.

        The lexical path comes first: a symlink inside the tree that
        points outside it is named where it sits, never where it points.
        """
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.resolve().relative_to(self.root).as_posix()

    def excluded(self, rel: str) -> bool:
        parts = rel.split("/")
        if any(part in SKIP_DIRS for part in parts[:-1]):
            return True
        return any(glob_match(pattern, rel) for pattern in self.config.exclude)

    # --- Python files

    @cached_property
    def src_roots(self) -> tuple[Path, ...]:
        """The source directories the `src` globs match, in path order."""
        found: set[Path] = set()
        for pattern in self.config.src:
            found.update(p for p in self.root.glob(pattern) if p.is_dir())
        return tuple(sorted(found))

    @cached_property
    def python_files(self) -> tuple[SourceFile, ...]:
        """Every `.py` file under a source root, excluded ones left out, in path order."""
        out: dict[str, SourceFile] = {}
        for src in self.src_roots:
            for path in sorted(src.rglob("*.py")):
                rel = self.rel(path)
                if rel in out or self.excluded(rel) or not path.is_file():
                    continue
                parts = list(path.relative_to(src).with_suffix("").parts)
                is_package = parts[-1] == "__init__"
                if is_package:
                    parts.pop()
                if not parts:
                    continue
                out[rel] = SourceFile(path=path, rel=rel, module=".".join(parts), is_package=is_package)
        return tuple(out[k] for k in sorted(out))

    @cached_property
    def by_module(self) -> dict[str, SourceFile]:
        """Module name to file; the first in path order wins when two roots ship one name."""
        out: dict[str, SourceFile] = {}
        for f in self.python_files:
            out.setdefault(f.module, f)
        return out

    def module(self, name: str) -> SourceFile | None:
        return self.by_module.get(name)

    def modules_under(self, *prefixes: str) -> list[SourceFile]:
        """The files whose module is one of `prefixes` or below one, in path order."""
        return [f for f in self.python_files if any(is_under(f.module, p) for p in prefixes)]

    def tree(self, file: SourceFile) -> ast.Module | None:
        """The parsed module, cached; None when it does not parse (see `parse_errors`)."""
        if file.rel not in self._trees:
            try:
                # bytes, so the parser honours a BOM or a PEP 263 coding line as the interpreter would
                source = file.path.read_bytes()
                self._trees[file.rel] = ast.parse(source, filename=file.rel)
            except SyntaxError as e:
                self._trees[file.rel] = None
                self.parse_errors[file.rel] = (e.lineno or 1, e.msg)
            except (OSError, ValueError, RecursionError, MemoryError) as e:
                # a source that exhausts the parser's stack is this file's defect, not a crash of the run
                self._trees[file.rel] = None
                self.parse_errors[file.rel] = (1, str(e) or type(e).__name__)
            else:
                tree = self._trees[file.rel]
                if tree is not None and depth(tree) > MAX_DEPTH:
                    self._trees[file.rel] = None
                    self.parse_errors[file.rel] = (1, f"nests deeper than the {MAX_DEPTH} levels arch-check walks")
                elif tree is not None:
                    self._owners.update((id(n), file) for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
        return self._trees[file.rel]

    def trees(self, *prefixes: str) -> Iterator[tuple[SourceFile, ast.Module]]:
        """(file, tree) for every file that parses, under `prefixes` when any are given."""
        files = self.modules_under(*prefixes) if prefixes else self.python_files
        for f in files:
            t = self.tree(f)
            if t is not None:
                yield f, t

    def parse_all(self) -> None:
        for f in self.python_files:
            self.tree(f)

    # --- imports

    def resolve(self, file: SourceFile, level: int, module: str | None) -> str:
        """The absolute module a relative import names, as the import system resolves it."""
        package = file.module if file.is_package else file.module.rpartition(".")[0]
        parts = package.split(".") if package else []
        if level > 1:
            parts = parts[: max(0, len(parts) - (level - 1))]
        base = ".".join(parts)
        if module:
            return f"{base}.{module}" if base else module
        return base

    def imports(self, file: SourceFile) -> list[Import]:
        """Every import statement of a file, anywhere in it, resolved to absolute names."""
        if file.rel in self._imports:
            return self._imports[file.rel]
        out: list[Import] = []
        t = self.tree(file)
        if t is not None:
            for node in ast.walk(t):
                if isinstance(node, ast.Import):
                    out.extend(Import(module=a.name, names=(), node=node) for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    target = self.resolve(file, node.level, node.module) if node.level else node.module or ""
                    out.append(Import(module=target, names=tuple(a.name for a in node.names), node=node))
        out.sort(key=lambda i: (i.node.lineno, i.node.col_offset))
        self._imports[file.rel] = out
        return out

    def bound_names(self, file: SourceFile) -> dict[str, str]:
        """Local name to the absolute dotted name an import binds it to, for every import of a file.

        `from a.b import C as D` gives `D -> a.b.C`; `import a.b` gives
        `a -> a`; `import a.b as c` gives `c -> a.b`.
        """
        if file.rel in self._bound:
            return self._bound[file.rel]
        out: dict[str, str] = {}
        for imp in self.imports(file):
            node = imp.node
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        out[alias.asname or alias.name] = f"{imp.module}.{alias.name}"
            else:
                for alias in node.names:
                    if alias.asname:
                        out[alias.asname] = alias.name
                    else:
                        head = alias.name.split(".")[0]
                        out[head] = head
        self._bound[file.rel] = out
        return out

    def bases(self, cls: ast.ClassDef) -> list[str]:
        """The class names a class's bases mean, last segment only, read through its file's imports.

        `abc.ABC` gives `ABC`, and `Root` after `from acme.om.storage
        import StorageInterface as Root` gives `StorageInterface`. A
        class this project did not parse is read as written.
        """
        file = self._owners.get(id(cls))
        bound = self.bound_names(file) if file is not None else {}
        out: list[str] = []
        for name in base_names(cls):
            head, _, rest = name.partition(".")
            target = bound.get(head, head)
            out.append(last(f"{target}.{rest}" if rest else target) or name)
        return out

    def import_graph(self, *prefixes: str) -> dict[str, set[str]]:
        """Module to every module name it may import (see `Import.targets`)."""
        files = self.modules_under(*prefixes) if prefixes else self.python_files
        return {f.module: {t for i in self.imports(f) for t in i.targets()} for f in files}

    # --- any file

    def files(self, *patterns: str) -> list[str]:
        """Paths relative to the root of every file a glob matches, excluded ones left out."""
        found: set[str] = set()
        for pattern in patterns:
            if not relative_glob(pattern):
                raise ConfigError(f"{pattern!r} is not a glob relative to the root; check the options that name it")
            for p in self.root.glob(pattern):
                if p.is_file():
                    rel = self.rel(p)
                    if not self.excluded(rel):
                        found.add(rel)
        return sorted(found)

    def read(self, rel: str) -> str | None:
        """The text of a file relative to the root, or None when it is missing.

        A byte that is not UTF-8 is replaced, never a reason to read the file
        as empty: every token a rule looks for is ASCII, and a Dockerfile with a
        Latin-1 comment is still a Dockerfile. A leading byte order mark is
        dropped, as some editors save UTF-8 with one.
        """
        path = self.root / rel
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return None
        with contextlib.suppress(ValueError):  # a path that climbs out of the root has no ignores to settle
            self.read_paths.add(self.rel(path))
        return text

    def lines(self, rel: str) -> list[str]:
        text = self.read(rel)
        return text.splitlines() if text is not None else []


def names(value: object) -> bool:
    """Whether an option's table entry is a name or a non-empty list of names, the one shape a table option takes."""
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value)


# --- ast helpers


def depth(tree: ast.AST) -> int:
    """How deep a syntax tree nests, counted with a stack so a deep tree cannot exhaust it."""
    deepest = 0
    stack = [(tree, 1)]
    while stack:
        node, level = stack.pop()
        deepest = max(deepest, level)
        stack.extend((child, level + 1) for child in ast.iter_child_nodes(node))
    return deepest


def is_under(name: str, prefix: str) -> bool:
    """Whether module `name` is `prefix` or inside it: `acme.om.x` is under `acme.om`, `acme.omx` is not."""
    return name == prefix or name.startswith(prefix + ".")


def dotted(node: ast.AST | None) -> str | None:
    """The dotted name an expression spells (`a.b.C`), subscripts and calls unwrapped; else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    if isinstance(node, ast.Subscript):
        return dotted(node.value)
    if isinstance(node, ast.Call):
        return dotted(node.func)
    return None


def last(name: str | None) -> str | None:
    """The last segment of a dotted name: `last("a.b.C")` is `C`."""
    return name.rpartition(".")[2] if name else None


def classes(tree: ast.AST) -> list[ast.ClassDef]:
    """Every class defined anywhere under `tree`, nested ones included, in source order."""
    found = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    return sorted(found, key=lambda n: (n.lineno, n.col_offset))


def top_classes(tree: ast.Module) -> list[ast.ClassDef]:
    """The classes defined at the top level of a module."""
    return [n for n in tree.body if isinstance(n, ast.ClassDef)]


def base_names(cls: ast.ClassDef) -> list[str]:
    """The dotted names of a class's bases, as written: `Generic[T]` gives `Generic`."""
    return [n for n in (dotted(b) for b in cls.bases) if n]


def keywords(cls: ast.ClassDef) -> dict[str, ast.expr]:
    """The class keywords: `class A(B, metaclass=M, frozen=True)` gives metaclass and frozen."""
    return {k.arg: k.value for k in cls.keywords if k.arg}


def decorator_names(node: ast.ClassDef | Function) -> list[str]:
    """The dotted names of the decorators, a call reduced to what it calls."""
    return [n for n in (dotted(d) for d in node.decorator_list) if n]


def functions(node: ast.AST) -> list[Function]:
    """Every function or method defined anywhere under `node`, in source order."""
    found = [n for n in ast.walk(node) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    return sorted(found, key=lambda n: (n.lineno, n.col_offset))


def methods(cls: ast.ClassDef) -> list[Function]:
    """The functions defined directly in a class body."""
    return [n for n in cls.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


@dataclass(frozen=True)
class Parameter:
    """One parameter of a signature. `kind` is positional, vararg, keyword, or varkw."""

    name: str
    annotation: str | None
    kind: str
    has_default: bool


def parameters(fn: Function) -> list[Parameter]:
    """A function's parameters in declaration order, `self` and `cls` included, annotations unparsed."""
    a = fn.args
    positional = [*a.posonlyargs, *a.args]
    first_default = len(positional) - len(a.defaults)
    out = [Parameter(p.arg, annotation(p.annotation), "positional", i >= first_default) for i, p in enumerate(positional)]
    if a.vararg:
        out.append(Parameter(a.vararg.arg, annotation(a.vararg.annotation), "vararg", False))
    for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True):
        out.append(Parameter(p.arg, annotation(p.annotation), "keyword", d is not None))
    if a.kwarg:
        out.append(Parameter(a.kwarg.arg, annotation(a.kwarg.annotation), "varkw", False))
    return out


def annotation(node: ast.expr | None) -> str | None:
    """An annotation as source text, or None when there is none."""
    return ast.unparse(node) if node is not None else None


def returns(fn: Function) -> str | None:
    """A function's return annotation as source text, or None."""
    return annotation(fn.returns)
