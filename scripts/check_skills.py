#!/usr/bin/env python3
"""Check every skill under skills/ for a uniform shape.

Rules:
- every skills/<name>/SKILL.md has YAML frontmatter with `name` equal to the
  folder name, matching ^arch-[a-z0-9-]+$, and a non-empty `description`
  of at most 500 characters, written as one double-quoted string; the
  descriptions together stay under 6000 characters. Each description is
  listed in a budget the host shares across every installed skill. The
  host truncates one entry at 1,536 characters, and when the listing
  overflows it drops the descriptions of the least-used skills. So this
  plugin keeps its share small;
- the frontmatter holds only keys the host reads (`name`, `description`,
  `allowed-tools`, `argument-hint`, `model`, `disable-model-invocation`), so
  a misspelled key, `allowed_tools` for one, is an error and never a skill
  that silently runs with no tool limits;
- every arch-scaffold-* skill references `skills/_shared/scaffold-conventions.md`
  when that file exists;
- every `${CLAUDE_SKILL_DIR}/...` reference in a skill body resolves to a file
  or directory that exists inside this repository; a reference inside
  `skills/_shared/scaffold-conventions.md` is resolved from the folder of
  every skill whose body references that file, since that is the skill
  the reader runs;
- there is exactly one arch-review-<group> skill per lens group and none for
  a group that does not exist;
- arch-review-full names every group's review skill;
- allowed-tools is comma-separated, each entry `Name` or `Name(rule)`, with no
  space outside the parentheses (a space inside, `Bash(make check)`, is
  part of the rule); a bare
  `Bash` is refused, as is a rule with a trailing space inside the
  parentheses or the `Bash(cmd *)` spelling; a Bash rule is the
  `Bash(cmd:*)` prefix form, with no other `*`, or an exact
  `Bash(make <target>)`, and anything else (`Bash(*)`, `Bash(curl*)`, an
  exact command that is not make) is refused, as is a rule with a shell
  operator (`;`, `&`, `|`, a redirect, a substitution, a quote) that would
  chain a second command; a make entry names a target, so `Bash(make:*)`
  and `Bash(make -C dir:*)` are refused;
- every skill and every ops-skill template has a non-empty allowed-tools:
  a skill without one runs with every tool the session has;
- allowed-tools names only what the body runs; the checker holds the make
  targets to it: for every `Bash(make <target>)` or `Bash(make <target>:*)`,
  `make <target>`, as whole words, appears inside a backticked span of the
  skill body, or of `skills/_shared/scaffold-conventions.md` when the body
  references that file. Git, uv, and pnpm entries are checked by hand;
- frontmatter is flat `key: value` lines, one per key, no key repeated,
  and a space follows each key's colon (`name:foo` is one string to YAML);
- a double-quoted value is one complete YAML double-quoted scalar: it
  closes, its inner quotes are escaped, its escapes are ones YAML defines,
  and nothing but a comment follows the closing quote;
- an unquoted value contains no ": " or " #", and does not start with a
  YAML indicator character, so strict YAML loaders accept it;
- every arch-scaffold-* skill has the five scaffold sections, `## Input`,
  `## Created`, `## Changed`, `## Procedure`, `## Output`, in that order;
  a heading inside fenced code is not a section, and a fence of backticks
  or tildes is read by the one rule in `_common.py`;
- every ops-skill template under `skills/_shared/ops-skills/`, which a
  scaffold copies into a new tree as a real skill, has the frontmatter a
  skill has: its name is its file name, its description one
  double-quoted string, and its allowed-tools entries each a Name or a
  `Bash(cmd:*)` prefix, comma-separated;
- a skill keeps its spine and names its detail. Every Markdown file under a
  skill's folder other than its `SKILL.md` is reference material, and at
  least one numbered step of that skill's `## Procedure` names it as
  `${CLAUDE_SKILL_DIR}/<path>`, so the step that reads it says so. A
  reference file no step names is an orphan: nothing opens it, so it is an
  error. A `${CLAUDE_SKILL_DIR}/...` reference inside a reference file
  resolves from the skill's folder, the same way the body's does;
- every audit template (`audit-*`) agrees with Operations (Operational
  Skills) of architecture.md: the first words of its `## Role and
  credential` section, up to a comma or a period, are the role the
  section's table gives it, every audit in the table has a template, and
  every paragraph or list item of the template or the section that names
  parallel calls ranks remove, fold, defer, cache, and parallel, first
  named in that order. An adopter copies the template, so a template that
  disagrees with the text puts the disagreement in every tree;
- when the guideline has work rows (`work.<kind>`), the text, STO-20, and
  the two scaffolds that write the outbox relay each say "a work row is
  done once its item is queued", in those words, so the rule cannot
  change in one of them alone;
- a skill body stays under `BODY_WORDS` words. The body is loaded in full
  every time the skill runs, so its length is a cost paid per run, and the
  fix a failure names is the split: move the long per-step material into the
  file a step reads.

Exit status is non-zero on any failure. Standard library only.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import arguments, fenced_lines, headings

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
LENSES = ROOT / "lenses"
NAME = re.compile(r"^arch-[a-z0-9-]+$")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
REF = re.compile(r"\$\{CLAUDE_SKILL_DIR\}/([^\s`'\")]+)")
TABLE_ROW = re.compile(r"^\|\s*`([a-z]+)`\s*\|")
TOOL = re.compile(r"^(?:[A-Za-z]+|mcp__[a-z0-9-]+__[a-z0-9_]+)(\([^()]*\))?$")
BASH_RULE = re.compile(r"^Bash\((.*)\)$")
# A rule names one command: no shell operator (`;`, `&`, `|`, a redirect, a
# substitution, a quote) may chain a second one behind the first.
PREFIX_RULE = re.compile(r"^[^*:;&|<>`$()'\"\\\n]+:\*$")  # `cmd:*`: a command, then the one `*`
EXACT_MAKE = re.compile(r"^make [^*:;&|<>`$()'\"\\\n]+$")  # `make <target>`, arguments allowed, no wildcard
MAKE_TARGET = re.compile(r"^make [^\s-]")  # a make entry names a target first, not an option
QUOTED_DESCRIPTION = re.compile(r'^description:\s*"', re.M)
PARENS = re.compile(r"\([^()]*\)")
CODE_SPAN = re.compile(r"`([^`\n]+)`")
CONVENTIONS = "_shared/scaffold-conventions.md"
DESCRIPTION_LIMIT = 500
DESCRIPTIONS_TOTAL = 6000
KNOWN_KEYS = frozenset({"name", "description", "allowed-tools", "argument-hint", "model", "disable-model-invocation"})
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
ESCAPES = '0abtnvfre "/\\N_LP\t'  # single-character escapes YAML defines after a backslash
HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}
SCAFFOLD_SECTIONS = ("Input", "Created", "Changed", "Procedure", "Output")
STEP = re.compile(r"^\d+\.\s")
BODY_WORDS = 3000
"""The bound on a skill body, in words.

