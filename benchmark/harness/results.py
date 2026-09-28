"""The result of a run: one JSON file in a fixed schema, one Markdown report.

`results.json` is the record a later run is compared against, so its
shape is fixed by `schema/result.schema.json` and validated before it is
written. `report.md` is the same data for a person: a table per repeat,
the summary, what the run spent, the findings with the most severe first,
and the paths.

A judgement is one-shot, with a verdict, or agentic, with a score per
reference, the gaps behind each, and the weighted score the harness
computed. Every mean, spread, and count reads a judgement's score the
same way: the verdict's score, or the weighted score. An agentic run's
summary also carries each reference's scores and its gaps by severity,
and its report shows the scores per reference, the gaps, and where each
judge's transcript is.

Paths in the report are written as code spans, never as links: a run
folder is served, uploaded, and checked in, and a link out of it would
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
from .references import SEVERITIES, Judged

# A judgement of either kind: one-shot, or agentic against references.
AnyJudgement = Judgement | Judged

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
    judgements: list[AnyJudgement] = field(default_factory=list)
    # Which planted findings the artifact names, from `harness.evidence.named`;
    # None when the scenario plants none.
    expected: dict[str, Any] | None = None
    # The models the subject's result reports it ran on; empty when it reports none.
    subject_models: list[str] = field(default_factory=list)
    # What the subject spent: its tokens, and their cost in US dollars, None when unknown.
    subject_usage: dict[str, int] = field(default_factory=dict)
    subject_cost_usd: float | None = None
    # A skill subject's sessions, each with how it ended and what it spent; None for another kind.
    phases: list[dict[str, Any]] | None = None
    # The output's zip: its path, its manifest's, its SHA-256, its size, its files, and the commit.
    archive: dict[str, Any] | None = None
    # The scenario's gates, run on the final tree: each command and whether it passed.
    gates: list[dict[str, Any]] | None = None
    # The phases the run's spend cap kept from running; such a repeat is not judged and scores nothing.
    cut_short: list[str] | None = None
    # The phase that ended the run early, why (`no_tree` or `incomplete`), and the phases after it, which
    # did not run; such a repeat failed, and is not judged.
    ended_early: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {
            "index": self.index,
            "exit_status": self.exit_status,
            "artifact_paths": list(self.artifact_paths),
            "subject_models": list(self.subject_models),
            "subject_usage": dict(self.subject_usage),
            "subject_cost_usd": self.subject_cost_usd,
            "judgements": [j.as_dict() for j in self.judgements],
        }
        if self.expected is not None:
            out["expected"] = dict(self.expected)
        for key in ("phases", "archive", "gates", "cut_short", "ended_early"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
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
    # Every version that decides a score, from `harness.versions`: the
    # checkout, Claude Code, the image, the target, the expected findings.
    versions: dict[str, Any] = field(default_factory=dict)
    subject: dict[str, Any] = field(default_factory=dict)
    repeats: list[RepeatResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Each reference's weight, by name, when the judges are agentic; empty when they are one-shot.
    weights: dict[str, float] = field(default_factory=dict)
    # A rehearsal's outcome and where its money went; None for a run that is not one.
    rehearsal: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "scenario": self.scenario,
            "runtime": self.runtime,
            "started_at": self.started_at,
            "finished_at": self.finished_at or now(),
            "guideline_sha": self.guideline_sha,
            "target_sha": self.target_sha,
            "versions": self.versions,
            "subject": self.subject,
            "repeats": [r.as_dict() for r in self.repeats],
            "summary": summarize(self.repeats, self.subject, self.weights),
            "spend": spend(self.repeats),
            "notes": list(self.notes),
        }
        if self.rehearsal is not None:
            out["rehearsal"] = dict(self.rehearsal)
        return out


def failed(exit_status: dict[str, Any]) -> bool:
    """Whether a subject's session failed: a nonzero exit, a timeout, or `is_error` in its result."""
    return exit_status.get("code", 0) != 0 or bool(exit_status.get("timed_out")) or bool(exit_status.get("is_error"))


