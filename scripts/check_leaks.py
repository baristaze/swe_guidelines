#!/usr/bin/env python3
"""Check the published Markdown for vocabulary that must not appear.

The guideline is generic on purpose. Product names and hardware nouns
belong to the projects it was extracted from, never to the guideline or
the lenses. `REFUSED_TERMS` below is the whole vocabulary; `SCOPES` says
which group applies to which files.

It also refuses changelog phrasing in the guideline, and the one
spelling of an update copy the guideline forbids, wherever a snippet
could teach it. Those groups apply to Markdown only.

The guideline stands alone: nothing in it may name or lean on the
reference implementation, whose name is refused in every tracked text
file (a file holding a NUL byte or bytes that are not UTF-8 is binary
and skipped) except the closing Next section of architecture.md, which links it
on purpose, and the changelog, which is history.
The product vocabulary is also refused in the published text that is
not Markdown: the workflows and templates under `.github/`, the plugin
manifests, the YAML and JSON of the skills, the agents, and the
benchmark, and the docstrings of the Python under `scripts/`,
`checkers/`, and `benchmark/`.
Exit status is non-zero on any hit. Standard library only.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import SKIP_DIRS, SKIP_PATHS, arguments, markdown_files

ROOT = Path(__file__).resolve().parent.parent

# The refused vocabulary, one regular expression per term, matched case-insensitively.
# "product" is the vocabulary of the projects the guideline was extracted from and
# must not flow back into it; a fork replaces that list with its own. "history" is
# changelog phrasing, refused in the guideline only. "shape" is a spelling the
# guideline forbids in code: a copy built from a dump must go through
# model_validate, so `model_copy(update={**...` cannot appear in any snippet.
REFUSED_TERMS: dict[str, list[str]] = {
    "product": [
        r"\brodeo\b",
        r"\brobot(s|ic|ics)?\b",
        r"\bbench(es)?\b",
        r"\blabs?\b",
        r"\bhil\b",
        r"\bfirmware\b",
        r"\bsimulat(or|ors|ion|ions|ed)\b",
        r"\bteleoperat\w*\b",
    ],
    "history": [
        r"\bused to\b",
        r"\bpreviously\b",
        r"\bformerly\b",
        r"\bwas considered\b",
        r"\bwere considered\b",
        r"\bset aside\b",
        r"\bsuperseded\b",
        r"\bdeprecated\b",
        r"\bwe changed\b",
        r"\bhas changed\b",
    ],
    "shape": [
        r"model_copy\(update=\{\*\*",
    ],
    "reference": [
        r"\btadas\b",
    ],
}

# The reference implementation's name is refused in every tracked text file,
# Markdown or not, whatever its suffix, everywhere but the places named below.
REFERENCE_ALLOWED_FILES = frozenset({"CHANGELOG.md", "scripts/check_leaks.py", "tests/test_check_leaks.py"})
NEXT_SECTION = "## Next: An End-to-End Reference Implementation"

# scope -> the term groups refused there. A scope ending in "/" is a
# directory and covers every Markdown file under it at any depth; any
# other scope is one file. A file inside several scopes is scanned once
# per group, whichever scopes name it. The files come from
# `markdown_files`, the one list of the repository's Markdown.
SCOPES: list[tuple[str, list[str]]] = [
    ("architecture.md", ["product", "history", "shape"]),
    ("lenses/", ["product", "shape"]),
    ("skills/", ["product", "shape"]),
    ("README.md", ["product"]),
    ("CONTRIBUTING.md", ["product"]),
    ("docs/", ["product", "shape"]),
    ("agents/", ["product", "shape"]),
    ("AGENTS.md", ["product"]),
    ("CLAUDE.md", ["product"]),
    ("SECURITY.md", ["product"]),
    ("benchmark/", ["product", "shape"]),
    ("checkers/", ["product", "shape"]),
    (".github/", ["product"]),
]


# The published text that is not Markdown: (directory, suffixes, groups).
# The manifests, the workflows, and the YAML a skill or a benchmark
# scenario carries are read whole.
TEXT_SCOPES: list[tuple[str, tuple[str, ...], list[str]]] = [
    (".github/", (".yml", ".yaml"), ["product"]),
    (".claude-plugin/", (".json",), ["product"]),
    ("skills/", (".yml", ".yaml", ".json"), ["product"]),
    ("agents/", (".yml", ".yaml", ".json"), ["product"]),
    ("benchmark/", (".yml", ".yaml", ".json"), ["product"]),
]

# The Python whose docstrings are published prose: the scripts, the
# checker, and the benchmark harness. Code and comments are not read, so
# this file can hold the terms it refuses; the tests are not read either.
DOCSTRING_SCOPES = ("scripts/", "checkers/", "benchmark/")


def in_scope(rel: str, scope: str) -> bool:
    return rel.startswith(scope) if scope.endswith("/") else rel == scope


def docstring_lines(path: Path) -> set[int]:
    """The line numbers the docstrings of a Python file span; none when it does not parse."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError):
        return set()
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            out.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return out


