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

A scenario can name how many times a run repeats its subject, `repeat`,
and the run's spend cap in US dollars, `max_spend_usd`. A run takes each
when its flag, `--repeat` or `--max-spend-usd`, is not given, and a flag
overrides it. A scenario that names neither runs 3 repeats with no run
cap.

A scenario can name what `run.py --preflight` checks its runtime has,
beyond what every run needs: `preflight.registries`, the URLs its
subject reaches, and `preflight.disk_gib` and `preflight.memory_gib`,
the free disk and the available memory it needs where it runs.

A skill subject is bounded by money and time: a cap in US dollars,
`max_usd`, which every skill subject names, and a timeout. A turn count
is no bound: a scenario may name `max_turns`, and only then does Claude
Code get one. A skill subject can also run in `phases`, each a session of its own with its own prompt
and bounds, building one `output` folder the harness commits after every
phase. A phase is `fresh`, a new session with a new HOME, unless it says
`resume`, which continues the session of the phase before it in the same
HOME and the same working folder. The container runtime starts every
phase in a new container, so a scenario that resumes a phase does not
list it.

A subject in phases can declare named optional `groups`. A phase that
names a `group` runs only in a run that takes it, with `run.py --with
<group>`, and a run takes none by default. A group can add a sentence to
the rubric, so the judges know what was built. `select` returns the
scenario as a run with its groups runs it, and `as_ran` as one repeat
of that run ran it, when the repeat ran only some of its phases. The
run's spend cap is the scenario's `max_spend_usd` on the path it names,
a run that takes no group; else, for a subject in phases, the sum of the
caps of the phases that run and the budgets of the agentic judges
(`spend_cap`). A run that judges an earlier run's output again runs no
phase, and its cap is the judges' budgets alone (`judging_cap`).

The judges are one-shot by default: one prompt, one verdict each. A
scenario can ask for `agentic` judges instead, which read the subject's
output and each of its `references` through tools, within a budget, and
score the output against every reference. A reference is folders of
this checkout, named by `paths`, or a public repository, named by its
`https` URL and pinned at a `tag`. Each has a weight, and the weights
sum to 1: the harness computes the weighted score from them.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import math
import re
from dataclasses import dataclass, field, fields
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from . import agentic as A
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
# How the judges judge: one prompt each, or a loop over tools that reads the output and the references.
MODES = ("one-shot", "agentic")
# The root an agentic judge reads the subject's output under; no reference takes its name.
OUTPUT_ROOT = "output"
REFERENCE_KEYS = ("name", "weight", "paths", "repository", "tag")
# A public repository by its https URL: a host, a path, and no credentials, query, or fragment.
REPOSITORY = re.compile(r"https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~-]+)+/?")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+-]*")
# A URL the subject reaches, as the preflight asks it: https, a host, and a path, with no credentials, query, or fragment.
URL = re.compile(r"https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?")
PREFLIGHT_KEYS = ("registries", "disk_gib", "memory_gib")


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
    max_usd: float
    timeout_s: int
    # A turn cap, when the scenario names one; a turn count is no bound unless named.
    max_turns: int | None = None
    session: str = "fresh"
    cwd: str = "workspace"
    hint: bool = False
    max_gate_reruns: int = GATE_RERUNS
    on_cap: str = "continue"
    # The optional group the phase belongs to; None for a phase every run takes.
    group: str | None = None

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
            "group": self.group,
        }


@dataclass(frozen=True)
class Group:
    """A named optional group of phases: a run takes it with `--with <name>`, and `rubric` is what it adds to the rubric."""

    name: str
    rubric: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "rubric": self.rubric}