The split that moved the scaffolds' file-by-file lists into their
`references/` folders left the largest body at about 2,400 words, and
the three it split at about 1,400 each. 3,000 clears the largest with a
quarter to spare, so a skill can grow a section without tripping it,
and still refuses the 10,000-word body the split started from.
"""
INDICATORS = ("'", "[", "{", "&", "*", "!", "|", ">", "%", "@", "`", "#", "-", "?", ",", "]", "}")


def double_quoted(value: str) -> tuple[str, str | None]:
    """Parse one YAML double-quoted scalar; return (content, error).

    `value` starts with the opening quote. The content is returned raw,
    escapes kept, because the checks only need its length and text.
    """
    i = 1
    while i < len(value):
        ch = value[i]
        if ch == '"':
            rest = value[i + 1 :].strip()
            if rest and not rest.startswith("#"):
                return value[1:i], f"text after the closing quote: {rest!r}"
            return value[1:i], None
        if ch == "\\":
            esc = value[i + 1 : i + 2]
            if esc in HEX_ESCAPES:
                digits = value[i + 2 : i + 2 + HEX_ESCAPES[esc]]
                if len(digits) != HEX_ESCAPES[esc] or not all(c in "0123456789abcdefABCDEF" for c in digits):
                    return value[1:], f"bad escape \\{esc}{digits}"
                i += 2 + HEX_ESCAPES[esc]
                continue
            if not esc or esc not in ESCAPES:
                return value[1:], f"bad escape \\{esc}"
            i += 2
            continue
        i += 1
    return value[1:], "unterminated quoted value"


def frontmatter(text: str, errors: list[str], rel: str) -> dict[str, str]:
    """Parse the flat `key: value` frontmatter strictly enough for any YAML loader.

    A value is either one complete double-quoted scalar on the key's line
    or a plain scalar that no YAML rule would read differently: no ": ",
    no " #", no leading indicator. Anything else is refused here so a
    strict loader elsewhere never sees it first.
    """
    m = FRONTMATTER.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        if line[0] in " \t":
            errors.append(f"{rel}: frontmatter line is indented; values are one line each: {line!r}")
            continue
        key, sep, value = line.partition(":")
        # YAML reads `name:foo` as one plain string, never a key: a colon ends a key only before a space
        if not sep or not key.strip() or not KEY.match(key.strip()) or (value and value[0] not in " \t"):
            errors.append(f"{rel}: frontmatter line is not `key: value`: {line!r}")
            continue
        key, value = key.strip(), value.strip()
        if key in out:
            errors.append(f"{rel}: frontmatter key {key!r} repeated")
        if value.startswith('"'):
            value, problem = double_quoted(value)
            if problem:
                errors.append(f"{rel}: value of {key} is not one double-quoted string: {problem}")
        elif ": " in value or " #" in value or value.endswith(":") or value[:1] in INDICATORS:
            errors.append(f"{rel}: value of {key} must be double-quoted for strict YAML")
        out[key] = value
    return out


def body_of(text: str) -> str:
    """The skill text after the frontmatter."""
    m = FRONTMATTER.match(text)
    return text[m.end() :] if m else text


SECTION = re.compile(r"^##\s+(.+?)\s*$")


def steps(body: str) -> list[str]:
    """The numbered steps of a skill's `## Procedure`, one string each.

    A step opens with `<n>. ` at the left margin and runs to the next
    one or to the end of the section, so its indented continuation lines
    are part of it. A skill with no `## Procedure` has no steps, and a
    reference file beside it is therefore an orphan.
    """
    inside = False
    found: list[list[str]] = []
    for line in body.split("\n"):
        heading = SECTION.match(line)
        if heading:
            inside = heading.group(1) == "Procedure"
        elif inside and STEP.match(line):
            found.append([line])
        elif inside and found:
            found[-1].append(line)
    return ["\n".join(step) for step in found]


def code_spans(text: str) -> list[str]:
    """Every inline backticked span of a Markdown text, fences left out.

    A fenced block in a skill is a report template, never a command, so
    only inline spans count as the body running something.
    """
    out: list[str] = []
    for line, code in zip(text.split("\n"), fenced_lines(text), strict=True):
        if not code:
            out.extend(CODE_SPAN.findall(line))
    return out


def bash_command(tool: str) -> str | None:
    """The command a `Bash(cmd)` or `Bash(cmd:*)` entry names, or None.

    Only a make target is held to the body; the review skills read a
    diff without spelling the git command, and `uv run` and `pnpm run`
    are a shell in practice, so those entries are left to the reader.
    """
    m = BASH_RULE.match(tool)
    if not m:
        return None
    return m.group(1).removesuffix(":*").strip()


def runs_command(cmd: str, spans: list[str]) -> bool:
    """Whether a span runs `cmd` as whole words: `make test` is not `make test-e2e`."""
    word = re.compile(rf"(?<![\w-]){re.escape(cmd)}(?![\w-])")
    return any(word.search(span) for span in spans)


def lens_groups() -> set[str]:
    groups = set()
    for line in (LENSES / "README.md").read_text(encoding="utf-8").splitlines():
        m = TABLE_ROW.match(line)
        if m:
            groups.add(m.group(1))
    return groups


NO_TOOLS = "no allowed-tools; a skill names the tools it runs"
"""A skill with no allowed-tools runs with every tool the session has, so the key is required."""

OPS_TEMPLATES = SKILLS / "_shared" / "ops-skills"
"""The operational skills a scaffold copies into a new tree, where their frontmatter becomes a real skill's."""


