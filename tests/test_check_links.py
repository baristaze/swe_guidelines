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
    repo.write("benchmark/runs/one/20260101-000000-one-aa/report.md", "# Run\n\n[x](missing.md)\n")
    assert links.main() == 0
    repo.write("benchmark/sub/x.md", "# Deep\n\n[x](missing.md)\n")
    assert links.main() == 1


def test_the_scaffolds_skills_are_scanned(repo, links, capsys):
    rel = "scaffold/acme_root/.agents/skills/ops-watch/SKILL.md"
    repo.write(rel, "# ops-watch\n\nSee [the runbook](../../../docs/runbooks/operate.md).\n")
    assert links.main() == 1
    assert f"{rel}:3: missing file ../../../docs/runbooks/operate.md" in capsys.readouterr().out
    repo.write("scaffold/acme_root/docs/runbooks/operate.md", "# Operate\n")
    assert links.main() == 0


def test_the_index_of_the_benchmark_runs_is_scanned(repo, links, capsys):
    repo.write("benchmark/runs/one/report.md", "# Run\n")
    repo.write("benchmark/runs/README.md", "# Runs\n\n[one](one/report.md) [two](two/report.md)\n")
    assert links.main() == 1
    out = capsys.readouterr().out
    assert "two/report.md" in out and "one/report.md" not in out


def test_a_scenario_s_page_of_the_benchmark_runs_is_scanned(repo, links, capsys):
    repo.write("benchmark/runs/one/20260101-000000-one-aa/report.md", "# Run\n")
    page = "# one\n\n[aa](20260101-000000-one-aa/report.md) [bb](20260102-000000-one-bb/report.md)\n"
    repo.write("benchmark/runs/one/README.md", page)
    assert links.main() == 1
    out = capsys.readouterr().out
    assert "20260102-000000-one-bb/report.md" in out and "20260101-000000-one-aa/report.md" not in out


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


@pytest.mark.parametrize(
    "line",
    ["See section 4 for the details.", "## 2.1 The storage layer", "As subsection 3.2 says.", "See §4.", "See § 4.1."],
)
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
