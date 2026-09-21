#!/usr/bin/env python3
"""Check that every lens file follows the format in lenses/README.md and cites a real section.

Rules:
- every group listed in lenses/README.md has a file, and every file is listed;
- lens headings are `## <PREFIX>-NN Title`, ids unique and numbered 01.. in order;
- every lens has Principle, Source, Look for, Violation, Severity, in that order;
- Source names sections of architecture.md by title, never by number:
  `<Section>` or `<Section>, <Subsection>`, several separated by `;`, where a
  bare `<Subsection>` after a `;` belongs to the section cited before it;
- Severity is high, medium, or low;
- a Principle is at most 60 words, and Look for and Violation are at most
  three sentences each, so a lens stays one rule a reviewer can hold;
- a field value runs to the next field or lens heading, wrapped lines
  and list items included; fenced code is neither a lens nor a field,
  so a lens file can show lens syntax in an example;
- no line of a lens file is wider than 80 columns;
- a lens count stated in README.md or lenses/README.md ("N lenses") equals
  the size of the catalog.

Exit status is non-zero when any rule fails. Standard library only.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import arguments

ROOT = Path(__file__).resolve().parent.parent
GUIDELINE = ROOT / "architecture.md"
LENSES = ROOT / "lenses"
README = ROOT / "README.md"

FIELDS = ("Principle", "Source", "Look for", "Violation", "Severity")
SEVERITIES = {"high", "medium", "low"}
HEADING = re.compile(r"^## ([A-Z]{2,3})-(\d{2}) (.+)$")
FIELD = re.compile(r"^\*\*(Principle|Source|Look for|Violation|Severity)\.\*\*\s*(.*)$")
LIST_MARKER = re.compile(r"^(?:[-*+]|\d+\.)\s+")
NUMBERED = re.compile(r"\bSections? \d+")
TABLE_ROW = re.compile(r"^\|\s*`([a-z]+)`\s*\|\s*`([a-z]+\.md)`\s*\|")
SKIP_SECTIONS = {"Contents"}
COUNT = re.compile(r"\b(\d+) lenses\b")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
MAX_PRINCIPLE_WORDS = 60
MAX_SENTENCES = 3
MAX_COLUMNS = 80


def sections() -> dict[str, set[str]]:
    """Map each section title to the set of its subsection titles, skipping fenced code."""
    out: dict[str, set[str]] = {}
    current: str | None = None
    in_fence = False
    for line in GUIDELINE.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^## (.+)$", line)
        if m:
            current = m.group(1).strip()
            if current in SKIP_SECTIONS:
                current = None
                continue
            out[current] = set()
            continue
        m = re.match(r"^### (.+)$", line)
        if m and current is not None:
            out[current].add(m.group(1).strip())
    return out


def listed_groups() -> dict[str, str]:
    groups: dict[str, str] = {}
    for line in (LENSES / "README.md").read_text(encoding="utf-8").splitlines():
        m = TABLE_ROW.match(line)
        if m:
            groups[m.group(1)] = m.group(2)
    return groups


def check_source(value: str, path: Path, ln: int, known: dict[str, set[str]], errors: list[str]) -> None:
    """A source is one or more citations separated by ';'.

    Each citation is `<Section>`, `<Section>, <Subsection>`, or, after a
    previous citation, a bare `<Subsection>` of that section. Titles are
    matched exactly against the headings of architecture.md.
    """
    if NUMBERED.search(value):
        errors.append(f"{path.name}:{ln}: cites a section by number: '{value}'")
        return
    citations = [c.strip().rstrip(".") for c in value.split(";") if c.strip()]
    if not citations:
        errors.append(f"{path.name}:{ln}: empty source")
        return
    sec: str | None = None
    for citation in citations:
        citation = re.sub(r"\s*\([^)]*\)\s*$", "", citation).strip()
        if citation in known:
            sec = citation
            continue
        head, _, tail = citation.partition(", ")
        if head in known and tail.strip() in known[head]:
            sec = head
            continue
        if sec is not None and citation in known[sec]:
            continue
        if head in known:
            errors.append(f"{path.name}:{ln}: '{head}' has no subsection '{tail.strip()}'")
        elif sec is not None:
            errors.append(f"{path.name}:{ln}: '{citation}' is neither a section nor a subsection of '{sec}'")
        else:
            errors.append(f"{path.name}:{ln}: '{citation}' is not a section of architecture.md")


def check_file(path: Path, known: dict[str, set[str]], errors: list[str]) -> int:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    prefix: str | None = None
    expected = 1
    count = 0
    i = 0
    for ln, line in enumerate(lines, start=1):
        if NUMBERED.search(line):
            errors.append(f"{path.name}:{ln}: refers to a section by number")
        if len(line) > MAX_COLUMNS:
            errors.append(f"{path.name}:{ln}: {len(line)} columns, limit {MAX_COLUMNS}")
    fenced: set[int] = set()
    in_fence = False
    for n, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
            fenced.add(n)
        elif in_fence:
            fenced.add(n)
    while i < len(lines):
        m = None if i in fenced else HEADING.match(lines[i])
        if not m:
            i += 1
            continue
        count += 1
        pre, num, _title = m.group(1), int(m.group(2)), m.group(3)
        where = f"{path.name}:{i + 1}"
        if prefix is None:
            prefix = pre
        elif pre != prefix:
            errors.append(f"{where}: prefix {pre} differs from {prefix}")
        if num != expected:
            errors.append(f"{where}: expected id {prefix}-{expected:02d}, found {pre}-{num:02d}")
        expected = num + 1
        # collect fields until next heading
        fields: list[tuple[str, str, int]] = []
        j = i + 1
        while j < len(lines) and (j in fenced or not HEADING.match(lines[j])):
            fm = None if j in fenced else FIELD.match(lines[j])
            if fm:
                fields.append((fm.group(1), fm.group(2).strip(), j + 1))
            elif fields and j not in fenced and lines[j].strip():
                # a wrapped line or a list item continues the field before it
                name, value, ln = fields[-1]
                line = LIST_MARKER.sub("", lines[j].strip())
                fields[-1] = (name, f"{value} {line}".strip(), ln)
            j += 1
        names = [f[0] for f in fields]
        if names != list(FIELDS):
            errors.append(f"{where}: fields are {names}, expected {list(FIELDS)}")
        for name, value, ln in fields:
            if name == "Severity" and value.strip("` ") not in SEVERITIES:
                errors.append(f"{path.name}:{ln}: severity '{value}' is not high, medium, or low")
            if name == "Source":
                check_source(value, path, ln, known, errors)
            if name == "Principle" and len(value.split()) > MAX_PRINCIPLE_WORDS:
                errors.append(f"{path.name}:{ln}: Principle is {len(value.split())} words, limit {MAX_PRINCIPLE_WORDS}")
            if name in ("Look for", "Violation"):
                n = len(SENTENCE_END.findall(value))
                if n > MAX_SENTENCES:
                    errors.append(f"{path.name}:{ln}: {name} is {n} sentences, limit {MAX_SENTENCES}")
        i = j
    if count == 0:
        errors.append(f"{path.name}: no lenses found")
    return count


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    known = sections()
    groups = listed_groups()
    files = {p.name: p for p in LENSES.glob("*.md") if p.name != "README.md"}
    for group, filename in groups.items():
        if filename not in files:
            errors.append(f"lenses/README.md lists {group} -> {filename}, file missing")
    for name in files:
        if name not in groups.values():
            errors.append(f"lenses/{name} is not listed in lenses/README.md")
    for ln, line in enumerate((LENSES / "README.md").read_text(encoding="utf-8").splitlines(), 1):
        if NUMBERED.search(line):
            errors.append(f"README.md:{ln}: refers to a section by number")
    total = 0
    for name in sorted(files):
        total += check_file(files[name], known, errors)
    for path in (README, LENSES / "README.md"):
        if not path.exists():
            continue
        for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in COUNT.finditer(line):
                if int(m.group(1)) != total:
                    errors.append(f"{path.relative_to(ROOT)}:{ln}: says {m.group(1)} lenses, the catalog has {total}")
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} problem(s) in {len(files)} lens file(s)")
        return 1
    print(f"lenses ok: {total} lenses in {len(files)} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