@dataclass(frozen=True)
class Subject:
    """What the run puts in front of the judges."""

    skill: str | None = None
    prompt: str = ""
    argv: list[str] = field(default_factory=list)
    # A skill subject's turn cap, when the scenario names one.
    max_turns: int | None = None
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
    # The optional groups of phases the subject declares, in the scenario's order.
    groups: list[Group] = field(default_factory=list)


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
    that target so the subject never reads the answers. `lenses` gives
    the judges the text of each lens the artifact cites.
    """

    files: list[str] = field(default_factory=list)
    expected: str | None = None
    lenses: bool = False

    @property
    def empty(self) -> bool:
        return not self.files and not self.expected and not self.lenses


@dataclass(frozen=True)
class PreflightSpec:
    """What `run.py --preflight` checks the runtime has for this scenario, beyond what every run needs.

    `registries` are URLs the subject reaches, each of which must answer
    from where it runs. `disk_gib` and `memory_gib` are the free disk and
    the available memory it needs there.
    """

    registries: list[str] = field(default_factory=list)
    disk_gib: float | None = None
    memory_gib: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"registries": list(self.registries), "disk_gib": self.disk_gib, "memory_gib": self.memory_gib}


@dataclass(frozen=True)
class Reference:
    """What an agentic judge reads besides the subject's output, and how much its score weighs.

    It is `paths` of this checkout, or a public `repository` pinned at
    `tag`: one or the other.
    """

    name: str
    weight: float
    paths: list[str] = field(default_factory=list)
    repository: str | None = None
    tag: str | None = None

    def as_dict(self) -> dict[str, Any]:
        if self.repository:
            return {"name": self.name, "weight": self.weight, "repository": self.repository, "tag": self.tag}
        return {"name": self.name, "weight": self.weight, "paths": list(self.paths)}


@dataclass(frozen=True)
class JudgeSpec:
    """The default judge selection of the scenario; the flags override it.

    `mode` is `one-shot` or `agentic`. Only agentic judges have a
    `budget`, the bounds of each judgement, and `references`.
    """

    providers: str = "3"
    effort: str = "medium"
    mode: str = "one-shot"
    budget: A.Budget | None = None
    references: list[Reference] = field(default_factory=list)

    @property
    def agentic(self) -> bool:
        return self.mode == "agentic"

    @property
    def weights(self) -> dict[str, float]:
        """Each reference's weight, by name, in the scenario's order."""
        return {r.name: r.weight for r in self.references}

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"providers": self.providers, "effort": self.effort, "mode": self.mode}
        if self.agentic:
            out["budget"] = self.budget.as_dict() if self.budget else None
            out["references"] = [r.as_dict() for r in self.references]
        return out


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
    # The repeats and the run's spend cap a run takes when no flag names them; None when the scenario names none.
    repeat: int | None = None
    max_spend_usd: float | None = None
    preflight: PreflightSpec = field(default_factory=PreflightSpec)
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
                "groups": [g.as_dict() for g in self.subject.groups],
            },
            "artifact": {"stdout": self.artifact.stdout, "files": list(self.artifact.files)},
            "rubric": self.rubric,
            "judges": self.judges.as_dict(),
            "runtimes": list(self.runtimes),
            "requires": list(self.requires),
            "repeat": self.repeat,
            "max_spend_usd": self.max_spend_usd,
            "preflight": self.preflight.as_dict(),
            "evidence": {
                "files": list(self.evidence.files),
                "expected": self.evidence.expected,
                "lenses": self.evidence.lenses,
            },
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
    _only(data, SCENARIO_KEYS, "scenario")
    name = str(data.get("name") or (path.stem if path else ""))
    if not name:
        raise ScenarioError("scenario: name is required")
    kind = str(data.get("kind") or "skill")
    if kind not in KINDS:
        raise ScenarioError(f"scenario {name}: kind {kind!r} is not one of {', '.join(KINDS)}")
    runtimes, requires = _runtimes(data, name)
    repeat = None if data.get("repeat") is None else _whole(data["repeat"], f"scenario {name}: repeat")
    max_spend_usd = _usd(data.get("max_spend_usd"), f"scenario {name}: max_spend_usd")
    preflight = _preflight(data.get("preflight"), f"scenario {name}: preflight")

    raw_subject = data.get("subject") or {}
    if not isinstance(raw_subject, dict):
        raise ScenarioError(f"scenario {name}: subject holds a mapping")
    _only(raw_subject, SUBJECT_KEYS, f"scenario {name}: subject")
    groups = _groups(raw_subject, name)
    phases = _phases(raw_subject, kind, runtimes, name, groups)
    subject = Subject(
        skill=raw_subject.get("skill"),
        prompt=str(raw_subject.get("prompt") or ""),
        argv=_strings(raw_subject.get("argv"), f"scenario {name}: subject.argv"),
        max_turns=None
        if raw_subject.get("max_turns") is None
        else _int(raw_subject["max_turns"], f"scenario {name}: subject.max_turns"),
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
        groups=groups,
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

    judges = _judges(data.get("judges") or {}, name)
    raw_evidence = data.get("evidence") or {}
    if not isinstance(raw_evidence, dict):
        raise ScenarioError(f"scenario {name}: evidence holds a mapping")
    _only(raw_evidence, ("files", "expected", "lenses"), f"scenario {name}: evidence")
    lenses = raw_evidence.get("lenses", False)
    if not isinstance(lenses, bool):
        raise ScenarioError(f"scenario {name}: evidence.lenses is true or false, got {lenses!r}")
    evidence = EvidenceSpec(
        files=_strings(raw_evidence.get("files"), f"scenario {name}: evidence.files"),
        expected=raw_evidence.get("expected"),
        lenses=lenses,
    )
    if evidence.expected and not subject.target:
        raise ScenarioError(f"scenario {name}: evidence.expected describes a target, and subject.target names none")
    if judges.agentic and not evidence.empty:
        raise ScenarioError(
            f"scenario {name}: evidence goes into a one-shot judge's prompt; an agentic judge reads its references instead"
        )
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
        repeat=repeat,
        max_spend_usd=max_spend_usd,
        preflight=preflight,
        path=path,
    )


SCENARIO_KEYS = (
    "name",
    "kind",
    "subject",
    "artifact",
    "rubric",
    "judges",
    "runtimes",
    "requires",
    "repeat",
    "max_spend_usd",
    "preflight",
    "evidence",
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
    "groups",
)
PHASE_KEYS = (
    "name",
    "prompt",
    "max_turns",
    "max_usd",
    "timeout_s",
    "session",
    "cwd",
    "hint",
    "max_gate_reruns",
    "on_cap",
    "group",
)
# A phase's name, and a group's: a lowercase word of its own.
WORD = re.compile(r"[a-z0-9][a-z0-9-]*")
# What a subject in phases takes from each phase instead, so a subject-wide one would be read by nothing.
PER_PHASE = ("prompt", "max_turns", "timeout_s", "max_usd")


def _judges(raw: Any, name: str) -> JudgeSpec:
    """The judges of a scenario: who judges, at what effort, and how; for agentic judges, their budget and references."""
    where = f"scenario {name}: judges"
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where} holds a mapping")
    _only(raw, ("providers", "effort", "mode", "budget", "references"), where)
    providers, effort = str(raw.get("providers", "3")), str(raw.get("effort", "medium"))
    try:
        P.parse(providers)
    except ValueError as exc:
        raise ScenarioError(f"{where}.providers: {exc}") from exc
    if effort not in EFFORTS:
        raise ScenarioError(f"{where}.effort is one of {', '.join(EFFORTS)}, got {effort!r}")
    mode = raw.get("mode", "one-shot")
    if mode not in MODES:
        raise ScenarioError(f"{where}.mode is one of {', '.join(MODES)}, got {mode!r}")
    if mode != "agentic":
        extra = [k for k in ("budget", "references") if k in raw]
        if extra:
            raise ScenarioError(f"{where}: {', '.join(extra)} belong to agentic judges; set judges.mode: agentic")
        return JudgeSpec(providers=providers, effort=effort)
    return JudgeSpec(
        providers=providers,
        effort=effort,
        mode=mode,
        budget=_budget(raw.get("budget"), f"{where}.budget"),
        references=_references(raw.get("references"), f"{where}.references"),
    )


# A budget's whole-number bounds; the rest, wall_s and max_usd, are amounts above 0.
WHOLE_BOUNDS = ("tool_calls", "input_tokens", "submits")


def _budget(raw: Any, where: str) -> A.Budget:
    """An agentic judgement's bounds: the defaults, each replaced by what the scenario sets."""
    if raw is None:
        return A.Budget()
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where} holds a mapping")
    _only(raw, tuple(f.name for f in fields(A.Budget)), where)
    for key, value in raw.items():
        if key in WHOLE_BOUNDS:
            _whole(value, f"{where}.{key}")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < math.inf:
            raise ScenarioError(f"{where}.{key}: an amount above 0, got {value!r}")
    given: dict[str, Any] = {k: v if k in WHOLE_BOUNDS else float(v) for k, v in raw.items()}
    return A.Budget(**given)


