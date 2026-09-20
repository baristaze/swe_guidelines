"""scripts/check_agents.py: the reviewer agent mirrors the review template."""

import pytest

TEMPLATE = """\
---
name: arch-review-{group}
description: "Review code through the {title} lenses."
allowed-tools: Read
---

# arch-review-{group}

## Input

1. Empty: the current branch.
2. A path.

## Procedure

1. Read the lens file end to end.
2. Establish the scope.
3. For every lens decide **finding**, **pass**, **not applicable**, or
   **unverified**.
4. Verify every finding.
5. Assign severity.
6. Write the report in the format below.

Never edit, stage, or commit.

## Output

```markdown
# Architecture review: {title}

**Scope.** <what was reviewed>

## Findings

- **<LENS-ID> <severity>** `<path>:<line>` <what breaks the rule>.
```

Findings are ordered most severe first.
"""

AGENT = """\
---
name: arch-reviewer
description: "Reviews a scope through one lens group."
tools: Read
---

You are an architecture reviewer.

Procedure (the same as the `arch-review-<group>` skills):

1. Read the lens file end to end.
2. Establish the scope.
3. For every lens decide **finding**, **pass**, **not
   applicable**, or **unverified**.
4. Verify every finding.
5. Assign severity.
6. Write the report in the format below.

Never edit, stage, or commit. Return only the report:

```markdown
# Architecture review: <group title>

**Scope.** <what was reviewed>

## Findings

- **<LENS-ID> <severity>** `<path>:<line>` <what breaks the rule>.
```

Findings are ordered most severe first.
"""


@pytest.fixture
def agents(repo):
    repo.write("skills/_template/review.SKILL.md", TEMPLATE)
    repo.write("agents/arch-reviewer.md", AGENT)
    return repo.script("check_agents")


def test_mirrored_files_pass(repo, agents, capsys):
    assert agents.main() == 0
    assert "agents ok" in capsys.readouterr().out


def test_step_count_must_agree(repo, agents, capsys):
    repo.edit("agents/arch-reviewer.md", "6. Write the report in the format below.\n", "")
    assert agents.main() == 1
    assert "5 procedure steps, skills/_template/review.SKILL.md has 6" in capsys.readouterr().out


def test_report_block_must_be_identical(repo, agents, capsys):
    repo.edit("agents/arch-reviewer.md", "## Findings", "## Problems")
    assert agents.main() == 1
    assert "report block differs" in capsys.readouterr().out


def test_decision_words_must_be_present_and_in_order(repo, agents, capsys):
    repo.edit("agents/arch-reviewer.md", "**unverified**", "**unsure**")
    assert agents.main() == 1
    assert "lacks the bold decision word(s) unverified" in capsys.readouterr().out
    repo.edit("agents/arch-reviewer.md", "**unsure**", "**unverified**")
    repo.edit(
        "agents/arch-reviewer.md",
        "**finding**, **pass**, **not\n   applicable**, or **unverified**",
        "**pass**, **finding**, **not\n   applicable**, or **unverified**",
    )
    assert agents.main() == 1
    assert "different order" in capsys.readouterr().out


def test_input_numbering_does_not_count_as_steps(repo, agents):
    repo.edit("skills/_template/review.SKILL.md", "2. A path.\n", "2. A path.\n3. A ref.\n")
    assert agents.main() == 0


def test_missing_agent_file_fails(repo, agents, capsys):
    (repo.root / "agents" / "arch-reviewer.md").unlink()
    assert agents.main() == 1
    assert "agents/arch-reviewer.md: missing" in capsys.readouterr().out