def failed_repeat(repeat: RepeatResult) -> bool:
    """Whether a repeat failed: its subject failed, or a phase ended the run early."""
    return failed(repeat.exit_status) or repeat.ended_early is not None


def is_claude(subject: dict[str, Any]) -> bool:
    """Whether the subject is a Claude model: a skill's `claude -p`, an Anthropic `qa`, or a pinned Claude model."""
    kind = subject.get("kind")
    if kind == "skill":
        return True
    if kind == "qa" and (subject.get("provider") or "anthropic") == "anthropic":
        return True
    return str(subject.get("model") or "").startswith("claude")


def score_of(j: AnyJudgement) -> float | None:
    """What a judgement scored: its verdict's score, or its weighted score; None when it did not answer."""
    if j.status != "ok":
        return None
    if isinstance(j, Judged):
        return j.score
    return float(j.verdict.score) if j.verdict is not None else None


def number(value: float) -> int | float:
    """A score as the record keeps it: a whole number as an int, so a verdict's 72 stays 72."""
    return int(value) if float(value).is_integer() else value


def stdev(values: list[float]) -> float | None:
    """The sample standard deviation, to one place; None with fewer than two values."""
    return half_up(statistics.stdev(values), 1) if len(values) > 1 else None


def summarize(
    repeats: list[RepeatResult], subject: dict[str, Any] | None = None, weights: dict[str, float] | None = None
) -> dict[str, Any]:
    """Scores per provider over every repeat, their spread, who did not answer, and who missed some.

    A judgement's score is its verdict's, or, for an agentic one, the
    weighted score. With `weights`, the summary also holds `references`
    (see `reference_summary`).

    A repeat whose subject failed is a failure, never a gap: it scores 0
    for every provider that scored the run, so a subject that fails one
    time in three cannot keep the mean of the two times it did not. When
    every repeat failed, the run scores 0.

    The overall mean is the mean of the providers' means, so each provider
    weighs once: a provider that answered more repeats does not outweigh one
    that answered fewer. A provider that answered no judgement is skipped; one
    that answered some and missed others is named under `missed`, with the
    count and the first reason, and is not a failure of the run by itself.

    The spread is over the repeats: each repeat's mean over its providers,
    and their minimum, maximum, and standard deviation. A Claude subject
    judged by a panel that scores with Claude is named under `self_judged`,
    because a model may favor its own kind.
    """
    scores: dict[str, list[float]] = {}
    misses: dict[str, list[str]] = {}
    for repeat in repeats:
        for j in repeat.judgements:
            score = score_of(j)
            if score is not None:
                scores.setdefault(j.provider, []).append(score)
            else:
                misses.setdefault(j.provider, []).append(j.error or j.status)
    failures = [r.index for r in repeats if failed_repeat(r)]
    for values in scores.values():
        values.extend([0.0] * len(failures))
    per_provider = {
        provider: {
            "mean": half_up(statistics.fmean(values), 1),
            "min": number(min(values)),
            "max": number(max(values)),
            "n": len(values),
            "stdev": stdev(values),
        }
        for provider, values in sorted(scores.items())
    }
    means = [statistics.fmean(values) for values in scores.values()]
    if means:
        overall: float | None = half_up(statistics.fmean(means), 1)
    else:
        overall = 0.0 if failures else None
    repeat_means: list[float] = []
    for repeat in repeats:
        if failed_repeat(repeat):
            repeat_means.append(0.0)
            continue
        answered = [score for j in repeat.judgements if (score := score_of(j)) is not None]
        if answered:
            repeat_means.append(half_up(statistics.fmean(answered), 1))
    spread = (
        {"repeat_means": repeat_means, "min": min(repeat_means), "max": max(repeat_means), "stdev": stdev(repeat_means)}
        if repeat_means
        else None
    )
    self_judged = None
    if subject and is_claude(subject) and "anthropic" in scores:
        self_judged = (
            "a Claude subject is judged by a panel that includes Claude (anthropic); "
            "read its score beside the other providers' before trusting the mean"
        )
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
    out = {
        "per_provider": per_provider,
        "overall_mean": overall,
        "failed_repeats": failures,
        "cut_short": [r.index for r in repeats if r.cut_short],
        "spread": spread,
        "self_judged": self_judged,
        "fallbacks": [fallbacks[k] for k in sorted(fallbacks)],
        "skipped": [{"provider": p, "reason": r[0]} for p, r in sorted(misses.items()) if p not in scores],
        "missed": [{"provider": p, "count": len(r), "reason": r[0]} for p, r in sorted(misses.items()) if p in scores],
    }
    if weights:
        out["references"] = reference_summary(repeats, weights)
    return out