def check_template(path: Path, errors: list[str]) -> None:
    """An ops-skill template's frontmatter holds to the rules a skill's does: the name is the file's, the
    description one double-quoted string, and every allowed-tools entry a Name or a Bash(cmd:*) prefix."""
    rel = path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    fm = frontmatter(text, errors, str(rel))
    if not fm:
        errors.append(f"{rel}: missing frontmatter")
        return
    if fm.get("name", "") != path.stem:
        errors.append(f"{rel}: name '{fm.get('name', '')}' differs from the file name '{path.stem}'")
    desc = fm.get("description", "")
    head = FRONTMATTER.match(text)
    if not desc:
        errors.append(f"{rel}: empty description")
    elif len(desc) > 1024:
        errors.append(f"{rel}: description is {len(desc)} characters, limit 1024")
    elif head and not QUOTED_DESCRIPTION.search(head.group(1)):
        errors.append(f"{rel}: description must be one double-quoted string")
    tools = fm.get("allowed-tools", "")
    if not tools.strip().strip('"').strip():
        errors.append(f"{rel}: {NO_TOOLS}")
    if any(" " in PARENS.sub("", t.strip()) for t in tools.split(",")):
        errors.append(f"{rel}: allowed-tools must be comma-separated")
    for tool in (t.strip() for t in tools.split(",") if t.strip()):
        if not TOOL.match(tool):
            errors.append(f"{rel}: allowed-tools entry {tool!r} is not Name or Name(rule)")
        elif tool == "Bash":
            errors.append(f"{rel}: a bare Bash is refused; name the command, Bash(cmd:*)")
        elif bash_command(tool) is not None:
            rule = tool[len("Bash(") : -1]
            if " *" in rule or not (PREFIX_RULE.match(rule.strip()) or EXACT_MAKE.match(rule.strip())):
                errors.append(f"{rel}: {tool!r} is neither the Bash(cmd:*) prefix form nor an exact Bash(make <target>)")


