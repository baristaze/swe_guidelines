"""Helpers for rules that read files other than Python: a tree walk, a Makefile, a Dockerfile, JSON and TOML.

The standard library has no YAML or HCL parser, so nothing here reads
compose files, workflows, or Terraform as a structure. A rule over
those reads single lines, and only where a line decides the question
alone.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import tomllib

from arch_check.project import SKIP_DIRS, Project, dotted

WALK_SKIP = SKIP_DIRS | {"dist", "build", ".terraform", ".venv", "coverage", ".next", ".turbo"}
"""Directory names a tree walk never enters: tool caches, installs, and build output."""


def walk(project: Project, start: str = "", *, names: tuple[str, ...] = ("*",)) -> Iterator[str]:
    """Paths relative to the root of every file under `start` whose name matches one of `names`.

    Hidden directories, installs, caches, build output, and excluded
    paths are left out. The order is the path order.
    """
    base = project.root / start if start else project.root
    if not base.is_dir():
        return
    stack = [base]
    found: list[str] = []
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in WALK_SKIP or entry.name.startswith("."):
                    continue
                stack.append(entry)
            elif entry.is_file() and any(fnmatchcase(entry.name, n) for n in names):
                rel = entry.relative_to(project.root).as_posix()
                if not project.excluded(rel):
                    found.append(rel)
    yield from sorted(found)


def subdirs(project: Project, rel: str) -> list[str]:
    """The non-hidden directories directly under `rel`, as paths relative to the root."""
    base = project.root / rel
    if not base.is_dir():
        return []
    return sorted(
        p.relative_to(project.root).as_posix()
        for p in base.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in WALK_SKIP
    )


def load_toml(project: Project, rel: str) -> dict[str, Any] | None:
    """A TOML file as a dict, or None when it is missing or does not parse."""
    text = project.read(rel)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def load_json(project: Project, rel: str) -> Any:
    """A JSON file, or None when it is missing or does not parse."""
    text = project.read(rel)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def npm_dependencies(manifest: Any) -> set[str]:
    """Every package a `package.json` names in its dependency tables."""
    out: set[str] = set()
    if isinstance(manifest, dict):
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            table = manifest.get(key)
            if isinstance(table, dict):
                out.update(str(k) for k in table)
    return out


REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def python_dependencies(pyproject: dict[str, Any]) -> set[str]:
    """Every distribution a `pyproject.toml` requires, names lowercased with `_` as `-`."""
    raw: list[Any] = []
    project = pyproject.get("project", {})
    if isinstance(project, dict):
        raw.extend(project.get("dependencies", []) or [])
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for group in optional.values():
                raw.extend(group or [])
    groups = pyproject.get("dependency-groups", {})
    if isinstance(groups, dict):
        for group in groups.values():
            raw.extend(group or [])
    out: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            m = REQUIREMENT.match(item)
            if m:
                out.add(m.group(1).lower().replace("_", "-"))
    return out


def workspace_members(project: Project) -> list[str] | None:
    """The uv workspace members of the root `pyproject.toml`, as directories holding a `pyproject.toml`.

    None when the root declares no `[tool.uv.workspace]`.
    """
    root = load_toml(project, "pyproject.toml")
    if root is None:
        return None
    workspace = root.get("tool", {}).get("uv", {}).get("workspace")
    if not isinstance(workspace, dict):
        return None
    members = [m for m in workspace.get("members", []) if isinstance(m, str)]
    excluded = [m for m in workspace.get("exclude", []) if isinstance(m, str)]
    found: set[str] = set()
    for pattern in members:
        for p in project.root.glob(pattern):
            if p.is_dir() and (p / "pyproject.toml").is_file():
                rel = p.relative_to(project.root).as_posix()
                if not any(fnmatchcase(rel, x) for x in excluded) and not project.excluded(rel + "/pyproject.toml"):
                    found.add(rel)
    return sorted(found)


# --- Makefile

TARGET = re.compile(r"^([A-Za-z0-9_./%-]+(?:[ \t]+[A-Za-z0-9_./%-]+)*)[ \t]*:(?![=:])(.*)$")


@dataclass(frozen=True)
class Target:
    """One Makefile rule: its line, its prerequisites, and its recipe lines."""

    name: str
    line: int
    prerequisites: tuple[str, ...]
    recipe: tuple[str, ...]


def make_targets(project: Project, rel: str = "Makefile") -> dict[str, Target]:
    """The explicit targets of a Makefile by name; the first rule of a name wins."""
    out: dict[str, Target] = {}
    lines = project.lines(rel)
    i = 0
    while i < len(lines):
        m = TARGET.match(lines[i])
        if m and not lines[i].startswith("\t"):
            names = m.group(1).split()
            prereq = tuple(m.group(2).split(";", 1)[0].split())
            recipe: list[str] = []
            j = i + 1
            while j < len(lines) and (lines[j].startswith("\t") or not lines[j].strip() or lines[j].startswith("#")):
                if lines[j].startswith("\t"):
                    recipe.append(lines[j].strip())
                j += 1
            for name in names:
                out.setdefault(name, Target(name, i + 1, prereq, tuple(recipe)))
            i = j
            continue
        i += 1
    return out


# --- Dockerfile


@dataclass(frozen=True)
class Instruction:
    """One Dockerfile instruction, continuation lines joined: the keyword upper-cased, the rest, its line."""

    keyword: str
    args: str
    line: int


def dockerfile(project: Project, rel: str) -> list[Instruction]:
    """The instructions of a Dockerfile, comments dropped and `\\` continuations joined."""
    out: list[Instruction] = []
    buffer = ""
    start = 0
    for number, raw in enumerate(project.lines(rel), start=1):
        text = raw.strip()
        if not buffer and (not text or text.startswith("#")):
            continue
        if buffer and text.startswith("#"):
            continue
        if not buffer:
            start = number
        if text.endswith("\\"):
            buffer += text[:-1] + " "
            continue
        buffer += text
        keyword, _, args = buffer.partition(" ")
        out.append(Instruction(keyword.upper(), args.strip(), start))
        buffer = ""
    if buffer:
        keyword, _, args = buffer.partition(" ")
        out.append(Instruction(keyword.upper(), args.strip(), start))
    return out


def is_dockerfile(name: str) -> bool:
    return name == "Dockerfile" or name.endswith(".Dockerfile") or name.startswith("Dockerfile.")


# --- Python names


def module_matches(module: str, pattern: str) -> bool:
    """Whether a dotted module name matches a dotted glob where `*` spans one segment: `acme.services.*.main`."""
    parts, want = module.split("."), pattern.split(".")
    return len(parts) == len(want) and all(fnmatchcase(p, w) for p, w in zip(parts, want, strict=True))


def imported_names(project: Project, file: Any) -> dict[str, str]:
    """Local name to the absolute dotted name it binds, for every import of a file.

    `from a.b import C as D` gives `D -> a.b.C`; `import a.b` gives
    `a -> a`; `import a.b as c` gives `c -> a.b`.
    """
    out: dict[str, str] = {}
    for imp in project.imports(file):
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
    return out


def resolved(name: str | None, names: dict[str, str]) -> str | None:
    """A dotted name as written, its head replaced by what the file imported under it."""
    if not name:
        return None
    head, _, rest = name.partition(".")
    base = names.get(head)
    if base is None:
        return name
    return f"{base}.{rest}" if rest else base


def call_name(node: ast.Call, names: dict[str, str]) -> str | None:
    """The absolute dotted name a call's function resolves to through the file's imports."""
    return resolved(dotted(node.func), names)


def kwarg(node: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in node.keywords if k.arg == name), None)


def const_str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def is_file(project: Project, rel: str) -> bool:
    return (project.root / rel).is_file()


def is_dir(project: Project, rel: str) -> bool:
    return (project.root / rel).is_dir()


def rel_of(project: Project, path: Path) -> str:
    return path.relative_to(project.root).as_posix()
