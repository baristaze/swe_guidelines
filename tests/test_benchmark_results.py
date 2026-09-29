"""benchmark/harness/results.py: the summary, the schema, and the two writers."""

import json
from pathlib import Path

import pytest

from harness import agentic as A
from harness import judge as J
from harness import references as RF
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
    assert set(judged) == {
        "provider",
        "model",
        "effort",
        "status",
        "latency_s",
        "usage",
        "cost_usd",
        "error",
        "fallback",
        "verdict",
    }
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


def priced(provider, model, usage, cost):
    """A judgement that spent `usage`; with no usage it is a judge that was skipped."""
    j = judgement(provider, 80 if usage else None, status="ok" if usage else "skipped", model=model)
    j.usage, j.cost_usd = usage, cost
    return j


def test_the_run_totals_its_spend_per_judge_for_the_subject_and_in_all(tmp_path):
    run = a_run(
        [
            R.RepeatResult(
                0,
                {"code": 0},
                [],
                [
                    priced("anthropic", "a", {"input_tokens": 100, "output_tokens": 50}, 0.25),
                    priced("xai", "x", {"input_tokens": 10, "output_tokens": 40, "reasoning_tokens": 30}, 0.5),
                    priced("gemini", "g", {}, None),
                ],
                subject_models=["s"],
                subject_usage={"input_tokens": 1000, "output_tokens": 200},
                subject_cost_usd=1.0,
            ),
            R.RepeatResult(
                1,
                {"code": 0},
                [],
                [priced("anthropic", "a", {"input_tokens": 100, "output_tokens": 50}, 0.25)],
                subject_models=["s"],
                subject_usage={"input_tokens": 1000, "output_tokens": 200},
                subject_cost_usd=1.0,
            ),
        ]
    )
    spent = run.as_dict()["spend"]
    assert set(spent["judges"]) == {"anthropic", "xai"}
    assert spent["judges"]["anthropic"] == {
        "input_tokens": 200,
        "output_tokens": 100,
        "reasoning_tokens": 0,
        "cost_usd": 0.5,
        "unpriced": [],
    }
    assert spent["judges"]["xai"]["reasoning_tokens"] == 30
    assert spent["subject"]["input_tokens"] == 2000 and spent["subject"]["cost_usd"] == 2.0
    assert spent["total_usd"] == 3.0 and spent["unpriced"] == []
    assert R.validate(R.write_results(run, tmp_path / "results.json"), SCHEMA) in ([], [R.UNVALIDATED])
    text = R.report_text(run)
    assert "## Spend" in text
    assert "| judge `xai` | 10 | 40 | 30 | $0.5000 |" in text
    assert "Total: $3.0000." in text


def test_a_model_without_a_price_keeps_its_tokens_and_makes_the_total_a_lower_bound():
    run = a_run(
        [
            R.RepeatResult(
                0,
                {"code": 0},
                [],
                [
                    priced("openai", "unknown-model", {"input_tokens": 5, "output_tokens": 7}, None),
                    priced("anthropic", "a", {"input_tokens": 1, "output_tokens": 1}, 0.1),
                ],
            )
        ]
    )
    spent = run.as_dict()["spend"]
    assert spent["judges"]["openai"]["output_tokens"] == 7
    assert spent["judges"]["openai"]["cost_usd"] == 0.0
    assert spent["unpriced"] == ["unknown-model"]
    assert spent["total_usd"] == 0.1
    assert "Total: at least $0.1000. No price for `unknown-model`" in R.report_text(run)


RUNS = Path(__file__).resolve().parent.parent / "benchmark" / "runs"


def test_every_checked_in_run_still_validates_against_the_schema():
    """A later schema adds fields as optional, so a run recorded before them stays valid."""
    pytest.importorskip("jsonschema")
    for results in sorted(RUNS.glob("*/results.json")):
        data = json.loads(results.read_text(encoding="utf-8"))
        assert R.validate(data, SCHEMA) == [], results.parent.name


