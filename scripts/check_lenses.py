#!/usr/bin/env python3
"""Check that every lens file follows the format in lenses/README.md and cites a real section.

A lens holds the checkable detail under a rule the guideline states. It is
stricter than the story, never contrary to it. This check holds its shape,
its citations, and the names it quotes; that it stays inside its rule is
held by review.

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
- a Principle is at most 120 words, and Look for and Violation are at most
  three sentences each, so a lens stays one rule a reviewer can hold;
- a field value runs to the next field or lens heading, wrapped lines
  and list items included; fenced code is neither a lens nor a field,
  so a lens file can show lens syntax in an example;
- no line of a lens file is wider than 80 columns;
- a lens count stated in README.md or lenses/README.md ("N lenses") equals
  the size of the catalog;
- an optional Shape field, after Severity, names one or two files or
  folders of the scaffold that show the rule, each a path in backticks
  that starts `scaffold/acme_root/` and exists, separated by a comma or
  `and`; a review compares the code with them;
- an optional Check field, after Severity and Shape, reads "`arch-check` decides it."
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
  before a reader meets it. A section's text includes its agents-only
  blocks (`<!-- agents-only ... -->`), which a rendered page hides and an
  agent reads, and no other HTML comment. It also includes the code of
  every scaffold file the section links (`[..](scaffold/...)`), a folder
  standing for the code files under it, so an identifier the text leaves
  to the scaffold is still held, and a rename in the scaffold fails here;
- a tag is one of `core`, `default`, `optional`, and `style`, in inline
  code, alone on the first line under a `##` or `###` heading. A lone
  backticked word there that is none of the four is refused. A tag covers
  its own heading's text, not the headings below it;
- a lens whose every citation names a section or subsection tagged
  `style` is `low`: a house convention's breach is a low finding at most.

Exit status is non-zero when any rule fails. Standard library only.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import NUMBERED_REFERENCE, arguments, commented_lines, fenced_lines, headings, unfenced

ROOT = Path(__file__).resolve().parent.parent
GUIDELINE = ROOT / "architecture.md"
LENSES = ROOT / "lenses"
README = ROOT / "README.md"
RULES = ROOT / "checkers" / "src" / "arch_check" / "rules"
SCAFFOLD = ROOT / "scaffold"

FIELDS = ("Principle", "Source", "Look for", "Violation", "Severity")
SEVERITIES = {"high", "medium", "low"}
HEADING = re.compile(r"^## ([A-Z]{2,3})-(\d{2}) (.+)$")
OPTIONAL = "Check"
SHAPE = "Shape"
ORDERS = (list(FIELDS), [*FIELDS, SHAPE], [*FIELDS, OPTIONAL], [*FIELDS, SHAPE, OPTIONAL])
"""The fields of a lens, in order: the five, then Shape and Check when the lens has them."""
FIELD = re.compile(r"^\*\*(Principle|Source|Look for|Violation|Severity|Shape|Check)\.\*\*\s*(.*)$")
SHAPE_ROOT = "scaffold/acme_root/"
MAX_SHAPES = 2
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
TAGS = ("core", "default", "optional", "style")
TAG_LINE = re.compile(r"^`([a-z]+)`$")
SCAFFOLD_LINK = re.compile(r"\]\(\s*<?(scaffold/[^)\s>#]*)")
CODE_SUFFIXES = frozenset(
    {".py", ".pyi", ".sql", ".ts", ".tsx", ".js", ".mjs", ".tf", ".hcl", ".toml", ".json", ".yml", ".yaml", ".sh"}
)
CODE_NAMES = frozenset({"Makefile", "Dockerfile"})
SKIPPED_DIRS = frozenset({"node_modules", ".venv", "__pycache__", "dist", "build", ".git"})
"""What counts as the scaffold's code when a section links a file of it, or a folder."""
CROSS_REFERENCES = frozenset(
    {
        ("CTX-24", "created_by"),  # the provenance field an operator row stamps, defined with the OM root
        ("OM-15", "utcnow()"),  # the clock helper of the OM root, named as what a pure rule never calls
    }
)
"""(lens, identifier) pairs a lens quotes from a section it does not cite, on purpose."""
MAX_PRINCIPLE_WORDS = 120
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
    text = GUIDELINE.read_text(encoding="utf-8")
    for line, comment in zip(unfenced(text).split("\n"), commented_lines(text), strict=True):
        if comment:
            continue  # a label a rendered page hides is no label to cite
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


