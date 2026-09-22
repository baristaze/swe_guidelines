#!/usr/bin/env python3
"""Check that every lens file follows the format in lenses/README.md and cites a real section.

Rules:
- every group listed in lenses/README.md has a file, and every file is listed;
- lens headings are `## <PREFIX>-NN Title`, ids numbered 01.. in order and
  unique across every lens file, not only within one;
- every lens has Principle, Source, Look for, Violation, Severity, in that order;
- Source names sections of architecture.md by title, never by number:
  `<Section>` or `<Section>, <Subsection>`, several separated by `;`, where a
  bare `<Subsection>` after a `;` belongs to the section cited before it;
  a citation of a subsection may end in `(<Label>, <Label>)`, and each
  label is a bold paragraph label `**<Label>.**` inside that subsection,
  from its heading to the next heading of the same or a higher level; a
  citation of a whole section takes no parentheses;
- Severity is high, medium, or low;
- a Principle is at most 100 words, and Look for and Violation are at most
  three sentences each, so a lens stays one rule a reviewer can hold;
- a field value runs to the next field or lens heading, wrapped lines
  and list items included; fenced code is neither a lens nor a field,
  so a lens file can show lens syntax in an example;
- no line of a lens file is wider than 80 columns;
- a lens count stated in README.md or lenses/README.md ("N lenses") equals
  the size of the catalog;
- an optional Check field, after Severity, reads "`arch-check` decides it."
  or "`arch-check` decides <part>; the rest is judged.", and agrees both
  ways with the rules `checkers/src/arch_check/rules/` registers: a lens
  with a Check line has a rule of its id with the same coverage (`full`
  for the first sentence, `partial` for the second), and every rule has
  a lens that says so. The rules are read with `ast`, never imported;
- an identifier a lens quotes stays in the section it cites. A section is
  the text under its `##` heading, its subsections and code included; a
  citation of `<Section>, <Subsection>` cites the section. An identifier
  is a backticked name written as code: it holds an underscore, a
  lower-case letter before a capital, a dot, or a closing `()`
  (`org_id`, `OpContext`, `ctx.user_id`, `get_cache()`); a file name
  (`base.py`) is not one. Every identifier in a Principle is held to the
  cited sections, because the Principle restates them. An identifier in
  Look for or Violation is held to them when the guideline names it
  anywhere; one it never names is the lens's own example of a breach
  (`uuid4()`). `CROSS_REFERENCES` lists the few a lens names from
  another section on purpose. A renamed or moved identifier fails here
  before a reader meets it.

Exit status is non-zero when any rule fails. Standard library only.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import NUMBERED_REFERENCE, arguments, fenced_lines, headings, unfenced

ROOT = Path(__file__).resolve().parent.parent
GUIDELINE = ROOT / "architecture.md"
LENSES = ROOT / "lenses"
README = ROOT / "README.md"
RULES = ROOT / "checkers" / "src" / "arch_check" / "rules"

FIELDS = ("Principle", "Source", "Look for", "Violation", "Severity")
SEVERITIES = {"high", "medium", "low"}
HEADING = re.compile(r"^## ([A-Z]{2,3})-(\d{2}) (.+)$")
OPTIONAL = "Check"
FIELD = re.compile(r"^\*\*(Principle|Source|Look for|Violation|Severity|Check)\.\*\*\s*(.*)$")
CHECK_FULL = "`arch-check` decides it."
CHECK_PARTIAL = re.compile(r"^`arch-check` decides (.+); the rest is judged\.$")
LIST_MARKER = re.compile(r"^(?:[-*+]|\d+\.)\s+")
NUMBERED = NUMBERED_REFERENCE
TABLE_ROW = re.compile(r"^\|\s*`([a-z]+)`\s*\|\s*`([a-z]+\.md)`\s*\|")
SKIP_SECTIONS = {"Contents"}
LABELLED = re.compile(r"^(.*?)\s*\(([^()]*)\)$")
BOLD_LABEL = re.compile(r"\*\*([^*]+?)\.\*\*")
COUNT = re.compile(r"\b(\d+) lenses\b")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
IDENTIFIER = re.compile(r"(?=.*(?:_|[a-z][A-Z]|\.|\(\)$))[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\(\))?")
FILE_NAME = re.compile(r".+\.(?:py|pyi|toml|json|jsonc|html|md|txt|ya?ml|sql|ts|tsx|js|mjs|lock|cfg|ini|sh|env)")
CODE_SPAN = re.compile(r"`([^`]+)`")
CROSS_REFERENCES = frozenset(
    {
        ("CTX-24", "created_by"),  # the provenance field an operator row stamps, defined with the OM root
        ("OM-15", "utcnow()"),  # the clock helper of the OM root, named as what a pure rule never calls
    }
)
"""(lens, identifier) pairs a lens quotes from a section it does not cite, on purpose."""
MAX_PRINCIPLE_WORDS = 100
MAX_SENTENCES = 3
MAX_COLUMNS = 80


def sections() -> dict[str, set[str]]:
    """Map each section title to the set of its subsection titles, skipping fenced code."""
    out: dict[str, set[str]] = {}
    current: str | None = None
    for level, title in headings(GUIDELINE.read_text(encoding="utf-8")):
        if level == 2:
            current = None if title in SKIP_SECTIONS else title
            if current is not None:
                out[current] = set()
        elif level == 3 and current is not None:
            out[current].add(title)
    return out


def paragraph_labels() -> dict[tuple[str, str], set[str]]:
    """Map each (section, subsection) to the bold paragraph labels in its text.

    A subsection's text runs from its heading to the next heading of the
    same or a higher level, so a deeper heading stays inside it. Fenced
    code holds no label.
    """
    out: dict[tuple[str, str], set[str]] = {}
    section: str | None = None
    current: tuple[str, str] | None = None
    for line in unfenced(GUIDELINE.read_text(encoding="utf-8")).splitlines():
        m = re.match(r"^(#{1,3}) (.+)$", line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            section = title if level == 2 else section if level == 3 else None
            current = (section, title) if level == 3 and section is not None else None
            if current is not None:
                out.setdefault(current, set())
            continue
        if current is not None:
            out[current].update(BOLD_LABEL.findall(line))
    return out


def section_texts() -> dict[str, str]:
    """Map each section title to its text: from its `##` heading to the next, subsections and code included."""
    text = GUIDELINE.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line, code in zip(text.split("\n"), fenced_lines(text), strict=True):
        m = None if code else re.match(r"^## (.+?)\s*$", line)
        if m:
            current = m.group(1)
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return {title: "\n".join(lines) for title, lines in out.items()}


def cited_sections(value: str, known: dict[str, set[str]]) -> list[str]:
    """The sections a Source value cites, in order: `<Section>, <Subsection>` cites its section."""
    out: list[str] = []
    for citation in (c.strip().rstrip(".") for c in value.split(";") if c.strip()):
        m = LABELLED.match(citation)
        citation = m.group(1).strip() if m else citation
        head = citation.partition(", ")[0]
        section = citation if citation in known else head if head in known else None
        if section is not None and section not in out:
            out.append(section)
    return out


def names(text: str, identifier: str) -> bool:
    """Whether `text` names an identifier as a whole word; a call is named by its function (`get_cache` for `get_cache()`)."""
    name = identifier.removesuffix("()")
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None


def check_identifiers(
    lens_id: str,
    fields: list[tuple[str, str, int]],
    path: Path,
    known: dict[str, set[str]],
    texts: dict[str, str],
    errors: list[str],
) -> None:
    """Every identifier the lens quotes is in a section it cites (see the module docstring)."""
    source = next((value for name, value, _ in fields if name == "Source"), "")
    cited = cited_sections(source, known)
    if not cited:
        return  # an unknown citation is reported by check_source
    held = "\n".join(texts.get(c, "") for c in cited)
    everywhere = GUIDELINE.read_text(encoding="utf-8")
    for name, value, ln in fields:
        if name not in ("Principle", "Look for", "Violation"):
            continue
        for span in CODE_SPAN.findall(value):
            identifier = span.strip()
            if not IDENTIFIER.fullmatch(identifier) or FILE_NAME.fullmatch(identifier):
                continue
            if names(held, identifier) or (lens_id, identifier) in CROSS_REFERENCES:
                continue
            if name != "Principle" and not names(everywhere, identifier):
                continue  # the lens's own example of a breach, which the guideline never names
            errors.append(f"{path.name}:{ln}: {lens_id} quotes `{identifier}`, which {' and '.join(cited)} does not hold")


def listed_groups() -> dict[str, str]:
    groups: dict[str, str] = {}
    for line in (LENSES / "README.md").read_text(encoding="utf-8").splitlines():
        m = TABLE_ROW.match(line)
        if m:
            groups[m.group(1)] = m.group(2)
    return groups


def check_source(
    value: str,
    path: Path,
    ln: int,
    known: dict[str, set[str]],
    errors: list[str],
    labels: dict[tuple[str, str], set[str]] | None = None,
) -> None:
    """A source is one or more citations separated by ';'.

    Each citation is `<Section>`, `<Section>, <Subsection>`, or, after a
    previous citation, a bare `<Subsection>` of that section. Titles are
    matched exactly against the headings of architecture.md. A citation
    of a subsection may end in `(<Label>, ...)`: each label is matched,
    case and all, against the `**<Label>.**` paragraph labels of that
    subsection.
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
        cited: list[str] | None = None
        m = LABELLED.match(citation)
        if m:
            citation, cited = m.group(1).strip(), [label.strip() for label in m.group(2).split(",")]
        sub: str | None = None
        head, _, tail = citation.partition(", ")
        if citation in known:
            sec = citation
        elif head in known and tail.strip() in known[head]:
            sec, sub = head, tail.strip()
        elif sec is not None and citation in known[sec]:
            sub = citation
        else:
            if head in known:
                errors.append(f"{path.name}:{ln}: '{head}' has no subsection '{tail.strip()}'")
            elif sec is not None:
                errors.append(f"{path.name}:{ln}: '{citation}' is neither a section nor a subsection of '{sec}'")
            else:
                errors.append(f"{path.name}:{ln}: '{citation}' is not a section of architecture.md")
            continue
        if cited is None:
            continue
        if sub is None:
            errors.append(f"{path.name}:{ln}: '{citation}' names no subsection, so it takes no labels in parentheses")
            continue
        if labels is None:
            labels = paragraph_labels()
        found = labels.get((str(sec), sub), set())
        for label in cited:
            if label not in found:
                errors.append(f"{path.name}:{ln}: '{sec}, {sub}' has no paragraph labelled '**{label}.**'")


