"""Helpers for rules that read files other than Python: a tree walk, a Makefile, a Dockerfile, JSON and TOML.

The standard library has no YAML or HCL parser, so nothing here reads
compose files, workflows, or Terraform as a structure. A rule over
those reads single lines, and only where a line decides the question
alone.
"""

from __future__ import annotations

import ast
import json
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import tomllib

from arch_check.config import relative_glob
from arch_check.project import SKIP_DIRS, Project, dotted

WALK_SKIP = SKIP_DIRS | {".terraform", ".venv"}
"""Directory names a tree walk never enters: tool caches and installs."""
OUTPUT_DIRS = frozenset({"dist", "build", "coverage", ".next", ".turbo"})
"""Build output, skipped only where a tool writes it: at the root, or beside a
`pyproject.toml` or `package.json`. Anywhere else a folder of that name is the
project's own, a package called `build` for one, and is walked."""
MANIFESTS = ("pyproject.toml", "package.json")


def is_output(project: Project, entry: Path) -> bool:
    """Whether a directory is build output: an output name, beside a manifest or at the root."""
    if entry.name not in OUTPUT_DIRS:
        return False
    parent = entry.parent
    return parent == project.root or any((parent / m).is_file() for m in MANIFESTS)


def walk(project: Project, start: str = "", *, names: tuple[str, ...] = ("*",)) -> Iterator[str]:
    """Paths relative to the root of every file under `start` whose name matches one of `names`.

    Hidden directories, installs, caches, build output, and excluded
    paths are left out. A symlinked directory is never entered, as
    `Project.python_files` never enters one: a link to a parent would
    never end, and a link to another folder would read its files twice.
    The order is the path order.
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
                if entry.is_symlink() or entry.name in WALK_SKIP or entry.name.startswith(".") or is_output(project, entry):
                    continue
                stack.append(entry)
            elif entry.is_file() and any(fnmatchcase(entry.name, n) for n in names):
                rel = entry.relative_to(project.root).as_posix()
                if not project.excluded(rel):
                    found.append(rel)
    yield from sorted(found)


def subdirs(project: Project, rel: str) -> list[str]:
    """The non-hidden directories directly under `rel`, as paths relative to the root; a symlinked one is left out."""
    base = project.root / rel
    if not base.is_dir():
        return []
    return sorted(
        p.relative_to(project.root).as_posix()
        for p in base.iterdir()
        if p.is_dir()
        and not p.is_symlink()
        and not p.name.startswith(".")
        and p.name not in WALK_SKIP
        and not is_output(project, p)
    )


def load_toml(project: Project, rel: str) -> dict[str, Any] | None:
    """A TOML file as a dict, or None when it is missing or does not parse."""
    text = project.read(rel)
    if text is None:
        return None
    try:
        return tomllib.loads(text.removeprefix("\ufeff"))
    except tomllib.TOMLDecodeError:
        return None


def subtable(data: Any, *keys: str) -> dict[str, Any]:
    """The table at `keys` inside parsed TOML, or an empty one when a step is missing or not a table."""
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data if isinstance(data, dict) else {}


def strings_at(table: dict[str, Any], key: str) -> list[str]:
    """The strings of the array at `key`; anything else there reads as empty."""
    value = table.get(key)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def load_json(project: Project, rel: str) -> Any:
    """A JSON file, or None when it is missing or does not parse."""
    text = project.read(rel)
    if text is None:
        return None
    try:
        return json.loads(text.removeprefix("\ufeff"))  # a BOM is how some editors save UTF-8
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
    project = subtable(pyproject, "project")
    raw: list[Any] = list(strings_at(project, "dependencies"))
    for table in (subtable(project, "optional-dependencies"), subtable(pyproject, "dependency-groups")):
        for group in table:
            raw.extend(strings_at(table, group))
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
    workspace = subtable(root, "tool", "uv").get("workspace")
    if not isinstance(workspace, dict):
        return None
    members = strings_at(workspace, "members")
    excluded = strings_at(workspace, "exclude")
    found: set[str] = set()
    for pattern in members:
        if not relative_glob(pattern) or pattern.strip().strip("/") in {"", "."}:
            continue  # the root is the workspace, never one of its members
        try:
            matches = list(project.root.glob(pattern))
        except ValueError:  # a pattern the library refuses, such as one of dots alone
            continue
        for p in matches:
            if p.is_dir() and (p / "pyproject.toml").is_file():
                rel = p.relative_to(project.root).as_posix()
                if not any(fnmatchcase(rel, x) for x in excluded) and not project.excluded(rel + "/pyproject.toml"):
                    found.add(rel)
    return sorted(found)


# --- Makefile

TARGET = re.compile(r"^([A-Za-z0-9_./%-]+(?:[ \t]+[A-Za-z0-9_./%-]+)*)[ \t]*(?:::(?![=:])|:(?![=:]))(.*)$")
"""A rule line, `target:` or the double-colon `target::`; never an assignment (`:=`, `::=`)."""


@dataclass(frozen=True)
class Target:
    """One Makefile rule: its line, its prerequisites, and its recipe lines."""

    name: str
    line: int
    prerequisites: tuple[str, ...]
    recipe: tuple[str, ...]


INCLUDE = re.compile(r"^-?s?include\s+(.+?)\s*$")


def make_lines(project: Project, rel: str, seen: set[str] | None = None) -> list[str]:
    """The lines of a Makefile with every `include` of a literal path spliced in, each file once."""
    seen = set() if seen is None else seen
    if rel in seen:
        return []
    seen.add(rel)
    out: list[str] = []
    for line in project.lines(rel):
        m = INCLUDE.match(line)
        if m and "$(" not in m.group(1):
            base = posixpath.dirname(rel)
            for inc in m.group(1).split():
                if relative_glob(inc):
                    out.extend(make_lines(project, posixpath.normpath(posixpath.join(base, inc)), seen))
            continue
        out.append(line)
    return out


def make_targets(project: Project, rel: str = "Makefile") -> dict[str, Target]:
    """The explicit targets of a Makefile by name, its includes read too; the first rule of a name wins."""
    out: dict[str, Target] = {}
    lines = make_lines(project, rel)
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


# A heredoc opens only in the instructions Docker lets carry one, and its
# word starts like a name: `$((1<<20))` is a shift, `<<<` a here-string.
HEREDOC = re.compile(r"(?<![<\w)])<<-?\s*(['\"]?)([A-Za-z_]\w*)\1(?!\w)")
HEREDOC_INSTRUCTIONS = frozenset({"RUN", "COPY", "ADD"})


def instruction(buffer: str, line: int) -> Instruction:
    """One instruction from its joined text: the keyword up to the first run of whitespace, a tab included."""
    parts = re.split(r"\s+", buffer.strip(), maxsplit=1)
    return Instruction(parts[0].upper(), parts[1].strip() if len(parts) > 1 else "", line)


def dockerfile(project: Project, rel: str) -> list[Instruction]:
    """The instructions of a Dockerfile: comments dropped, `\\` continuations joined across a blank line, and
    a heredoc body (`RUN <<EOF ... EOF`) carried as the rest of its instruction, one line per command."""
    out: list[Instruction] = []
    buffer = ""
    start = 0
    terminator: str | None = None
    for number, raw in enumerate(project.lines(rel), start=1):
        if terminator is not None:
            if raw.strip() == terminator:
                out.append(instruction(buffer, start))
                buffer, terminator = "", None
            elif buffer.endswith("\\"):
                buffer = buffer[:-1] + " " + raw.strip()
            else:
                # each line of a heredoc is a command of its own, as the shell runs it
                buffer += "\n" + raw.strip()
            continue
        text = raw.strip()
        if not text or text.startswith("#"):
            continue  # a blank line or a comment inside a continuation is skipped, as the engine skips it
        if not buffer:
            start = number
        if text.endswith("\\"):
            buffer += text[:-1] + " "
            continue
        buffer += text
        opened = HEREDOC.search(buffer)
        if opened is not None and instruction(buffer, start).keyword in HEREDOC_INSTRUCTIONS:
            terminator = opened.group(2)
            continue
        out.append(instruction(buffer, start))
        buffer = ""
    if buffer:
        out.append(instruction(buffer, start))
    return out


NOT_AN_IMAGE = (".dockerignore", ".md", ".rst", ".txt", ".bak", ".orig", ".example")


def is_dockerfile(name: str) -> bool:
    """Whether a file name is a Dockerfile: `Dockerfile`, `*.Dockerfile`, or `Dockerfile.*`, never a `*.dockerignore`
    or a document or a backup named after one (`Dockerfile.md`, `Dockerfile.bak`)."""
    if name.endswith(NOT_AN_IMAGE):
        return False
    return name == "Dockerfile" or name.endswith(".Dockerfile") or name.startswith("Dockerfile.")


# --- Python names


def module_matches(module: str, pattern: str) -> bool:
    """Whether a dotted module name matches a dotted glob where `*` spans one segment: `acme.services.*.main`."""
    parts, want = module.split("."), pattern.split(".")
    return len(parts) == len(want) and all(fnmatchcase(p, w) for p, w in zip(parts, want, strict=True))


def imported_names(project: Project, file: Any) -> dict[str, str]:
    """Local name to the absolute dotted name it binds, for every import of a file (`Project.bound_names`)."""
    return project.bound_names(file)


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
