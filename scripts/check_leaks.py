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
file except the closing Next section of architecture.md, which links it
on purpose, and the changelog, which is history.
Exit status is non-zero on any hit. Standard library only.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import arguments, markdown_files

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

# The reference implementation's name is refused in these files, Markdown
# or not, everywhere but the two places named below.
REFERENCE_SUFFIXES = (".md", ".py", ".yml", ".yaml", ".toml", ".json", ".txt", ".sh")
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
    ("benchmark/", ["product", "shape"]),
    ("checkers/", ["product", "shape"]),
    (".github/", ["product"]),
]


def in_scope(rel: str, scope: str) -> bool:
    return rel.startswith(scope) if scope.endswith("/") else rel == scope


def scan(root: Path, path: Path, label: str, errors: list[str]) -> None:
    compiled = [re.compile(p, re.IGNORECASE) for p in REFUSED_TERMS[label]]
    # The Next section links the reference on purpose, in the guideline only;
    # the same heading pasted anywhere else exempts nothing.
    exempt = label == "reference" and path.relative_to(root).as_posix() == "architecture.md"
    in_next = False
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if exempt and line.startswith("## "):
            in_next = line.strip() == NEXT_SECTION
        if in_next:
            continue
        for pat in compiled:
            m = pat.search(line)
            if m:
                errors.append(f"{path.relative_to(root)}:{ln}: {label} term '{m.group(0)}'")


def reference_files(root: Path) -> list[Path]:
    """Every text file the reference name is refused in, from git when it can."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        out = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return [
        root / rel
        for rel in sorted(set(out))
        if rel.endswith(REFERENCE_SUFFIXES) and rel not in REFERENCE_ALLOWED_FILES and (root / rel).is_file()
    ]


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
    for path in sorted(labels):
        for label in labels[path]:
            scan(ROOT, path, label, errors)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} leak(s)")
        return 1
    print(f"leaks ok: {len(labels)} file(s) scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