VERSIONS: dict = {
    "checkout": {"commit": "c" * 40, "plugin_version": "1.0.0", "dirty": False, "dirty_paths": [], "dirty_sha256": None},
    "claude_code": "2.1.0 (Claude Code)",
    "image": {"name": "img:latest", "id": "sha256:" + "a" * 64},
    "target": {"path": "benchmark/fixtures/one", "sha256": "b" * 64},
    "expected": {"path": "benchmark/fixtures/one.expected.yaml", "sha256": "d" * 64},
}


def test_the_versions_are_recorded_and_reported(tmp_path):
    run = a_run([R.RepeatResult(0, {"code": 0}, [], [judgement("anthropic", 50)])])
    run.versions = VERSIONS
    data = run.as_dict()
    assert data["versions"] == VERSIONS
    assert R.validate(data, SCHEMA) in ([], ["jsonschema is not installed; results.json was written unvalidated"])
    text = R.report_text(run)
    assert f"- Checkout: `{'c' * 40}`, plugin `1.0.0`, clean." in text
    assert "- Claude Code: `2.1.0 (Claude Code)`." in text
    assert f"- Image: `img:latest`, id `sha256:{'a' * 64}`." in text
    assert f"- Expected findings: `benchmark/fixtures/one.expected.yaml`, sha256 `{'d' * 64}`." in text


def test_a_dirty_checkout_is_reported_with_its_paths(tmp_path):
    run = a_run([R.RepeatResult(0, {"code": 0}, [], [judgement("anthropic", 50)])])
    checkout = dict(VERSIONS["checkout"], dirty=True, dirty_paths=["skills/a.md"], dirty_sha256="e" * 64)
    run.versions = dict(VERSIONS, checkout=checkout, claude_code=None, image=None)
    text = R.report_text(run)
    assert f"with changes no commit holds, sha256 `{'e' * 64}`: `skills/a.md`." in text
    assert "- Claude Code: not run, or not known." in text


def test_a_hash_that_is_not_one_is_refused_by_the_schema():
    pytest.importorskip("jsonschema")
    run = a_run([R.RepeatResult(0, {"code": 0}, [], [judgement("anthropic", 50)])])
    run.versions = dict(VERSIONS, target={"path": "t", "sha256": "not-a-hash"})
    assert R.validate(run.as_dict(), SCHEMA)


# Agentic judgements against references ------------------------------------

WEIGHTS = {"guideline": 0.4, "reference": 0.6}


def judged(provider, scores=None, status="ok", error=None, gaps=("high",)):
    """An agentic judgement of each reference; `scores` maps a reference to its score, None when it has no answer."""
    answer = None
    if scores is not None:
        gap = [{"severity": s, "what": f"a {s} gap", "in_output": "src/app.py", "in_reference": "Async"} for s in gaps]
        entry = {
            name: {"score": score, "gaps": gap, "strengths": [f"a strength against {name}"]} for name, score in scores.items()
        }
        answer = {"references": entry, "rationale": f"{provider} weighed it"}
    judgement = A.AgenticJudgement(
        provider=provider,
        model="m",
        effort="medium",
        status=status,
        latency_s=30.0,
        usage={"input_tokens": 1000, "output_tokens": 100},
        cost_usd=0.01,
        error=error,
        answer=answer,
        tool_calls=9,
        turns=10,
    )
    return RF.Judged.of(judgement, WEIGHTS, f"judgements/0-{provider}.jsonl")


def agentic_run(repeats):
    run = a_run(repeats)
    run.weights = dict(WEIGHTS)
    return run


