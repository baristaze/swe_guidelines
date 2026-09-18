"""scripts/gen_toc.py: the table of contents follows the headings."""

import pytest


@pytest.fixture
def toc(repo):
    return repo.script("gen_toc")


def test_current_table_passes_check(repo, toc, capsys):
    assert toc.main(["--check"]) == 0
    assert "toc ok" in capsys.readouterr().out


def test_stale_table_fails_check_and_leaves_the_file(repo, toc, capsys):
    before = repo.read("architecture.md").replace("  - [Tables](#tables)\n", "")
    repo.write("architecture.md", before)
    assert toc.main(["--check"]) == 1
    assert "stale" in capsys.readouterr().out
    assert repo.read("architecture.md") == before


def test_regenerate_writes_the_table(repo, toc):
    repo.edit("architecture.md", "  - [Tables](#tables)\n", "")
    assert toc.main([]) == 0
    assert "  - [Tables](#tables)" in repo.read("architecture.md")
    assert toc.main(["--check"]) == 0


def test_repeated_heading_gets_a_numbered_anchor_the_link_checker_accepts(repo, toc):
    links = repo.script("check_links")
    repo.edit("architecture.md", "### Tables\n", "### Principles\n")
    toc.main([])
    assert "  - [Principles](#principles-2)" in repo.read("architecture.md")
    assert links.main() == 0


def test_headings_inside_fences_and_contents_are_left_out(repo, toc):
    rendered = toc.render(repo.read("architecture.md"))
    assert "Contents" not in rendered
    assert "not a heading" not in rendered
    assert rendered.startswith("- [Interfaces](#interfaces)")


def test_missing_markers_fail(repo, toc, capsys):
    repo.edit("architecture.md", "<!-- toc -->", "")
    assert toc.main(["--check"]) == 1
    assert "no <!-- toc -->" in capsys.readouterr().out