def _references(raw: Any, where: str) -> list[Reference]:
    """What agentic judges read besides the output: at least one reference, each named once, the weights summing to 1."""
    if not isinstance(raw, list) or not raw:
        raise ScenarioError(f"{where}: a list of at least one reference, each a folder of this checkout or a repository")
    refs: list[Reference] = []
    for index, item in enumerate(raw):
        at = f"{where}[{index}]"
        if not isinstance(item, dict):
            raise ScenarioError(f"{at}: a reference holds a mapping")
        _only(item, REFERENCE_KEYS, at)
        ref_name = item.get("name")
        if not isinstance(ref_name, str) or not A.ROOT_NAME.fullmatch(ref_name):
            raise ScenarioError(f"{at}.name: lowercase letters, digits, - and _, at most 32, got {ref_name!r}")
        if ref_name == OUTPUT_ROOT or ref_name in (r.name for r in refs):
            raise ScenarioError(f"{at}.name {ref_name!r} is taken: the output, or another reference, reads under it")
        weight = item.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < weight <= 1:
            raise ScenarioError(f"{at}.weight: a share above 0 and at most 1, got {weight!r}")
        refs.append(_reference(item, ref_name, float(weight), at))
    total = sum(Decimal(str(r.weight)) for r in refs)
    if total != 1:
        raise ScenarioError(f"{where}: the weights sum to 1, got {total}")
    return refs


