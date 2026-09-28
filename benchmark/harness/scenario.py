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

A scenario says where it may run: `runtimes`, the runtimes it runs on,
is required, and a run takes the first it lists when `--runtime` names
none. `requires` names what its runtime must provide. A
scenario that lists a runtime unable to provide what it requires is
refused when it loads, so no run of it starts there.

A skill subject is bounded by count and by spend: a turn cap and a cap in
US dollars, `max_usd`, which every skill subject names. A skill subject
can also run in `phases`, each a session of its own with its own prompt
and bounds, building one `output` folder the harness commits after every
phase. A phase is `fresh`, a new session with a new HOME, unless it says
`resume`, which continues the session of the phase before it in the same
HOME and the same working folder. The container runtime starts every
phase in a new container, so a scenario that resumes a phase does not
list it.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import phases as PH
from . import providers as P
from . import runtime as RT
from .judge import EFFORTS

KINDS = ("skill", "command", "qa")
SUFFIXES = (".yaml", ".yml", ".json")
SESSIONS = ("fresh", "resume")
# Where a phase starts: the workspace, or the output folder in it.
WORKDIRS = ("workspace", "output")
# What follows a phase that hit a bound: the next phase, or the end of the repeat.
AFTER_CAP = ("continue", "stop")
# A failed gate run is followed by at most this many more, unless the phase says otherwise.
GATE_RERUNS = 3


class ScenarioError(ValueError):
    """A scenario file that cannot be read as a scenario."""


@dataclass(frozen=True)
class Phase:
    """One session of a skill subject: its prompt and its bounds.

    `session` is `fresh` or `resume`. `cwd` is where it starts: the
    workspace or the output folder. `hint` gives it the handoff note.
    `on_cap` says whether the next phase runs after this one hits a bound.
    """

    name: str
    prompt: str
    max_turns: int
    max_usd: float
    timeout_s: int
    session: str = "fresh"
    cwd: str = "workspace"
    hint: bool = False
    max_gate_reruns: int = GATE_RERUNS
    on_cap: str = "continue"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prompt": self.prompt,
            "session": self.session,
            "cwd": self.cwd,
            "hint": self.hint,
            "max_turns": self.max_turns,
            "max_usd": self.max_usd,
            "timeout_s": self.timeout_s,
            "max_gate_reruns": self.max_gate_reruns,
            "on_cap": self.on_cap,
        }


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
    # A skill subject's spend cap in US dollars, passed to Claude Code.
    max_usd: float | None = None
    # The folder in the workspace a skill subject builds, committed after
    # every phase and archived at the end; the gates run in it.
    output: str | None = None
    gates: list[str] = field(default_factory=list)
    gate_timeout_s: int = 3600
    phases: list[Phase] = field(default_factory=list)


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
    runtimes: list[str]
    evidence: EvidenceSpec = field(default_factory=EvidenceSpec)
    requires: list[str] = field(default_factory=list)
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
                "max_usd": self.subject.max_usd,
                "output": self.subject.output,
                "gates": list(self.subject.gates),
                "gate_timeout_s": self.subject.gate_timeout_s,
                "phases": [p.as_dict() for p in self.subject.phases],
            },
            "artifact": {"stdout": self.artifact.stdout, "files": list(self.artifact.files)},
            "rubric": self.rubric,
            "judges": {"providers": self.judges.providers, "effort": self.judges.effort},
            "runtimes": list(self.runtimes),
            "requires": list(self.requires),
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


