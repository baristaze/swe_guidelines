"""benchmark/harness/results.py: the summary, the schema, and the two writers."""

import json
from pathlib import Path

from harness import judge as J
from harness import results as R

SCHEMA = Path(__file__).resolve().parent.parent / "benchmark" / "schema" / "result.schema.json"

REQUIRED = [
    "schema_version",
    "run_id",
    "scenario",
    "runtime",
    "started_at",
    "finished_at",
    "guideline_sha",
    "target_sha",
    "subject",
    "repeats",
    "summary",
]


def verdict(score, severity="high", note="a note"):
    return J.Verdict.from_data(
        {
            "score": score,
            "verdict": "pass" if score >= 70 else "weak",
            "findings": [{"severity": severity, "note": note}],
            "strengths": ["a strength"],
            "rationale": "a rationale",
        }
    )


def judgement(provider, score=None, status="ok", model="m", error=None):
    return J.Judgement(
        provider=provider,
        model=model,
        effort="medium",
        status=status,
        latency_s=1.5,
        usage={"input_tokens": 1, "output_tokens": 2},
        error=error,
        verdict=verdict(score) if score is not None else None,
    )


def a_run(repeats):
    return R.RunResult(
        run_id="20260101-000000-one",
        scenario="one",
        runtime="host",
        started_at=R.now(),
        guideline_sha="abc123",
        subject={"kind": "skill", "skill": "arch-explain", "prompt": "why?"},
        repeats=repeats,
    )


def test_the_summary_averages_per_provider_over_every_repeat():
    repeats = [
        R.RepeatResult(0, {"code": 0}, [], [judgement("anthropic", 80), judgement("openai", 60)]),
        R.RepeatResult(1, {"code": 0}, [], [judgement("anthropic", 90), judgement("openai", 70)]),
    ]
    summary = R.summarize(repeats)
    assert summary["per_provider"]["anthropic"] == {"mean": 85.0, "min": 80, "max": 90, "n": 2, "stdev": 7.1}

    assert summary["overall_mean"] == 75.0
    assert summary["skipped"] == []


def test_a_provider_that_did_not_answer_is_named_with_its_reason():
    repeats = [R.RepeatResult(0, {"code": 0}, [], [judgement("xai", status="error", error="403 from the account")])]
    summary = R.summarize(repeats)
    assert summary["per_provider"] == {}
    assert summary["overall_mean"] is None
    assert summary["skipped"] == [{"provider": "xai", "reason": "403 from the account"}]


def test_each_provider_weighs_once_in_the_overall_mean():
    # One provider answered three times, the other once: pooled, the mean
    # would be 42.5; per provider, it is the mean of 30 and 80.
    repeats = [
        R.RepeatResult(0, {"code": 0}, [], [judgement("gemini", 30), judgement("openai", 80)]),
        R.RepeatResult(1, {"code": 0}, [], [judgement("gemini", 30), judgement("openai", status="error", error="429")]),
        R.RepeatResult(2, {"code": 0}, [], [judgement("gemini", 30), judgement("openai", status="error", error="timeout")]),
    ]
    summary = R.summarize(repeats)
    assert summary["overall_mean"] == 55.0
    assert summary["skipped"] == []
    assert summary["missed"] == [{"provider": "openai", "count": 2, "reason": "429"}]


def test_findings_come_back_most_severe_first():
    repeats = [
        R.RepeatResult(
            0,
            {"code": 0},
            [],
            [
                J.Judgement(provider="openai", model="m", effort="medium", verdict=verdict(70, "low", "small")),
                J.Judgement(provider="anthropic", model="m", effort="medium", verdict=verdict(70, "high", "big")),
            ],
        )
    ]
    assert [f["note"] for f in R.findings_by_severity(repeats)] == ["big", "small"]


