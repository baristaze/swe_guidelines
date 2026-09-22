"""The result of a run: one JSON file in a fixed schema, one Markdown report.

`results.json` is the record a later run is compared against, so its
shape is fixed by `schema/result.schema.json` and validated before it is
written. `report.md` is the same data for a person: a table per repeat,
the summary, the findings with the most severe first, and the paths.

Paths in the report are written as code spans, never as links: a report
lives in a run folder that no one checks in, and a link out of it would
point at nothing.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .judge import Judgement, half_up

SCHEMA_VERSION = 1
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def now() -> str:
    """The time as the results file writes it: UTC, to the second."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class RepeatResult:
    """One run of the subject and every judgement of it."""

    index: int
    exit_status: dict[str, Any]
    artifact_paths: list[str] = field(default_factory=list)
    judgements: list[Judgement] = field(default_factory=list)
    # Which planted findings the artifact names, from `harness.evidence.named`;
    # None when the scenario plants none.
    expected: dict[str, Any] | None = None
    # The models the subject's envelope reports it ran on; empty when it reports none.
    subject_models: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out = {
            "index": self.index,
            "exit_status": self.exit_status,
            "artifact_paths": list(self.artifact_paths),
            "subject_models": list(self.subject_models),
            "judgements": [j.as_dict() for j in self.judgements],
        }

        if self.expected is not None:
            out["expected"] = dict(self.expected)
        return out


@dataclass
class RunResult:
    """Everything one run produced."""

    run_id: str
    scenario: str
    runtime: str
    started_at: str
    finished_at: str = ""
    guideline_sha: str = ""
    target_sha: str | None = None
    subject: dict[str, Any] = field(default_factory=dict)
    repeats: list[RepeatResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "scenario": self.scenario,
            "runtime": self.runtime,
            "started_at": self.started_at,
            "finished_at": self.finished_at or now(),
            "guideline_sha": self.guideline_sha,
            "target_sha": self.target_sha,
            "subject": self.subject,
            "repeats": [r.as_dict() for r in self.repeats],
            "summary": summarize(self.repeats),
            "notes": list(self.notes),
        }


def summarize(repeats: list[RepeatResult]) -> dict[str, Any]:
    """Scores per provider over every repeat, who did not answer, and who missed some.

    The overall mean is the mean of the providers' means, so each provider
    weighs once: a provider that answered more repeats does not outweigh one
    that answered fewer. A provider that answered no judgement is skipped; one
    that answered some and missed others is named under `missed`, with the
    count and the first reason, and is not a failure of the run by itself.
    """
    scores: dict[str, list[int]] = {}
    misses: dict[str, list[str]] = {}
    for repeat in repeats:
        for j in repeat.judgements:
            if j.status == "ok" and j.verdict is not None:
                scores.setdefault(j.provider, []).append(j.verdict.score)
            else:
                misses.setdefault(j.provider, []).append(j.error or j.status)
    per_provider = {
        provider: {
            "mean": half_up(statistics.fmean(values), 1),
            "min": min(values),
            "max": max(values),
            "n": len(values),
        }
        for provider, values in sorted(scores.items())
    }
    means = [statistics.fmean(values) for values in scores.values()]
    fallbacks: dict[tuple[str, str, str], dict[str, Any]] = {}
    for repeat in repeats:
        for j in repeat.judgements:
            if j.status == "ok" and j.fallback:
                entry = fallbacks.setdefault(
                    (j.provider, j.fallback["from"], j.model),
                    {
                        "provider": j.provider,
                        "from": j.fallback["from"],
                        "to": j.model,
                        "count": 0,
                        "reason": j.fallback["reason"],
                    },
                )
                entry["count"] += 1
    return {
        "per_provider": per_provider,
        "overall_mean": half_up(statistics.fmean(means), 1) if means else None,
        "fallbacks": [fallbacks[k] for k in sorted(fallbacks)],
        "skipped": [{"provider": p, "reason": r[0]} for p, r in sorted(misses.items()) if p not in scores],
        "missed": [{"provider": p, "count": len(r), "reason": r[0]} for p, r in sorted(misses.items()) if p in scores],
    }


def findings_by_severity(repeats: list[RepeatResult]) -> list[dict[str, Any]]:
    """Every finding, most severe first, each carrying who said it."""
    out: list[dict[str, Any]] = []
    for repeat in repeats:
        for j in repeat.judgements:
            if j.verdict is None:
                continue
            for f in j.verdict.findings:
                out.append({"severity": f.severity, "note": f.note, "provider": j.provider, "repeat": repeat.index})
    out.sort(key=lambda f: (SEVERITY_ORDER.get(str(f["severity"]).lower(), 3), f["provider"], f["repeat"]))
    return out


UNVALIDATED = "jsonschema is not installed; results.json was written unvalidated"


