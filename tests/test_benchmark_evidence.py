"""benchmark/harness/evidence.py: the source the judges read, and the check no model makes."""

import re
from pathlib import Path

from harness import evidence as E

EXPECTED = {
    "findings": [
        {"id": "F1", "lens": "OM-04", "file": "om/types/warehouse.py", "line": 4},
        {"id": "F2", "lens": "OM-12", "file": "om/impl/manager.py", "line": 37},
        {"id": "F3", "lens": "OM-15", "file": "om/rules.py", "line": 11},
        {"lens": "OM-01", "file": "no-id.py"},
    ]
}


def test_the_source_is_numbered_in_path_order_and_cut_when_long(tmp_path):
    (tmp_path / "om" / "b").mkdir(parents=True)
    (tmp_path / "om" / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "om" / "b" / "c.py").write_text("three\n", encoding="utf-8")
    (tmp_path / "om" / "notes.md").write_text("skip\n", encoding="utf-8")
    text = E.source(tmp_path, ["om/**/*.py", "om/*.py"])
    assert text.index("### Source: om/a.py") < text.index("### Source: om/b/c.py")
    assert "1 | one\n2 | two" in text and "skip" not in text
    assert "truncated at 10 characters" in E.source(tmp_path, ["om/**/*.py"], limit=10)


def test_a_planted_finding_is_named_by_its_lens_and_its_file_together():
    artifact = "\n".join(
        [
            "| Lens | Where | What |",
            "|------|-------|------|",
            "| OM-04 | `warehouse.py:4` | order |",
            "| OM-12 | `bin.py:7` | uuid4 |",
            "",
            "### OM-15 Rules are pure",
            "",
            "`rules.py:11` reads the clock.",
            "",
            "`manager.py:37` breaks it too, see OM-07.",
        ]
    )
    result = E.named(EXPECTED, artifact)
    assert result == {"expected": 3, "named": ["F1", "F3"], "missed": ["F2"]}


def test_a_file_named_before_the_lens_on_the_same_line_counts():
    result = E.named(EXPECTED, "- `manager.py:37`: OM-12, uuid4 for an id")
    assert result is not None
    assert result["named"] == ["F2"]


def test_no_planted_list_means_no_check():
    assert E.named(None, "OM-04") is None
    assert E.named({"findings": []}, "OM-04") is None


def test_render_puts_the_expected_list_before_the_source():
    text = E.render("findings: []", "### Source: a.py")
    assert text.index("### Expected findings") < text.index("### Source: a.py")
    assert "never saw this list" in text
    assert E.render(None, "") == ""


def test_every_planted_finding_of_review_om_points_at_the_line_it_shows():
    """The answers and the checkout they describe stay in step. Read without pyyaml."""
    fixtures = Path(__file__).resolve().parent.parent / "benchmark" / "fixtures"
    text = (fixtures / "review-om.expected.yaml").read_text(encoding="utf-8")
    entries = re.findall(r"- id: (F\d+)\n\s+lens: (\S+)\n\s+file: (\S+)\n\s+line: (\d+)\n\s+shows: '(.*)'", text)
    assert len(entries) == 10
    assert len({fid for fid, *_ in entries}) == 10, "one id per planted finding"
    for fid, _lens, file, line, shows in entries:
        path = fixtures / "review-om" / file
        assert path.is_file(), f"{fid}: {file}"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert 1 <= int(line) <= len(lines), f"{fid}: line {line}"
        assert lines[int(line) - 1].strip() == shows.replace("''", "'"), f"{fid}: line {line} no longer shows the defect"
    assert "expected" not in {p.name for p in (fixtures / "review-om").rglob("*")}, "the answers stay out of the checkout"


def test_two_planted_files_of_one_base_name_are_told_apart_by_their_path():
    expected = {
        "findings": [
            {"id": "A", "lens": "OM-07", "file": "om/tasks/__init__.py"},
            {"id": "B", "lens": "OM-07", "file": "om/orders/__init__.py"},
        ]
    }
    # names only the tasks package; the bare base name would have counted both
    result = E.named(expected, "- OM-07 in om/tasks/__init__.py: the root relaxes extra")
    assert result == {"expected": 2, "named": ["A"], "missed": ["B"]}
    assert E.shortest_name("om/types/warehouse.py", {"om/types/warehouse.py", "om/rules.py"}) == "warehouse.py"