def guideline_text() -> str:
    """The guideline as an agent reads it: every line but those of an HTML comment that is not an agents-only block."""
    text = GUIDELINE.read_text(encoding="utf-8")
    kept = (line for line, comment in zip(text.split("\n"), commented_lines(text), strict=True) if comment != "comment")
    return "\n".join(kept)


def scaffold_code(target: str, cache: dict[str, str]) -> str:
    """The code a link into the scaffold names: the file, or every code file under the folder; empty when it is missing.

    A missing target is `check_links.py`'s to report; here it simply holds nothing.
    """
    if target not in cache:
        path = (ROOT / target).resolve()
        if not path.is_relative_to(SCAFFOLD.resolve()):
            files: list[Path] = []
        elif path.is_file():
            files = [path]
        elif path.is_dir():
            files = sorted(
                p
                for p in path.rglob("*")
                if p.is_file()
                and not SKIPPED_DIRS.intersection(p.relative_to(path).parts)
                and (p.suffix in CODE_SUFFIXES or p.name in CODE_NAMES or p.name.endswith(".Dockerfile"))
            )
        else:
            files = []
        cache[target] = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
    return cache[target]


def section_texts() -> dict[str, str]:
    """Map each section title to its text: from its `##` heading to the next, subsections and code included.

    The text keeps the section's agents-only blocks and drops every other
    HTML comment, and it takes in the code of each scaffold file or folder
    the section links.
    """
    text = guideline_text()
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line, code in zip(text.split("\n"), fenced_lines(text), strict=True):
        m = None if code else re.match(r"^## (.+?)\s*$", line)
        if m:
            current = m.group(1)
            out[current] = []
        elif current is not None:
            out[current].append(line)
    cache: dict[str, str] = {}
    texts: dict[str, str] = {}
    for title, lines in out.items():
        body = "\n".join(lines)
        linked = dict.fromkeys(SCAFFOLD_LINK.findall(body))
        texts[title] = "\n".join([body, *(scaffold_code(t.rstrip("/"), cache) for t in linked)])
    return texts


def tags(errors: list[str] | None = None) -> dict[tuple[str, str | None], str]:
    """Map (section, subsection or None) to the tag under its heading; a lone backticked word that is no tag is an error.

    A tag is the first non-blank line under a `##` or `###` heading, a
    backticked word alone on its line. It covers that heading's own text.
    """
    text = GUIDELINE.read_text(encoding="utf-8")
    out: dict[tuple[str, str | None], str] = {}
    section: str | None = None
    pending: tuple[str, str | None] | None = None
    lines = unfenced(text).split("\n")
    for n, (line, comment) in enumerate(zip(lines, commented_lines(text), strict=True), start=1):
        if comment:
            continue
        m = re.match(r"^(#{2,3}) (.+?)\s*$", line)
        if m:
            level, title = len(m.group(1)), m.group(2)
            section = title if level == 2 else section
            pending = (title, None) if level == 2 else (section, title) if section is not None else None
            continue
        if not line.strip():
            continue
        tag = TAG_LINE.match(line.strip())
        if pending is not None and tag:
            if tag.group(1) in TAGS:
                out[pending] = tag.group(1)
            elif errors is not None:
                errors.append(f"architecture.md:{n}: `{tag.group(1)}` is no tag; a tag is one of {', '.join(TAGS)}")
        pending = None
    return out