def reference_summary(repeats: list[RepeatResult], weights: dict[str, float]) -> dict[str, Any]:
    """Each reference's scores over every agentic judgement, in the scenario's order.

    Per reference: its weight; each provider's mean; the mean of those
    means, so each provider weighs once, as in the overall mean; the
    minimum, the maximum, and how many scores; and its gaps counted by
    severity. A failed repeat scores 0 against every reference, for every
    provider that scored the run, as it does in the weighted score.
    """
    failures = sum(1 for r in repeats if failed_repeat(r))
    out: dict[str, Any] = {}
    for name, weight in weights.items():
        scores: dict[str, list[int]] = {}
        gaps = {severity: 0 for severity in SEVERITIES}
        for repeat in repeats:
            for j in repeat.judgements:
                entry = j.references.get(name) if isinstance(j, Judged) and j.score is not None else None
                if entry is None:
                    continue
                scores.setdefault(j.provider, []).append(entry["score"])
                for gap in entry["gaps"]:
                    gaps[gap["severity"]] = gaps.get(gap["severity"], 0) + 1
        for values in scores.values():
            values.extend([0] * failures)
        means = {p: half_up(statistics.fmean(v), 1) for p, v in sorted(scores.items())}
        every = [v for values in scores.values() for v in values]
        out[name] = {
            "weight": weight,
            "mean": half_up(statistics.fmean([statistics.fmean(v) for v in scores.values()]), 1) if scores else None,
            "per_provider": means,
            "min": min(every) if every else None,
            "max": max(every) if every else None,
            "n": len(every),
            "gaps": gaps,
        }
    return out


TOKENS = ("input_tokens", "output_tokens", "reasoning_tokens")


def _add(total: dict[str, Any], usage: dict[str, int], cost: float | None, what: str) -> None:
    """Add one call's tokens and cost to a total; a call with no cost is named under `unpriced`."""
    for name in TOKENS:
        total[name] += int(usage.get(name, 0))
    if cost is None:
        if what not in total["unpriced"]:
            total["unpriced"].append(what)
    else:
        total["cost_usd"] += cost


def _total() -> dict[str, Any]:
    return {**{name: 0 for name in TOKENS}, "cost_usd": 0.0, "unpriced": []}


def spend(repeats: list[RepeatResult]) -> dict[str, Any]:
    """What the run spent: tokens and US dollars per judge, for the subject, and in all.

    Only a call that used tokens counts: a skipped judge and a subject that
    reported no usage spent nothing the run can see. A call whose model has
    no price adds its tokens and is named under `unpriced`, so `cost_usd` is
    what the priced calls cost, and a total with anything unpriced is a
    lower bound, never a guess.
    """
    judges: dict[str, dict[str, Any]] = {}
    subject = _total()
    for repeat in repeats:
        if repeat.subject_usage or repeat.subject_cost_usd is not None:
            _add(subject, repeat.subject_usage, repeat.subject_cost_usd, ", ".join(repeat.subject_models) or "subject")
        for j in repeat.judgements:
            if j.usage:
                _add(judges.setdefault(j.provider, _total()), j.usage, j.cost_usd, j.model)
    everything = [*judges.values(), subject]
    for total in everything:
        total["cost_usd"] = round(total["cost_usd"], 4)
    return {
        "judges": dict(sorted(judges.items())),
        "subject": subject,
        "total_usd": round(sum(t["cost_usd"] for t in everything), 4),
        "unpriced": sorted({m for t in everything for m in t["unpriced"]}),
    }