def registered_rules(errors: list[str]) -> dict[str, tuple[str, str]]:
    """Every `@rule("<ID>", coverage=...)` under the rules package: id -> (coverage, file)."""
    out: dict[str, tuple[str, str]] = {}
    if not RULES.is_dir():
        return out
    for path in sorted(RULES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "rule"):
                continue
            if not (node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                continue
            coverage = next(
                (k.value.value for k in node.keywords if k.arg == "coverage" and isinstance(k.value, ast.Constant)), None
            )
            rid = node.args[0].value
            rel = path.relative_to(ROOT).as_posix()
            if rid in out:
                errors.append(f"{rel}:{node.lineno}: rule {rid} is registered twice")
            out[rid] = (str(coverage), rel)
    return out


def check_file(
    path: Path,
    known: dict[str, set[str]],
    errors: list[str],
    checks: dict[str, tuple[str, int]] | None = None,
    ids: dict[str, str] | None = None,
    labels: dict[tuple[str, str], set[str]] | None = None,
    texts: dict[str, str] | None = None,
) -> int:
    """Check one lens file; return its lens count.

    `ids` maps every lens id seen so far to the file that holds it, so a
    second file reusing a prefix and number is caught.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    prefix: str | None = None
    expected = 1
    count = 0
    i = 0
    for ln, line in enumerate(lines, start=1):
        if NUMBERED.search(line):
            errors.append(f"{path.name}:{ln}: refers to a section by number")
        if len(line) > MAX_COLUMNS:
            errors.append(f"{path.name}:{ln}: {len(line)} columns, limit {MAX_COLUMNS}")
    fenced = {n for n, code in enumerate(fenced_lines(text)) if code}
    while i < len(lines):
        m = None if i in fenced else HEADING.match(lines[i])
        if not m:
            i += 1
            continue
        count += 1
        pre, num, _title = m.group(1), int(m.group(2)), m.group(3)
        lens_id = f"{pre}-{num:02d}"
        where = f"{path.name}:{i + 1}"
        if prefix is None:
            prefix = pre
        elif pre != prefix:
            errors.append(f"{where}: prefix {pre} differs from {prefix}")
        if num != expected:
            errors.append(f"{where}: expected id {prefix}-{expected:02d}, found {pre}-{num:02d}")
        expected = num + 1
        if ids is not None:
            if lens_id in ids and ids[lens_id] != path.name:
                errors.append(f"{where}: id {lens_id} is already used in {ids[lens_id]}")
            ids.setdefault(lens_id, path.name)
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
        found = [f[0] for f in fields]
        if found not in (list(FIELDS), [*FIELDS, OPTIONAL]):
            errors.append(f"{where}: fields are {found}, expected {list(FIELDS)}, optionally followed by {OPTIONAL}")
        check_identifiers(lens_id, fields, path, known, section_texts() if texts is None else texts, errors)
        for name, value, ln in fields:
            if name == "Severity" and value.strip("` ") not in SEVERITIES:
                errors.append(f"{path.name}:{ln}: severity '{value}' is not high, medium, or low")
            if name == "Source":
                check_source(value, path, ln, known, errors, labels)
            if name == "Principle" and len(value.split()) > MAX_PRINCIPLE_WORDS:
                errors.append(f"{path.name}:{ln}: Principle is {len(value.split())} words, limit {MAX_PRINCIPLE_WORDS}")
            if name == OPTIONAL:
                if value == CHECK_FULL:
                    coverage = "full"
                elif CHECK_PARTIAL.match(value):
                    coverage = "partial"
                else:
                    errors.append(f"{path.name}:{ln}: Check reads '{value}'; see lenses/README.md for its two sentences")
                    continue
                if checks is not None:
                    checks[lens_id] = (coverage, ln)
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
    labels = paragraph_labels()
    texts = section_texts()
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
    checks: dict[str, tuple[str, int]] = {}
    lens_files: dict[str, str] = {}
    ids: dict[str, str] = {}
    for name in sorted(files):
        before = set(checks)
        total += check_file(files[name], known, errors, checks, ids, labels, texts)
        lens_files.update(dict.fromkeys(set(checks) - before, name))
    rules = registered_rules(errors)
    for lens_id, (coverage, ln) in sorted(checks.items()):
        where = f"{lens_files[lens_id]}:{ln}"
        if lens_id not in rules:
            errors.append(f"{where}: {lens_id} says arch-check decides it, and no rule of that id is registered")
        elif rules[lens_id][0] != coverage:
            errors.append(f"{where}: {lens_id} Check line says {coverage}, its rule registers {rules[lens_id][0]}")
    for rid, (_coverage, rel) in sorted(rules.items()):
        if rid not in checks:
            errors.append(f"{rel}: rule {rid} is registered, and lens {rid} has no Check line")
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
