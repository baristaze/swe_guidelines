"""Helpers shared by the checkers and generators under scripts/.

Every script runs as `python3 scripts/<name>.py`, which puts this
directory first on `sys.path`, so `from _common import ...` resolves
without packaging. Standard library only.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# What "the repository's Markdown" leaves out: tool caches, installed
# packages, Claude Code's own folder (agent worktrees live under
# .claude/worktrees/), and the benchmark run folders git ignores. A
# directory name is skipped at any depth; a path is skipped from the root.
SKIP_DIRS = {".git", ".claude", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".markdownlint-cli2-cache"}
SKIP_PATHS = {("benchmark", "runs")}

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CLOSING = re.compile(r"(?:^|\s+)#+$")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def markdown_files(root: Path) -> list[Path]:
    """Every Markdown file of the repository at `root`, at any depth, in path order.

    This is the one definition every script uses, so a checker cannot
    miss a file another checker reads.
    """
    out = []
    for path in root.rglob("*.md"):
        parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in parts[:-1]):
            continue
        if any(parts[: len(skip)] == skip for skip in SKIP_PATHS):
            continue
        if path.is_file():
            out.append(path)
    return sorted(out)


def plain(heading: str) -> str:
    """A heading's text as it renders: a link keeps its text, an image drops out."""
    return LINK.sub(r"\1", IMAGE.sub("", heading))


# a paired emphasis delimiter: `*x*`, `**x**`, or `_x_` at a word boundary, never a lone `*`
EMPHASIS = re.compile(r"(\*{1,3}|(?<!\w)_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)")


def slug(heading: str) -> str:
    """The anchor GitHub derives from a heading.

    This is the rule of github-slugger, applied to the rendered text:
    links keep their text, backticks and paired emphasis markers are
    stripped (a lone `*` is punctuation, dropped after the trim), the
    rest is lowercased, and every character that is not a letter, a
    digit, a space, `-`, or `_` is dropped. Each space then
    becomes one hyphen, so a double space is `--`. Underscores stay
    (`EMPTY_UUID` anchors as `empty_uuid`). `anchors` numbers repeats;
    this function does not.
    """
    text = EMPHASIS.sub(r"\2", plain(heading).replace("`", "")).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def unfenced(text: str) -> str:
    """The text with every line of fenced code, fences included, blanked to spaces.

    A fence opens with three or more backticks or tildes, indented at most
    three spaces, and closes with a run of the same character at least as
    long, as CommonMark reads it. Blanked, not dropped, so offsets and line
    numbers still map back to the file.
    """
    out: list[str] = []
    opener: str | None = None
    for line in text.split("\n"):
        m = FENCE.match(line)
        if opener is None and m:
            opener = m.group(1)
            out.append(" " * len(line))
        elif opener is not None:
            if m and m.group(1)[0] == opener[0] and len(m.group(1)) >= len(opener) and not line.strip().strip(opener[0]):
                opener = None
            out.append(" " * len(line))
        else:
            out.append(line)
    return "\n".join(out)


def headings(text: str) -> list[tuple[int, str]]:
    """(level, title) for every ATX heading, in order, skipping fenced code.

    A closing sequence of `#` is not part of the title, as CommonMark
    reads it: `## Tables ##` is the heading `Tables`.
    """
    out: list[tuple[int, str]] = []
    for line in unfenced(text).splitlines():
        m = HEADING.match(line)
        if m:
            out.append((len(m.group(1)), CLOSING.sub("", m.group(2))))
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


def parser(doc: str | None) -> argparse.ArgumentParser:
    """The command line parser every script starts from.

    Abbreviations are off, so only a flag spelled in full is read.
    """
    return argparse.ArgumentParser(description=(doc or "").split("\n", 1)[0], allow_abbrev=False)


def arguments(doc: str | None, argv: Sequence[str], check: str | None = None) -> argparse.Namespace:
    """Parse a script's command line; an unknown argument exits 2.

    Every script parses its arguments here or through `parser`, so a
    typo such as `--chekc` stops the run instead of falling through to
    the default action. `check` is the help text of a `--check` flag,
    for the generators that have one.
    """
    p = parser(doc)
    if check is not None:
        p.add_argument("--check", action="store_true", help=check)
    return p.parse_args(list(argv))