def findings_by_severity(repeats: list[RepeatResult]) -> list[dict[str, Any]]:
    """Every one-shot finding, most severe first, each carrying who said it."""
    out: list[dict[str, Any]] = []
    for repeat in repeats:
        for j in repeat.judgements:
            if isinstance(j, Judged) or j.verdict is None:
                continue
            for f in j.verdict.findings:
                out.append({"severity": f.severity, "note": f.note, "provider": j.provider, "repeat": repeat.index})
    out.sort(key=lambda f: (SEVERITY_ORDER.get(str(f["severity"]).lower(), 3), f["provider"], f["repeat"]))
    return out


def gaps_by_severity(repeats: list[RepeatResult], name: str) -> list[dict[str, Any]]:
    """Every gap agentic judges named against one reference, most severe first, each carrying who named it."""
    out: list[dict[str, Any]] = []
    for repeat in repeats:
        for j in repeat.judgements:
            entry = j.references.get(name) if isinstance(j, Judged) else None
            for gap in entry["gaps"] if entry else []:
                out.append({**gap, "provider": j.provider, "repeat": repeat.index})
    out.sort(key=lambda g: (SEVERITY_ORDER.get(str(g["severity"]).lower(), 3), g["provider"], g["repeat"]))
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


def _usd(value: float) -> str:
    return f"${value:,.4f}"


def spend_lines(spent: dict[str, Any]) -> list[str]:
    """The report's section on what the run spent."""
    lines = [
        "## Spend",
        "",
        "Tokens as each provider billed them: the output includes the reasoning. Dollars at the list",
        "prices in `models.yaml`, every input token priced as uncached input.",
        "",
        _row(["Who", "Input tokens", "Output tokens", "Of which reasoning", "Cost (USD)"]),
        _row(["---"] * 5),
    ]
    rows = [(f"judge `{p}`", t) for p, t in spent["judges"].items()] + [("subject", spent["subject"])]
    for who, t in rows:
        cost = _usd(t["cost_usd"]) + (" + unpriced" if t["unpriced"] else "")
        lines.append(_row([who, f"{t['input_tokens']:,}", f"{t['output_tokens']:,}", f"{t['reasoning_tokens']:,}", cost]))
    total = _usd(spent["total_usd"])
    if spent["unpriced"]:
        unpriced = ", ".join(f"`{m}`" for m in spent["unpriced"])
        lines += ["", f"Total: at least {total}. No price for {unpriced}, so its tokens are counted and its cost is not.", ""]
    else:
        lines += ["", f"Total: {total}.", ""]
    return lines


def _cost(phase: dict[str, Any]) -> str:
    if phase.get("cost_usd") is not None:
        return _usd(phase["cost_usd"])
    return f"{_usd(phase['estimated_usd'])} (estimated)" if phase.get("estimated_usd") else "-"


def phase_lines(repeats: list[RepeatResult]) -> list[str]:
    """The report's sections on a skill subject's sessions, its output, and its gates."""
    lines: list[str] = []
    ran = [r for r in repeats if r.phases]
    if ran:
        lines += [
            "## Phases",
            "",
            "Each session of the subject: how it ended, the bound that ended it, its turns, its wall time, and what it spent.",
            "",
            _row(["Repeat", "Phase", "Session", "Status", "Cap", "Turns", "Wall (s)", "Cost (USD)", "Checkpoint"]),
            _row(["---"] * 9),
        ]
        for repeat in ran:
            for phase in repeat.phases or []:
                turns = phase.get("turns")
                wall = phase.get("wall_s")
                commit = phase.get("checkpoint")
                lines.append(
                    _row(
                        [
                            str(repeat.index),
                            phase["name"],
                            phase["session"],
                            phase["status"],
                            phase.get("capped") or "-",
                            str(turns) if turns is not None else "-",
                            f"{wall:.1f}" if wall is not None else "-",
                            _cost(phase),
                            f"`{commit[:12]}`" if commit else "-",
                        ]
                    )
                )
        lines.append("")
    kept = [r for r in repeats if r.archive]
    if kept:
        lines += ["## Output", "", "The output's last commit, archived whole; `results.json` has each zip's SHA-256.", ""]
        for repeat in kept:
            a = repeat.archive or {}
            lines.append(
                f"- repeat {repeat.index}: `{a['path']}`, {a['files']} file(s), {a['bytes']:,} bytes; manifest `{a['manifest']}`"
            )
        lines.append("")
    gated = [r for r in repeats if r.gates]
    if gated:
        lines += [
            "## Gates",
            "",
            "The scenario's gates, run on the final tree. They are recorded beside the scores and cap none.",
            "",
        ]
        for repeat in gated:
            for gate in repeat.gates or []:
                verdict = (
                    "passed" if gate["passed"] else "timed out" if gate.get("timed_out") else f"failed, exit {gate['exit_code']}"
                )
                lines.append(f"- repeat {repeat.index}: `{gate['command']}` {verdict}")
        lines.append("")
    return lines