GUIDELINE = ROOT / "architecture.md"
OPS_SECTION = "Operational Skills"
OPS_ROW = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*([^|]*?)\s*\|")
ROLE_SECTION = "Role and credential"
ANY_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FIX_ORDER = ("remove", "fold", "defer", "cache", "parallel")
"""The order in which an audit of calls ranks its fixes, as Operations (Operational Skills) states it."""
WORK_ROW_DONE = "a work row is done once its item is queued"
"""The relay's rule for a `work.<kind>` outbox row, as The Storage Layer (Database Roles) states it."""
WORK_ROW_DONE_IN = (
    "architecture.md",
    "lenses/storage.md",
    "skills/arch-scaffold-worker/SKILL.md",
    "skills/arch-scaffold-new/references/object-model.md",
)
"""The text, STO-20, and the two scaffolds that write the relay: each states the rule in the same words."""


def section(text: str, title: str) -> str | None:
    """The text under the heading `title`, to the next heading of its level or higher; None when there is none."""
    lines = text.split("\n")
    start: int | None = None
    level = 0
    for i, (line, code) in enumerate(zip(lines, fenced_lines(text), strict=True)):
        m = None if code else ANY_HEADING.match(line)
        if not m:
            continue
        if start is None:
            if m.group(2) == title:
                start, level = i + 1, len(m.group(1))
        elif len(m.group(1)) <= level:
            return "\n".join(lines[start:i])
    return None if start is None else "\n".join(lines[start:])