def _reference(item: dict[str, Any], name: str, weight: float, at: str) -> Reference:
    """One reference: paths of this checkout, or a repository at a tag, never both."""
    if ("paths" in item) == ("repository" in item):
        raise ScenarioError(f"{at}: a reference names paths of this checkout or a repository, one of the two")
    if "paths" in item:
        if "tag" in item:
            raise ScenarioError(f"{at}.tag pins a repository, and this reference names paths of this checkout")
        paths = _strings(item["paths"], f"{at}.paths")
        if not paths:
            raise ScenarioError(f"{at}.paths: at least one path of this checkout")
        for path in paths:
            parts = PurePosixPath(path).parts
            if path.startswith("/") or not parts or any(p in (".", "..") for p in parts) or "\\" in path:
                raise ScenarioError(f"{at}.paths: a file or a folder inside this checkout, such as `lenses`, got {path!r}")
        return Reference(name=name, weight=weight, paths=paths)
    url, tag = item.get("repository"), item.get("tag")
    if not isinstance(url, str) or not REPOSITORY.fullmatch(url):
        raise ScenarioError(f"{at}.repository: a public repository's https URL, with no credentials, got {url!r}")
    if not isinstance(tag, str) or not TAG.fullmatch(tag) or ".." in tag or tag.endswith((".lock", "/")):
        raise ScenarioError(f"{at}.tag: the tag the repository is pinned at, such as v1.2.0, got {tag!r}")
    return Reference(name=name, weight=weight, repository=url, tag=tag)


def _preflight(raw: Any, where: str) -> PreflightSpec:
    """What the preflight checks for the scenario: the URLs its subject reaches, and the disk and memory it needs."""
    if raw is None:
        return PreflightSpec()
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where} holds a mapping")
    _only(raw, PREFLIGHT_KEYS, where)
    registries = _strings(raw.get("registries"), f"{where}.registries")
    for url in registries:
        if not URL.fullmatch(url):
            raise ScenarioError(
                f"{where}.registries: an https URL with no credentials, such as https://pypi.org/simple/, got {url!r}"
            )
    return PreflightSpec(
        registries=registries,
        disk_gib=_amount(raw.get("disk_gib"), f"{where}.disk_gib", "an amount of GiB above 0"),
        memory_gib=_amount(raw.get("memory_gib"), f"{where}.memory_gib", "an amount of GiB above 0"),
    )


def _whole(value: Any, where: str, least: int = 1) -> int:
    """A whole number of at least `least`, or a ScenarioError that says where."""
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise ScenarioError(f"{where}: a whole number of at least {least}, got {value!r}")
    return value


def _usd(value: Any, where: str) -> float | None:
    """A cap in US dollars, above zero; None when the scenario names none."""
    return _amount(value, where, "an amount in US dollars above 0")


