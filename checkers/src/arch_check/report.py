"""The two report formats: text for a person, JSON for a review skill or CI."""

from __future__ import annotations

import json as jsonlib
from typing import Any

from arch_check.model import Rule
from arch_check.runner import Result


def rule_dict(r: Rule) -> dict[str, Any]:
    """A rule as JSON; `lens` is the lens id it decides, which is its id."""
    return {
        "id": r.id,
        "lens": r.id,
        "group": r.group,
        "coverage": r.coverage,
        "severity": r.severity,
        "summary": r.summary,
        "origin": r.origin,
    }


def text(result: Result) -> str:
    """One `path:line:col: RULE message` line per finding, then a one-line summary."""
    lines = [f"{f.path}:{f.line}:{f.col}: {f.rule} {f.message}" for f in result.findings]
    rules = f"{len(result.rules_run)} rule(s) over {result.files} Python file(s)"
    accepted = f", {len(result.applied)} accepted by an exception" if result.applied else ""
    if result.findings:
        lines.append(f"\n{len(result.findings)} finding(s) from {rules}{accepted}")
    else:
        lines.append(f"arch-check ok: {rules}{accepted}")
    return "\n".join(lines)


def json(result: Result) -> str:
    """The whole result as one JSON document, keys in a fixed order."""
    return jsonlib.dumps(
        {
            "version": result.version,
            "root": result.root,
            "rules_run": [rule_dict(r) for r in result.rules_run],
            "findings": [f.as_dict() for f in result.findings],
            "exceptions_applied": [a.as_dict() for a in result.applied],
        },
        indent=2,
    )


def listing(rules: list[Rule]) -> str:
    """`--list`: one line per rule, id, group, coverage, severity, origin, summary."""
    return "\n".join(f"{r.id:<7} {r.group:<10} {r.coverage:<8} {r.severity:<7} {r.origin:<10} {r.summary}" for r in rules)
