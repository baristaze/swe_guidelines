#!/usr/bin/env python3
"""Check the published Markdown for vocabulary that must not appear.

The guideline is generic on purpose. Product names, hardware nouns, and
assistant-tooling concepts belong to the projects that use it, never to
the guideline or the lenses. Skills may name the tooling they run on
(the review skills run inside an assistant), so they get a shorter list.
`REFUSED_TERMS` below is the whole vocabulary; `SCOPES` says which group
applies to which files.

Also refuses em-dashes everywhere and changelog phrasing in the guideline.
Exit status is non-zero on any hit. Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The refused vocabulary, one regular expression per term, matched case-insensitively.
# "product" is the vocabulary of the projects the guideline was extracted from and
# must not flow back into it; a fork replaces that list with its own. "assistant-tooling"
# is the vocabulary of the assistants the skills run inside, allowed in the skills only.
# "history" is changelog phrasing, refused in the guideline only.
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
    "assistant-tooling": [
        r"\bagents?\b",
        r"\bagentic\b",
        r"\bsub-?agents?\b",
        r"\bLLMs?\b",
        r"\bAI\b",
        r"\bprompts?\b",
        r"\bmodel provider(s)?\b",
        r"\btoken budget(s)?\b",
        r"\bcoding assistant(s)?\b",
        r"\bmachine learning\b",
        r"\bclaude\b",
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
}

EM_DASH = "\u2014"

# file glob -> the term groups refused there; a file matched by several globs is
# scanned once per group, whichever globs name it
SCOPES: list[tuple[str, list[str]]] = [
    ("architecture.md", ["product", "assistant-tooling", "history"]),
    ("lenses/*.md", ["product", "assistant-tooling"]),
    ("skills/*/*.md", ["product"]),
    ("README.md", ["product"]),
    ("CONTRIBUTING.md", ["product"]),
    ("docs/*.md", ["product"]),
]

# file glob -> em-dashes are refused in every one of these
EVERYWHERE = ["*.md", "lenses/*.md", "skills/*/*.md", "skills/*/*/*.md", "docs/*.md", ".github/**/*.md"]


def files(root: Path, globs: list[str]) -> list[Path]:
    """Every file one of the globs names, once, in path order."""
    return sorted({p for g in globs for p in root.glob(g) if p.is_file()})


def scan(root: Path, path: Path, label: str, errors: list[str]) -> None:
    compiled = [re.compile(p, re.IGNORECASE) for p in REFUSED_TERMS[label]]
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for pat in compiled:
            m = pat.search(line)
            if m:
                errors.append(f"{path.relative_to(root)}:{ln}: {label} term '{m.group(0)}'")


def main() -> int:
    errors: list[str] = []
    labels: dict[Path, list[str]] = {}
    for glob, groups in SCOPES:
        for path in files(ROOT, [glob]):
            mine = labels.setdefault(path, [])
            mine.extend(g for g in groups if g not in mine)
    for path in sorted(labels):
        for label in labels[path]:
            scan(ROOT, path, label, errors)
    for path in files(ROOT, EVERYWHERE):
        for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if EM_DASH in line:
                errors.append(f"{path.relative_to(ROOT)}:{ln}: em-dash")
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} leak(s)")
        return 1
    print(f"leaks ok: {len(labels)} file(s) scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