def test_the_results_file_has_every_required_key(tmp_path):
    run = a_run(
        [
            R.RepeatResult(
                0,
                {"code": 0, "signal": None, "duration_s": 1.0, "timed_out": False},
                ["artifacts/0/answer.md"],
                [judgement("anthropic", 88)],
            )
        ]
    )
    data = R.write_results(run, tmp_path / "results.json")
    written = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert written == data
    for key in REQUIRED:
        assert key in data
    judged = data["repeats"][0]["judgements"][0]
    assert set(judged) == {"provider", "model", "effort", "status", "latency_s", "usage", "error", "fallback", "verdict"}
    assert judged["verdict"]["score"] == 88


def test_the_schema_file_parses_and_requires_what_the_writer_writes():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["required"]) == set(REQUIRED)
    assert set(schema["properties"]["summary"]["required"]) == {"per_provider", "overall_mean", "skipped"}
    providers = schema["properties"]["repeats"]["items"]["properties"]["judgements"]["items"]["properties"]["provider"]
    assert providers["enum"] == ["anthropic", "openai", "gemini", "xai"]


def test_the_report_names_the_scores_the_findings_and_the_paths(tmp_path):
    run = a_run(
        [
            R.RepeatResult(
                0,
                {"code": 0},
                ["artifacts/0/answer.md"],
                [judgement("anthropic", 88, model="claude-opus-5"), judgement("xai", status="skipped", error="no key")],
            )
        ]
    )
    text = R.write_report(run, tmp_path / "report.md")
    assert "# Benchmark run 20260101-000000-one" in text
    assert "| 0 | anthropic | `claude-opus-5` | medium | 88 | pass | 1.5 | ok |" in text
    assert "Overall mean: 88.0." in text
    assert "- `xai`: no key" in text
    assert "- **high** (anthropic, repeat 0): a note" in text
    assert "`artifacts/0/answer.md`" in text
    assert text.endswith("\n")


def test_the_report_says_so_when_no_one_scored(tmp_path):
    run = a_run([R.RepeatResult(0, {"code": 0}, [], [judgement("xai", status="error", error="403")])])

    text = R.report_text(run)
    assert "Overall mean: no score." in text
    assert "No judge raised a finding." in text


def test_validation_without_jsonschema_says_so_instead_of_claiming_a_pass(monkeypatch, tmp_path):
    run = a_run([R.RepeatResult(0, {"code": 0}, [], [judgement("anthropic", 50)])])
    data = run.as_dict()
    import builtins

    real = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "jsonschema":
            raise ModuleNotFoundError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    problems = R.validate(data, SCHEMA)
    assert problems == ["jsonschema is not installed; results.json was written unvalidated"]


def test_the_expected_check_is_recorded_per_repeat_and_reported(tmp_path):
    checked = R.RepeatResult(
        index=0,
        exit_status={"code": 0},
        judgements=[judgement("anthropic", 80)],
        expected={"expected": 3, "named": ["F1", "F2"], "missed": ["F3"]},
    )
    plain = R.RepeatResult(index=1, exit_status={"code": 0}, judgements=[judgement("anthropic", 70)])
    run = R.RunResult(
        run_id="r", scenario="s", runtime="host", started_at="t", subject={"kind": "skill"}, repeats=[checked, plain]
    )
    data = run.as_dict()
    assert data["repeats"][0]["expected"]["missed"] == ["F3"]
    assert "expected" not in data["repeats"][1]
    assert "- repeat 0: named 2 of 3; missed: F3" in R.report_text(run)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert set(schema["properties"]["repeats"]["items"]["properties"]["expected"]["required"]) == {"expected", "named", "missed"}
    problems = R.validate(data, SCHEMA)
    assert problems in ([], ["jsonschema is not installed; results.json was written unvalidated"])


def test_a_mean_and_a_score_round_halves_up():
    from harness.judge import half_up

    assert half_up(72.5) == 73.0 and half_up(0.25, 1) == 0.3 and half_up(80.45, 1) == 80.5