def test_an_agentic_judgement_scores_its_weighted_score_in_every_mean():
    repeats = [
        R.RepeatResult(
            0,
            {"code": 0},
            [],
            [judged("anthropic", {"guideline": 72, "reference": 66}), judged("openai", {"guideline": 80, "reference": 60})],
        ),
        R.RepeatResult(
            1,
            {"code": 0},
            [],
            [
                judged("anthropic", {"guideline": 70, "reference": 70}),
                judged("openai", None, "missed", "wall time: the budget of 900 s is spent"),
            ],
        ),
    ]
    summary = R.summarize(repeats, weights=WEIGHTS)
    # anthropic: 68.4 and 70.0; openai: 68.0 once, and the miss named.
    assert summary["per_provider"]["anthropic"] == {"mean": 69.2, "min": 68.4, "max": 70, "n": 2, "stdev": 1.1}
    assert summary["per_provider"]["openai"] == {"mean": 68.0, "min": 68, "max": 68, "n": 1, "stdev": None}
    assert summary["overall_mean"] == 68.6
    assert summary["missed"] == [{"provider": "openai", "count": 1, "reason": "wall time: the budget of 900 s is spent"}]
    assert summary["spread"]["repeat_means"] == [68.2, 70.0]
    refs = summary["references"]
    assert list(refs) == ["guideline", "reference"]
    assert refs["guideline"] == {
        "weight": 0.4,
        "mean": 75.5,  # anthropic's 71, openai's 80
        "per_provider": {"anthropic": 71.0, "openai": 80.0},
        "min": 70,
        "max": 80,
        "n": 3,
        "gaps": {"high": 3, "medium": 0, "low": 0},
    }
    assert refs["reference"]["mean"] == 64.0 and refs["reference"]["per_provider"] == {"anthropic": 68.0, "openai": 60.0}


def test_a_failed_repeat_scores_zero_against_every_reference():
    repeats = [
        R.RepeatResult(0, {"code": 0}, [], [judged("anthropic", {"guideline": 80, "reference": 60})]),
        R.RepeatResult(1, {"code": 1}, []),
    ]
    summary = R.summarize(repeats, weights=WEIGHTS)
    assert summary["per_provider"]["anthropic"]["mean"] == 34.0  # 68.0 and a failure's 0
    assert summary["references"]["guideline"]["per_provider"] == {"anthropic": 40.0}
    assert summary["references"]["reference"]["min"] == 0 and summary["references"]["reference"]["n"] == 2


def test_a_reference_no_judge_scored_has_no_mean():
    summary = R.summarize([R.RepeatResult(0, {"code": 0}, [], [judged("xai", None, "skipped", "no key")])], weights=WEIGHTS)
    assert summary["references"]["guideline"] == {
        "weight": 0.4,
        "mean": None,
        "per_provider": {},
        "min": None,
        "max": None,
        "n": 0,
        "gaps": {"high": 0, "medium": 0, "low": 0},
    }
    assert summary["skipped"] == [{"provider": "xai", "reason": "no key"}] and summary["overall_mean"] is None


REFERENCE_VERSIONS = {
    "guideline": {"source": "checkout", "paths": ["architecture.md", "lenses"], "sha256": "b" * 64},
    "reference": {
        "source": "repository",
        "url": "https://github.com/acme/acme-system",
        "tag": "v0.7.0",
        "commit": "a" * 40,
        "pins": "v0.37.0",
    },
}


def test_an_agentic_run_validates_against_the_schema_and_records_each_reference(tmp_path):
    pytest.importorskip("jsonschema")
    run = agentic_run(
        [
            R.RepeatResult(
                0,
                {"code": 0},
                ["artifacts/0/output.zip"],
                [
                    judged("anthropic", {"guideline": 72, "reference": 66}, gaps=("high", "low")),
                    judged("openai", None, "missed", "spend: $3"),
                ],
            )
        ]
    )
    run.versions = dict(VERSIONS, references=REFERENCE_VERSIONS)
    data = R.write_results(run, tmp_path / "results.json")
    assert R.validate(data, SCHEMA) == []
    first, second = data["repeats"][0]["judgements"]
    assert first["verdict"] is None and first["judged"]["score"] == 68.4
    assert (
        first["judged"]["references"]["guideline"]["weight"] == 0.4
        and len(first["judged"]["references"]["guideline"]["gaps"]) == 2
    )
    assert second["status"] == "missed" and second["judged"]["score"] is None and second["judged"]["references"] == {}
    assert data["summary"]["references"]["reference"]["gaps"] == {"high": 1, "medium": 0, "low": 1}
    assert data["versions"]["references"] == REFERENCE_VERSIONS
    bad = json.loads(json.dumps(data))
    bad["versions"]["references"]["reference"]["commit"] = "v0.7.0"
    bad["repeats"][0]["judgements"][0]["judged"]["references"]["guideline"]["gaps"][0]["severity"] = "critical"
    assert len(R.validate(bad, SCHEMA)) == 2