def _amount(value: Any, where: str, what: str) -> float | None:
    """A number above zero, which `what` describes when it is refused; None when the scenario names none."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0 or value == float("inf"):
        raise ScenarioError(f"{where}: {what}, got {value!r}")
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


def _groups(raw: dict[str, Any], name: str) -> list[Group]:
    """The optional groups of phases a subject declares, each a lowercase word with an optional rubric sentence."""
    if raw.get("groups") is None:
        return []
    where = f"scenario {name}: subject.groups"
    if raw.get("phases") is None:
        raise ScenarioError(f"{where}: a group holds phases, and the subject runs in none")
    if not isinstance(raw["groups"], dict) or not raw["groups"]:
        raise ScenarioError(f"{where}: a mapping of at least one group, by its name")
    groups: list[Group] = []
    for key, item in raw["groups"].items():
        if not isinstance(key, str) or not WORD.fullmatch(key):
            raise ScenarioError(f"{where}: a group's name is a lowercase word, such as `extras`, got {key!r}")
        item = {} if item is None else item
        if not isinstance(item, dict):
            raise ScenarioError(f"{where}.{key} holds a mapping")
        _only(item, ("rubric",), f"{where}.{key}")
        rubric = item.get("rubric") or ""
        if not isinstance(rubric, str):
            raise ScenarioError(f"{where}.{key}.rubric is a sentence the rubric takes, got {rubric!r}")
        groups.append(Group(name=key, rubric=rubric.strip()))
    return groups


def _phases(raw: dict[str, Any], kind: str, runtimes: list[str], name: str, groups: list[Group]) -> list[Phase]:
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
        missing = [k for k in ("name", "prompt", "max_usd", "timeout_s") if item.get(k) in (None, "")]
        if missing:
            raise ScenarioError(f"{at}: every phase names its {', '.join(missing)}")
        phase = Phase(
            name=str(item["name"]),
            prompt=str(item["prompt"]),
            max_turns=None if item.get("max_turns") is None else _whole(item["max_turns"], f"{at}.max_turns"),
            max_usd=_usd(item["max_usd"], f"{at}.max_usd") or 0.0,
            timeout_s=_whole(item["timeout_s"], f"{at}.timeout_s"),
            session=str(item.get("session", "fresh")),
            cwd=str(item.get("cwd", "workspace")),
            hint=item.get("hint", False),
            max_gate_reruns=_whole(item.get("max_gate_reruns", GATE_RERUNS), f"{at}.max_gate_reruns", least=0),
            on_cap=str(item.get("on_cap", "continue")),
            group=item.get("group"),
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
        if not WORD.fullmatch(phase.name) or phase.name in (p.name for p in phases):
            raise ScenarioError(f"{at}.name is a lowercase word of its own, such as `scaffold`, got {phase.name!r}")
        if phase.group is not None and phase.group not in (g.name for g in groups):
            declared = ", ".join(g.name for g in groups) or "none"
            raise ScenarioError(f"{at}.group names a group subject.groups declares ({declared}), got {phase.group!r}")
        if phase.session == "resume":
            if not phases:
                raise ScenarioError(f"{at}: the first phase has no session before it to resume")
            if phase.cwd != phases[-1].cwd:
                raise ScenarioError(f"{at}: a resumed session starts where the phase before it did, in its {phases[-1].cwd}")
            if phases[-1].group not in (None, phase.group):
                raise ScenarioError(
                    f"{at}: a resumed session needs the phase before it in every run that takes it, "
                    f"and that phase is in the group {phases[-1].group}"
                )
            if "container" in runtimes:
                raise ScenarioError(
                    f"{at}: the container runtime starts every phase in a new container, so no phase resumes there; "
                    "take it out of runtimes"
                )
        phases.append(phase)
    if groups:
        empty = [g.name for g in groups if g.name not in (p.group for p in phases)]
        if empty:
            raise ScenarioError(f"{where}: no phase is in the group {', '.join(empty)}")
        if all(p.group for p in phases):
            raise ScenarioError(f"{where}: a run that takes no group runs the phases in none, and every phase is in one")
    return phases


def select(scn: Scenario, taken: list[str]) -> Scenario:
    """The scenario as a run that takes these groups runs it.

    It holds the phases in no group and those of the groups taken, in the
    scenario's order, and the rubric with each taken group's sentence
    after it. The scenario's own `max_spend_usd` is the cap of a run that
    takes no group, so a run that takes one drops it and takes the sum of
    its phases' caps instead (`spend_cap`). A group the scenario does not
    declare is a ScenarioError.
    """
    declared = [g.name for g in scn.subject.groups]
    unknown = [g for g in taken if g not in declared]
    if unknown:
        known = ", ".join(declared) or "none"
        raise ScenarioError(f"scenario {scn.name} declares no group {', '.join(unknown)}; its groups: {known}")
    if not taken:
        return dataclasses.replace(
            scn, subject=dataclasses.replace(scn.subject, phases=[p for p in scn.subject.phases if not p.group])
        )
    groups = [g for g in scn.subject.groups if g.name in taken]
    phases = [p for p in scn.subject.phases if p.group is None or p.group in taken]
    rubric = "\n\n".join([scn.rubric, *(g.rubric for g in groups if g.rubric)])
    return dataclasses.replace(scn, subject=dataclasses.replace(scn.subject, phases=phases), rubric=rubric, max_spend_usd=None)


def taken_groups(scn: Scenario) -> list[str]:
    """The groups a selected scenario's phases come from, in the scenario's order."""
    return [g.name for g in scn.subject.groups if g.name in (p.group for p in scn.subject.phases)]


def ran_groups(scn: Scenario, taken: list[str], ran: list[str] | None) -> list[str]:
    """The taken groups every one of whose phases is named in `ran`, in the scenario's order; every taken one when None.

    A group's sentence names all of its phases, so a group some of whose
    phases did not run adds no sentence to a repeat's rubric.
    """
    return [
        g.name
        for g in scn.subject.groups
        if g.name in taken and (ran is None or all(p.name in ran for p in scn.subject.phases if p.group == g.name))
    ]


def as_ran(scn: Scenario, taken: list[str], ran: list[str] | None) -> Scenario:
    """The scenario as one repeat of a run that took these groups ran it: only the phases named in `ran`.

    Its rubric is that of `select` with the groups `ran_groups` gives, and
    it holds only the phases that ran, of no group or of a taken one. So a
    group whose phases did not all run adds no sentence to the rubric, and
    no phase that did not run is described. With `ran` None, nothing says
    which phases ran, and it is `select`.
    """
    if ran is None:
        return select(scn, taken)
    selected = select(scn, ran_groups(scn, taken, ran))
    phases = [p for p in scn.subject.phases if p.name in ran and (p.group is None or p.group in taken)]
    return dataclasses.replace(selected, subject=dataclasses.replace(selected.subject, phases=phases))


def spend_cap(scn: Scenario, repeat: int, judges: int) -> float | None:
    """The run's spend cap when no flag names one: the scenario's, else what the phases and the judges may spend.

    That is, for a subject in phases, the sum of the caps of the phases
    that run and the dollar budgets of its `judges` agentic judges, over
    every repeat. One-shot judges have no budget, and add nothing. A
    subject in one session has no run cap unless the scenario names one.
    """
    if scn.max_spend_usd is not None:
        return scn.max_spend_usd
    if not scn.subject.phases:
        return None
    return round((sum(p.max_usd for p in scn.subject.phases) + judges_budget(scn, judges)) * repeat, 6)


def judges_budget(scn: Scenario, judges: int) -> float:
    """What `judges` agentic judges of one repeat may spend: each one's dollar budget. One-shot judges have none: 0."""
    return judges * scn.judges.budget.max_usd if scn.judges.agentic and scn.judges.budget else 0.0


def judging_cap(scn: Scenario, repeats: int, judges: int) -> float | None:
    """The spend cap of a run that judges an earlier run's output again, when no flag names one.

    It is the dollar budgets of its `judges` agentic judges over the
    `repeats` it judges. No phase runs, so no phase's cap is in it, nor the
    scenario's own `max_spend_usd`, which covers a subject too. One-shot
    judges have no budget, so a run of them has no cap.
    """
    return round(judges_budget(scn, judges) * repeats, 6) if scn.judges.agentic and scn.judges.budget else None


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