def _int(value: Any, where: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ScenarioError(f"{where}: expected a whole number, got {value!r}") from exc


def from_data(data: Any, path: Path | None = None) -> Scenario:
    """Build a scenario from parsed data."""
    if not isinstance(data, dict):
        raise ScenarioError("a scenario file holds a mapping at the top level")
    _only(data, ("name", "kind", "subject", "artifact", "rubric", "judges", "runtimes", "requires", "evidence"), "scenario")
    name = str(data.get("name") or (path.stem if path else ""))
    if not name:
        raise ScenarioError("scenario: name is required")
    kind = str(data.get("kind") or "skill")
    if kind not in KINDS:
        raise ScenarioError(f"scenario {name}: kind {kind!r} is not one of {', '.join(KINDS)}")
    runtimes, requires = _runtimes(data, name)

    raw_subject = data.get("subject") or {}
    if not isinstance(raw_subject, dict):
        raise ScenarioError(f"scenario {name}: subject holds a mapping")
    _only(raw_subject, SUBJECT_KEYS, f"scenario {name}: subject")
    phases = _phases(raw_subject, kind, runtimes, name)
    subject = Subject(
        skill=raw_subject.get("skill"),
        prompt=str(raw_subject.get("prompt") or ""),
        argv=_strings(raw_subject.get("argv"), f"scenario {name}: subject.argv"),
        max_turns=_int(raw_subject.get("max_turns") or 6, f"scenario {name}: subject.max_turns"),
        allowed_tools=_strings(raw_subject.get("allowed_tools"), f"scenario {name}: subject.allowed_tools"),
        target=raw_subject.get("target"),
        model=raw_subject.get("model"),
        provider=raw_subject.get("provider"),
        context=_strings(raw_subject.get("context"), f"scenario {name}: subject.context"),
        timeout_s=_int(raw_subject.get("timeout_s") or 900, f"scenario {name}: subject.timeout_s"),
        max_usd=_usd(raw_subject.get("max_usd"), f"scenario {name}: subject.max_usd"),
        output=_output(raw_subject.get("output"), name),
        gates=_strings(raw_subject.get("gates"), f"scenario {name}: subject.gates"),
        gate_timeout_s=_whole(raw_subject.get("gate_timeout_s", 3600), f"scenario {name}: subject.gate_timeout_s"),
        phases=phases,
    )
    if kind == "skill" and not subject.skill:
        raise ScenarioError(f"scenario {name}: kind skill needs subject.skill")
    _bounded(subject, kind, name)
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
        name=name,
        kind=kind,
        subject=subject,
        artifact=artifact,
        rubric=rubric,
        judges=judges,
        runtimes=runtimes,
        evidence=evidence,
        requires=requires,
        path=path,
    )


SUBJECT_KEYS = (
    "skill",
    "prompt",
    "argv",
    "max_turns",
    "allowed_tools",
    "target",
    "model",
    "provider",
    "context",
    "timeout_s",
    "max_usd",
    "output",
    "gates",
    "gate_timeout_s",
    "phases",
)
PHASE_KEYS = ("name", "prompt", "max_turns", "max_usd", "timeout_s", "session", "cwd", "hint", "max_gate_reruns", "on_cap")
# What a subject in phases takes from each phase instead, so a subject-wide one would be read by nothing.
PER_PHASE = ("prompt", "max_turns", "timeout_s", "max_usd")


def _whole(value: Any, where: str, least: int = 1) -> int:
    """A whole number of at least `least`, or a ScenarioError that says where."""
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise ScenarioError(f"{where}: a whole number of at least {least}, got {value!r}")
    return value


