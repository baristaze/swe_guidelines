#!/usr/bin/env python3
"""Check that every relative Markdown link and image points at a file that exists.

External links (http, https, mailto) are not fetched; CI has no reason to
depend on the network. A link whose text wraps across lines is checked
like any other: the file is scanned with newlines read as spaces, so an
anchor cannot hide behind a line break. Anchors come from
`scripts/_common.py`, the same rule `gen_toc.py` writes them with, so a
repeated heading resolves as `#title-1`, `#title-2`, and headings inside
fenced code do not count. A link that starts with `/` resolves against
the repository root, as GitHub resolves it. A link that resolves
outside the repository is broken, whichever way it gets there.
Exit status is non-zero on any broken link.
Standard library only.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import ROOT, anchors, arguments

LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")
SKIP_DIRS = {".git", "node_modules", ".venv"}


def headings(path: Path) -> set[str]:
    """Every anchor the file defines, duplicates numbered as the generator numbers them."""
    return {anchor for _, _, anchor in anchors(path.read_text(encoding="utf-8"))}


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    root = ROOT.resolve()
    files = [
        p
        for p in ROOT.rglob("*.md")
        if not any(part in SKIP_DIRS for part in p.parts)
    ]
    for path in sorted(files):
        own = headings(path)
        text = path.read_text(encoding="utf-8")
        flat = text.replace("\n", " ")  # same length, so offsets map back to lines
        for m in LINK.finditer(flat):
            ln = text.count("\n", 0, m.start()) + 1
            for target in [m.group(1)]:
                if target.startswith(SKIP_PREFIXES[:3]):
                    continue
                if target.startswith("#"):
                    if target[1:] not in own:
                        errors.append(f"{path.relative_to(ROOT)}:{ln}: missing anchor {target}")
                    continue
                file_part, _, anchor = target.partition("#")
                base = root if file_part.startswith("/") else path.parent
                resolved = (base / file_part.lstrip("/")).resolve()
                if not resolved.is_relative_to(root):
                    errors.append(f"{path.relative_to(ROOT)}:{ln}: {file_part} leaves the repository")
                elif not resolved.exists():
                    errors.append(f"{path.relative_to(ROOT)}:{ln}: missing file {file_part}")
                elif anchor and resolved.suffix == ".md" and anchor not in headings(resolved):
                    errors.append(f"{path.relative_to(ROOT)}:{ln}: missing anchor #{anchor} in {file_part}")
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} broken link(s)")
        return 1
    print(f"links ok: {len(files)} file(s) scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
