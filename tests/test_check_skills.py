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


def test_em_dash_fails(repo, skills, capsys):
    repo.edit("skills/arch-review-full/SKILL.md", "merge the reports", "merge \u2014 the reports")
    assert skills.main() == 1
    assert "em-dash" in capsys.readouterr().out


def test_scaffold_sections_must_appear_in_order(repo, skills, capsys):
    text = repo.read("skills/arch-scaffold-thing/SKILL.md")
    swapped = text.replace("## Created", "## TEMP").replace("## Changed", "## Created").replace("## TEMP", "## Changed")
    repo.write("skills/arch-scaffold-thing/SKILL.md", swapped)
    assert skills.main() == 1
    assert "scaffold sections are ['Input', 'Changed', 'Created', 'Procedure', 'Output']" in capsys.readouterr().out
    repo.write("skills/arch-scaffold-thing/SKILL.md", text.replace("## Procedure\n\n1. Write the thing.\n\n", ""))
    assert skills.main() == 1
    assert "expected ['Input', 'Created', 'Changed', 'Procedure', 'Output']" in capsys.readouterr().out
