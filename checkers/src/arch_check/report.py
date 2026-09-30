"""The two report formats: text for a person, JSON for a review skill or CI."""

from __future__ import annotations

import json as jsonlib
from collections.abc import Sequence
from typing import Any

from arch_check.model import Rule, ToJudge
from arch_check.runner import Result


def rule_dict(r: Rule, to_judge: Sequence[ToJudge] = ()) -> dict[str, Any]:
    """A rule as JSON; `lens` is the lens id it decides, which is its id.

    `to_judge` is each place the rule named and left to the review: no
    finding, and empty for a rule that names none.
    """
    return {
        "id": r.id,
        "lens": r.id,
        "group": r.group,
        "coverage": r.coverage,
        "severity": r.severity,
        "summary": r.summary,
        "origin": r.origin,
        "to_judge": [t.as_dict() for t in to_judge],
    }


def text(result: Result) -> str:
    """One `path:line:col: RULE message` line per finding, then a one-line summary.

    A place a rule leaves to the review has a line too, marked `to judge:`, and the summary counts it apart.
    """
    lines = [f"{f.path}:{f.line}:{f.col}: {f.rule} {f.message}" for f in result.findings]
    left = [f"{t.path}:{t.line}:{t.col}: {id} to judge: {t.message}" for (id, _), named in result.to_judge.items() for t in named]
    rules = f"{len(result.rules_run)} rule(s) over {result.files} Python file(s)"
    accepted = f", {len(result.applied)} accepted by an exception" if result.applied else ""
    judged = f", {len(left)} left to a review" if left else ""
    if result.findings:
        summary = f"{len(result.findings)} finding(s) from {rules}{accepted}{judged}"
    else:
        summary = f"arch-check ok: {rules}{accepted}{judged}"
    return "\n".join([*lines, *left, f"\n{summary}" if lines or left else summary])


def json(result: Result) -> str:
    """The whole result as one JSON document, keys in a fixed order."""
    return jsonlib.dumps(
        {
            "version": result.version,
            "root": result.root,
            "rules_run": [rule_dict(r, result.to_judge.get((r.id, r.origin), ())) for r in result.rules_run],
            "findings": [f.as_dict() for f in result.findings],
            "exceptions_applied": [a.as_dict() for a in result.applied],
        },
        indent=2,
    )


def listing(rules: list[Rule]) -> str:
    """`--list`: one line per rule, id, group, coverage, severity, origin, summary."""
    return "\n".join(f"{r.id:<7} {r.group:<10} {r.coverage:<8} {r.severity:<7} {r.origin:<10} {r.summary}" for r in rules)