def version_lines(versions: dict[str, Any]) -> list[str]:
    """The report's section on what ran: each version as a reference, and what is not known."""
    lines = ["## Versions", ""]
    checkout = versions.get("checkout")
    if checkout:
        where = f"`{checkout['commit']}`" if checkout.get("commit") else "no commit"
        plugin = f", plugin `{checkout['plugin_version']}`" if checkout.get("plugin_version") else ""
        if checkout.get("dirty") is None:
            state = "not a git checkout, so no one can say what it held"
        elif checkout["dirty"]:
            paths = ", ".join(f"`{p}`" for p in checkout["dirty_paths"])
            state = f"with changes no commit holds, sha256 `{checkout['dirty_sha256']}`: {paths}"
        else:
            state = "clean"
        lines.append(f"- Checkout: {where}{plugin}, {state}.")
    claude = versions.get("claude_code")
    lines.append(f"- Claude Code: `{claude}`." if claude else "- Claude Code: not run, or not known.")
    image = versions.get("image")
    if image:
        lines.append(f"- Image: `{image['name']}`, id `{image['id'] or 'unknown'}`.")
    for key, label in (("target", "Target"), ("expected", "Expected findings")):
        found = versions.get(key)
        if found:
            lines.append(f"- {label}: `{found['path']}`, sha256 `{found['sha256']}`.")
    for name, ref in (versions.get("references") or {}).items():
        if ref.get("source") == "repository":
            pins = f", pins the guideline at `{ref['pins']}`" if ref.get("pins") else ", names no guideline release it pins"
            lines.append(f"- Reference `{name}`: {ref['url']} at tag `{ref['tag']}`, commit `{ref['commit']}`{pins}.")
        else:
            paths = ", ".join(f"`{p}`" for p in ref["paths"])
            lines.append(f"- Reference `{name}`: {paths} of the checkout, sha256 `{ref['sha256']}`.")
    return [*lines, ""]


def agentic_score_lines(run: RunResult) -> list[str]:
    """The report's table of agentic judgements: each reference's score, the weighted score, and the tool calls."""
    names = list(run.weights)
    header = [
        "Repeat",
        "Provider",
        "Model",
        "Effort",
        *(f"`{n}`" for n in names),
        "Weighted",
        "Tool calls",
        "Latency (s)",
        "Status",
    ]
    lines = [_row(header), _row(["---"] * len(header))]
    for repeat in run.repeats:
        for j in repeat.judgements:
            if not isinstance(j, Judged):
                continue
            scores = [str(j.references[n]["score"]) if n in j.references else "-" for n in names]
            weighted = str(j.score) if j.score is not None else "-"
            model = f"`{j.model}`" if j.model else "-"
            cells = [str(repeat.index), j.provider, model, j.effort, *scores, weighted, str(j.tool_calls), f"{j.latency_s:.1f}"]
            lines.append(_row([*cells, j.status]))
    return lines