def test_a_fallback_is_recorded_in_the_result_and_named_in_the_summary():
    fell = judgement("anthropic", 80, model="claude-sonnet-5")
    fell.fallback = {"from": "claude-opus-5", "reason": "claude-opus-5: RuntimeError: out of quota"}
    repeats = [
        R.RepeatResult(0, {"code": 0}, [], [fell, judgement("openai", 60)]),
        R.RepeatResult(1, {"code": 0}, [], [judgement("anthropic", 90, model="claude-opus-5")]),
    ]
    run = a_run(repeats)
    data = run.as_dict()
    assert data["repeats"][0]["judgements"][0]["fallback"]["from"] == "claude-opus-5"
    assert data["summary"]["fallbacks"] == [
        {"provider": "anthropic", "from": "claude-opus-5", "to": "claude-sonnet-5", "count": 1, "reason": fell.fallback["reason"]}
    ]
    assert "- `anthropic`: `claude-sonnet-5` answered in place of `claude-opus-5` in 1 judgement(s)" in R.report_text(run)
    assert R.validate(data, SCHEMA) in ([], ["jsonschema is not installed; results.json was written unvalidated"])


def test_a_failed_repeat_scores_zero_and_stays_in_the_mean():
    repeats = [
        R.RepeatResult(0, {"code": 0}, [], [judgement("anthropic", 80), judgement("openai", 60)]),
        R.RepeatResult(1, {"code": 1}, [], []),
        R.RepeatResult(2, {"code": 0, "timed_out": True}, [], []),
        R.RepeatResult(3, {"code": 0, "is_error": True}, [], []),
    ]
    summary = R.summarize(repeats)
    assert summary["failed_repeats"] == [1, 2, 3]
    assert summary["per_provider"]["anthropic"]["mean"] == 20.0
    assert summary["per_provider"]["anthropic"]["n"] == 4 and summary["per_provider"]["anthropic"]["min"] == 0
    assert summary["overall_mean"] == 17.5
    assert summary["spread"] == {"repeat_means": [70.0, 0.0, 0.0, 0.0], "min": 0.0, "max": 70.0, "stdev": 35.0}


def test_a_run_whose_every_repeat_failed_scores_zero():
    summary = R.summarize([R.RepeatResult(0, {"code": 127}, [], []), R.RepeatResult(1, {"code": 127}, [], [])])
    assert summary["overall_mean"] == 0.0 and summary["failed_repeats"] == [0, 1]


def test_one_repeat_has_no_spread_to_report():
    summary = R.summarize([R.RepeatResult(0, {"code": 0}, [], [judgement("anthropic", 80)])])
    assert summary["per_provider"]["anthropic"]["stdev"] is None
    assert summary["spread"] == {"repeat_means": [80.0], "min": 80.0, "max": 80.0, "stdev": None}


def test_a_claude_subject_judged_by_a_panel_with_claude_is_named():
    judged = [R.RepeatResult(0, {"code": 0}, [], [judgement("anthropic", 80), judgement("openai", 60)])]
    assert "anthropic" in (R.summarize(judged, {"kind": "skill"})["self_judged"] or "")
    assert "anthropic" in (R.summarize(judged, {"kind": "qa", "provider": None})["self_judged"] or "")
    assert R.summarize(judged, {"kind": "qa", "provider": "openai"})["self_judged"] is None
    assert R.summarize(judged, {"kind": "command", "model": None})["self_judged"] is None
    others = [R.RepeatResult(0, {"code": 0}, [], [judgement("openai", 60)])]
    assert R.summarize(others, {"kind": "skill"})["self_judged"] is None
    run = a_run(judged)
    data = run.as_dict()
    assert data["summary"]["self_judged"]
    assert data["summary"]["self_judged"] in R.report_text(run)
    assert R.validate(data, SCHEMA) in ([], ["jsonschema is not installed; results.json was written unvalidated"])
