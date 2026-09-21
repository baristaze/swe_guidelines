"""benchmark/harness/judge.py: the prompt, the matrix, and a judgement per provider."""

import json
from pathlib import Path

import pytest

from harness import judge as J
from harness import providers as P

VERDICT = {
    "score": 82,
    "verdict": "pass",
    "findings": [{"severity": "medium", "note": "one thing"}],
    "strengths": ["clear"],
    "rationale": "because",
}


def fake_call(*_args):
    return VERDICT, json.dumps(VERDICT), {"input_tokens": 10, "output_tokens": 20}


def failing_call(model, effort, prompt, key):
    if model == "claude-opus-5":
        raise RuntimeError("out of quota")
    return fake_call()


def test_the_prompt_carries_the_rubric_the_subject_and_the_artifact():
    prompt = J.build_prompt("the rubric", "the subject", "the artifact")
    assert "the rubric" in prompt and "the subject" in prompt and "the artifact" in prompt
    assert "0 to 100" in prompt


def test_a_long_artifact_is_cut_and_says_so():
    prompt = J.build_prompt("r", "s", "x" * 100, limit=20)
    assert "truncated at 20 characters of 100" in prompt
    assert J.truncate("short", 20) == "short"


def test_a_provider_without_a_key_is_skipped_not_failed():
    judgement = J.judge_one(P.Provider.OPENAI, "p", "medium", J.DEFAULT_MATRIX, env={}, call=fake_call)
    assert judgement.status == "skipped"
    assert "OPENAI_API_KEY" in judgement.error
    assert judgement.verdict is None


def test_a_judgement_carries_the_model_the_effort_and_the_usage():
    judgement = J.judge_one(P.Provider.ANTHROPIC, "p", "medium", J.DEFAULT_MATRIX, env={"ANTHROPIC_API_KEY": "k"}, call=fake_call)
    assert judgement.status == "ok"
    assert judgement.model == "claude-opus-5"
    assert judgement.effort == "medium"
    assert judgement.usage == {"input_tokens": 10, "output_tokens": 20}
    assert judgement.verdict.score == 82
    assert judgement.verdict.findings[0].severity == "medium"


def test_a_refused_model_falls_back_and_the_result_names_what_answered():
    judgement = J.judge_one(P.Provider.ANTHROPIC, "p", "high", J.DEFAULT_MATRIX, env={"ANTHROPIC_API_KEY": "k"}, call=failing_call)
    assert judgement.status == "ok"
    assert judgement.model == "claude-sonnet-5"


def test_every_model_failing_is_an_error_that_keeps_what_each_said():
    def always_fails(model, effort, prompt, key):
        raise RuntimeError(f"{model} said no")

    judgement = J.judge_one(P.Provider.XAI, "p", "low", J.DEFAULT_MATRIX, env={"GROK_API_KEY": "k"}, call=always_fails)
    assert judgement.status == "error"
    assert "grok-4 said no" in judgement.error
    assert judgement.verdict is None


def test_judge_all_answers_once_per_selected_provider():
    judgements = J.judge_all(P.parse("3"), "p", "medium", env={"ANTHROPIC_API_KEY": "k"}, call=fake_call)
    assert [j.provider for j in judgements] == ["anthropic", "openai"]
    assert [j.status for j in judgements] == ["ok", "skipped"]


def test_the_effort_word_comes_from_the_matrix():
    assert J.effort_for(J.DEFAULT_MATRIX, "gemini", "low") == "low"
    assert J.effort_for(J.DEFAULT_MATRIX, "xai", "medium") == "high"
    with pytest.raises(ValueError, match="effort is one of"):
        J.effort_for(J.DEFAULT_MATRIX, "openai", "maximum")


def test_a_score_outside_the_range_is_pulled_back_in():
    assert J.Verdict.from_data({"score": 140, "verdict": "pass"}).score == 100
    assert J.Verdict.from_data({"score": -5, "verdict": "fail"}).score == 0


def test_the_built_in_matrix_covers_every_provider_and_effort():
    for provider in P.members(P.ALL):
        spec = J.DEFAULT_MATRIX[P.name(provider)]
        assert spec["model"]
        assert set(spec["effort"]) == set(J.EFFORTS)


def test_the_matrix_file_names_every_provider():
    text = (Path(__file__).resolve().parent.parent / "benchmark" / "models.yaml").read_text(encoding="utf-8")
    for provider in P.members(P.ALL):
        assert f"{P.name(provider)}:" in text


def test_a_missing_matrix_file_falls_back_to_the_built_in_one(tmp_path):
    assert J.load_matrix(tmp_path / "nothing.yaml") == J.DEFAULT_MATRIX
    assert J.load_matrix(None) == J.DEFAULT_MATRIX


def test_evidence_goes_in_only_when_there_is_some_and_the_judge_is_told_to_check_it():
    plain = J.build_prompt("r", "s", "a")
    assert "## Evidence" not in plain and "against the evidence" not in plain
    with_evidence = J.build_prompt("r", "s", "a", evidence="the source")
    assert "## Evidence" in with_evidence and "the source" in with_evidence
    assert "against the evidence" in with_evidence
    assert with_evidence.index("## Artifact") < with_evidence.index("## Evidence") < with_evidence.index("## How to answer")