def reference_lines(run: RunResult, summary: dict[str, Any]) -> list[str]:
    """The report's summary of each reference: its weight, its mean over the providers, and its gaps by severity."""
    refs = summary.get("references") or {}
    providers = sorted({p for entry in refs.values() for p in entry["per_provider"]})
    header = ["Reference", "Weight", "Mean", *providers, "Min", "Max", "n", "Gaps high / medium / low"]
    lines = ["### References", "", _row(header), _row(["---"] * len(header))]
    for name, entry in refs.items():
        per = [str(entry["per_provider"].get(p, "-")) for p in providers]
        gaps = " / ".join(str(entry["gaps"].get(s, 0)) for s in SEVERITIES)
        cells = [f"`{name}`", f"{entry['weight']:g}", "-" if entry["mean"] is None else str(entry["mean"]), *per]
        cells += ["-" if entry["min"] is None else str(entry["min"]), "-" if entry["max"] is None else str(entry["max"])]
        lines.append(_row([*cells, str(entry["n"]), gaps]))
    formula = " + ".join(f"{w:g} * `{n}`" for n, w in run.weights.items())
    return [*lines, "", f"The harness weighs each judgement's scores: {formula}.", ""]


def sentence(text: str) -> str:
    """A judge's note as a sentence of the report: its own ending, or a period."""
    text = text.strip()
    return text if not text or text.endswith((".", "!", "?")) else f"{text}."


def gap_lines(run: RunResult) -> list[str]:
    """The report's gaps, per reference, most severe first, each with where it is in each tree."""
    lines = ["## Gaps", ""]
    for name, weight in run.weights.items():
        lines += [f"### `{name}` (weight {weight:g})", ""]
        gaps = gaps_by_severity(run.repeats, name)
        for g in gaps:
            lens = f" {g['lens']}" if g.get("lens") else ""
            output = f"`{g['in_output']}`" if g["in_output"] else "nothing there"
            reference = f"`{g['in_reference']}`" if g["in_reference"] else "not named"
            where = f" In the output: {output}. In the reference: {reference}."
            fix = f" Fix: {sentence(g['fix'])}" if g.get("fix") else ""
            lines.append(
                f"- **{g['severity']}**{lens} ({g['provider']}, repeat {g['repeat']}): {sentence(g['what'])}{where}{fix}"
            )
        if not gaps:
            lines.append("No judge named a gap.")
        lines.append("")
    return lines


def rehearsal_lines(record: dict[str, Any]) -> list[str]:
    """The report's section on a rehearsal: what it is, how it ended, and where its money went."""
    lines = [
        "## Rehearsal",
        "",
        "The scenario ran as it will really run, with every bound cut small. The scores mean nothing,",
        "and a rehearsal is never checked in.",
        "",
    ]
    cap = f" of its {_usd(record['max_spend_usd'])} cap" if record.get("max_spend_usd") is not None else ""
    lines += [f"It ended `{record['status']}`, having spent {_usd(record['spent_usd'])}{cap}:", ""]
    lines += [f"- {s['what']}: {_usd(s['usd'])}" for s in record["spent"]] or ["- nothing"]
    if record.get("missing"):
        lines += ["", "It did not prove the pipeline to its end:", ""]
        lines += [f"- {step}" for step in record["missing"]]
    return [*lines, ""]