def cited_parts(value: str, known: dict[str, set[str]]) -> list[tuple[str, str | None]]:
    """What a Source value cites, in order: (section, None) for a whole section, (section, subsection) for a part."""
    out: list[tuple[str, str | None]] = []
    sec: str | None = None
    for citation in (c.strip().rstrip(".") for c in value.split(";") if c.strip()):
        m = LABELLED.match(citation)
        citation = m.group(1).strip() if m else citation
        head, _, tail = citation.partition(", ")
        if citation in known:
            sec = citation
            out.append((sec, None))
        elif head in known and tail.strip() in known[head]:
            sec = head
            out.append((sec, tail.strip()))
        elif sec is not None and citation in known[sec]:
            out.append((sec, citation))
    return out


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
    everywhere = guideline_text()
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


def check_style(
    lens_id: str,
    fields: list[tuple[str, str, int]],
    path: Path,
    known: dict[str, set[str]],
    tagged: dict[tuple[str, str | None], str],
    errors: list[str],
) -> None:
    """A lens whose every citation is tagged `style` is `low`: a house convention's breach is low at most."""
    source = next((value for name, value, _ in fields if name == "Source"), "")
    severity = next(((value.strip("` "), ln) for name, value, ln in fields if name == "Severity"), None)
    parts = cited_parts(source, known)
    if severity is None or severity[0] == "low" or not parts:
        return
    if all(tagged.get(part) == "style" for part in parts):
        errors.append(
            f"{path.name}:{severity[1]}: {lens_id} is {severity[0]}, and every section it cites is tagged `style`; "
            "a lens on a house convention is low"
        )


def check_shape(value: str, path: Path, ln: int, errors: list[str]) -> None:
    """A Shape names one or two paths of the scaffold, each in backticks and each there, and nothing else."""
    spans = CODE_SPAN.findall(value)
    rest = CODE_SPAN.sub("", value).strip(" ,.")
    if not 1 <= len(spans) <= MAX_SHAPES or rest not in ("", "and"):
        errors.append(f"{path.name}:{ln}: Shape reads '{value}'; it names one or two paths under {SHAPE_ROOT}, each in backticks")
        return
    for span in spans:
        target = (ROOT / span).resolve()
        if not span.startswith(SHAPE_ROOT) or not target.is_relative_to((ROOT / SHAPE_ROOT).resolve()):
            errors.append(f"{path.name}:{ln}: Shape names `{span}`, which is not under {SHAPE_ROOT}")
        elif not target.exists():
            errors.append(f"{path.name}:{ln}: Shape names `{span}`, which does not exist")


def check_file(
    path: Path,
    known: dict[str, set[str]],
    errors: list[str],
    checks: dict[str, tuple[str, int]] | None = None,
    ids: dict[str, str] | None = None,
    labels: dict[tuple[str, str], set[str]] | None = None,
    texts: dict[str, str] | None = None,
    tagged: dict[tuple[str, str | None], str] | None = None,
) -> int:
    """Check one lens file; return its lens count.

    `ids` maps every lens id seen so far to the file that holds it, so a
    second file reusing a prefix and number is caught.
    """
    tagged = tags() if tagged is None else tagged
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
        if found not in ORDERS:
            errors.append(
                f"{where}: fields are {found}, expected {list(FIELDS)}, optionally followed by {SHAPE}, then {OPTIONAL}"
            )
        check_identifiers(lens_id, fields, path, known, section_texts() if texts is None else texts, errors)
        check_style(lens_id, fields, path, known, tagged, errors)
        for name, value, ln in fields:
            if name == "Severity" and value.strip("` ") not in SEVERITIES:
                errors.append(f"{path.name}:{ln}: severity '{value}' is not high, medium, or low")
            if name == "Source":
                check_source(value, path, ln, known, errors, labels)
            if name == SHAPE:
                check_shape(value, path, ln, errors)
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
    tagged = tags(errors)
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
        total += check_file(files[name], known, errors, checks, ids, labels, texts, tagged)
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
