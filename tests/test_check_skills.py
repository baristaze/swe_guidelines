"""scripts/check_skills.py: skill shape and frontmatter."""

import pytest


@pytest.fixture
def skills(repo):
    return repo.script("check_skills")


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
    set_description(repo, '"' + "x" * 1025 + '"')
    assert skills.main() == 1
    assert "limit 1024" in capsys.readouterr().out


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
        "3. Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.",
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


def test_skill_dir_reference_must_exist(repo, skills, capsys):
    repo.edit("skills/arch-review-om/SKILL.md", "lenses/om.md", "lenses/om.md` and `${CLAUDE_SKILL_DIR}/notes.md")
    assert skills.main() == 1
    assert "${CLAUDE_SKILL_DIR}/notes.md does not exist" in capsys.readouterr().out


def test_every_group_has_one_review_skill_named_by_full(repo, skills, capsys):
    repo.edit("lenses/README.md", "principles  |\n", "principles  |\n| `ctx` | `context.md` | OpContext: tenancy |\n")
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