def scan(root: Path, path: Path, label: str, errors: list[str], only: set[int] | None = None) -> None:
    """Report every refused term of `label` in a file; `only` limits the scan to those line numbers."""
    compiled = [re.compile(p, re.IGNORECASE) for p in REFUSED_TERMS[label]]
    # The Next section links the reference on purpose, in the guideline only;
    # the same heading pasted anywhere else exempts nothing.
    exempt = label == "reference" and path.relative_to(root).as_posix() == "architecture.md"
    in_next = False
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if only is not None and ln not in only:
            continue
        if exempt and line.startswith("## "):
            in_next = line.strip() == NEXT_SECTION
        if in_next:
            continue
        for pat in compiled:
            m = pat.search(line)
            if m:
                errors.append(f"{path.relative_to(root)}:{ln}: {label} term '{m.group(0)}'")


def is_text(path: Path) -> bool:
    """Whether a file reads as text: UTF-8, with no NUL byte."""
    try:
        data = path.read_bytes()
        data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return b"\0" not in data


def text_files(root: Path) -> list[Path]:
    """Every text file of the repository, from git when it can, the skipped directories left out."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        out = [p.relative_to(root).as_posix() for p in root.rglob("*")]
    return [
        root / rel
        for rel in sorted(set(out))
        if not any(part in SKIP_DIRS for part in rel.split("/")[:-1])
        and not any(tuple(rel.split("/")[: len(skip)]) == skip for skip in SKIP_PATHS)
        and (root / rel).is_file()
        and is_text(root / rel)
    ]


def reference_files(root: Path) -> list[Path]:
    """Every text file the reference name is refused in."""
    return [p for p in text_files(root) if p.relative_to(root).as_posix() not in REFERENCE_ALLOWED_FILES]


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    labels: dict[Path, list[str]] = {}
    for path in reference_files(ROOT):
        labels.setdefault(path, []).append("reference")
    for path in markdown_files(ROOT):
        rel = path.relative_to(ROOT).as_posix()
        for scope, groups in SCOPES:
            if in_scope(rel, scope):
                mine = labels.setdefault(path, [])
                mine.extend(g for g in groups if g not in mine)
    docstrings: dict[Path, list[str]] = {}
    for path in text_files(ROOT):
        rel = path.relative_to(ROOT).as_posix()
        for scope, suffixes, groups in TEXT_SCOPES:
            if in_scope(rel, scope) and rel.endswith(suffixes):
                mine = labels.setdefault(path, [])
                mine.extend(g for g in groups if g not in mine)
        if rel.endswith(".py") and rel.startswith(DOCSTRING_SCOPES):
            docstrings[path] = ["product"]
    for path in sorted(labels):
        for label in labels[path]:
            scan(ROOT, path, label, errors)
    for path in sorted(docstrings):
        lines = docstring_lines(path)
        for label in docstrings[path]:
            if label not in labels.get(path, []):
                scan(ROOT, path, label, errors, only=lines)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} leak(s)")
        return 1
    print(f"leaks ok: {len(set(labels) | set(docstrings))} file(s) scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
