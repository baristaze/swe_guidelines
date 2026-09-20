"""scripts/check_prose.py: no paragraph of the guideline over the word limit."""

import pytest


@pytest.fixture
def prose(repo):
    return repo.script("check_prose")


def test_valid_tree_passes(repo, prose, capsys):
    assert prose.main() == 0
    assert "prose ok" in capsys.readouterr().out


def test_a_long_paragraph_fails_with_its_line(repo, prose, capsys):
    repo.edit("architecture.md", "One table per entity.", "One table per entity. " + "A word. " * 100)
    assert prose.main() == 1
    assert "architecture.md:32: 204 words, limit 200" in capsys.readouterr().out


def test_a_code_fence_and_a_table_are_not_prose(repo, prose):
    fence = "``` python\n" + "word " * 250 + "\n```\n"
    table = "| a | b |\n|---|---|\n| " + "word " * 250 + " | c |\n"
    repo.edit("architecture.md", "One table per entity.", "One table per entity.\n\n" + fence + "\n" + table)
    assert prose.main() == 0


def test_list_items_are_counted_one_at_a_time(repo, prose):
    items = "".join("- " + "word " * 150 + "\n" for _ in range(3))
    repo.edit("architecture.md", "One table per entity.", "One table per entity.\n\n" + items)
    assert prose.main() == 0
