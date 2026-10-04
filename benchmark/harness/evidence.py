"""What the judges get besides the artifact, and one check no model makes.

A judge that sees only a rubric and a review can grade how the review
reads. It cannot tell whether a cited defect is in the code, or whether
the review missed one. Two judges agreeing does not change that. So a
scenario can give the judges evidence:

- the target's source, with line numbers, so a finding that points at a
  file and a line can be checked against that line;
- the expected findings, the defects planted in the scenario's own
  target, so a miss is visible;
- the text of each lens the artifact cites, from this checkout, so a
  finding is weighed against what the lens says and not against what
  a judge remembers of it.

`named` is the part that is not a model's opinion: it counts which
planted findings the artifact names by lens id and file. It is a
mechanical cross-check beside the scores, not a score. A review can
name a finding and be wrong about it, and a review can find a planted
defect under another lens; the judges read for both.
"""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path
from typing import Any

SOURCE_LIMIT = 80_000
# About thirty lenses, and a whole group of them. Every judge of every
# repeat reads it, so it bounds what the lens text adds to a run's spend.
LENS_LIMIT = 40_000
# An answer can name any number of ids; the ones no lens carries are
# listed up to this many.
UNKNOWN_SHOWN = 20
LENS_ID = re.compile(r"\b[A-Z]{2,4}-\d{2}\b")
LENS_HEADING = re.compile(r"^## ([A-Z]{2,4}-\d{2}) ", re.MULTILINE)
SECTION = re.compile(r"^## ", re.MULTILINE)


def source(target: Path, globs: list[str], limit: int = SOURCE_LIMIT) -> str:
    """Every target file the globs name, once, in path order, with line numbers.

    Cut at `limit` characters and said so, as the artifact is. A path that
    leaves the target, through `..` in a glob or a symlink, is left out:
    what the judges read is the target's own source and nothing beside it.
    """
    root = target.resolve()
    found: set[Path] = set()
    for pattern in globs:
        for path in target.glob(pattern):
            if path.is_symlink() or not path.is_file():
                continue
            if not path.resolve().is_relative_to(root):
                continue
            found.add(path)
    parts: list[str] = []
    for path in sorted(found):
        # Split on the newline alone, as an editor and `grep -n` count lines.
        body = path.read_text(encoding="utf-8", errors="replace").removesuffix("\n")
        lines = body.split("\n") if body else []
        width = len(str(len(lines)))
        numbered = "\n".join(f"{n:>{width}} | {line}" for n, line in enumerate(lines, 1))
        parts.append(f"### Source: {path.relative_to(target).as_posix()}\n\n```text\n{numbered}\n```")
    text = "\n\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + f"\n\n[... source truncated at {limit} characters of {len(text)} ...]"
    return text


def planted(expected: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The planted findings of an expected file, each with an id, a lens, and a file."""
    if not expected:
        return []
    out = []
    for raw in expected.get("findings") or []:
        if isinstance(raw, dict) and raw.get("id") and raw.get("lens") and raw.get("file"):
            out.append(raw)
    return out


def _names(artifact: str, lens: str, basename: str) -> bool:
    """Whether some mention of the lens has the file near it.

    Near means on the same line, or between that mention and the next
    lens id: a table row, a bullet, or a heading and the paragraphs
    under it. `basename` is whatever names the file unambiguously: its
    base name, or a longer tail of its path when two planted files share
    a base name.
    """
    mentions = list(LENS_ID.finditer(artifact))
    for i, m in enumerate(mentions):
        if m.group(0) != lens:
            continue
        end = mentions[i + 1].start() if i + 1 < len(mentions) else len(artifact)
        line_start = artifact.rfind("\n", 0, m.start()) + 1
        line_end = artifact.find("\n", m.end())
        line = artifact[line_start : line_end if line_end >= 0 else len(artifact)]
        if basename in artifact[m.start() : end] or basename in line:
            return True
    return False


def shortest_name(path: str, paths: set[str]) -> str:
    """The shortest tail of a path no other planted path ends with: `task.py`, or `tasks/impl/__init__.py`."""
    parts = Path(path).parts
    for n in range(1, len(parts) + 1):
        tail = "/".join(parts[-n:])
        if not any(other != path and (other == tail or other.endswith("/" + tail)) for other in paths):
            return tail
    return path


def named(expected: dict[str, Any] | None, artifact: str) -> dict[str, Any] | None:
    """Which planted findings the artifact names by lens id and file, and which it misses."""
    findings = planted(expected)
    if not findings:
        return None
    hit: list[str] = []
    miss: list[str] = []
    paths = {str(f["file"]) for f in findings}
    for f in findings:
        (hit if _names(artifact, str(f["lens"]), shortest_name(str(f["file"]), paths)) else miss).append(str(f["id"]))
    return {"expected": len(findings), "named": hit, "missed": miss}


def lens_sections(lenses_dir: Path) -> dict[str, str]:
    """Every lens in `lenses_dir`, by id: its section of `<group>.md`, from its heading to the next.

    `README.md` defines the format, so it holds no lens, whatever it shows.
    """
    out: dict[str, str] = {}
    for path in sorted(lenses_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        starts = [m.start() for m in SECTION.finditer(text)] + [len(text)]
        for start, end in pairwise(starts):
            heading = LENS_HEADING.match(text, start)
            if heading:
                out[heading.group(1)] = text[start:end].strip()
    return out


def cited(lenses_dir: Path, artifact: str, limit: int = LENS_LIMIT) -> str:
    """The text of each lens the artifact cites, once, in the order it first cites them.

    The ids come from a model's answer, so an id brings text in only when
    a lens carries it. An id of a lens group that no lens carries is
    listed by name, up to `UNKNOWN_SHOWN` of them, so a judge sees it
    does not exist. The text is cut at `limit` characters and says so.
    """
    sections = lens_sections(lenses_dir)
    prefixes = {lens.split("-")[0] for lens in sections}
    ids = list(dict.fromkeys(LENS_ID.findall(artifact)))
    # A lens heading sits under the prompt's own, so it drops two levels.
    text = "\n\n".join("##" + sections[lens] for lens in ids if lens in sections)
    if len(text) > limit:
        text = text[:limit] + f"\n\n[... lenses truncated at {limit} characters of {len(text)} ...]"
    unknown = [lens for lens in ids if lens not in sections and lens.split("-")[0] in prefixes]
    if unknown:
        more = len(unknown) - UNKNOWN_SHOWN
        listed = ", ".join(unknown[:UNKNOWN_SHOWN]) + (f", and {more} more" if more > 0 else "")
        text = (text + "\n\n" if text else "") + f"No lens has the id {listed}."
    if not text:
        return ""
    return (
        "### Lenses the artifact cites\n\n"
        "The text of each lens, as this checkout holds it. A finding is\n"
        "weighed against what its lens says.\n\n" + text
    )


def render(expected_text: str | None, source_text: str, lens_text: str = "") -> str:
    """The evidence section of the judge prompt, or an empty string when there is none."""
    parts: list[str] = []
    if expected_text:
        parts.append(
            "### Expected findings\n\n"
            "The defects planted in the target, and what the target does right.\n"
            "The subject never saw this list.\n\n"
            f"```yaml\n{expected_text.strip()}\n```"
        )
    if lens_text:
        parts.append(lens_text)
    if source_text:
        parts.append(source_text)
    return "\n\n".join(parts)
