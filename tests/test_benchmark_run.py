"""benchmark/run.py: the subject's command, with the plugin and the target it can reach."""

import importlib.util
from pathlib import Path

import pytest

from harness import scenario as S

RUN = Path(__file__).resolve().parent.parent / "benchmark" / "run.py"
spec = importlib.util.spec_from_file_location("benchmark_run", RUN)
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

SKILL = {"name": "one", "kind": "skill", "subject": {"skill": "arch-review-om", "prompt": "Review it."}, "rubric": "r"}


def test_a_skill_gets_the_plugin_and_the_target_it_is_given():
    scn = S.from_data(SKILL)
    argv = run.subject_argv(scn, "swe-guidelines", "/plugin", "/target", "claude")
    assert argv[argv.index("--plugin-dir") + 1] == "/plugin"
    assert argv[argv.index("--add-dir") + 1] == "/target"
    prompt = argv[argv.index("-p") + 1]
    assert prompt.startswith("/swe-guidelines:arch-review-om Review it.")
    assert "The checkout to work on is at /target." in prompt


def test_a_skill_with_no_target_is_told_of_none():
    argv = run.subject_argv(S.from_data(SKILL), "swe-guidelines", "/plugin", None)
    assert "--add-dir" not in argv
    assert "checkout" not in argv[argv.index("-p") + 1]


def test_the_target_placeholder_is_filled_and_refused_when_there_is_no_target():
    scn = S.from_data(dict(SKILL, subject={"skill": "arch-review-om", "prompt": "Review {target}/om."}))
    assert run.subject_prompt(scn, "/target") == "Review /target/om."
    with pytest.raises(S.ScenarioError, match="has no target"):
        run.subject_prompt(scn, None)


def test_a_command_gets_the_paths_in_its_argv():
    scn = S.from_data({"name": "c", "kind": "command", "subject": {"argv": ["ls", "{target}", "{plugin}"]}, "rubric": "r"})
    assert run.subject_argv(scn, "p", "/plugin", "/target") == ["ls", "/target", "/plugin"]


def test_the_shipped_review_runs_on_its_planted_checkout_with_the_answers_outside_it():
    pytest.importorskip("yaml")
    scn = S.load(RUN.parent / "scenarios" / "review-om.yaml")
    target = scn.resolve(scn.subject.target)
    expected = scn.resolve(scn.evidence.expected)
    assert (target / "om").is_dir() and expected.is_file()
    assert target not in expected.parents
