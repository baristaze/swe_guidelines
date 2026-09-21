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


RULE = """from arch_check.registry import rule


@rule("OM-01", coverage="{coverage}", summary="s")
def one(project):
    return iter(())
"""


def test_a_check_line_and_its_rule_agree(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Severity.** high", "**Severity.** high\n\n**Check.** `arch-check` decides it.")
    repo.write("checkers/src/arch_check/rules/om.py", RULE.format(coverage="full"))
    assert lenses.main() == 0


def test_a_partial_check_line_needs_a_partial_rule(repo, lenses, capsys):
    repo.edit(
        "lenses/om.md",
        "**Severity.** high",
        "**Severity.** high\n\n**Check.** `arch-check` decides the import direction; the rest is judged.",
    )
    repo.write("checkers/src/arch_check/rules/om.py", RULE.format(coverage="full"))
    assert lenses.main() == 1
    assert "OM-01 Check line says partial, its rule registers full" in capsys.readouterr().out
    repo.write("checkers/src/arch_check/rules/om.py", RULE.format(coverage="partial"))
    assert lenses.main() == 0


def test_a_check_line_without_a_rule_fails(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Severity.** high", "**Severity.** high\n\n**Check.** `arch-check` decides it.")
    assert lenses.main() == 1
    assert "OM-01 says arch-check decides it, and no rule of that id is registered" in capsys.readouterr().out


def test_a_rule_without_a_check_line_fails(repo, lenses, capsys):
    repo.write("checkers/src/arch_check/rules/om.py", RULE.format(coverage="full"))
    assert lenses.main() == 1
    assert "rule OM-01 is registered, and lens OM-01 has no Check line" in capsys.readouterr().out


def test_a_check_line_in_another_wording_fails(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Severity.** high", "**Severity.** high\n\n**Check.** a script decides it.")
    assert lenses.main() == 1
    assert "Check reads 'a script decides it.'" in capsys.readouterr().out


def test_check_comes_after_severity(repo, lenses, capsys):
    repo.edit("lenses/om.md", "**Severity.** high", "**Check.** `arch-check` decides it.\n\n**Severity.** high")
    repo.write("checkers/src/arch_check/rules/om.py", RULE.format(coverage="full"))
    assert lenses.main() == 1
    assert "optionally followed by Check" in capsys.readouterr().out


def test_an_id_repeated_in_another_file_fails(repo, lenses, capsys):
    repo.edit("lenses/README.md", "principles  |\n", "principles  |\n| `st` | `storage.md` | The Storage Layer: tables |\n")
    repo.write("lenses/storage.md", repo.read("lenses/om.md").replace("## OM-02 Tables are per entity\n", "## ST-01 Tables\n"))
    repo.edit("lenses/storage.md", "## OM-01 Interfaces first", "## OM-01 Interfaces again")
    repo.edit("README.md", "2 lenses", "4 lenses")
    assert lenses.main() == 1
    out = capsys.readouterr().out
    assert "storage.md:5: id OM-01 is already used in om.md" in out
    repo.edit("lenses/storage.md", "## OM-01 Interfaces again", "## ST-01 Interfaces again")
    repo.edit("lenses/storage.md", "## ST-01 Tables\n", "## ST-02 Tables\n")
    assert lenses.main() == 0


def test_a_section_cited_by_number_in_lowercase_fails(repo, lenses, capsys):
    repo.edit("lenses/om.md", "Tables holding two entities.", "Tables holding two entities, as in section 4.")
    assert lenses.main() == 1
    assert "om.md:24: refers to a section by number" in capsys.readouterr().out


@pytest.fixture
def labelled(repo):
    """The Tables subsection gains two bold paragraph labels; Principles has one of its own."""
    repo.edit(
        "architecture.md",
        "One table per entity.\n",
        "One table per entity.\n\n-   **Keys.** Every table has one key.\n\n#### Detail\n\n**Soft delete.** A row is marked.\n",
    )
    repo.edit("architecture.md", "Storage is behind an interface.\n", "Storage is behind an interface.\n\n**Scope.** All.\n")


def test_labels_in_parentheses_name_paragraphs_of_the_subsection(repo, lenses, labelled, capsys):
    repo.edit("lenses/om.md", "The Storage Layer, Tables; Principles.", "The Storage Layer, Tables (Keys, Soft delete).")
    assert lenses.main() == 0
    repo.edit("lenses/om.md", "Tables (Keys, Soft delete).", "Tables; Principles (Scope).")
    assert lenses.main() == 0  # a bare subsection takes labels too


def test_a_label_the_subsection_does_not_hold_fails(repo, lenses, labelled, capsys):
    repo.edit("lenses/om.md", "**Source.** Interfaces, Principles.", "**Source.** Interfaces, Principles (No Such Part).")
    assert lenses.main() == 1
    assert "'Interfaces, Principles' has no paragraph labelled '**No Such Part.**'" in capsys.readouterr().out


def test_a_label_of_another_subsection_or_in_another_case_fails(repo, lenses, labelled, capsys):
    repo.edit("lenses/om.md", "The Storage Layer, Tables; Principles.", "The Storage Layer, Tables (Scope, keys).")
    assert lenses.main() == 1
    out = capsys.readouterr().out
    assert "'The Storage Layer, Tables' has no paragraph labelled '**Scope.**'" in out
    assert "'The Storage Layer, Tables' has no paragraph labelled '**keys.**'" in out


def test_labels_on_a_whole_section_are_refused(repo, lenses, labelled, capsys):
    repo.edit("lenses/om.md", "The Storage Layer, Tables; Principles.", "The Storage Layer (Keys).")
    assert lenses.main() == 1
    assert "'The Storage Layer' names no subsection, so it takes no labels" in capsys.readouterr().out