LIST_ITEM = re.compile(r"^\s*(?:\d+\.|[-*])\s")


def blocks(text: str) -> list[str]:
    """The paragraphs and list items of a text, in order, each on one line with its whitespace collapsed.

    A blank line ends a block and a list marker starts one, so a numbered step
    is a block of its own even when no blank line sets it apart."""
    found: list[list[str]] = [[]]
    for line in text.split("\n"):
        if not line.strip() or LIST_ITEM.match(line):
            found.append([])
        if line.strip():
            found[-1].append(line)
    return [" ".join(" ".join(b).split()) for b in found if b]


def ranks_in_order(block: str) -> bool:
    """Whether a block names every step of FIX_ORDER, each first named after the one before it."""
    found = [re.search(rf"\b{word}", block, re.IGNORECASE) for word in FIX_ORDER]
    starts = [m.start() for m in found if m]
    return len(starts) == len(FIX_ORDER) and starts == sorted(starts)


def misranked(text: str) -> bool:
    """Whether a block of the text names parallel calls without ranking FIX_ORDER in it, in that order."""
    return any(re.search(r"\bparallel", b, re.IGNORECASE) and not ranks_in_order(b) for b in blocks(text))


def ops_roles(text: str) -> dict[str, str] | None:
    """Skill name to role, from the table of the guideline's Operational Skills; None when it has no such section."""
    table = section(text, OPS_SECTION)
    if table is None:
        return None
    return {m.group(1): m.group(2).lower() for m in map(OPS_ROW.match, table.splitlines()) if m}


def check_audits(templates: list[Path], errors: list[str]) -> None:
    """Every audit template agrees with Operations (Operational Skills): its Role and credential section opens with the
    role the table gives it, and a paragraph or list item that names parallel calls ranks FIX_ORDER first, as the text
    does. An adopter copies the template, so a template that disagrees with the text makes every copy disagree too."""
    audits = {t.stem: t for t in templates if t.stem.startswith("audit-")}
    if not audits:
        return
    text = GUIDELINE.read_text(encoding="utf-8") if GUIDELINE.exists() else ""
    roles = ops_roles(text)
    if roles is None:
        errors.append(f"architecture.md: no {OPS_SECTION} section to hold the audit templates to")
        return
    order = ", ".join(FIX_ORDER)
    if misranked(section(text, OPS_SECTION) or ""):
        errors.append(f"architecture.md: {OPS_SECTION} names parallel calls without ranking {order} first, in that order")
    for name in sorted(n for n in roles if n.startswith("audit-") and n not in audits):
        errors.append(f"architecture.md: the {OPS_SECTION} table lists {name}, which has no template")
    for name, template in sorted(audits.items()):
        rel = template.relative_to(ROOT)
        body = body_of(template.read_text(encoding="utf-8"))
        first = next(iter(blocks(section(body, ROLE_SECTION) or "")), "")
        said = re.split(r"[,.]", first, maxsplit=1)[0].strip().lower()
        if name not in roles:
            errors.append(f"{rel}: the {OPS_SECTION} table of architecture.md gives it no role")
        elif said != roles[name]:
            errors.append(f"{rel}: {ROLE_SECTION} opens with the role {said!r}; the {OPS_SECTION} table gives {roles[name]!r}")
        if misranked(template.read_text(encoding="utf-8")):
            errors.append(f"{rel}: names parallel calls without ranking {order} first, in that order")


