"""The ops lenses a parser can decide: the skill set, the traffic tool, the READMEs, and the knowledge map.

Each rule reads files by path: a skill's `SKILL.md`, a manifest's
dependencies, a folder's `README.md`, the lines of `llms.txt`. What a
document says, and whether it says it at the right altitude, stays
with the review.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from arch_check.model import Violation
from arch_check.project import Project
from arch_check.registry import rule
from arch_check.rules._text_util import (
    is_dir,
    is_file,
    load_json,
    load_toml,
    npm_dependencies,
    python_dependencies,
    subdirs,
    walk,
)

SKILLS = (
    "ops-investigate",
    "ops-watch",
    "ops-root-cause",
    "ops-infra-as-code",
    "ops-cloud-deployment-create",
    "ops-cloud-deployment-nuke",
    "ops-simulate-traffic",
    "stress-test-create-or-update",
    "stress-test-run",
)


@rule(
    "OPS-11",
    coverage="partial",
    summary="The nine operational skills exist under .claude/skills, each a SKILL.md with frontmatter.",
)
def the_skill_set(project: Project) -> Iterator[Violation]:
    """Every system ships the nine operational skills the guideline lists.

    Each of `.claude/skills/<name>/SKILL.md` exists for the nine names,
    and starts with a `---` frontmatter line. The five statements each
    skill makes are judged.
    """
    for name in SKILLS:
        rel = f".claude/skills/{name}/SKILL.md"
        lines = project.lines(rel)
        if not is_file(project, rel):
            yield Violation(rel, 1, 1, f"no {name} skill; every system ships the nine operational skills")
        elif not lines or lines[0].strip() != "---":
            yield Violation(rel, 1, 1, f"{name}/SKILL.md opens with no frontmatter")


LOAD_TOOLS_PY = frozenset({"locust", "molotov", "bzt"})
LOAD_TOOLS_NPM = frozenset({"k6", "artillery", "autocannon", "loadtest"})


@rule(
    "OPS-20",
    coverage="partial",
    summary="No manifest depends on a second load tool (locust, k6, artillery, and the like).",
)
def one_traffic_generator(project: Project) -> Iterator[Violation]:
    """Everything that drives the system at load rides the one traffic generator.

    No `pyproject.toml` requires `locust`, `molotov`, or `bzt`, and no
    `package.json` names `k6`, `artillery`, `autocannon`, or
    `loadtest`. The routes a session calls and the profiles are judged.
    """
    for rel in walk(project, names=("pyproject.toml",)):
        for dep in sorted(python_dependencies(load_toml(project, rel) or {}) & LOAD_TOOLS_PY):
            yield Violation(rel, 1, 1, f"requires {dep}; one traffic generator drives every load")
    for rel in walk(project, names=("package.json",)):
        for dep in sorted(npm_dependencies(load_json(project, rel)) & LOAD_TOOLS_NPM):
            yield Violation(rel, 1, 1, f"depends on {dep}; one traffic generator drives every load")


LEVELS = ("om", "infra", "deployment", "ops")
LEVEL_GROUPS = ("services", "workers", "apps", "clients")


@rule(
    "OPS-24",
    coverage="partial",
    summary="om/, infra/, deployment/, ops/, and each service, worker, app, and client folder has a README.md.",
)
def a_readme_at_every_level(project: Project) -> Iterator[Violation]:
    """Every folder that is an abstraction level carries a README.

    When they exist, `om/`, `infra/`, `deployment/`, `ops/`, and each
    folder under `services/`, `workers/`, `apps/`, and `clients/` has a
    `README.md`. Whether each speaks its level's language is judged.
    """
    folders = [f for f in LEVELS if is_dir(project, f)]
    for group in LEVEL_GROUPS:
        folders.extend(subdirs(project, group))
    for folder in folders:
        if not is_file(project, f"{folder}/README.md"):
            yield Violation(f"{folder}/README.md", 1, 1, f"{folder}/ has no README.md; every level carries one")


COMMAND = re.compile(r"^\s*\$ |`(make|uv|uvx|pnpm|npm|npx|pip|python3?|docker|terraform|aws|git) [^`]*`")


@rule(
    "OPS-25",
    coverage="partial",
    summary="Every OM namespace has a README.md, and om/README.md holds no code block or command line.",
)
def om_readme_for_a_reader_with_no_code(project: Project) -> Iterator[Violation]:
    """`om/README.md` names nouns for a reader with no code, and points down to a README per namespace.

    Every package directly under `om/src/<root>/om/` except the storage
    root has a `README.md`. `om/README.md` has no fenced code block and no
    shell command: no line starting `$ `, and no inline code that runs
    a tool (`make`, `uv`, `pnpm`, `docker`, and the like). Whether it
    assumes the code is open is judged.

    Option `[tool.arch-check.options.OPS-25]`: `not_namespaces`
    (default `["storage"]`), the packages under the OM that are not
    namespaces.
    """
    skip = project.option("OPS-25", "not_namespaces", ["storage"], {"not_namespaces"})
    base = f"om/src/{project.package.replace('.', '/')}/om"
    for folder in subdirs(project, base):
        name = folder.rpartition("/")[2]
        if name in skip or not is_file(project, f"{folder}/__init__.py"):
            continue
        if not is_file(project, f"{folder}/README.md"):
            yield Violation(f"{folder}/README.md", 1, 1, f"namespace {name} has no README.md; om/README.md points to one")
    for number, text in enumerate(project.lines("om/README.md"), start=1):
        if text.lstrip().startswith(("```", "~~~")):
            yield Violation("om/README.md", number, 1, "a code block in om/README.md; it is written for a reader with no code")
        elif COMMAND.search(text):
            yield Violation("om/README.md", number, 1, "a command in om/README.md; it carries no developer instruction")


LINK = re.compile(r"^- \[[^\]]+\]\(([^)\s]+)\): \S")


@rule(
    "OPS-26",
    coverage="partial",
    summary="llms.txt has a title, a summary, three audience sections, and one described link per line to a file that exists.",
)
def the_knowledge_map(project: Project) -> Iterator[Violation]:
    """One map at the root names what each audience is served.

    `llms.txt` exists at the root. Its first line is a `# ` title, a
    `> ` summary follows, and it has exactly three `## ` sections. Every
    non-blank line inside a section is `- [text](target): line`, and a
    relative target exists in the tree. Which documents each audience
    gets, and one subject per document, are judged.
    """
    rel = "llms.txt"
    if not is_file(project, rel):
        yield Violation(rel, 1, 1, "no llms.txt; one map at the root names what each audience is served")
        return
    lines = project.lines(rel)
    content = [(n, t) for n, t in enumerate(lines, start=1) if t.strip()]
    if not content or not content[0][1].startswith("# "):
        yield Violation(rel, 1, 1, "llms.txt opens with no `# ` title")
    first_section = next((n for n, t in content if t.startswith("## ")), len(lines) + 1)
    if not any(t.startswith("> ") for n, t in content if n < first_section):
        yield Violation(rel, 1, 1, "llms.txt has no `> ` summary before its sections")
    sections = [(n, t) for n, t in content if t.startswith("## ")]
    if len(sections) != 3:
        where = sections[3][0] if len(sections) > 3 else 1
        yield Violation(rel, where, 1, f"llms.txt has {len(sections)} audience sections; it names three")
    for number, text in content:
        if number <= first_section or text.startswith("## "):
            continue
        m = LINK.match(text)
        if not m:
            yield Violation(rel, number, 1, "a line in a section that is not `- [text](target): line`")
            continue
        target = m.group(1).split("#")[0]
        local = target and "://" not in target and not target.startswith("mailto:")
        if local and not (project.root / target.lstrip("/")).exists():
            yield Violation(rel, number, 1, f"{target} does not exist")
