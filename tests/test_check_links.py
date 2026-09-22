"""scripts/check_links.py: relative links and anchors resolve."""

import pytest


@pytest.fixture
def links(repo):
    return repo.script("check_links")


def test_valid_tree_passes(repo, links, capsys):
    assert links.main() == 0
    assert "links ok" in capsys.readouterr().out


def test_missing_file_fails(repo, links, capsys):
    repo.write("docs/extra.md", "# Extra\n\nSee [the guide](../missing.md).\n")
    assert links.main() == 1
    assert "docs/extra.md:3: missing file ../missing.md" in capsys.readouterr().out


def test_missing_anchor_in_own_file_fails(repo, links, capsys):
    repo.edit("architecture.md", "(#the-storage-layer)", "(#the-storage-layers)")
    assert links.main() == 1
    assert "missing anchor #the-storage-layers" in capsys.readouterr().out


def test_missing_anchor_in_other_file_fails(repo, links, capsys):
    repo.edit("README.md", "lenses/README.md#groups", "lenses/README.md#group")
    assert links.main() == 1
    assert "README.md:3: missing anchor #group in lenses/README.md" in capsys.readouterr().out


def test_repeated_heading_resolves_with_the_generator_numbering(repo, links):
    # #principles-1 is in the fixture already; a third copy would be -2
    repo.edit("architecture.md", "[its principles](#principles-1)", "[its principles](#principles-2)")
    assert links.main() == 1


def test_heading_inside_fenced_code_is_not_an_anchor(repo, links, capsys):
    repo.edit("architecture.md", "One table per entity.", "One table per entity. See [code](#not-a-heading-fenced-code).")
    assert links.main() == 1
    assert "missing anchor #not-a-heading-fenced-code" in capsys.readouterr().out


def test_link_text_wrapped_across_lines_is_still_checked(repo, links, capsys):
    repo.write("docs/extra.md", "# Extra\n\nSee [a link whose text\nwraps](../nowhere.md).\n")
    assert links.main() == 1
    assert "missing file ../nowhere.md" in capsys.readouterr().out


def test_leading_slash_resolves_against_the_repository_root(repo, links, capsys):
    repo.write("docs/sub/extra.md", "# Extra\n\nSee [the guide](/architecture.md#the-storage-layer).\n")
    assert links.main() == 0
    repo.write("docs/sub/extra.md", "# Extra\n\nSee [the guide](/docs/architecture.md).\n")
    assert links.main() == 1
    assert "docs/sub/extra.md:3: missing file /docs/architecture.md" in capsys.readouterr().out


def test_absolute_path_is_not_read_from_the_filesystem_root(repo, links, capsys):
    repo.write("docs/extra.md", "# Extra\n\nSee [hosts](/etc/hosts).\n")
    assert links.main() == 1
    assert "docs/extra.md:3: missing file /etc/hosts" in capsys.readouterr().out


@pytest.mark.parametrize("target", ["/../etc/hosts", "../../etc/hosts", "../../../../../../../../../etc/hosts"])
def test_link_that_leaves_the_repository_fails(repo, links, capsys, target):
    repo.write("docs/extra.md", f"# Extra\n\nSee [a file]({target}).\n")
    assert links.main() == 1
    assert f"docs/extra.md:3: {target} leaves the repository" in capsys.readouterr().out


def test_caches_and_benchmark_runs_are_not_scanned(repo, links):
    repo.write(".pytest_cache/README.md", "# Cache\n\n[x](missing.md)\n")
    repo.write("benchmark/runs/one/report.md", "# Run\n\n[x](missing.md)\n")
    assert links.main() == 0
    repo.write("benchmark/sub/x.md", "# Deep\n\n[x](missing.md)\n")
    assert links.main() == 1


def test_external_links_are_not_fetched(repo, links):
    repo.write("docs/extra.md", "# Extra\n\n[x](https://example.invalid/none) [m](mailto:a@b.c)\n")
    assert links.main() == 0


@pytest.mark.parametrize(
    "link",
    [
        "[the guide](../missing.md 'single-quoted title')",
        "[the guide](<../missing.md>)",
        "[see [the note]](../missing.md)",
        "[the guide][ref]\n\n[ref]: ../missing.md",
        "[the guide](../missing.md (a parenthesised title))",
    ],
)
def test_every_link_form_is_checked(repo, links, capsys, link):
    repo.write("docs/extra.md", f"# Extra\n\n{link}\n")
    assert links.main() == 1
    assert "missing file ../missing.md" in capsys.readouterr().out


def test_a_definition_inside_fenced_code_is_not_a_link(repo, links):
    repo.write("docs/extra.md", "# Extra\n\n```text\n[ref]: ../missing.md\n```\n")
    assert links.main() == 0


@pytest.mark.parametrize("line", ["See section 4 for the details.", "## 2.1 The storage layer"])
def test_a_section_by_number_fails_outside_the_lenses_too(repo, links, capsys, line):
    repo.write("skills/extra.md", f"# Extra\n\n{line}\n")
    assert links.main() == 1
    assert "skills/extra.md:3:" in capsys.readouterr().out


def test_a_release_heading_in_the_changelog_is_a_version_not_a_section(repo, links):
    repo.write("CHANGELOG.md", "# Changelog\n\n## 0.27.0 (2026-09-21)\n")
    assert links.main() == 0


def test_a_link_inside_fenced_code_is_not_checked(repo, links, capsys):
    repo.write(
        "docs/extra.md",
        "# Extra\n\n```markdown\nSee [the guide](../missing.md).\n```\n\n~~~\n[x](#nowhere)\n~~~\n",
    )
    assert links.main() == 0, capsys.readouterr().out