def test_the_source_never_leaves_the_target(tmp_path):
    target = tmp_path / "target"
    (target / "om").mkdir(parents=True)
    (target / "om" / "a.py").write_text("inside\n", encoding="utf-8")
    (tmp_path / "answers.py").write_text("OUTSIDE\n", encoding="utf-8")
    (target / "om" / "link.py").symlink_to(tmp_path / "answers.py")
    text = E.source(target, ["om/*.py", "../*.py"])
    assert "inside" in text
    assert "OUTSIDE" not in text


def test_a_line_is_numbered_as_an_editor_numbers_it(tmp_path):
    # Only a newline ends a line: a form feed or U+2028 inside one does not
    # shift the numbers a finding is checked against.
    (tmp_path / "a.py").write_bytes("one\x0cstill one\ntwo\u2028still two\nthree\n".encode())
    text = E.source(tmp_path, ["*.py"])
    assert "1 | one\x0cstill one\n2 | two\u2028still two\n3 | three\n```" in text


def test_review_om_expects_what_its_lenses_say():
    """The answer key never penalizes a finding the lens text bears out."""
    fixtures = Path(__file__).resolve().parent.parent / "benchmark" / "fixtures"
    text = (fixtures / "review-om.expected.yaml").read_text(encoding="utf-8")
    findings, clean = text.split("\nclean:\n")
    assert "lens: OM-16" in findings
    assert re.search(r"lens: OM-03\n(?:\s+\w+: .*\n)*?\s+what: .*MANAGER_OWNED_FIELDS", findings)
    assert "OM-16" not in clean and "tenancy" not in clean, "no clean line absolves a missing tenancy namespace"


def write_lenses(root: Path) -> Path:
    lenses = root / "lenses"
    lenses.mkdir()
    (lenses / "README.md").write_text("# Lenses\n\n## OM-99 Not a lens file\n", encoding="utf-8")
    (lenses / "om.md").write_text(
        "# Object model\n\n## OM-01 One model\n\nOne text.\n\n## OM-03 Mixins\n\nMixin text.\n\n## Notes\n\nAfter.\n",
        encoding="utf-8",
    )
    (lenses / "storage.md").write_text("# Storage\n\n## ST-02 Tables\n\nTable text.\n", encoding="utf-8")
    return lenses


def test_the_cited_lenses_come_once_in_the_order_the_answer_cites_them(tmp_path):
    lenses = write_lenses(tmp_path)
    text = E.cited(lenses, "ST-02 first, then OM-03, then OM-03 again and ISO-86 and UTF-08.")
    assert text.startswith("### Lenses the artifact cites")
    assert text.index("#### ST-02 Tables") < text.index("#### OM-03 Mixins")
    assert text.count("Mixin text.") == 1
    assert "One text." not in text and "After." not in text  # an uncited lens, and a section that is no lens
    assert "No lens has the id" not in text  # an id of no lens group is not a lens id


def test_an_id_no_lens_carries_is_named_and_brings_no_text(tmp_path):
    lenses = write_lenses(tmp_path)
    text = E.cited(lenses, "OM-42 and OM-99 and OM-01")
    assert text.endswith("No lens has the id OM-42, OM-99.")  # README.md holds no lens
    assert "One text." in text
    many = " ".join(f"OM-{n:02d}" for n in range(50, 80))
    assert E.cited(lenses, many).endswith("OM-69, and 10 more.")
    assert E.cited(lenses, "no lens here") == ""


def test_the_cited_lenses_are_cut_when_long(tmp_path):
    lenses = write_lenses(tmp_path)
    text = E.cited(lenses, "OM-01 OM-03 ST-02", limit=20)
    assert "lenses truncated at 20 characters" in text and "Table text." not in text


def test_render_puts_the_lenses_between_the_expected_list_and_the_source():
    text = E.render("findings: []", "### Source: a.py", "### Lenses the artifact cites")
    assert text.index("### Expected findings") < text.index("### Lenses") < text.index("### Source: a.py")


def test_the_judges_of_the_last_review_om_answer_read_the_lenses_it_cites():
    """The checked-in answer cites OM-03 and OM-16; their text is what a judge weighs them against."""
    root = Path(__file__).resolve().parent.parent
    runs = sorted((root / "benchmark" / "runs" / "review-om").glob("2*/artifacts/0/answer.md"))
    answer = runs[-1].read_text(encoding="utf-8")
    text = E.cited(root / "lenses", answer)
    assert "#### OM-03 The mixins declare exactly their fields" in text
    assert "`MANAGER_OWNED_FIELDS`, a tuple, even when empty" in text
    assert "#### OM-16 Cross-cutting namespaces are ordinary namespaces" in text
    assert len(text) <= E.LENS_LIMIT + 200
