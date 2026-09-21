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
    text = text.replace(
        "**Look for.** Tables holding two entities.\n\n**Violation.** A table with a discriminator column.",
        "**Violation.** A table with a discriminator column.\n\n**Look for.** Tables holding two entities.",
    )
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


def test_stated_count_must_equal_the_catalog(repo, lenses, capsys):
    repo.edit("README.md", "2 lenses", "3 lenses")
    assert lenses.main() == 1
    assert "README.md:3: says 3 lenses, the catalog has 2" in capsys.readouterr().out
    repo.edit("README.md", "3 lenses", "2 lenses")
    repo.edit("lenses/README.md", "Ids are the group prefix", "The 5 lenses here. Ids are the group prefix")
    assert lenses.main() == 1
    assert "lenses/README.md" in capsys.readouterr().out


def test_principle_over_sixty_words_fails(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Principle.** One table per entity.", "**Principle.** " + "word " * 61)
    assert lenses.main() == 1
    assert "Principle is 61 words, limit 60" in capsys.readouterr().out


def test_four_sentences_in_look_for_or_violation_fail(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Violation.** A table with a discriminator column.", "**Violation.** One. Two. Three. Four.")
    assert lenses.main() == 1
    assert "Violation is 4 sentences, limit 3" in capsys.readouterr().out
    repo.edit("lenses/om.md", "**Violation.** One. Two. Three. Four.", "**Violation.** One; two; three; four `x.y`. Five.")
    assert lenses.main() == 0


def test_line_wider_than_eighty_columns_fails(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Look for.** Tables holding two entities.", "**Look for.** " + "x" * 70)
    assert lenses.main() == 1
    assert "84 columns, limit 80" in capsys.readouterr().out


def test_list_continuation_lines_count_toward_the_principle(repo, lenses, capsys):
    items = "\n".join(f"{marker} {' '.join(['word'] * 10)}" for marker in ["-", "*", "**", "-", "*", "-"])
    repo.edit("lenses/om.md", "**Principle.** One table per entity.\n", f"**Principle.** One table per entity.\n{items}\n")
    assert lenses.main() == 1
    assert "Principle is 65 words, limit 60" in capsys.readouterr().out


def test_a_new_field_line_still_ends_the_value_before_it(repo, lenses):
    repo.edit("lenses/om.md", "**Principle.** One table per entity.\n", "**Principle.** One table per entity.\n- one item\n")
    assert lenses.main() == 0


def test_lens_syntax_inside_fenced_code_is_an_example(repo, lenses, capsys):
    example = "```markdown\n## OM-09 An example lens\n\n**Principle.** Shown, not counted.\n```\n"
    repo.edit(
        "lenses/om.md",
        "**Violation.** A table with a discriminator column.\n",
        f"**Violation.** A table with a discriminator column, like this:\n\n{example}",
    )
    assert lenses.main() == 0
    assert "lenses ok: 2 lenses" in capsys.readouterr().out
