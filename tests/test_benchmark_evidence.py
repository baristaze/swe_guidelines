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
    assert len(entries) == 8
    lenses = {lens for _, lens, _, _, _ in entries}
    assert len(lenses) == 8, "one lens per planted finding"
    for fid, _lens, file, line, shows in entries:
        path = fixtures / "review-om" / file
        assert path.is_file(), f"{fid}: {file}"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert 1 <= int(line) <= len(lines), f"{fid}: line {line}"
        assert lines[int(line) - 1].strip() == shows.replace("''", "'"), f"{fid}: line {line} no longer shows the defect"
    assert "expected" not in {p.name for p in (fixtures / "review-om").rglob("*")}, "the answers stay out of the checkout"
