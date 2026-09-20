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
    assert summary["per_provider"]["anthropic"] == {"mean": 85.0, "min": 80, "max": 90, "n": 2}
    assert summary["overall_mean"] == 75.0
    assert summary["skipped"] == []


def test_a_provider_that_did_not_answer_is_named_with_its_reason():
    repeats = [R.RepeatResult(0, {"code": 0}, [], [judgement("xai", status="error", error="403 from the account")])]
    summary = R.summarize(repeats)
    assert summary["per_provider"] == {}
    assert summary["overall_mean"] is None
    assert summary["skipped"] == [{"provider": "xai", "reason": "403 from the account"}]


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
    run = a_run([R.RepeatResult(0, {"code": 0, "signal": None, "duration_s": 1.0, "timed_out": False}, ["artifacts/0/answer.md"], [judgement("anthropic", 88)])])
    data = R.write_results(run, tmp_path / "results.json")
    written = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert written == data
    for key in REQUIRED:
        assert key in data
    judged = data["repeats"][0]["judgements"][0]
    assert set(judged) == {"provider", "model", "effort", "status", "latency_s", "usage", "error", "verdict"}
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
    run = a_run([R.RepeatResult(0, {"code": 1}, [], [judgement("xai", status="error", error="403")])])
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
