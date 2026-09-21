"""A scenario: the subject to run, the artifact to keep, the rubric to judge by.

A scenario file is YAML or JSON. JSON always loads; YAML loads when
`pyyaml` is importable, which it is under `uv run benchmark/run.py`. The
tests at the repository root use JSON, so they need nothing installed.

The dataclasses below are the whole shape. A key a scenario does not set
takes the default here, and an unknown key is refused: a misspelled key
is a scenario that silently judges something else.

A relative path in a scenario (`subject.target`, `subject.context`,
`evidence.expected`) is read from the scenario file's folder, so a scenario means the same
thing from wherever the run starts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import providers as P
from .judge import EFFORTS

KINDS = ("skill", "command", "qa")
SUFFIXES = (".yaml", ".yml", ".json")


class ScenarioError(ValueError):
    """A scenario file that cannot be read as a scenario."""


@dataclass(frozen=True)
class Subject:
    """What the run puts in front of the judges."""

    skill: str | None = None
    prompt: str = ""
    argv: list[str] = field(default_factory=list)
    max_turns: int = 6
    allowed_tools: list[str] = field(default_factory=list)
    target: str | None = None
    model: str | None = None
    provider: str | None = None
    context: list[str] = field(default_factory=list)
    timeout_s: int = 900


@dataclass(frozen=True)
class ArtifactSpec:
    """What is collected from the run and shown to the judges."""

    stdout: bool = True
    files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceSpec:
    """What the judges get besides the artifact, so they need not take its word.

    `files` are globs over the target: the source the artifact talks
    about, shown to the judges with line numbers. `expected` is a file
    of the findings planted in the scenario's own target, kept outside
    that target so the subject never reads the answers.
    """

    files: list[str] = field(default_factory=list)
    expected: str | None = None

    @property
    def empty(self) -> bool:
        return not self.files and not self.expected


@dataclass(frozen=True)
class JudgeSpec:
    """The default judge selection of the scenario; the flags override it."""

    providers: str = "3"
    effort: str = "medium"


@dataclass(frozen=True)
class Scenario:
    """One scenario file, resolved."""

    name: str
    kind: str
    subject: Subject
    artifact: ArtifactSpec
    rubric: str
    judges: JudgeSpec
    evidence: EvidenceSpec = field(default_factory=EvidenceSpec)
    path: Path | None = None

    def resolve(self, value: str | None) -> Path | None:
        """A path the scenario names, read from the scenario file's folder."""
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute() and self.path is not None:
            path = self.path.parent / path
        return path.resolve()

    def as_dict(self) -> dict[str, Any]:
        """The scenario as plain data, for `run.json`."""
        return {
            "name": self.name,
            "kind": self.kind,
            "subject": {
                "skill": self.subject.skill,
                "prompt": self.subject.prompt,
                "argv": list(self.subject.argv),
                "max_turns": self.subject.max_turns,
                "allowed_tools": list(self.subject.allowed_tools),
                "target": self.subject.target,
                "model": self.subject.model,
                "provider": self.subject.provider,
                "context": list(self.subject.context),
                "timeout_s": self.subject.timeout_s,
            },
            "artifact": {"stdout": self.artifact.stdout, "files": list(self.artifact.files)},
            "rubric": self.rubric,
            "judges": {"providers": self.judges.providers, "effort": self.judges.effort},
            "evidence": {"files": list(self.evidence.files), "expected": self.evidence.expected},
            "path": str(self.path) if self.path else None,
        }


def _only(data: dict[str, Any], allowed: tuple[str, ...], where: str) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ScenarioError(f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(allowed)}")


