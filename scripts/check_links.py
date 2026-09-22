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

No file refers to a section by number, and no heading is numbered: a
cross-reference names the section by title, because numbers shift when
a section is inserted. The changelog's release headings are versions,
not section numbers, and fenced code is not read.
Exit status is non-zero on any broken link or numbered section.
Standard library only.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import NUMBERED_REFERENCE, ROOT, anchors, arguments, fenced_lines, markdown_files, unfenced

# The text may hold one level of brackets (`[see [the note]](x.md)`), the
# destination may be wrapped in `<...>`, and the title may be quoted either
# way or parenthesised.
LINK = re.compile(
    r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]"
    r"\(\s*(?:<([^>]*)>|([^)\s]+))(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
# A reference definition, `[label]: target "title"`, at the start of a line.
DEFINITION = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(?:<([^>]*)>|(\S+))")
NUMBERED_HEADING = re.compile(r"^#{1,6} \d+(\.\d+)*[.)]?\s")
VERSIONED = ("CHANGELOG.md",)
EXTERNAL = ("http://", "https://", "mailto:")


def headings(path: Path) -> set[str]:
    """Every anchor the file defines, duplicates numbered as the generator numbers them."""
    return {anchor for _, _, anchor in anchors(path.read_text(encoding="utf-8"))}


def definitions(text: str) -> list[tuple[int, str]]:
    """(line, target) of every reference definition outside fenced code."""
    out: list[tuple[int, str]] = []
    for number, (line, code) in enumerate(zip(text.split("\n"), fenced_lines(text), strict=True), start=1):
        m = DEFINITION.match(line) if not code else None
        if m:
            out.append((number, m.group(1) if m.group(1) is not None else m.group(2)))
    return out


def numbered(path: Path, text: str) -> list[str]:
    """Every line outside fenced code that refers to a section by number or numbers a heading."""
    out: list[str] = []
    for number, (line, code) in enumerate(zip(text.split("\n"), fenced_lines(text), strict=True), start=1):
        if code:
            continue
        if NUMBERED_REFERENCE.search(line):
            out.append(f"{path.relative_to(ROOT)}:{number}: refers to a section by number")
        elif NUMBERED_HEADING.match(line) and path.name not in VERSIONED:
            out.append(f"{path.relative_to(ROOT)}:{number}: a numbered heading; headings are unnumbered")
    return out


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    root = ROOT.resolve()
    files = markdown_files(ROOT)
    for path in files:
        own = headings(path)
        text = path.read_text(encoding="utf-8")
        errors += numbered(path, text)
        # fenced code blanked, same length, so offsets map back to lines
        flat = unfenced(text).replace("\n", " ")
        found = [
            (text.count("\n", 0, m.start()) + 1, m.group(1) if m.group(1) is not None else m.group(2))
            for m in LINK.finditer(flat)
        ]
        for ln, target in found + definitions(text):
            where = f"{path.relative_to(ROOT)}:{ln}"
            if target.startswith(EXTERNAL):
                continue
            if target.startswith("#"):
                if target[1:] not in own:
                    errors.append(f"{where}: missing anchor {target}")
                continue
            file_part, _, anchor = target.partition("#")
            base = root if file_part.startswith("/") else path.parent
            resolved = (base / file_part.lstrip("/")).resolve()
            if not resolved.is_relative_to(root):
                errors.append(f"{where}: {file_part} leaves the repository")
            elif not resolved.exists():
                errors.append(f"{where}: missing file {file_part}")
            elif anchor and resolved.suffix == ".md" and anchor not in headings(resolved):
                errors.append(f"{where}: missing anchor #{anchor} in {file_part}")
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} broken link(s) or numbered section(s)")
        return 1
    print(f"links ok: {len(files)} file(s) scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