def _usd(value: Any, where: str) -> float | None:
    """A cap in US dollars, above zero; None when the scenario names none."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0 or value == float("inf"):
        raise ScenarioError(f"{where}: an amount in US dollars above 0, got {value!r}")
    return float(value)


def _output(value: Any, name: str) -> str | None:
    """The output folder: a folder of the workspace, by a relative path that stays in it."""
    if value is None:
        return None
    parts = str(value).split("/")
    if not isinstance(value, str) or value.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ScenarioError(f"scenario {name}: subject.output is a folder of the workspace, such as `site`, got {value!r}")
    if parts[0] in (PH.HANDOFF, PH.ARCHIVE.split("/")[0]):
        raise ScenarioError(f"scenario {name}: subject.output {value!r} is where the harness keeps its own files")
    return value


def _phases(raw: dict[str, Any], kind: str, runtimes: list[str], name: str) -> list[Phase]:
    """The phases of a skill subject, each checked; none when the subject names none."""
    if raw.get("phases") is None:
        return []
    where = f"scenario {name}: subject.phases"
    if kind != "skill":
        raise ScenarioError(f"{where}: only a skill subject runs in phases")
    if not isinstance(raw["phases"], list) or not raw["phases"]:
        raise ScenarioError(f"{where}: a list of at least one phase")
    shared = [k for k in PER_PHASE if k in raw]
    if shared:
        raise ScenarioError(f"{where}: each phase names its own {', '.join(shared)}; the subject names none")
    phases: list[Phase] = []
    for index, item in enumerate(raw["phases"]):
        at = f"{where}[{index}]"
        if not isinstance(item, dict):
            raise ScenarioError(f"{at}: a phase holds a mapping")
        _only(item, PHASE_KEYS, at)
        missing = [k for k in ("name", "prompt", "max_turns", "max_usd", "timeout_s") if item.get(k) in (None, "")]
        if missing:
            raise ScenarioError(f"{at}: every phase names its {', '.join(missing)}")
        phase = Phase(
            name=str(item["name"]),
            prompt=str(item["prompt"]),
            max_turns=_whole(item["max_turns"], f"{at}.max_turns"),
            max_usd=_usd(item["max_usd"], f"{at}.max_usd") or 0.0,
            timeout_s=_whole(item["timeout_s"], f"{at}.timeout_s"),
            session=str(item.get("session", "fresh")),
            cwd=str(item.get("cwd", "workspace")),
            hint=item.get("hint", False),
            max_gate_reruns=_whole(item.get("max_gate_reruns", GATE_RERUNS), f"{at}.max_gate_reruns", least=0),
            on_cap=str(item.get("on_cap", "continue")),
        )
        for key, value, allowed in (
            ("session", phase.session, SESSIONS),
            ("cwd", phase.cwd, WORKDIRS),
            ("on_cap", phase.on_cap, AFTER_CAP),
        ):
            if value not in allowed:
                raise ScenarioError(f"{at}.{key} is one of {', '.join(allowed)}, got {value!r}")
        if not isinstance(phase.hint, bool):
            raise ScenarioError(f"{at}.hint is true or false, got {phase.hint!r}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", phase.name) or phase.name in (p.name for p in phases):
            raise ScenarioError(f"{at}.name is a lowercase word of its own, such as `scaffold`, got {phase.name!r}")
        if phase.session == "resume":
            if not phases:
                raise ScenarioError(f"{at}: the first phase has no session before it to resume")
            if phase.cwd != phases[-1].cwd:
                raise ScenarioError(f"{at}: a resumed session starts where the phase before it did, in its {phases[-1].cwd}")
            if "container" in runtimes:
                raise ScenarioError(
                    f"{at}: the container runtime starts every phase in a new container, so no phase resumes there; "
                    "take it out of runtimes"
                )
        phases.append(phase)
    return phases


def _bounded(subject: Subject, kind: str, name: str) -> None:
    """A skill subject has its spend cap, and what builds an output names one."""
    where = f"scenario {name}: subject"
    if kind != "skill":
        extra = [k for k in ("max_usd", "output", "gates") if getattr(subject, k)]
        if extra:
            raise ScenarioError(f"{where}: {', '.join(extra)} belong to a skill subject")
        return
    if not subject.phases and subject.max_usd is None:
        raise ScenarioError(f"{where}.max_usd is required: the most a skill subject may spend, in US dollars")
    if subject.phases and not subject.output:
        raise ScenarioError(f"{where}.output is required: the folder the phases build, committed after each")
    if subject.gates and not subject.output:
        raise ScenarioError(f"{where}.gates run in the output folder, and subject.output names none")
    if not subject.output and any(p.cwd == "output" for p in subject.phases):
        raise ScenarioError(f"{where}: a phase starts in the output folder, and subject.output names none")


def _runtimes(data: dict[str, Any], name: str) -> tuple[list[str], list[str]]:
    """The runtimes a scenario runs on and what it requires of them, each runtime able to provide it."""
    runtimes = _strings(data.get("runtimes"), f"scenario {name}: runtimes")
    if not runtimes:
        raise ScenarioError(
            f"scenario {name}: runtimes is required: the runtimes it may run on, of {', '.join(RT.NAMES)}; "
            "a run takes the first when --runtime names none"
        )
    unknown = [r for r in runtimes if r not in RT.NAMES]
    if unknown:
        raise ScenarioError(f"scenario {name}: runtimes: {', '.join(unknown)} is not one of {', '.join(RT.NAMES)}")
    requires = _strings(data.get("requires"), f"scenario {name}: requires")
    unknown = [r for r in requires if r not in RT.REQUIREMENTS]
    if unknown:
        raise ScenarioError(f"scenario {name}: requires: {', '.join(unknown)} is not one of {', '.join(RT.REQUIREMENTS)}")
    for runtime in runtimes:
        missing = [r for r in requires if r not in RT.PROVIDES[runtime]]
        if missing:
            raise ScenarioError(
                f"scenario {name}: the {runtime} runtime cannot provide {', '.join(missing)}, which the scenario requires; "
                "take it out of runtimes"
            )
    return runtimes, requires


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


def parse_errors() -> tuple[type[Exception], ...]:
    """What the parsers raise on text that does not parse: json's error, and yaml's when pyyaml is there."""
    try:
        import yaml  # imported here: the harness stays standard library at import time
    except ModuleNotFoundError:
        return (json.JSONDecodeError,)
    return (json.JSONDecodeError, yaml.YAMLError)


def load(path: str | Path) -> Scenario:
    """Load one scenario file. A file that does not parse is a ScenarioError, as one that is not a scenario is."""
    path = Path(path)
    if path.suffix not in SUFFIXES:
        raise ScenarioError(f"{path}: a scenario file ends in {', '.join(SUFFIXES)}")
    if not path.exists():
        raise ScenarioError(f"{path}: no such scenario file")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ScenarioError(f"{path}: is not UTF-8: {exc}") from exc
    try:
        data = parse_text(text, path.suffix)
    except parse_errors() as exc:
        raise ScenarioError(f"{path}: does not parse as {path.suffix.lstrip('.').upper()}: {exc}") from exc
    return from_data(data, path)


def catalog(folder: str | Path) -> list[Path]:
    """Every scenario file in a folder, in name order, one per name."""
    folder = Path(folder)
    seen: dict[str, Path] = {}
    for path in sorted(folder.iterdir() if folder.is_dir() else []):
        if path.suffix in SUFFIXES and path.is_file():
            seen.setdefault(path.stem, path)
    return [seen[k] for k in sorted(seen)]


def find(name: str, folder: str | Path) -> Path:
    """The file of a scenario named on the command line: by path, by the name the file gives itself, else by file stem.

    The name is what `run.py list` prints, and it is matched before any
    stem, so what it lists is what `--scenario` takes, even where one
    file's name is another file's stem. A file that does not load has no
    name to match and is found by its stem.
    """
    direct = Path(name)
    if direct.suffix in SUFFIXES and direct.exists():
        return direct
    names: dict[str, Path] = {}
    for path in catalog(folder):
        with contextlib.suppress(ScenarioError):
            names.setdefault(load(path).name, path)
    if name in names:
        return names[name]
    for path in catalog(folder):
        if path.stem == name:
            return path
    known = ", ".join(sorted({*names, *(p.stem for p in catalog(folder))})) or "none"
    raise ScenarioError(f"no scenario named {name!r} in {folder}; known: {known}")