def report_text(run: RunResult) -> str:
    """The Markdown report as one string."""
    data = run.as_dict()
    summary = data["summary"]
    agentic = bool(run.weights)
    lines: list[str] = [
        f"# Benchmark run {run.run_id}",
        "",
        f"Scenario `{run.scenario}`, runtime `{run.runtime}`, "
        f"{len(run.repeats)} repeat(s), guideline `{run.guideline_sha or 'unknown'}`.",
        "",
        f"Started {run.started_at}, finished {data['finished_at']}.",
        "",
    ]
    groups = run.subject.get("groups")
    if groups is not None:
        taken = ", ".join(f"`{g}`" for g in groups)
        lines += [f"Optional groups taken: {taken}." if groups else "Optional groups taken: none.", ""]
    if run.rehearsal:
        lines += rehearsal_lines(run.rehearsal)
    lines += ["## Scores", ""]
    if agentic:
        lines += agentic_score_lines(run)
    else:
        lines += [_row(["Repeat", "Provider", "Model", "Effort", "Score", "Verdict", "Latency (s)", "Status"]), _row(["---"] * 8)]
    for repeat in run.repeats:
        for j in repeat.judgements:
            if isinstance(j, Judged):
                continue
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
    lines += ["", "## Summary", "", _row(["Provider", "Mean", "Min", "Max", "Stdev", "n"]), _row(["---"] * 6)]
    for provider, stats in summary["per_provider"].items():
        deviation = "-" if stats["stdev"] is None else str(stats["stdev"])
        lines.append(_row([provider, str(stats["mean"]), str(stats["min"]), str(stats["max"]), deviation, str(stats["n"])]))
    overall = summary["overall_mean"]
    weighed = ", of the weighted scores" if agentic else ""
    lines += ["", f"Overall mean{weighed}: {overall if overall is not None else 'no score'}.", ""]
    if agentic:
        lines += reference_lines(run, summary)
    spread = summary["spread"]
    if spread:
        deviation = "-" if spread["stdev"] is None else str(spread["stdev"])
        means = ", ".join(str(m) for m in spread["repeat_means"])
        lines += [f"Spread over the repeats: means {means}; min {spread['min']}, max {spread['max']}, stdev {deviation}.", ""]
    if summary["failed_repeats"]:
        failed_list = ", ".join(str(i) for i in summary["failed_repeats"])
        lines += [f"Failed repeat(s) {failed_list}: the subject failed, and each scores 0 in the means.", ""]
    for repeat in run.repeats:
        if repeat.ended_early:
            ended = repeat.ended_early
            left = ", ".join(ended["not_run"])
            after = f" {left} did not run." if left else ""
            why = (
                "left no file in the output folder"
                if ended["reason"] == "no_tree"
                else "ended with Agent calls that had no result"
            )
            lines += [
                f"Repeat {repeat.index} ended after phase {ended['phase']}, which {why}.{after} "
                "It is not judged, and it scores 0 as a failed repeat.",
                "",
            ]
        if repeat.cut_short:
            left = ", ".join(repeat.cut_short)
            lines += [
                f"Repeat {repeat.index} was cut short by the run's spend cap: {left} did not run. It is not judged "
                "and is in no mean, neither scored nor a failure.",
                "",
            ]
    if summary["self_judged"]:
        lines += [f"Note: {summary['self_judged']}.", ""]

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
    lines += spend_lines(data["spend"])
    lines += phase_lines(run.repeats)
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
    if agentic:
        lines += gap_lines(run)
    else:
        findings = findings_by_severity(run.repeats)
        lines += ["## Findings", ""]
        if findings:
            lines += [f"- **{f['severity']}** ({f['provider']}, repeat {f['repeat']}): {f['note']}" for f in findings]
        else:
            lines.append("No judge raised a finding.")
        lines.append("")
    lines += ["## Strengths", ""]
    strengths: list[str] = []
    for repeat in run.repeats:
        for j in repeat.judgements:
            if isinstance(j, Judged):
                strengths += [f"- ({j.provider}, `{n}`) {s}" for n, entry in j.references.items() for s in entry["strengths"]]
            elif j.verdict:
                strengths += [f"- ({j.provider}) {s}" for s in j.verdict.strengths]
    lines += strengths or ["No judge named a strength."]
    lines += ["", "## Rationales", ""]
    for repeat in run.repeats:
        for j in repeat.judgements:
            rationale = j.rationale if isinstance(j, Judged) else j.verdict.rationale if j.verdict else ""
            if rationale:
                lines.append(f"- **{j.provider}**, repeat {repeat.index}: {rationale}")
    lines += [""]
    if run.versions:
        lines += version_lines(run.versions)
    lines += ["## Paths", "", f"- run folder: `{run.run_id}`"]
    for repeat in run.repeats:
        for path in repeat.artifact_paths:
            lines.append(f"- artifact, repeat {repeat.index}: `{path}`")
        for j in repeat.judgements:
            if isinstance(j, Judged):
                lines.append(f"- transcript, repeat {repeat.index}, {j.provider}: `{j.transcript}`")
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
