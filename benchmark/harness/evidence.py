"""What the judges get besides the artifact, and one check no model makes.

A judge that sees only a rubric and a review can grade how the review
reads. It cannot tell whether a cited defect is in the code, or whether
the review missed one. Two judges agreeing does not change that. So a
scenario can give the judges evidence:

- the target's source, with line numbers, so a finding that points at a
  file and a line can be checked against that line;
- the expected findings, the defects planted in the scenario's own
  target, so a miss is visible.

`named` is the part that is not a model's opinion: it counts which
planted findings the artifact names by lens id and file. It is a
mechanical cross-check beside the scores, not a score. A review can
name a finding and be wrong about it, and a review can find a planted
defect under another lens; the judges read for both.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SOURCE_LIMIT = 80_000
LENS_ID = re.compile(r"\b[A-Z]{2,4}-\d{2}\b")


def source(target: Path, globs: list[str], limit: int = SOURCE_LIMIT) -> str:
    """Every target file the globs name, once, in path order, with line numbers.

    Cut at `limit` characters and said so, as the artifact is.
    """
    found: set[Path] = set()
    for pattern in globs:
        for path in target.glob(pattern):
            if path.is_file():
                found.add(path)
    parts: list[str] = []
    for path in sorted(found):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
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


def render(expected_text: str | None, source_text: str) -> str:
    """The evidence section of the judge prompt, or an empty string when there is none."""
    parts: list[str] = []
    if expected_text:
        parts.append(
            "### Expected findings\n\n"
            "The defects planted in the target, and what the target does right.\n"
            "The subject never saw this list.\n\n"
            f"```yaml\n{expected_text.strip()}\n```"
        )
    if source_text:
        parts.append(source_text)
    return "\n\n".join(parts)
