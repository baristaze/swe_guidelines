#!/usr/bin/env python3
"""Generate the table of contents of architecture.md from its headings.

The table sits between `<!-- toc -->` and `<!-- /toc -->` under the
`## Contents` heading: one entry per section (`##`) with its subsections
(`###`) nested under it, each a link to the heading's anchor. Headings
inside fenced code are ignored. Anchors come from `scripts/_common.py`,
the same rule `check_links.py` resolves them with, so every link the
table emits is one that checker accepts, a repeated heading included.

`--check` exits non-zero when the table on disk differs from what the
headings produce, and never writes. Any other argument is refused with
exit status 2, so a mistyped flag cannot fall through to a write.
Standard library only.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from _common import ROOT, anchors, arguments, plain

GUIDELINE = ROOT / "architecture.md"
START, END = "<!-- toc -->", "<!-- /toc -->"
SKIP = {"Contents"}
CHECK_HELP = "exit 1 when the table on disk is stale; never write"


def render(text: str) -> str:
    lines: list[str] = []
    for level, title, anchor in anchors(text):
        if level not in (2, 3) or (level == 2 and title in SKIP):
            continue
        indent = "" if level == 2 else "  "
        lines.append(f"{indent}- [{plain(title)}](#{anchor})")
    return "\n".join(lines)


def main(argv: Sequence[str] = ()) -> int:
    args = arguments(__doc__, argv, check=CHECK_HELP)
    text = GUIDELINE.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print(f"architecture.md: no {START} ... {END} block")
        return 1
    head, rest = text.split(START, 1)
    _old, tail = rest.split(END, 1)
    new = f"{head}{START}\n{render(text)}\n{END}{tail}"
    if new == text:
        print("toc ok")
        return 0
    if args.check:
        print("architecture.md: table of contents is stale (run `make gen-toc`)")
        return 1
    GUIDELINE.write_text(new, encoding="utf-8")
    print("toc: regenerated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
