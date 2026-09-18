"""scripts/check_lenses.py: lens format and citations."""

import pytest


@pytest.fixture
def lenses(repo):
    return repo.script("check_lenses")


def test_valid_tree_passes(repo, lenses, capsys):
    assert lenses.main() == 0
    assert "lenses ok: 2 lenses in 1 groups" in capsys.readouterr().out


def test_unknown_section_fails(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Source.** Interfaces, Principles.", "**Source.** Interface, Principles.")
    assert lenses.main() == 1
    assert "'Interface, Principles' is not a section of architecture.md" in capsys.readouterr().out


def test_unknown_subsection_fails(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Source.** Interfaces, Principles.", "**Source.** Interfaces, Rules.")
    assert lenses.main() == 1
    assert "'Interfaces' has no subsection 'Rules'" in capsys.readouterr().out


def test_bare_subsection_belongs_to_the_previous_section(repo, lenses, capsys):
    repo.edit("lenses/om.md", "The Storage Layer, Tables; Principles.", "Interfaces; Tables.")
    assert lenses.main() == 1
    assert "'Tables' is neither a section nor a subsection of 'Interfaces'" in capsys.readouterr().out


def test_section_cited_by_number_fails(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Source.** Interfaces, Principles.", "**Source.** Section 3, Principles.")
    assert lenses.main() == 1
    assert "cites a section by number" in capsys.readouterr().out


def test_bad_severity_fails(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Severity.** medium", "**Severity.** critical")
    assert lenses.main() == 1
    assert "severity 'critical' is not high, medium, or low" in capsys.readouterr().out


def test_ids_out_of_order_fail(repo, lenses, capsys):
    repo.edit("lenses/om.md", "## OM-02 ", "## OM-03 ")
    assert lenses.main() == 1
    assert "expected id OM-02, found OM-03" in capsys.readouterr().out


def test_fields_out_of_order_fail(repo, lenses, capsys):
    text = repo.read("lenses/om.md")
    text = text.replace("**Look for.** Tables holding two entities.\n\n**Violation.** A table with a discriminator column.",
                        "**Violation.** A table with a discriminator column.\n\n**Look for.** Tables holding two entities.")
    repo.write("lenses/om.md", text)
    assert lenses.main() == 1
    assert "fields are" in capsys.readouterr().out


def test_unlisted_and_missing_files_fail(repo, lenses, capsys):
    repo.write("lenses/extra.md", "# Extra\n\n## EX-01 One\n")
    repo.edit("lenses/README.md", "principles  |\n", "principles  |\n| `ctx` | `context.md` | OpContext: tenancy |\n")
    assert lenses.main() == 1
    out = capsys.readouterr().out
    assert "lenses/extra.md is not listed" in out
    assert "lists ctx -> context.md, file missing" in out