def test_the_agentic_report_shows_each_reference_the_weighing_the_gaps_and_the_transcripts():
    run = agentic_run(
        [
            R.RepeatResult(
                0,
                {"code": 0},
                ["artifacts/0/output.zip"],
                [
                    judged("anthropic", {"guideline": 72, "reference": 66}, gaps=("low", "high")),
                    judged("openai", None, "missed", "spend: $3"),
                ],
            )
        ]
    )
    run.versions = dict(VERSIONS, references=REFERENCE_VERSIONS)
    text = R.report_text(run)
    assert (
        "| Repeat | Provider | Model | Effort | `guideline` | `reference` | Weighted | Tool calls | Latency (s) | Status |"
        in text
    )
    assert "| 0 | anthropic | `m` | medium | 72 | 66 | 68.4 | 9 | 30.0 | ok |" in text
    assert "| 0 | openai | `m` | medium | - | - | - | 9 | 30.0 | missed |" in text
    assert "Overall mean, of the weighted scores: 68.4." in text
    assert "| `guideline` | 0.4 | 72.0 | 72.0 | 72 | 72 | 1 | 1 / 0 / 1 |" in text
    assert "The harness weighs each judgement's scores: 0.4 * `guideline` + 0.6 * `reference`." in text
    gaps = text[text.index("## Gaps") : text.index("## Strengths")]
    assert "### `guideline` (weight 0.4)" in gaps and "### `reference` (weight 0.6)" in gaps
    assert gaps.index("**high**") < gaps.index("**low**")  # most severe first
    assert "- **high** (anthropic, repeat 0): a high gap. In the output: `src/app.py`. In the reference: `Async`." in gaps
    assert "## Findings" not in text
    assert "- (anthropic, `reference`) a strength against reference" in text
    assert "- **anthropic**, repeat 0: anthropic weighed it" in text
    assert "- transcript, repeat 0, anthropic: `judgements/0-anthropic.jsonl`" in text
    assert "- transcript, repeat 0, openai: `judgements/0-openai.jsonl`" in text
    assert "- Reference `guideline`: `architecture.md`, `lenses` of the checkout, sha256 `" + "b" * 64 + "`." in text
    assert (
        "- Reference `reference`: https://github.com/acme/acme-system at tag `v0.7.0`, commit `"
        + "a" * 40
        + "`, pins the guideline at `v0.37.0`."
    ) in text


def test_a_gap_in_the_report_reads_as_sentences_with_its_lens_and_its_fix():
    j = judged("xai", {"guideline": 50, "reference": 50}, gaps=())
    j.references["guideline"]["gaps"] = [
        {
            "severity": "medium",
            "what": "No outbox",
            "in_output": "",
            "in_reference": "Async, The Outbox",
            "lens": "ASY-03",
            "fix": "Add one",
        }
    ]
    text = R.report_text(agentic_run([R.RepeatResult(0, {"code": 0}, [], [j])]))
    expected = (
        "- **medium** ASY-03 (xai, repeat 0): No outbox. In the output: nothing there. In the reference: `Async, The Outbox`."
    )
    assert f"{expected} Fix: Add one." in text
    assert "### `reference` (weight 0.6)\n\nNo judge named a gap." in text


def test_a_repeat_is_marked_by_a_call_of_any_of_its_phases_a_carried_one_too():
    read = {"tool": "Bash", "id": "toolu_1", "key": "command", "value": "ls benchmark/runs"}
    phases: list[dict] = [{"name": "scaffold", "carried": True, "read_runs": [read]}, {"name": "review"}]
    marked = R.RepeatResult(index=0, exit_status={"code": 0}, phases=phases)
    assert marked.as_dict()["read_runs"] == [{"phase": "scaffold", **read}]
    assert "read_runs" not in R.RepeatResult(index=0, exit_status={"code": 0}, phases=[{"name": "review"}]).as_dict()
