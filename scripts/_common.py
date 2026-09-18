"""Helpers shared by the checkers and generators under scripts/.

Every script runs as `python3 scripts/<name>.py`, which puts this
directory first on `sys.path`, so `from _common import ...` resolves
without packaging. Standard library only.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def slug(heading: str) -> str:
    """The anchor a Markdown renderer derives from a heading.

    Inline code, emphasis, and underscores are stripped, the rest is
    lowercased, punctuation is dropped, and runs of whitespace become
    one hyphen. `anchors` numbers repeats; this function does not.
    """
    text = re.sub(r"[`*_]", "", heading).strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text)


def headings(text: str) -> list[tuple[int, str]]:
    """(level, title) for every ATX heading, in order, skipping fenced code."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING.match(line)
        if m:
            out.append((len(m.group(1)), m.group(2)))
    return out


def anchors(text: str) -> list[tuple[int, str, str]]:
    """(level, title, anchor) for every heading of a document.

    The second heading with a given slug gets `-1`, the third `-2`, and
    so on, which is the rule GitHub applies. Both the generator that
    writes anchors and the checker that resolves them use this function,
    so the two cannot disagree.
    """
    seen: dict[str, int] = {}
    out: list[tuple[int, str, str]] = []
    for level, title in headings(text):
        base = slug(title)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append((level, title, base if n == 0 else f"{base}-{n}"))
    return out
