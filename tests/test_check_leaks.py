"""scripts/check_leaks.py: refused vocabulary per file scope, em-dashes everywhere."""

import pytest


@pytest.fixture
def leaks(repo):
    return repo.script("check_leaks")


def test_valid_tree_passes(repo, leaks, capsys):
    assert leaks.main() == 0
    assert "leaks ok" in capsys.readouterr().out


def test_product_term_in_guideline_fails(repo, leaks, capsys):
    repo.edit("architecture.md", "One table per entity.", "One table per robot.")
    assert leaks.main() == 1
    assert "architecture.md:32: product term 'robot'" in capsys.readouterr().out


def test_agent_vocabulary_is_allowed_everywhere(repo, leaks):
    repo.edit("skills/arch-review-om/SKILL.md", "Never edit", "The agent never edits")
    repo.edit("lenses/om.md", "Tables holding two entities.", "Tables an agent holds.")
    repo.edit("architecture.md", "One table per entity.", "One table per entity, for people and agents.")
    assert leaks.main() == 0


def test_history_phrasing_fails_in_the_guideline_only(repo, leaks, capsys):
    repo.edit("lenses/om.md", "One table per entity.", "One table per entity, as previously.")
    assert leaks.main() == 0
    repo.edit("architecture.md", "One table per entity.", "One table per entity, as previously.")
    assert leaks.main() == 1
    assert "history term 'previously'" in capsys.readouterr().out


def test_em_dash_fails_anywhere(repo, leaks, capsys):
    repo.write(".github/PULL_REQUEST_TEMPLATE.md", "## What changes \u2014 and why\n")
    assert leaks.main() == 1
    assert ".github/PULL_REQUEST_TEMPLATE.md:1: em-dash" in capsys.readouterr().out


def test_a_skill_is_scanned_once(repo, leaks, capsys):
    repo.edit("skills/arch-review-om/SKILL.md", "Never edit", "Never edit the firmware;")
    assert leaks.main() == 1
    out = capsys.readouterr().out
    assert out.count("product term 'firmware'") == 1
    assert "1 leak(s)" in out


def test_terms_are_matched_case_insensitively_on_word_boundaries(repo, leaks):
    repo.edit("architecture.md", "One table per entity.", "One table per Labs entry; syllabus is fine.")
    assert leaks.main() == 1
    repo.edit("architecture.md", "One table per Labs entry; syllabus is fine.", "The syllabus is fine.")
    assert leaks.main() == 0


def test_em_dash_and_product_term_fail_under_agents(repo, leaks, capsys):
    repo.write("agents/arch-reviewer.md", "---\nname: arch-reviewer\n---\n\nJudge the code \u2014 one lens group.\n")
    assert leaks.main() == 1
    assert "agents/arch-reviewer.md:5: em-dash" in capsys.readouterr().out
    repo.write("agents/arch-reviewer.md", "---\nname: arch-reviewer\n---\n\nJudge the firmware.\n")
    assert leaks.main() == 1
    assert "agents/arch-reviewer.md:5: product term 'firmware'" in capsys.readouterr().out


def test_em_dash_fails_in_the_scripts_and_their_tests(repo, leaks, capsys):
    repo.write("scripts/check_thing.py", 'MARK = "\u2014"  # a literal em-dash\n')
    repo.write("tests/test_check_thing.py", 'assert "\u2014" not in "x"\n')
    assert leaks.main() == 1
    out = capsys.readouterr().out
    assert "scripts/check_thing.py:1: em-dash" in out
    assert "tests/test_check_thing.py:1: em-dash" in out
    repo.write("scripts/check_thing.py", "from _common import EM_DASH\n")
    repo.write("tests/test_check_thing.py", "from _common import EM_DASH\n")
    assert leaks.main() == 0


def test_product_term_fails_in_agents_md(repo, leaks, capsys):
    repo.write("AGENTS.md", "# Working here\n\nNo firmware talk.\n")
    assert leaks.main() == 1
    assert "AGENTS.md:3: product term 'firmware'" in capsys.readouterr().out
    repo.write("AGENTS.md", "# Working here\n\nAgents are agents.\n")
    assert leaks.main() == 0


def test_a_copy_built_from_a_dump_with_model_copy_fails_in_every_snippet(repo, leaks, capsys):
    repo.edit("lenses/om.md", "One table per entity.", "`current.model_copy(update={**caller.model_dump()})`")
    assert leaks.main() == 1
    assert "shape term 'model_copy(update={**'" in capsys.readouterr().out
    repo.edit("lenses/om.md", "`current.model_copy(update={**caller.model_dump()})`", "`current.model_copy(update={\"status\": s})`")
    assert leaks.main() == 0