def check_work_row(errors: list[str]) -> None:
    """When the guideline has work rows, the text, STO-20, and both scaffolds that write the relay each state
    WORK_ROW_DONE in those words, so a change to the rule in one place fails here until the others follow."""
    text = GUIDELINE.read_text(encoding="utf-8") if GUIDELINE.exists() else ""
    if "`work.<kind>`" not in text:
        return
    for rel in WORK_ROW_DONE_IN:
        path = ROOT / rel
        said = " ".join(path.read_text(encoding="utf-8").split()).lower() if path.exists() else ""
        if WORK_ROW_DONE not in said:
            errors.append(
                f"{rel}: does not say {WORK_ROW_DONE!r}; the text, STO-20, and the relay's two scaffolds must each say it"
            )


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    groups = lens_groups()
    review_groups: set[str] = set()
    skills = sorted(p for p in SKILLS.iterdir() if p.is_dir() and not p.name.startswith("_"))
    total = 0
    for folder in skills:
        skill = folder / "SKILL.md"
        rel = skill.relative_to(ROOT)
        if not skill.exists():
            errors.append(f"{folder.relative_to(ROOT)}: no SKILL.md")
            continue
        text = skill.read_text(encoding="utf-8")
        fm = frontmatter(text, errors, str(rel))
        if not fm:
            errors.append(f"{rel}: missing frontmatter")
            continue
        name = fm.get("name", "")
        if name != folder.name:
            errors.append(f"{rel}: name '{name}' differs from folder '{folder.name}'")
        if not NAME.match(name):
            errors.append(f"{rel}: name '{name}' must match {NAME.pattern}")
        for key in sorted(set(fm) - KNOWN_KEYS):
            errors.append(f"{rel}: frontmatter key {key!r} is not one the host reads ({', '.join(sorted(KNOWN_KEYS))})")
        desc = fm.get("description", "")
        total += len(desc)
        if not desc:
            errors.append(f"{rel}: empty description")
        elif len(desc) > DESCRIPTION_LIMIT:
            errors.append(f"{rel}: description is {len(desc)} characters, limit {DESCRIPTION_LIMIT}")
        head = FRONTMATTER.match(text)
        if desc and head and not QUOTED_DESCRIPTION.search(head.group(1)):
            errors.append(f"{rel}: description must be one double-quoted string")
        tools = fm.get("allowed-tools", "")
        if not tools.strip().strip('"').strip():
            errors.append(f"{rel}: {NO_TOOLS}")
        else:
            if any(" " in PARENS.sub("", t.strip()) for t in tools.split(",")):
                errors.append(f"{rel}: allowed-tools must be comma-separated")
            body = body_of(text)
            runs = code_spans(body)
            if CONVENTIONS in body and (SKILLS / CONVENTIONS).exists():
                runs.extend(code_spans((SKILLS / CONVENTIONS).read_text(encoding="utf-8")))
            for tool in (t.strip() for t in tools.split(",")):
                if not TOOL.match(tool):
                    errors.append(f"{rel}: allowed-tools entry {tool!r} is not Name or Name(rule)")
                    continue
                if tool == "Bash":
                    errors.append(f"{rel}: a bare Bash is refused; name the command, Bash(cmd:*)")
                    continue
                cmd = bash_command(tool)
                if cmd is None:
                    continue
                rule = tool[len("Bash(") : -1]
                if rule != rule.strip():
                    errors.append(f"{rel}: trailing space inside the parentheses of {tool!r}")
                if " *" in rule:
                    errors.append(f"{rel}: use the Bash(cmd:*) prefix form, not {tool!r}")
                elif not cmd:
                    errors.append(f"{rel}: allowed-tools entry {tool!r} names no command")
                elif not (PREFIX_RULE.match(rule.strip()) or EXACT_MAKE.match(rule.strip())):
                    errors.append(f"{rel}: {tool!r} is neither the Bash(cmd:*) prefix form nor an exact Bash(make <target>)")
                elif (cmd == "make" or cmd.startswith("make ")) and not MAKE_TARGET.match(cmd):
                    errors.append(f"{rel}: {tool!r} names no make target; name one, Bash(make <target>)")
                elif cmd.startswith("make ") and not runs_command(cmd, runs):
                    errors.append(f"{rel}: allowed-tools names Bash({cmd}) but the body never runs {cmd}")
        words = len(body_of(text).split())
        if words > BODY_WORDS:
            errors.append(
                f"{rel}: the body is {words} words, limit {BODY_WORDS}; "
                f"move the long per-step material into skills/{folder.name}/references/ "
                "and have the step that reads it name the file"
            )
        refs = [(ref, str(rel)) for ref in REF.findall(text)]
        named = steps(body_of(text))
        for reference in sorted(p for p in folder.rglob("*.md") if p != skill):
            at = str(reference.relative_to(ROOT))
            inside = reference.relative_to(folder).as_posix()
            if not any(f"${{CLAUDE_SKILL_DIR}}/{inside}" in step for step in named):
                errors.append(
                    f"{at}: no step of {rel} names ${{CLAUDE_SKILL_DIR}}/{inside}; "
                    "a reference file is read by the step that names it"
                )
            refs += [(ref, at) for ref in REF.findall(reference.read_text(encoding="utf-8"))]
        conventions = SKILLS / CONVENTIONS
        if CONVENTIONS in body_of(text) and conventions.exists():
            # the conventions file is read on this skill's behalf, from this skill's folder
            shared = conventions.read_text(encoding="utf-8")
            refs += [(ref, f"{rel} (via skills/{CONVENTIONS})") for ref in REF.findall(shared)]
        for ref, where in refs:
            if "<" in ref:
                continue  # a placeholder such as arch-review-<group>
            target = (folder / ref).resolve()
            if not target.is_relative_to(ROOT.resolve()):
                errors.append(f"{where}: reference ${{CLAUDE_SKILL_DIR}}/{ref} resolves outside the repository")
            elif not target.exists():
                errors.append(f"{where}: reference ${{CLAUDE_SKILL_DIR}}/{ref} does not exist")
        if name.startswith("arch-scaffold-") and (SKILLS / CONVENTIONS).exists() and CONVENTIONS not in body_of(text):
            errors.append(f"{rel}: a scaffold skill references skills/{CONVENTIONS}")
        if name.startswith("arch-scaffold-"):
            found = [t for level, t in headings(body_of(text)) if level == 2 and t in SCAFFOLD_SECTIONS]
            if found != list(SCAFFOLD_SECTIONS):
                errors.append(f"{rel}: scaffold sections are {found}, expected {list(SCAFFOLD_SECTIONS)} in that order")
        if name.startswith("arch-review-") and name != "arch-review-full":
            group = name.removeprefix("arch-review-")
            if group not in groups:
                errors.append(f"{rel}: no lens group '{group}'")
            elif group in review_groups:
                errors.append(f"{rel}: duplicate review skill for '{group}'")
            review_groups.add(group)
            if f"lenses/{group}.md" not in text:
                errors.append(f"{rel}: does not reference lenses/{group}.md")
    if total > DESCRIPTIONS_TOTAL:
        errors.append(f"skills/: the descriptions total {total} characters, limit {DESCRIPTIONS_TOTAL}")
    templates = sorted(OPS_TEMPLATES.glob("*.md")) if OPS_TEMPLATES.is_dir() else []
    for template in templates:
        check_template(template, errors)
    check_audits(templates, errors)
    check_work_row(errors)
    for group in sorted(groups - review_groups):
        errors.append(f"skills/: no arch-review-{group} skill for lens group '{group}'")
    full = SKILLS / "arch-review-full" / "SKILL.md"
    if full.exists():
        text = full.read_text(encoding="utf-8")
        for group in sorted(groups):
            if f"arch-review-{group}" not in text:
                errors.append(f"skills/arch-review-full/SKILL.md: does not name arch-review-{group}")
    else:
        errors.append("skills/arch-review-full/SKILL.md: missing")
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} problem(s) in {len(skills)} skill(s)")
        return 1
    print(f"skills ok: {len(skills)} skills, {len(review_groups)} review groups, {len(templates)} ops-skill templates")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
