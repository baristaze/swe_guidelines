"""The values every part of arch-check shares: a rule, a violation, a finding.

A rule is a function over a `Project` that yields `Violation`s. Its
registration (`arch_check.registry.rule`) carries what the lens says
about it: the id, the group, the severity, and how much of the lens the
rule decides. The runner turns each violation into a `Finding` stamped
with those fields, so a rule never repeats them.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from arch_check.project import Project

Severity = Literal["high", "medium", "low"]
Coverage = Literal["full", "partial"]
Origin = Literal["guideline", "local"]

SEVERITIES: tuple[Severity, ...] = ("high", "medium", "low")
COVERAGES: tuple[Coverage, ...] = ("full", "partial")

GROUPS: dict[str, str] = {
    "om": "OM",
    "contracts": "CON",
    "context": "CTX",
    "storage": "STO",
    "async": "ASY",
    "network": "NET",
    "delivery": "DEL",
    "ops": "OPS",
}
"""Each lens group and the id prefix of its lenses, in the catalog's order."""

FRAMEWORK = "framework"
"""The group of the findings arch-check raises about itself: `PARSE` and `IGNORE`."""


@dataclass(frozen=True)
class Violation:
    """One breach a rule reports: a file relative to the root, a position, one sentence."""

    path: str
    line: int
    col: int
    message: str

    @classmethod
    def at(cls, path: str, node: ast.AST | None, message: str) -> Violation:
        """A violation at an `ast` node, columns counted from 1; no node means line 1."""
        line = getattr(node, "lineno", 1) if node is not None else 1
        col = getattr(node, "col_offset", 0) + 1 if node is not None else 1
        return cls(path, line, col, message)


Check = Callable[["Project"], Iterable[Violation]]


@dataclass(frozen=True)
class Rule:
    """A registered rule. `id` is the id of the lens it decides.

    `origin` is `guideline` for a rule this package ships and `local`
    for one a project keeps in its own tree (`local` in the config).
    """

    id: str
    group: str
    severity: Severity
    coverage: Coverage
    summary: str
    check: Check
    origin: Origin = "guideline"


@dataclass(frozen=True)
class Finding:
    """A violation stamped with the rule that raised it, as the report prints it."""

    rule: str
    group: str
    severity: str
    path: str
    line: int
    col: int
    message: str
    origin: str = "guideline"

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "group": self.group,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "message": self.message,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class Applied:
    """A finding an exception accepted: from the config or an inline comment."""

    rule: str
    path: str
    line: int
    adr: str
    source: Literal["config", "inline"]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "path": self.path,
            "line": self.line,
            "adr": self.adr,
            "source": self.source,
            "reason": self.reason,
        }
