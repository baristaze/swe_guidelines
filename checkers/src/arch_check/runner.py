"""Run the selected rules over a project and settle every finding against its exceptions.

Order of work: each rule yields its violations; every Python file is
parsed, and one that does not parse is a `PARSE` finding; inline
`# arch-check: ignore[...] ADR-NNNN` comments are read; then each
finding is either accepted by an exception (config or inline) or kept.
An exception that names a missing ADR, a rule that did not run, or a
line with nothing to accept is itself an `IGNORE` finding, so an
exception cannot outlive the code it excused.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from arch_check import __version__
from arch_check.config import PYPROJECT, adr_by_number, glob_match
from arch_check.model import FRAMEWORK, Applied, Finding, Rule
from arch_check.project import Project

PARSE = "PARSE"
IGNORE = "IGNORE"
MARKER = re.compile(r"#\s*arch-check:\s*ignore\[(?P<rules>[^\]]*)\](?P<rest>.*)$")
ADR = re.compile(r"^\s*ADR-(\d{4})\b")


@dataclass
class Result:
    version: str
    root: str
    rules_run: list[Rule]
    files: int
    findings: list[Finding] = field(default_factory=list)
    applied: list[Applied] = field(default_factory=list)


@dataclass(frozen=True)
class Inline:
    """One inline ignore comment: the rules it names and the ADR it cites."""

    path: str
    line: int
    col: int
    rules: tuple[str, ...]
    adr: str


def framework(rule: str, path: str, line: int, col: int, message: str) -> Finding:
    return Finding(
        rule=rule, group=FRAMEWORK, severity="high", path=path, line=line, col=col, message=message, origin="arch-check"
    )


def comments(project: Project, rel: str) -> Iterator[tuple[int, int, str]]:
    """(line, column offset, text) of each place a marker may sit in a file.

    In a Python file that is each `#` comment token, so a marker quoted
    in a docstring or a string is text, not an ignore. Any other file,
    or a Python file that does not tokenize, is read line by line.
    """
    if rel.endswith(".py"):
        try:
            raw = (project.root / rel).read_bytes()
            found = [
                (t.start[0], t.start[1], t.string)
                for t in tokenize.tokenize(io.BytesIO(raw).readline)
                if t.type == tokenize.COMMENT
            ]
        except (OSError, SyntaxError, tokenize.TokenError):
            pass
        else:
            yield from found
            return
    for ln, text in enumerate(project.lines(rel), start=1):
        yield ln, 0, text


def read_markers(project: Project, paths: set[str], known: set[str], findings: list[Finding]) -> dict[tuple[str, int], Inline]:
    """The valid inline ignores of `paths`; a malformed one becomes an `IGNORE` finding."""
    out: dict[tuple[str, int], Inline] = {}
    for rel in sorted(paths):
        for ln, offset, text in comments(project, rel):
            m = MARKER.search(text)
            if not m:
                continue
            col = offset + m.start() + 1
            names = tuple(r.strip() for r in m.group("rules").split(",") if r.strip())
            adr = ADR.match(m.group("rest"))
            if not names:
                findings.append(framework(IGNORE, rel, ln, col, "an inline ignore names no rule"))
                continue
            unknown = [r for r in names if r not in known]
            if unknown:
                findings.append(framework(IGNORE, rel, ln, col, f"an inline ignore names unknown rule(s) {', '.join(unknown)}"))
                continue
            if not adr:
                findings.append(framework(IGNORE, rel, ln, col, "an inline ignore cites no ADR; write `ADR-NNNN` after it"))
                continue
            found = adr_by_number(project.root, adr.group(1))
            if found is None:
                findings.append(
                    framework(
                        IGNORE,
                        rel,
                        ln,
                        col,
                        f"an inline ignore cites ADR-{adr.group(1)}, and docs/adr has no {adr.group(1)}-*.md",
                    )
                )
                continue
            out[(rel, ln)] = Inline(path=rel, line=ln, col=col, rules=names, adr=project.rel(found))
    return out


def run(project: Project, rules: Sequence[Rule], known: set[str], paths: Sequence[str] = ()) -> Result:
    """Run `rules`; `known` is every registered id; `paths` limits what is reported, never what is read."""
    raw: list[Finding] = []
    for r in rules:
        for v in r.check(project):
            raw.append(Finding(r.id, r.group, r.severity, v.path, v.line, v.col, v.message, r.origin))
    project.parse_all()
    for rel, (line, message) in sorted(project.parse_errors.items()):
        raw.append(framework(PARSE, rel, line, 1, f"does not parse: {message}"))

    ran = {r.id for r in rules}
    kept: list[Finding] = []
    meta: list[Finding] = []
    applied: list[Applied] = []
    # a file the rules read may carry an ignore that no longer excuses anything, a Dockerfile as much as a
    # module; Markdown is left out, where a `#` line is a heading and a quoted marker documents the syntax
    read = {rel for rel in project.read_paths if not rel.endswith(".md")}
    scanned = {f.rel for f in project.python_files} | {f.path for f in raw if not f.path.endswith(".md")} | read
    markers = read_markers(project, scanned, known, meta)
    used_markers: set[tuple[tuple[str, int], str]] = set()
    used_exceptions: set[int] = set()
    for f in raw:
        if f.group == FRAMEWORK:
            kept.append(f)
            continue
        inline = markers.get((f.path, f.line))
        if inline is not None and f.rule in inline.rules:
            used_markers.add(((f.path, f.line), f.rule))
            applied.append(Applied(f.rule, f.path, f.line, inline.adr, "inline", ""))
            continue
        # every exception that matches is used, so a narrow one under a broad one is never reported stale
        matching = [
            i
            for i, e in enumerate(project.config.exceptions)
            if e.rule == f.rule and e.path is not None and glob_match(e.path, f.path)
        ]
        if not matching:
            kept.append(f)
            continue
        used_exceptions.update(matching)
        first = project.config.exceptions[matching[0]]
        applied.append(Applied(f.rule, f.path, f.line, first.adr, "config", first.reason))

    for key, inline in sorted(markers.items()):
        for name in inline.rules:
            if name in ran and (key, name) not in used_markers:
                meta.append(
                    framework(IGNORE, inline.path, inline.line, inline.col, f"ignores {name}, which reports nothing here")
                )
    for i, e in enumerate(project.config.exceptions):
        if e.rule in ran and i not in used_exceptions:
            meta.append(framework(IGNORE, PYPROJECT, 1, 1, f"the exception for {e.rule} on {e.path} matches no finding"))

    findings = kept + meta
    if paths:
        findings = [f for f in findings if any(f.path == p or f.path.startswith(p.rstrip("/") + "/") for p in paths)]
    findings.sort(key=lambda f: (f.path, f.line, f.col, f.rule, f.message))
    applied.sort(key=lambda a: (a.path, a.line, a.rule))
    return Result(
        version=__version__,
        root=str(project.root),
        rules_run=list(rules),
        files=len(project.python_files),
        findings=findings,
        applied=applied,
    )
