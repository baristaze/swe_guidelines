#!/usr/bin/env python3
"""Check that agents/arch-reviewer.md mirrors skills/_template/review.SKILL.md.

The subagent that arch-review-full fans out to carries the same
procedure and the same report shape as the generated review skills,
and nothing generates it, so this check holds the two files together:

- the four decision words (finding, pass, not applicable, unverified)
  appear in bold in the procedure of both, in the same order;
- the report block, from the ```markdown fence to its closing fence,
  is identical in both, the template's `{title}` read as the agent's
  `<group title>`;
- the numbered procedure steps agree in count.

Exit status is non-zero on any failure. Standard library only.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import arguments

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "skills" / "_template" / "review.SKILL.md"
AGENT = ROOT / "agents" / "arch-reviewer.md"

DECISIONS = ("finding", "pass", "not applicable", "unverified")
STEP = re.compile(r"^\d+\. ", re.M)
PLACEHOLDERS = {"{title}": "<group title>"}


def procedure(text: str, rel: str, errors: list[str]) -> str:
    """The procedure text: from its heading to the report block."""
    m = re.search(r"^(## Procedure|Procedure\b)[^\n]*\n(.*?)(?=^## Output|^```markdown)", text, re.M | re.S)
    if not m:
        errors.append(f"{rel}: no procedure before the report block")
        return ""
    return m.group(2)


def report_block(text: str, rel: str, errors: list[str]) -> str:
    m = re.search(r"^```markdown\n.*?^```$", text, re.M | re.S)
    if not m:
        errors.append(f"{rel}: no ```markdown report block")
        return ""
    block = m.group(0)
    for placeholder, stand_in in PLACEHOLDERS.items():
        block = block.replace(placeholder, stand_in)
    return block


def decision_order(proc: str) -> list[str]:
    flat = re.sub(r"\s+", " ", proc)
    found = [(flat.find(f"**{word}**"), word) for word in DECISIONS]
    return [word for pos, word in sorted(found) if pos >= 0]


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    files = {}
    for path in (TEMPLATE, AGENT):
        rel = str(path.relative_to(ROOT))
        if not path.exists():
            errors.append(f"{rel}: missing")
            continue
        files[rel] = path.read_text(encoding="utf-8")
    if errors:
        print("\n".join(errors))
        return 1
    (t_rel, t_text), (a_rel, a_text) = files.items()
    t_proc = procedure(t_text, t_rel, errors)
    a_proc = procedure(a_text, a_rel, errors)
    for rel, proc in ((t_rel, t_proc), (a_rel, a_proc)):
        missing = [w for w in DECISIONS if w not in decision_order(proc)]
        if missing:
            errors.append(f"{rel}: procedure lacks the bold decision word(s) {', '.join(missing)}")
    if decision_order(t_proc) != decision_order(a_proc):
        errors.append(f"{a_rel}: decision words are in a different order than {t_rel}")
    t_steps, a_steps = len(STEP.findall(t_proc)), len(STEP.findall(a_proc))
    if t_steps != a_steps:
        errors.append(f"{a_rel}: {a_steps} procedure steps, {t_rel} has {t_steps}")
    t_block = report_block(t_text, t_rel, errors)
    a_block = report_block(a_text, a_rel, errors)
    if t_block and a_block and t_block != a_block:
        errors.append(f"{a_rel}: report block differs from {t_rel}")
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} problem(s)")
        return 1
    print(f"agents ok: {a_rel} mirrors {t_rel} ({a_steps} steps)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
