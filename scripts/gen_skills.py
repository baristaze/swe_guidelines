#!/usr/bin/env python3
"""Generate the eight arch-review-<group> skills from one template and the lens catalog.

Source of truth:
- skills/_template/review.SKILL.md   the procedure and report format
- lenses/README.md                   the group table (id, file, covers)
- lenses/<group>.md                  the H1 title of each group

`--check` exits non-zero when any generated file differs from what the
template would produce. Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "skills" / "_template" / "review.SKILL.md"
LENSES = ROOT / "lenses"
SKILLS = ROOT / "skills"
TABLE_ROW = re.compile(r"^\|\s*`([a-z]+)`\s*\|\s*`([a-z]+\.md)`\s*\|\s*(.+?)\s*\|\s*$")


def groups() -> list[tuple[str, str, str]]:
    """(group id, lens file name, covers text) in README order."""
    out = []
    for line in (LENSES / "README.md").read_text(encoding="utf-8").splitlines():
        m = TABLE_ROW.match(line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def title_of(lens_file: Path) -> str:
    for line in lens_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    raise SystemExit(f"{lens_file}: no H1 title")


def render(group: str, title: str, covers: str) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    return (
        text.replace("{group}", group)
        .replace("{title}", title)
        .replace("{covers}", covers)
    )


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    written = 0
    for group, filename, covers in groups():
        content = render(group, title_of(LENSES / filename), covers)
        target = SKILLS / f"arch-review-{group}" / "SKILL.md"
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == content:
            continue
        if check:
            stale.append(str(target.relative_to(ROOT)))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written += 1
    if check:
        if stale:
            print("stale generated skills (run `make gen-skills`):")
            print("\n".join(f"  {s}" for s in stale))
            return 1
        print("generated skills ok")
        return 0
    print(f"generated skills: {written} written, {len(groups()) - written} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
