"""benchmark/harness/scenario.py: the scenario file and its defaults."""

import json
from pathlib import Path

import pytest

from harness import scenario as S

MINIMAL = {
    "name": "one",
    "kind": "skill",
    "subject": {"skill": "arch-explain", "prompt": "why?"},
    "rubric": "Score it 0 to 100.",
}


def write(folder: Path, name: str, data: dict) -> Path:
    path = folder / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_minimal_scenario_takes_the_defaults(tmp_path):
    scn = S.load(write(tmp_path, "one.json", MINIMAL))
    assert scn.kind == "skill"
    assert scn.subject.max_turns == 6
    assert scn.artifact.stdout is True
    assert scn.judges.providers == "3"
    assert scn.judges.effort == "medium"


def test_the_scenario_round_trips_as_plain_data(tmp_path):
    scn = S.load(write(tmp_path, "one.json", MINIMAL))
    data = scn.as_dict()
    assert data["subject"]["skill"] == "arch-explain"
    assert json.loads(json.dumps(data))["rubric"].startswith("Score it")


def test_an_unknown_key_is_refused(tmp_path):
    bad = dict(MINIMAL, rubrick="oops")
    with pytest.raises(S.ScenarioError, match="unknown key"):
        S.load(write(tmp_path, "bad.json", bad))


def test_each_kind_needs_its_own_field(tmp_path):
    with pytest.raises(S.ScenarioError, match=r"needs subject\.skill"):
        S.from_data(dict(MINIMAL, subject={"prompt": "x"}))
    with pytest.raises(S.ScenarioError, match=r"needs subject\.argv"):
        S.from_data(dict(MINIMAL, kind="command", subject={}))
    with pytest.raises(S.ScenarioError, match=r"needs subject\.prompt"):
        S.from_data(dict(MINIMAL, kind="qa", subject={}))


def test_an_unknown_kind_and_a_missing_rubric_are_refused():
    with pytest.raises(S.ScenarioError, match="is not one of"):
        S.from_data(dict(MINIMAL, kind="quiz"))
    with pytest.raises(S.ScenarioError, match="rubric is required"):
        S.from_data({k: v for k, v in MINIMAL.items() if k != "rubric"})


def test_a_list_field_written_as_a_string_is_refused():
    with pytest.raises(S.ScenarioError, match="expected a list"):
        S.from_data(dict(MINIMAL, subject={"skill": "arch-explain", "allowed_tools": "Read"}))


def test_the_catalog_lists_one_file_per_name(tmp_path):
    write(tmp_path, "b.json", MINIMAL)
    write(tmp_path, "a.json", MINIMAL)
    (tmp_path / "notes.md").write_text("not a scenario", encoding="utf-8")
    assert [p.stem for p in S.catalog(tmp_path)] == ["a", "b"]


def test_find_takes_a_name_or_a_path(tmp_path):
    path = write(tmp_path, "one.json", MINIMAL)
    assert S.find("one", tmp_path) == path
    assert S.find(str(path), tmp_path) == path
    with pytest.raises(S.ScenarioError, match="known: one"):
        S.find("two", tmp_path)


def test_the_shipped_scenarios_are_a_catalog_of_three():
    folder = Path(__file__).resolve().parent.parent / "benchmark" / "scenarios"
    assert [p.stem for p in S.catalog(folder)] == ["explain-tenancy", "review-om", "support-turn"]


def test_evidence_is_read_and_its_unknown_keys_refused(tmp_path):
    data = dict(MINIMAL, subject={"skill": "arch-explain", "target": "t"}, evidence={"files": ["**/*.py"], "expected": "e.yaml"})
    scn = S.load(write(tmp_path, "one.json", data))
    assert scn.evidence.files == ["**/*.py"] and scn.evidence.expected == "e.yaml"
    assert scn.as_dict()["evidence"] == {"files": ["**/*.py"], "expected": "e.yaml"}
    with pytest.raises(S.ScenarioError, match="unknown key"):
        S.from_data(dict(MINIMAL, evidence={"file": []}))


def test_expected_findings_need_a_target_of_their_own():
    with pytest.raises(S.ScenarioError, match="describes a target"):
        S.from_data(dict(MINIMAL, evidence={"expected": "e.yaml"}))


def test_a_relative_path_is_read_from_the_scenario_folder(tmp_path):
    (tmp_path / "scenarios").mkdir()
    scn = S.load(write(tmp_path / "scenarios", "one.json", MINIMAL))
    assert scn.resolve("../fixtures/x") == (tmp_path / "fixtures" / "x").resolve()
    assert scn.resolve("/abs/x") == Path("/abs/x").resolve()
    assert scn.resolve(None) is None
