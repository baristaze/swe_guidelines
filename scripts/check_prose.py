#!/usr/bin/env python3
"""Hold every paragraph of the guideline to a length a person can read.

A paragraph is a blank-line-delimited block of prose in `architecture.md`;
a list item is its own paragraph; a code fence, a table, a heading, and
the Contents block are not prose. A block over `LIMIT` words is a
finding: a protocol that long is a table or two paragraphs. Standard
library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDELINE = ROOT / "architecture.md"
LIMIT = 200

FENCE = re.compile(r"^\s*```")
HEADING = re.compile(r"^#{1,6} ")
LIST_ITEM = re.compile(r"^(\s*)([-*]|\d+\.)\s+")
TABLE = re.compile(r"^\s*\|")


def blocks(lines: list[str]) -> list[tuple[int, str]]:
    """Every prose block as (first line number, text); a list item is one block."""
    out: list[tuple[int, str]] = []
    in_fence = False
    in_contents = False
    start = 0
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            out.append((start, " ".join(buf)))
        buf = []

    for ln, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if FENCE.match(line):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if HEADING.match(line):
            flush()
            in_contents = line.strip() == "## Contents"
            continue
        if in_contents or TABLE.match(line):
            flush()
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if LIST_ITEM.match(line):
            flush()
            start = ln
            buf = [LIST_ITEM.sub("", line).strip()]
            continue
        body = re.sub(r"^\s*>\s?", "", line).strip()
        if not buf:
            start = ln
        buf.append(body)
    flush()
    return out


def words(text: str) -> int:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # a link counts as its text
    return len(text.split())


def main() -> int:
    lines = GUIDELINE.read_text(encoding="utf-8").splitlines()
    findings = [
        (ln, n, text)
        for ln, text in blocks(lines)
        if (n := words(text)) > LIMIT
    ]
    for ln, n, text in findings:
        print(f"architecture.md:{ln}: {n} words, limit {LIMIT}: {' '.join(text.split()[:6])} ...")
    if findings:
        print(f"\n{len(findings)} paragraph(s) over {LIMIT} words")
        return 1
    print(f"prose ok: {len(blocks(lines))} paragraph(s), none over {LIMIT} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