def _strings(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raise ScenarioError(f"{where}: expected a list, got a string")
    if not isinstance(value, (list, tuple)):
        raise ScenarioError(f"{where}: expected a list")
    return [str(v) for v in value]


def from_data(data: Any, path: Path | None = None) -> Scenario:
    """Build a scenario from parsed data."""
    if not isinstance(data, dict):
        raise ScenarioError("a scenario file holds a mapping at the top level")
    _only(data, ("name", "kind", "subject", "artifact", "rubric", "judges", "evidence"), "scenario")
    name = str(data.get("name") or (path.stem if path else ""))
    if not name:
        raise ScenarioError("scenario: name is required")
    kind = str(data.get("kind") or "skill")
    if kind not in KINDS:
        raise ScenarioError(f"scenario {name}: kind {kind!r} is not one of {', '.join(KINDS)}")

    raw_subject = data.get("subject") or {}
    if not isinstance(raw_subject, dict):
        raise ScenarioError(f"scenario {name}: subject holds a mapping")
    _only(
        raw_subject,
        ("skill", "prompt", "argv", "max_turns", "allowed_tools", "target", "model", "provider", "context", "timeout_s"),
        f"scenario {name}: subject",
    )
    subject = Subject(
        skill=raw_subject.get("skill"),
        prompt=str(raw_subject.get("prompt") or ""),
        argv=_strings(raw_subject.get("argv"), f"scenario {name}: subject.argv"),
        max_turns=int(raw_subject.get("max_turns") or 6),
        allowed_tools=_strings(raw_subject.get("allowed_tools"), f"scenario {name}: subject.allowed_tools"),
        target=raw_subject.get("target"),
        model=raw_subject.get("model"),
        provider=raw_subject.get("provider"),
        context=_strings(raw_subject.get("context"), f"scenario {name}: subject.context"),
        timeout_s=int(raw_subject.get("timeout_s") or 900),
    )
    if kind == "skill" and not subject.skill:
        raise ScenarioError(f"scenario {name}: kind skill needs subject.skill")
    if kind == "command" and not subject.argv:
        raise ScenarioError(f"scenario {name}: kind command needs subject.argv")
    if kind == "qa" and not subject.prompt:
        raise ScenarioError(f"scenario {name}: kind qa needs subject.prompt")
    if subject.provider is not None:
        try:
            one = P.parse(subject.provider)
        except ValueError as exc:
            raise ScenarioError(f"scenario {name}: subject.provider: {exc}") from exc
        if len(P.members(one)) != 1:
            raise ScenarioError(f"scenario {name}: subject.provider names one provider, got {subject.provider!r}")

    raw_artifact = data.get("artifact") or {}
    if not isinstance(raw_artifact, dict):
        raise ScenarioError(f"scenario {name}: artifact holds a mapping")
    _only(raw_artifact, ("stdout", "files"), f"scenario {name}: artifact")
    artifact = ArtifactSpec(
        stdout=bool(raw_artifact.get("stdout", True)),
        files=_strings(raw_artifact.get("files"), f"scenario {name}: artifact.files"),
    )

    rubric = str(data.get("rubric") or "").strip()
    if not rubric:
        raise ScenarioError(f"scenario {name}: rubric is required")

    raw_judges = data.get("judges") or {}
    if not isinstance(raw_judges, dict):
        raise ScenarioError(f"scenario {name}: judges holds a mapping")
    _only(raw_judges, ("providers", "effort"), f"scenario {name}: judges")
    judges = JudgeSpec(
        providers=str(raw_judges.get("providers", "3")),
        effort=str(raw_judges.get("effort", "medium")),
    )
    try:
        P.parse(judges.providers)
    except ValueError as exc:
        raise ScenarioError(f"scenario {name}: judges.providers: {exc}") from exc
    if judges.effort not in EFFORTS:
        raise ScenarioError(f"scenario {name}: judges.effort is one of {', '.join(EFFORTS)}, got {judges.effort!r}")
    raw_evidence = data.get("evidence") or {}
    if not isinstance(raw_evidence, dict):
        raise ScenarioError(f"scenario {name}: evidence holds a mapping")
    _only(raw_evidence, ("files", "expected"), f"scenario {name}: evidence")
    evidence = EvidenceSpec(
        files=_strings(raw_evidence.get("files"), f"scenario {name}: evidence.files"),
        expected=raw_evidence.get("expected"),
    )
    if evidence.expected and not subject.target:
        raise ScenarioError(f"scenario {name}: evidence.expected describes a target, and subject.target names none")
    return Scenario(
        name=name, kind=kind, subject=subject, artifact=artifact, rubric=rubric, judges=judges, evidence=evidence, path=path
    )


def parse_text(text: str, suffix: str = ".json") -> Any:
    """Parse scenario text by suffix. YAML needs pyyaml; JSON never does."""
    if suffix == ".json":
        return json.loads(text)
    try:
        import yaml  # imported here: the harness stays standard library at import time
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by hand, not in CI
        raise ScenarioError(
            "a YAML scenario needs pyyaml; run through `uv run benchmark/run.py`, or write the scenario as .json"
        ) from exc
    return yaml.safe_load(text)


def load(path: str | Path) -> Scenario:
    """Load one scenario file."""
    path = Path(path)
    if path.suffix not in SUFFIXES:
        raise ScenarioError(f"{path}: a scenario file ends in {', '.join(SUFFIXES)}")
    if not path.exists():
        raise ScenarioError(f"{path}: no such scenario file")
    return from_data(parse_text(path.read_text(encoding="utf-8"), path.suffix), path)


def catalog(folder: str | Path) -> list[Path]:
    """Every scenario file in a folder, in name order, one per name."""
    folder = Path(folder)
    seen: dict[str, Path] = {}
    for path in sorted(folder.iterdir() if folder.is_dir() else []):
        if path.suffix in SUFFIXES and path.is_file():
            seen.setdefault(path.stem, path)
    return [seen[k] for k in sorted(seen)]


def find(name: str, folder: str | Path) -> Path:
    """The file of a scenario named on the command line, by name or by path."""
    direct = Path(name)
    if direct.suffix in SUFFIXES and direct.exists():
        return direct
    for path in catalog(folder):
        if path.stem == name:
            return path
    known = ", ".join(p.stem for p in catalog(folder)) or "none"
    raise ScenarioError(f"no scenario named {name!r} in {folder}; known: {known}")