def validate(data: dict[str, Any], schema_path: str | Path) -> list[str]:
    """Every way the data misses the schema. Needs `jsonschema`; without it the one
    entry is `UNVALIDATED`, a note and not a mismatch: nothing is claimed either way."""
    try:
        import jsonschema
    except ModuleNotFoundError:
        return [UNVALIDATED]
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in sorted(validator.iter_errors(data), key=str)]


def write_results(run: RunResult, path: str | Path) -> dict[str, Any]:
    """Write `results.json` and return the data that was written."""
    data = run.as_dict()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def report_text(run: RunResult) -> str:
    """The Markdown report as one string."""
    data = run.as_dict()
    summary = data["summary"]
    lines: list[str] = [
        f"# Benchmark run {run.run_id}",
        "",
        f"Scenario `{run.scenario}`, runtime `{run.runtime}`, "
        f"{len(run.repeats)} repeat(s), guideline `{run.guideline_sha or 'unknown'}`.",
        "",
        f"Started {run.started_at}, finished {data['finished_at']}.",
        "",
        "## Scores",
        "",
        _row(["Repeat", "Provider", "Model", "Effort", "Score", "Verdict", "Latency (s)", "Status"]),
        _row(["---"] * 8),
    ]
    for repeat in run.repeats:
        for j in repeat.judgements:
            verdict = j.verdict.verdict if j.verdict else "-"
            score = str(j.verdict.score) if j.verdict else "-"
            lines.append(
                _row(
                    [
                        str(repeat.index),
                        j.provider,
                        f"`{j.model}`" if j.model else "-",
                        j.effort,
                        score,
                        verdict,
                        f"{j.latency_s:.1f}",
                        j.status,
                    ]
                )
            )
    lines += ["", "## Summary", "", _row(["Provider", "Mean", "Min", "Max", "n"]), _row(["---"] * 5)]
    for provider, stats in summary["per_provider"].items():
        lines.append(_row([provider, str(stats["mean"]), str(stats["min"]), str(stats["max"]), str(stats["n"])]))
    overall = summary["overall_mean"]
    lines += ["", f"Overall mean: {overall if overall is not None else 'no score'}.", ""]
    if summary["skipped"]:
        lines += ["### Not answered", ""]
        lines += [f"- `{s['provider']}`: {s['reason']}" for s in summary["skipped"]]
        lines += [""]
    if summary.get("fallbacks"):
        lines += ["### Fallbacks", "", "A model answered in place of the one the matrix put first.", ""]
        lines += [
            f"- `{f['provider']}`: `{f['to']}` answered in place of `{f['from']}` in {f['count']} judgement(s); "
            f"first reason: {f['reason']}"
            for f in summary["fallbacks"]
        ]
        lines += [""]
    if summary.get("missed"):
        lines += ["### Answered in part", ""]
        lines += [f"- `{m['provider']}`: {m['count']} judgement(s) missed, first: {m['reason']}" for m in summary["missed"]]
        lines += [""]
    checked = [(r.index, r.expected) for r in run.repeats if r.expected is not None]
    if checked:
        lines += [
            "## Expected findings",
            "",
            "Which planted findings the artifact names by lens id and file. A",
            "mechanical cross-check beside the scores, made by no model.",
            "",
        ]
        for index, e in checked:
            missed = ", ".join(e["missed"]) or "none"
            lines.append(f"- repeat {index}: named {len(e['named'])} of {e['expected']}; missed: {missed}")
        lines.append("")
    findings = findings_by_severity(run.repeats)
    lines += ["## Findings", ""]
    if findings:
        lines += [f"- **{f['severity']}** ({f['provider']}, repeat {f['repeat']}): {f['note']}" for f in findings]
    else:
        lines.append("No judge raised a finding.")
    lines += ["", "## Strengths", ""]
    strengths = [
        f"- ({j.provider}) {s}" for repeat in run.repeats for j in repeat.judgements if j.verdict for s in j.verdict.strengths
    ]
    lines += strengths or ["No judge named a strength."]
    lines += ["", "## Rationales", ""]
    for repeat in run.repeats:
        for j in repeat.judgements:
            if j.verdict and j.verdict.rationale:
                lines.append(f"- **{j.provider}**, repeat {repeat.index}: {j.verdict.rationale}")
    lines += ["", "## Paths", "", f"- run folder: `{run.run_id}`"]
    for repeat in run.repeats:
        for path in repeat.artifact_paths:
            lines.append(f"- artifact, repeat {repeat.index}: `{path}`")
    lines += ["- streams: `streams/cli.jsonl`", "- results: `results.json`"]
    if run.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in run.notes]
    return "\n".join(lines).rstrip() + "\n"


def write_report(run: RunResult, path: str | Path) -> str:
    """Write `report.md` and return what was written."""
    text = report_text(run)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
