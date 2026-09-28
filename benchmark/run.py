#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml==6.0.3",
#   "jsonschema==4.26.0",
#   "pydantic==2.13.5",
#   "anthropic==1.7.0",
#   "openai==3.17.0",
#   "google-genai==2.24.0",
#   "websockets==16.1.1",
# ]
# ///
# Pinned exactly: the harness installs in a step that holds no key, and the
# step that holds the keys runs what was installed there, never a newer one.
"""Run one scenario and have the frontier models judge what came out.

    uv run benchmark/run.py --scenario explain-tenancy --providers 7 --effort medium --repeat 1 --build
    uv run benchmark/run.py --scenario explain-tenancy --max-spend-usd 10 --build --preflight
    uv run benchmark/run.py --scenario create-full-system --runtime-config benchmark/runtime/lima/runtime-config.yaml \
      --rehearsal --out /tmp/rehearsals
    uv run benchmark/run.py --scenario create-full-system --runtime-config benchmark/runtime/lima/runtime-config.yaml \
      --with extras
    uv run benchmark/run.py judge --source benchmark/runs/<run folder> --dry-run
    uv run benchmark/run.py list

Everything a run produced lands in one folder under `--out`: the
resolved scenario, the streams as they were written, the artifact, one
file per judgement, and an agentic judge's transcript beside it,
`results.json` in the schema, and `report.md`.

`--preflight` resolves the run as `--dry-run` does, then checks what it
needs, where it runs, before it spends anything (`harness/preflight.py`).
`--rehearsal` runs the scenario as it will really run, with every bound
cut small, after its preflight (`harness/rehearsal.py`). `--with <group>`
takes an optional group of the scenario's phases, which a run takes none
of by default.

`judge --source <run folder>` judges an earlier run's archived output
again with this checkout's judges, and runs no subject: the judgement
lands in a new run folder beside the source.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

BENCHMARK = Path(__file__).resolve().parent
ROOT = BENCHMARK.parent
if str(BENCHMARK) not in sys.path:
    sys.path.insert(0, str(BENCHMARK))

from harness import archive as A  # noqa: E402
from harness import evidence as E  # noqa: E402
from harness import judge as J  # noqa: E402
from harness import phases as PH  # noqa: E402
from harness import preflight as PF  # noqa: E402
from harness import providers as P  # noqa: E402
from harness import redact as X  # noqa: E402
from harness import references as RF  # noqa: E402
from harness import rehearsal as RH  # noqa: E402
from harness import results as R  # noqa: E402
from harness import runtime as RT  # noqa: E402
from harness import scenario as S  # noqa: E402
from harness import versions as V  # noqa: E402
from harness.capture import CliStream, FrameSink  # noqa: E402

SCENARIOS = BENCHMARK / "scenarios"
SCHEMA = BENCHMARK / "schema" / "result.schema.json"
MODELS = BENCHMARK / "models.yaml"
DEFAULT_OUT = BENCHMARK / "runs"
# What a subject inherits beyond its private HOME and TMPDIR and its key.
PASSTHROUGH = ["PATH", "LANG", "LC_ALL", "SHELL", "TERM", "USER"]
# The paths of the checkout whose content decides a score: the plugin
# payload the subject reads, and the harness, its scenarios, and its
# fixtures. The run folders are the record, not an input.
VERSIONED = (*RT.PLUGIN_PAYLOAD, "benchmark", ":(exclude)benchmark/runs")
# The exit status of a run on a runtime its scenario does not list. Nothing
# was made or spent, and the benchmark workflow reads it as a scenario to
# skip, not one that failed.
NOT_LISTED = 7
# The exit status of a preflight that failed a check. Nothing was run and
# no paid endpoint was called.
PREFLIGHT_FAILED = 8
# The exit status of a rehearsal that did not prove the pipeline to its end:
# the run's spend cap cut it short, or a step it exists to prove did not happen.
REHEARSAL_UNPROVEN = 9
# How long a command the harness runs where the subject runs may take: a
# checkpoint, the archive.
HELPER_TIMEOUT_S = 600
# The name a subject that runs no phases of its own gives its one session.
ONE_PHASE = "subject"
# The session the harness's own commands run in, a name no phase can take.
HARNESS_SESSION = "_harness"
# One run of a subject is an anecdote, so a run repeats it this many times
# when neither `--repeat` nor the scenario's `repeat` says otherwise.
REPEAT = 3
# What a skill subject's Claude Code runs with, on every runtime: its
# subagents and the commands it starts run in the foreground, so each
# result returns to the session that asked for it. In print mode, a task
# left in the background ends the main agent's turn, and the session
# never gives it back. The runtimes carry the subject's environment each
# their own way, so these reach it through `env` in its command, which
# every runtime runs as it is.
SUBJECT_ENV = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}


def subject_keys(scn: S.Scenario) -> list[str]:
    """The key names the subject's command reads. A `qa` subject runs no command."""
    return [] if scn.kind == "qa" else list(RT.SUBJECT_KEYS)


def subject_env(scn: S.Scenario, source: dict[str, str] | None = None) -> dict[str, str]:
    """The environment the subject is spawned from: no judge's key is ever in it.

    `claude -p` reads ANTHROPIC_API_KEY. The subject's value for it comes
    from SUBJECT_ANTHROPIC_API_KEY, a key of its own, so no judge's key is
    copied into the subject's environment, on any runtime.
    """
    source = dict(os.environ) if source is None else source
    env = RT.passthrough_env(PASSTHROUGH, source)
    for name in subject_keys(scn):
        value = source.get(RT.SUBJECT_KEYS[name])
        if value:
            env[name] = value
    return RT.scrub(env, source)[0]


def new_run_dir(out: Path, scenario: str) -> tuple[str, Path]:
    """A run folder no other run has, and its name.

    The name starts with the second and the scenario, so the folders sort
    by time. A random suffix tells apart two runs of one scenario started
    in the same second, and the folder is created only when it is not
    there yet, so no run ever writes into another's.
    """
    out.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{scenario}-{uuid.uuid4().hex[:8]}"
        try:
            (out / run_id).mkdir()
        except FileExistsError:
            continue
        return run_id, out / run_id


def git_sha(path: Path) -> str:
    """The commit of a checkout, or an empty string when it is not one."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def static_versions(target: Path | None, staged: Path | None, expected: Path | None) -> dict:
    """What a run can name before anything runs: the checkout, the target, and the expected findings.

    The target is hashed as the runtime staged it, the copy the subject
    reads; its recorded path is the one it was staged from.
    """
    return {
        "checkout": V.checkout(ROOT, VERSIONED),
        "claude_code": None,
        "image": None,
        "target": V.content(target, ROOT, staged),
        "expected": V.content(expected, ROOT),
    }


def probe_versions(scn: S.Scenario, rt: RT.BaseRuntime, claude: str) -> tuple[dict, list[str]]:
    """What only the runtime can answer: its Claude Code and its image. Returns them and the notes they call for.

    Claude Code is asked inside the runtime, because a container or another
    machine carries its own. Only a skill runs it. The image is asked only
    for a subject that runs a command: a qa subject never runs in it.
    """
    found: dict = {"claude_code": None, "image": rt.image_version() if scn.kind != "qa" else None}
    notes: list[str] = []
    if scn.kind == "skill":
        found["claude_code"] = rt.probe([claude, "--version"])
        if found["claude_code"] is None:
            notes.append(f"`{claude} --version` answered nothing in the {rt.name} runtime; the run does not know its Claude Code")
    if found["image"] is not None and found["image"]["id"] is None:
        notes.append(f"the image {found['image']['name']} has no id the engine reports")
    # The vm runtime copies what this machine staged, so the versions
    # describe what the subject read. An override replaces a copy with a
    # path the operator placed there, which no version describes.
    if isinstance(rt, RT.VmRuntime) and rt.plugin is not None and rt.vm.remote_plugin:
        notes.append(
            f"the plugin on the other machine is the one at {rt.vm.remote_plugin}, which the runtime config names; "
            "versions.checkout describes this machine's checkout, not that one"
        )
    if isinstance(rt, RT.VmRuntime) and rt.target is not None and rt.vm.remote_target:
        notes.append(
            f"the target on the other machine is the one at {rt.vm.remote_target}, which the runtime config names; "
            "versions.target and the judges' evidence describe the copy staged here, not that one"
        )
    return found, notes


def plugin_name(root: Path) -> str:
    """The plugin name of the checkout, which is how a skill is addressed."""
    manifest = root / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            return str(json.loads(manifest.read_text(encoding="utf-8")).get("name") or "swe-guidelines")
        except json.JSONDecodeError:
            pass
    return "swe-guidelines"


def subject_prompt(scn: S.Scenario, target: str | None) -> str:
    """The prompt with the target in it, as the subject sees the target.

    `{target}` in the prompt is replaced with the path. A prompt that does
    not name it gets one sentence saying where the target is. The subject
    runs in its own empty workspace, so a target it is not told about is
    a target it never reads.
    """
    prompt = scn.subject.prompt
    if "{target}" in prompt:
        if not target:
            raise S.ScenarioError(f"scenario {scn.name}: the prompt names {{target}} and the run has no target")
        return prompt.replace("{target}", target)
    if target:
        return f"{prompt}\n\nThe checkout to work on is at {target}. Read it; do not change it."
    return prompt


def subject_model(scn: S.Scenario, flag: str | None, matrix: dict[str, Any]) -> str | None:
    """The model the subject runs on: the flag, else the scenario's, else the matrix's first.

    A skill runs `claude -p`, whose default model moves under the run; a
    score is only comparable to another on the same model, so the model is
    always pinned. A command subject runs what its argv says, and has no
    model unless the flag names one.
    """
    if flag:
        return flag
    if scn.subject.model:
        return scn.subject.model
    if scn.kind == "command":
        return None
    provider = "anthropic" if scn.kind == "skill" else P.name(P.parse(scn.subject.provider or "anthropic"))
    return J.models_for(matrix, provider)[0] or None


def phases_of(scn: S.Scenario) -> list[S.Phase]:
    """The sessions a skill subject runs: its phases, or one made of its prompt and its bounds."""
    if scn.subject.phases:
        return list(scn.subject.phases)
    return [
        S.Phase(
            name=ONE_PHASE,
            prompt=scn.subject.prompt,
            max_turns=scn.subject.max_turns,
            max_usd=scn.subject.max_usd or 0.0,
            timeout_s=scn.subject.timeout_s,
        )
    ]


def phase_prompt(scn: S.Scenario, phase: S.Phase, target: str | None) -> tuple[str, bool]:
    """A phase's prompt as the subject reads it, and whether it is given the target.

    A phase is told of the target only where its prompt names `{target}`,
    and nothing is added to its prompt but the handoff note's sentence
    when it is hinted. So a phase that is not told of the target, or of
    the note, reads only its prompt and what is in its working folder.
    """
    prompt = phase.prompt
    reads = "{target}" in prompt
    if reads:
        if not target:
            raise S.ScenarioError(f"scenario {scn.name}: phase {phase.name} names {{target}} and the run has no target")
        prompt = prompt.replace("{target}", target)
    if phase.hint:
        start = scn.subject.output if phase.cwd == "output" and scn.subject.output else "."
        prompt = f"{prompt.rstrip()}\n\n{PH.HINT.format(path=posixpath.relpath(PH.HANDOFF, start))}"
    return prompt, reads


def phase_argv(
    scn: S.Scenario,
    phase: S.Phase,
    name: str,
    plugin: str | None,
    target: str | None,
    claude: str = "claude",
    model: str | None = None,
    resume: str | None = None,
) -> list[str]:
    """The `claude -p` one session of a skill subject runs, with its bounds.

    The session writes every turn to stdout as a JSON line
    (`--output-format stream-json --verbose`). Claude Code holds the spend
    cap itself, and a turn cap only when the phase names one; it runs
    under `env` with `SUBJECT_ENV`.
    A resumed session names the session it continues. A phase that starts
    in the output folder is started there by a shell, since the runtime
    starts every command in the workspace.
    """
    if scn.subject.phases:
        prompt, reads = phase_prompt(scn, phase, target)
    else:
        # The workspace is the subject's working directory; the target is outside it.
        prompt, reads = f"/{name}:{scn.subject.skill} {subject_prompt(scn, target)}".strip(), bool(target)
    argv = [
        "env",
        *(f"{name}={value}" for name, value in SUBJECT_ENV.items()),
        claude,
        "-p",
        prompt,
        "--plugin-dir",
        str(plugin),
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-budget-usd",
        f"{phase.max_usd:g}",
    ]
    # A turn count is no bound; a phase that names one passes it on.
    if phase.max_turns is not None:
        argv += ["--max-turns", str(phase.max_turns)]
    if model:
        argv += ["--model", model]
    if reads and target:
        argv += ["--add-dir", target]
    if scn.subject.allowed_tools:
        argv += ["--allowedTools", ",".join(scn.subject.allowed_tools)]
    if resume:
        argv += ["--resume", resume]
    if phase.cwd == "output" and scn.subject.output:
        argv = ["sh", "-c", PH.IN_FOLDER, "sh", scn.subject.output, *argv]
    return argv


def subject_argv(
    scn: S.Scenario, name: str, plugin: str | None, target: str | None, claude: str = "claude", model: str | None = None
) -> list[str]:
    """The command a subject of each kind runs; for a skill, its first session. `qa` runs no command.

    `plugin` and `target` are paths as the subject sees them, which the
    runtime answers: this machine's paths on the host, the mount points
    in a container, the copies in the run's folder on another machine.
    """
    if scn.kind == "command":
        return [w.replace("{target}", target or "").replace("{plugin}", plugin or "") for w in scn.subject.argv]
    if scn.kind == "qa":
        return []
    return phase_argv(scn, phases_of(scn)[0], name, plugin, target, claude, model)


def envelope(stdout: str) -> dict[str, Any] | None:
    """The result of a `claude -p` session: its last `result` line, else the whole output read as one JSON object."""
    found = PH.final_result(stdout.split("\n"))
    if found is not None:
        return found
    try:
        data = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def read_envelope(stdout: str) -> tuple[str, list[str], bool]:
    """The answer, the models, and the error flag of a `claude -p` session's result.

    The models are the keys of `modelUsage`: the models that answered, read
    back so a run records what ran and not only what it asked for. A
    result that a bound ended carries no answer. Output with no result is
    the answer as it is, with no model and no error.
    """
    data = envelope(stdout)
    if data is None or not (isinstance(data.get("result"), str) or data.get("type") == "result"):
        return stdout, [], False
    usage = data.get("modelUsage")
    models = sorted(str(m) for m in usage) if isinstance(usage, dict) else []
    answer = data["result"] if isinstance(data.get("result"), str) else ""
    return answer, models, data.get("is_error") is True


ENVELOPE_TOKENS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def token_count(value: Any) -> int | None:
    """A token count as an int, or None when the value is not one."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def envelope_thinking(usage: Any, models: Any) -> int | None:
    """The thinking tokens a session's result reports, or None when it reports none.

    The result's usage names them under `output_tokens_details`; each
    model in `modelUsage` names its own as `thinkingTokens`, so the sum is
    the fallback when the first is absent.
    """
    details = usage.get("output_tokens_details") if isinstance(usage, dict) else None
    total = token_count(details.get("thinking_tokens")) if isinstance(details, dict) else None
    if total is not None:
        return total
    per_model = (
        [token_count(m.get("thinkingTokens")) for m in models.values() if isinstance(m, dict)] if isinstance(models, dict) else []
    )
    found = [n for n in per_model if n is not None]
    return sum(found) if found else None


def read_envelope_spend(stdout: str) -> tuple[dict[str, int], float | None]:
    """The tokens and the cost in US dollars of a `claude -p` session's result.

    Claude Code prices its own run, caching included, as `total_cost_usd`;
    that figure is the subject's cost. `input_tokens` is every input token,
    cached or not, with the cached ones also named on their own.
    `output_tokens` already counts the thinking, and `reasoning_tokens`
    names it: `usage.output_tokens_details.thinking_tokens`, else the sum of
    `thinkingTokens` over `modelUsage`, and no key when the result reports
    neither. Output with no result spent nothing the run can see: no
    tokens, cost None.
    """
    data = envelope(stdout)
    if data is None:
        return {}, None
    raw = data.get("usage")
    counts = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items() if isinstance(v, int) and not isinstance(v, bool)}
    usage: dict[str, int] = {}
    if counts:
        usage = {
            "input_tokens": sum(counts.get(k, 0) for k in ENVELOPE_TOKENS),
            "output_tokens": counts.get("output_tokens", 0),
            "cache_read_input_tokens": counts.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": counts.get("cache_creation_input_tokens", 0),
        }
        thinking = envelope_thinking(raw, data.get("modelUsage"))
        if thinking is not None:
            usage["reasoning_tokens"] = thinking
    cost = data.get("total_cost_usd")
    return usage, (round(float(cost), 6) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None)


def stdout_of(stream_path: Path) -> str:
    """Everything the subject wrote to stdout, in order."""
    return "\n".join(r["line"] for r in CliStream.read(stream_path) if r.get("s") == "out")


def context_text(scn: S.Scenario) -> str:
    """The context files of a `qa` subject, each under its own heading.

    A path is read from the scenario file's folder, as every path of a
    scenario is. A file that is not there is a ScenarioError: a subject
    that silently lost its context answers a different question.
    """
    out = ""
    for value in scn.subject.context:
        file = scn.resolve(value)
        if file is None or not file.is_file():
            raise S.ScenarioError(f"scenario {scn.name}: subject.context {value!r} is not a file ({file})")
        out += f"\n\n## Context: {file.name}\n\n{file.read_text(encoding='utf-8')}"
    return out


def run_subject_qa(
    scn: S.Scenario, streams: CliStream, env: dict[str, str], matrix: dict[str, Any], effort: str, model: str
) -> tuple[RT.ExitStatus, str, dict[str, int]]:
    """A `qa` subject: one provider model answers the prompt itself, at the effort the run names, within `timeout_s`.

    Returns the exit status, the answer, and the answer's usage.
    """
    provider = P.parse(scn.subject.provider or "anthropic")
    key = P.key(provider, env)
    started = time.monotonic()
    if not key:
        streams.note(f"[qa] no key for {P.name(provider)}")
        return RT.ExitStatus(code=2, duration_s=time.monotonic() - started), "", {}
    prompt = scn.subject.prompt + context_text(scn)
    streams.note(f"[qa] {P.name(provider)} {model}")
    try:
        text, usage = J.ask(
            provider, model, prompt, key, J.effort_for(matrix, P.name(provider), effort), timeout_s=scn.subject.timeout_s
        )
    except Exception as exc:
        streams.note(f"[qa] {type(exc).__name__}: {exc}")
        return RT.ExitStatus(code=1, duration_s=time.monotonic() - started), "", {}
    for line in text.split("\n"):
        streams.write("out", line)
    streams.note(f"[qa] usage {json.dumps(usage)}")
    return RT.ExitStatus(code=0, duration_s=time.monotonic() - started), text, usage


def collect_files(rt: RT.BaseRuntime, files: list[Path], art_dir: Path, index: int) -> tuple[list[str], list[str]]:
    """Copy the collected files of one repeat, byte for byte, and return their paths and their text for the judges.

    A file keeps its path inside the workspace, under `workspace/` in the
    repeat's artifact folder: two files of the same name in two folders
    stay two files, and none of them overwrites the harness's own
    `answer.md` or `judge-prompt.md`. A file with a NUL byte in its first
    8 KiB is binary, as git counts it: it is copied as it is, and the
    judges are told its size, not its bytes.
    """
    paths: list[str] = []
    parts: list[str] = []
    for file in files:
        rel = file.relative_to(rt.workspace).as_posix()
        copy = art_dir / "workspace" / rel
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, copy)
        data = copy.read_bytes()
        paths.append(f"artifacts/{index}/workspace/{rel}")
        if b"\0" in data[:8192]:
            parts.append(f"### File: {rel}\n\n(binary, {len(data)} bytes; not shown)")
        else:
            parts.append(f"### File: {rel}\n\n{data.decode('utf-8', errors='replace')}")
    return paths, parts


def keep_archive(zip_file: Path, art_dir: Path, run_dir: Path, commit: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Move the output's zip into the repeat's artifacts, redacted, with its manifest; its record, and a note if it is not whole.

    A zip redaction cannot read is replaced by a line that says so, and
    its record says it holds no file. An error here never ends the run:
    the results are written either way.
    """
    kept = art_dir / A.ZIP
    try:
        shutil.move(str(zip_file), str(kept))
        _, places = X.redact_file(kept, X.key_values())
        A.write_manifest(kept)
        record = {**A.record(kept, run_dir), "commit": commit}
    except (*X.READ_ERRORS, MemoryError) as exc:
        return None, f"the output's zip could not be kept: {type(exc).__name__}: {exc}"
    unread = [p for p in places if "(not scanned:" in p]
    if not A.readable(kept):
        why = f" and was replaced by a line that says so: {unread[0]}" if unread else ""
        return record, f"the output's zip does not open as a zip{why}; its record holds no file"
    if unread:
        return record, f"part of the output's zip could not be scanned for keys and was replaced: {', '.join(unread)}"
    return record, None


@dataclasses.dataclass
class Budget:
    """The run's spend cap over the subject and the judges, in US dollars, and what the run has spent."""

    cap: float | None
    spent: float = 0.0

    def reached(self) -> bool:
        return self.cap is not None and self.spent >= self.cap

    def covers(self, need: float | None) -> bool:
        """Whether what is left of the cap covers `need`; a run with no cap, or a need no one knows, is covered."""
        # A hair of tolerance, so a cap that is the sum of the phases' caps covers them.
        return self.cap is None or need is None or self.cap - self.spent + 1e-9 >= need

    def says(self) -> str:
        return f"the run's spend cap of ${self.cap:g} was reached at ${self.spent:.4f}"


@dataclasses.dataclass
class Plan:
    """What every session of a skill subject runs with, as the subject sees it."""

    name: str
    plugin: str | None
    target: str | None
    claude: str
    model: str | None
    prices: dict[str, dict[str, float]]
    budget: Budget
    env: dict[str, str]


@dataclasses.dataclass
class SkillRepeat:
    """One repeat of a skill subject: how it ended, its answer, and what its sessions did."""

    status: RT.ExitStatus
    answer: str
    models: list[str]
    usage: dict[str, int]
    cost: float | None
    phases: list[dict[str, Any]]
    commit: str | None = None
    gates: list[dict[str, Any]] | None = None
    notes: list[str] = dataclasses.field(default_factory=list)
    # The phases the run's spend cap kept from running, when it cut the repeat short.
    cut_short: list[str] | None = None
    # Whether the archive command succeeded where the subject ran; None when there was no checkpoint to archive.
    archived: bool | None = None
    # The phase that ended the run early, why, and the phases after it, which did not run: `no_tree`, the
    # output folder held no file after it; `incomplete`, its session ended with an Agent call unanswered.
    ended_early: dict[str, Any] | None = None


def harness_run(
    rt: RT.BaseRuntime, harness: CliStream, plan: Plan, argv: list[str], timeout_s: int = HELPER_TIMEOUT_S
) -> tuple[RT.ExitStatus, list[str]]:
    """Run a command of the harness's where the subject runs, in its workspace, with no key; its status and stdout.

    It gets a HOME of its own, never a session's: no phase's name starts with `_`.
    """
    rt.use_session(HARNESS_SESSION)
    mark = harness.count
    env = {k: v for k, v in plan.env.items() if k not in RT.SUBJECT_KEYS}
    status = rt.run(argv, rt.workspace, env, harness, timeout_s=timeout_s)
    return status, [r["line"] for r in CliStream.read(harness.path)[mark:] if r.get("s") == "out"]


def checkpoint(
    rt: RT.BaseRuntime, harness: CliStream, plan: Plan, folder: str, number: int, label: str
) -> tuple[str | None, str | None, bool | None]:
    """Commit the output folder's tree as the `number`th checkpoint.

    Returns the commit's id, or why there is none, and whether the folder
    holds a file the checkpoint commits: False when there is no folder or
    its tree is empty, None when git failed and no one can say. The label
    goes to the harness's own stream only: the commit says `checkpoint`
    and no more, so nothing in the repository names a phase.
    """
    harness.note(f"[checkpoint {number}] {label}")
    status, out = harness_run(rt, harness, plan, ["sh", "-c", PH.CHECKPOINT, "sh", folder, str(number)])
    commit = next((line.strip() for line in reversed(out) if re.fullmatch(r"[0-9a-f]{40,64}", line.strip())), None)
    if status.ok and commit:
        return commit, None, PH.EMPTY not in (line.strip() for line in out)
    if status.code == 3:
        return None, f"there is no output folder {folder} to commit", False
    return None, f"the checkpoint commit failed (exit {status.code}); see streams/harness.jsonl", None


def run_skill(
    scn: S.Scenario, rt: RT.BaseRuntime, streams: CliStream, harness: CliStream | None, plan: Plan, index: int
) -> SkillRepeat:
    """Run a skill subject's sessions in one repeat: its phases, or its one prompt.

    Each phase runs with its bounds, watched through the stream. After
    each, the output folder is committed. A phase that fails ends the
    repeat; one that hits a bound ends it only when it says so. A phase
    whose session ended with an Agent call unanswered is `incomplete`,
    and it ends the repeat, as does one after which the output folder
    holds no file; the repeat fails. The run's spend cap is checked
    before each phase.
    After the last phase, the harness archives the last commit and runs
    the gates on the tree.
    """
    folder = scn.subject.output
    phased = bool(scn.subject.phases)
    records: list[dict[str, Any]] = []
    notes: list[str] = []
    answers: list[str] = []
    models: set[str] = set()
    usage: dict[str, int] = {}
    cost = 0.0
    priced = False
    failed: RT.ExitStatus | None = None
    last = RT.ExitStatus(code=0)
    duration = 0.0
    sessions: dict[str, str | None] = {}
    homes: dict[str, str] = {}
    # Each session's running total so far, as its last result reported it or as the harness counted it,
    # and each model's cost in it, as the result's modelUsage gives it.
    running: dict[str, tuple[float, dict[str, int]]] = {}
    running_models: dict[str, dict[str, float]] = {}
    commit: str | None = None
    before: S.Phase | None = None
    cut_short: list[str] | None = None
    ended_early: dict[str, Any] | None = None
    every = phases_of(scn)
    for number, phase in enumerate(every, start=1):
        if plan.budget.reached():
            cut_short = [p.name for p in every[number - 1 :]]
            notes.append(
                f"repeat {index}: {plan.budget.says()}; phase {phase.name} and after did not run, and the repeat is not judged"
            )
            break
        home = phase.name if phase.session == "fresh" or before is None else homes[before.name]
        homes[phase.name] = home
        # A subject that builds an output shares its repeat with the harness's commands, so its HOME is named too.
        rt.use_session(home if phased or folder else None)
        resume = sessions.get(before.name) if phase.session == "resume" and before is not None else None
        if phase.session == "resume" and not resume:
            failed = RT.ExitStatus(code=2)
            notes.append(f"repeat {index}: phase {phase.name} resumes a session the phase before it did not report")
            records.append({"name": phase.name, "session": phase.session, "status": "failed", "capped": None})
            break
        if phase.hint:
            rt.show(PH.HANDOFF)
        argv = phase_argv(scn, phase, plan.name, plan.plugin, plan.target, plan.claude, plan.model, resume)
        watch = PH.Watch(phase.max_usd, plan.prices, scn.subject.gates, phase.max_gate_reruns)
        mark = streams.count
        turns = f"{phase.max_turns} turns, " if phase.max_turns is not None else ""
        streams.note(f"[phase {phase.name}] {phase.session}, at most {turns}${phase.max_usd:g} and {phase.timeout_s} s")
        streams.listener = watch.feed
        try:
            status = rt.run(argv, rt.workspace, plan.env, streams, timeout_s=phase.timeout_s, stop=watch.stop)
        finally:
            streams.listener = None
        lines = [r["line"] for r in CliStream.read(streams.path)[mark:] if r.get("s") == "out"]
        result = PH.final_result(lines)
        text = "\n".join(lines)
        answer, found, is_error = read_envelope(text) if result is not None else ("", [], False)
        spent_usage, spent = read_envelope_spend(text) if result is not None else ({}, None)
        if is_error:
            status = dataclasses.replace(status, is_error=True)
        # Money and time are the bounds: a session its timeout stopped ended at a bound, as one its spend cap did.
        cap = watch.capped or PH.cap_of(result) or ("time" if status.timed_out else None)
        outcome = "capped" if cap else "ok" if status.ok else "failed"
        if outcome == "ok" and watch.pending:
            # The session ended while a subagent it asked for had not answered: its work is not done.
            outcome = "incomplete"
        streams.note(f"[phase {phase.name}] {outcome}" + (f" at its {cap} cap" if outcome == "capped" else ""))
        if phase.hint:
            rt.hide(PH.HANDOFF)
        estimated = watch.estimated_usd
        # A resumed session's result carries the session's running total, the
        # phases before it included, so this phase's own is what it adds to it.
        earlier_cost, earlier_usage = running.get(homes[phase.name], (0.0, {})) if resume else (0.0, {})
        earlier_models = running_models.get(homes[phase.name], {}) if resume else {}
        by_model = PH.model_costs(result)
        if by_model:
            running_models[home] = dict(by_model)
            by_model = {m: round(max(c - earlier_models.get(m, 0.0), 0.0), 6) for m, c in by_model.items()}
        if spent is not None:
            running[home] = (spent, dict(spent_usage))
            spent = round(max(spent - earlier_cost, 0.0), 6)
            spent_usage = {k: max(v - earlier_usage.get(k, 0), 0) for k, v in spent_usage.items()}
        else:
            own_usage = watch.usage()
            running[home] = (
                earlier_cost + estimated,
                {k: earlier_usage.get(k, 0) + own_usage.get(k, 0) for k in {*earlier_usage, *own_usage}},
            )
        phase_cost = spent if spent is not None else estimated
        plan.budget.spent += phase_cost
        cost += phase_cost
        priced = priced or spent is not None
        if spent is None and estimated:
            notes.append(
                f"repeat {index}: phase {phase.name} reported no cost; its spend is the harness's estimate, ${estimated:.4f}"
            )
        for name, value in (spent_usage or watch.usage()).items():
            usage[name] = usage.get(name, 0) + value
        models.update(found)
        sessions[phase.name] = PH.session_id(lines)
        if answer.strip():
            answers.append(f"## {phase.name}\n\n{answer}" if phased else answer)
        record: dict[str, Any] = {
            "name": phase.name,
            "session": phase.session,
            "status": outcome,
            "capped": cap if outcome == "capped" else None,
            "exit_status": status.as_dict(),
            "session_id": sessions[phase.name],
            "turns": result.get("num_turns") if result and isinstance(result.get("num_turns"), int) else None,
            "models": sorted(found),
            "cost_usd": spent,
            "estimated_usd": estimated,
            "wall_s": round(status.duration_s, 3),
        }
        if by_model:
            # The models the session used and what each cost, so the run says what it measured.
            record["model_cost_usd"] = by_model
        if scn.subject.gates:
            record["gate_runs"] = watch.gate_runs()
        if watch.pending:
            record["pending_agents"] = list(watch.pending)
        if watch.unpriced:
            record["unpriced"] = sorted(watch.unpriced)
        holds: bool | None = None
        if folder and harness is not None:
            made, why, holds = checkpoint(rt, harness, plan, folder, number, f"after {phase.name}, {outcome}")
            record["checkpoint"] = made
            commit = made or commit
            if why:
                notes.append(f"repeat {index}: after phase {phase.name}, {why}")
        records.append(record)
        duration += status.duration_s
        last = status
        if outcome == "failed":
            failed = status
            if phased:
                notes.append(f"repeat {index}: phase {phase.name} failed; the phases after it did not run")
            break
        not_run = [p.name for p in every[number:]]
        left = f"; {', '.join(not_run)} did not run" if not_run else ""
        if outcome == "incomplete":
            # The phases after it would build on work the session never finished.
            ended_early = {"phase": phase.name, "reason": "incomplete", "not_run": not_run}
            calls = ", ".join(f"{a['id']} ({a['description']})" if a["description"] else a["id"] for a in watch.pending)
            notes.append(
                f"repeat {index}: phase {phase.name} ended with Agent calls that had no result: {calls}{left}, "
                "and the repeat is not judged"
            )
            break
        if holds is False:
            # Nothing to build on, archive, gate, or judge: the phases after it would spend on an empty tree.
            ended_early = {"phase": phase.name, "reason": "no_tree", "not_run": not_run}
            notes.append(f"repeat {index}: phase {phase.name} left no file in {folder}{left}, and the repeat is not judged")
            break
        if outcome == "capped" and phase.on_cap == "stop":
            notes.append(f"repeat {index}: phase {phase.name} ended at its {cap} cap, and it stops the repeat there")
            break
        before = phase
    gates: list[dict[str, Any]] | None = None
    archived_ok: bool | None = None
    if folder and harness is not None:
        if commit is None:
            notes.append(f"repeat {index}: no commit of {folder} to archive, and no tree to run the gates on")
        else:
            archived, _ = harness_run(rt, harness, plan, ["sh", "-c", PH.ARCHIVE_SCRIPT, "sh", folder, PH.ARCHIVE, commit])
            archived_ok = archived.ok
            if not archived.ok:
                notes.append(f"repeat {index}: the archive of {folder} failed (exit {archived.code})")
            gates = []
            for gate in scn.subject.gates:
                harness.note(f"[gate] {gate}")
                ran, _ = harness_run(rt, harness, plan, ["sh", "-c", PH.GATE, "sh", folder, gate], scn.subject.gate_timeout_s)
                gates.append(
                    {
                        "command": gate,
                        "passed": ran.ok,
                        "exit_code": ran.code,
                        "timed_out": ran.timed_out,
                        "duration_s": round(ran.duration_s, 3),
                    }
                )
    # One session ends the repeat as it ended; phases end it failed only when one failed.
    status = (failed or RT.ExitStatus(code=0, duration_s=duration)) if phased else last
    return SkillRepeat(
        status=status,
        answer="\n\n".join(answers),
        models=sorted(models),
        usage=usage,
        cost=round(cost, 6) if priced or cost else None,
        phases=records,
        commit=commit,
        gates=gates,
        notes=notes,
        cut_short=cut_short,
        archived=archived_ok,
        ended_early=ended_early,
    )


def repeat_need(scn: S.Scenario) -> float | None:
    """What one repeat's sessions may spend: the sum of the caps of every phase it runs; None for a subject with none."""
    return round(sum(p.max_usd for p in phases_of(scn)), 6) if scn.kind == "skill" else None


def subject_prices(matrix: dict[str, Any]) -> dict[str, dict[str, float]]:
    """The price of every Anthropic model the matrix prices, for the estimate a phase is watched by."""
    names = matrix.get("anthropic", {}).get("prices", {})
    return {m: price for m in names if (price := J.price_for(matrix, "anthropic", m)) is not None}


def planned_phases(
    scn: S.Scenario, name: str, plugin: str | None, target: str | None, claude: str, model: str | None
) -> list[dict[str, Any]]:
    """Each phase of a subject in phases as `run.json` records it, with the command it will run.

    A resumed session's id is known only once the phase before it has
    run, so the command names it by that phase.
    """
    out = []
    before: S.Phase | None = None
    for phase in scn.subject.phases:
        resume = f"<the session of {before.name}>" if phase.session == "resume" and before else None
        out.append({**phase.as_dict(), "argv": phase_argv(scn, phase, name, plugin, target, claude, model, resume)})
        before = phase
    return out


def subject_bounds(scn: S.Scenario) -> dict[str, Any]:
    """What `results.json` records of a skill subject's bounds and output, besides its prompt and turns."""
    if scn.kind != "skill":
        return {}
    out: dict[str, Any] = {"max_usd": scn.subject.max_usd, "output": scn.subject.output, "gates": list(scn.subject.gates)}
    if scn.subject.phases:
        out["phases"] = [p.as_dict() for p in scn.subject.phases]
    if scn.subject.groups:
        out["groups"] = S.taken_groups(scn)
    return out


def describe_subject(scn: S.Scenario, argv: list[str]) -> str:
    """The sentence the judge reads about what made the artifact."""
    if scn.kind == "skill" and scn.subject.phases:
        steps = "\n\n".join(f"{n}. {p.name} ({p.session} session):\n\n{p.prompt}" for n, p in enumerate(scn.subject.phases, 1))
        return f"A Claude Code subject with the guideline's skills ran these phases, in order:\n\n{steps}"
    if scn.kind == "skill":
        return f"The `{scn.subject.skill}` skill of the guideline answered this prompt:\n\n{scn.subject.prompt}"
    if scn.kind == "command":
        return "This command ran:\n\n" + " ".join(argv)
    return f"A model answered this prompt directly:\n\n{scn.subject.prompt}"


def judge_agentic(
    scn: S.Scenario,
    sandbox: Path,
    staged: RF.Staged,
    art_dir: Path,
    run_dir: Path,
    index: int,
    argv: list[str],
    flags: P.Provider,
    effort: str,
    matrix: dict[str, Any],
    notes: list[str],
) -> list[RF.Judged]:
    """One repeat's agentic judgements: the output staged as a root beside the references, and every judge's loop.

    The output root is built in the run's sandbox from the repeat's
    artifacts, and the task every judge gets is kept as the repeat's
    `judge-prompt.md`.
    """
    output = sandbox / "judged" / str(index) / S.OUTPUT_ROOT
    holds, why = RF.stage_output(art_dir, output, archived=bool(scn.subject.output))
    if why:
        notes.append(f"repeat {index}: {why}")
    refs = scn.judges.references
    prompt = RF.build_prompt(scn.rubric, describe_subject(scn, argv), holds, refs, staged.versions)
    (art_dir / "judge-prompt.md").write_text(prompt, encoding="utf-8")
    roots = {S.OUTPUT_ROOT: output, **staged.roots}
    return RF.judge_all(
        flags, prompt, roots, run_dir / "judgements", index, effort, scn.judges.weights, scn.judges.budget, matrix
    )


def keep_judgements(run_dir: Path, index: int, judgements: list[R.AnyJudgement]) -> None:
    """Write each judgement of a repeat to `judgements/<repeat>-<provider>.json`, with its answer, and print its score."""
    for j in judgements:
        record = j.as_dict()
        if isinstance(j, RF.Judged):
            record["answer"] = j.answer
        else:
            record["raw"] = j.raw
        (run_dir / "judgements").mkdir(parents=True, exist_ok=True)
        (run_dir / "judgements" / f"{index}-{j.provider}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"  repeat {index} {j.provider:10} {j.model:24} {j.status:8} score {said(j)}")


def write_record(run: R.RunResult, run_dir: Path) -> tuple[dict[str, Any], list[str]]:
    """Write `results.json` and `report.md`, print the summary, and return the results and what the schema refused."""
    data = R.write_results(run, run_dir / "results.json")
    problems = R.validate(data, SCHEMA)
    if problems == [R.UNVALIDATED]:  # no validator here: say so, and claim nothing
        print(R.UNVALIDATED, file=sys.stderr)
        problems = []
    if problems:
        print("results.json does not match the schema:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
    R.write_report(run, run_dir / "report.md")

    summary = data["summary"]
    for provider, stats in summary["per_provider"].items():
        print(f"{provider:10} mean {stats['mean']} over {stats['n']} judgement(s), stdev {stats['stdev']}")
    for name, stats in (summary.get("references") or {}).items():
        gaps = ", ".join(f"{count} {severity}" for severity, count in stats["gaps"].items())
        print(f"{name:10} weight {stats['weight']:g}, mean {stats['mean']} over {stats['n']} score(s), gaps: {gaps}")
    if summary["self_judged"]:
        print(f"note: {summary['self_judged']}")
    spent = data["spend"]
    unpriced = f" (at least; no price for {', '.join(spent['unpriced'])})" if spent["unpriced"] else ""
    print(f"spend      ${spent['total_usd']:.4f}{unpriced}")
    for fallback in summary["fallbacks"]:
        print(f"{fallback['provider']:10} {fallback['to']} answered in place of {fallback['from']} {fallback['count']} time(s)")

    for skipped in summary["skipped"]:
        print(f"{skipped['provider']:10} not answered: {skipped['reason']}")
    if run.rehearsal:
        print(RH.says(run.rehearsal))
    print(f"report: {run_dir / 'report.md'}")
    return data, problems


def said(j: R.AnyJudgement) -> str:
    """A judgement's score as the console shows it: the verdict's, or the weighted one with each reference's."""
    if isinstance(j, RF.Judged):
        if j.score is None:
            return "-"
        return f"{j.score} (" + ", ".join(f"{n} {e['score']}" for n, e in j.references.items()) + ")"
    return str(j.verdict.score) if j.verdict else "-"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark/run.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "judge", "list", "redact"],
        help="run a scenario, judge a run's archived output again, list what there is, "
        "or redact every key from the run folders under --out",
    )
    parser.add_argument("--scenario", help="scenario name or path")
    parser.add_argument(
        "--source", default=None, help="for judge: the run folder whose archived output is judged again, with no subject run"
    )
    parser.add_argument(
        "--with",
        dest="groups",
        action="append",
        default=None,
        metavar="GROUP",
        help="run an optional group of the scenario's phases as well; repeat it for more; a run takes none by default",
    )
    parser.add_argument("--providers", default=None, help="bit flag (3, 7, 15) or names (anthropic,openai)")
    parser.add_argument("--effort", default=None, choices=list(J.EFFORTS), help="judge effort")
    parser.add_argument(
        "--repeat",
        type=int,
        default=None,
        help=f"how many times the subject runs; the scenario's repeat, else {REPEAT}, since one run is an anecdote",
    )
    parser.add_argument(
        "--runtime",
        default=None,
        choices=list(RT.NAMES),
        help="where the subject runs: one of the scenario's runtimes, its first by default",
    )
    parser.add_argument("--runtime-config", default=None, help="JSON or YAML file with the runtime's settings")
    parser.add_argument("--target", default=None, help="a checkout the subject works on")
    parser.add_argument(
        "--out", default=None, help="folder the run folders are written under; benchmark/runs, and for judge the source's folder"
    )
    parser.add_argument(
        "--claude", default=os.environ.get("CLAUDE_BIN", "claude"), help="the Claude Code binary the subject runs"
    )
    parser.add_argument(
        "--subject-model", default=None, help="the model the subject runs on; the scenario's, else the matrix's first"
    )
    parser.add_argument(
        "--max-spend-usd",
        type=float,
        default=None,
        help="once the run has spent this many US dollars, subject and judges, it starts no further repeat or phase; "
        "the scenario's max_spend_usd when not given",
    )
    resolve_only = parser.add_mutually_exclusive_group()
    resolve_only.add_argument("--dry-run", action="store_true", help="resolve everything, write run.json, call nothing")
    resolve_only.add_argument(
        "--preflight",
        action="store_true",
        help="resolve as --dry-run does, then check what the run needs and stop at the first failure; call no paid endpoint",
    )
    parser.add_argument(
        "--rehearsal",
        action="store_true",
        help=f"run the scenario as it will really run, every bound cut small, after its preflight; "
        f"one repeat, the cheapest subject model, a spend cap of ${RH.MAX_SPEND_USD:g}; never checked in",
    )
    parser.add_argument("--strict", action="store_true", help="a provider without a key fails the run")
    parser.add_argument(
        "--build", action="store_true", help="build the container image before running, for a skill or command subject"
    )
    parser.add_argument(
        "--screencast-port", type=int, default=None, help="capture frames from a Chrome already listening on this port"
    )
    parser.add_argument("--screencast-seconds", type=float, default=10.0, help="how long to capture frames")
    return parser


def command_list(out: Path) -> int:
    print("scenarios:")
    for path in S.catalog(SCENARIOS):
        try:
            scn = S.load(path)
            # What a scenario requires, its optional groups, how many repeats it runs, and its run's spend cap,
            # each only when it names one.
            named = f" requires={','.join(scn.requires)}" if scn.requires else ""
            named += f" groups={','.join(g.name for g in scn.subject.groups)}" if scn.subject.groups else ""
            named += f" repeat={scn.repeat}" if scn.repeat else ""
            named += f" max_spend_usd={scn.max_spend_usd:g}" if scn.max_spend_usd else ""
            print(
                f"  {scn.name:18} kind={scn.kind:8} judges={scn.judges.providers} effort={scn.judges.effort} "
                f"runtimes={','.join(scn.runtimes)}{named}"
            )
        except S.ScenarioError as exc:
            print(f"  {path.stem:18} unreadable: {exc}")
    print("\nproviders:")
    for name, ready, keys in P.availability():
        flag = int(P.Provider[name.upper()])
        print(f"  {name:10} flag={flag:<3} key={'present' if ready else 'absent '} ({keys})")
    for source in RT.SUBJECT_KEYS.values():
        print(f"  {'subject':10} key={'present' if os.environ.get(source) else 'absent '} ({source})")

    print(f"\nruns folder: {out}")
    return 0


def command_redact(out: Path) -> int:
    """Redact every key value and every key-shaped string from the run folders, in place.

    A file that cannot be read or written is named, the rest are redacted
    still, and the command exits 1, so nothing unredacted is shown or
    uploaded after it.
    """
    failed: dict[Path, str] = {}
    found = X.redact_folder(out, X.key_values(), failed)
    for path, count in found.items():
        print(f"redacted {count} key(s) in {path.relative_to(out)}")
    print(f"redacted {sum(found.values())} key(s) in {len(found)} file(s) under {out}")
    for path, reason in failed.items():
        print(f"could not redact {path.relative_to(out)}: {reason}", file=sys.stderr)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Path(args.out).resolve() if args.out else DEFAULT_OUT
    if args.command == "list":
        return command_list(out)
    if args.command == "redact":
        return command_redact(out)
    if args.max_spend_usd is not None and not 0 < args.max_spend_usd < float("inf"):
        print(f"--max-spend-usd is an amount in US dollars above 0, got {args.max_spend_usd}", file=sys.stderr)
        return 2
    if args.command == "judge":
        return command_judge(args)
    if args.source:
        print("--source is for judge; a run of a scenario runs its subject", file=sys.stderr)
        return 2
    if not args.scenario:
        print("--scenario is required; `run.py list` shows the scenarios", file=sys.stderr)
        return 2
    if args.repeat is not None and args.repeat < 1:
        print(f"--repeat is a whole number of at least 1, got {args.repeat}", file=sys.stderr)
        return 2

    try:
        # The phases of no group, and those of each group --with takes.
        scn = S.select(S.load(S.find(args.scenario, SCENARIOS)), args.groups or [])
        flags = P.parse(args.providers if args.providers is not None else scn.judges.providers)
        if scn.kind == "qa":
            context_text(scn)  # a missing context file stops the run before anything is spent
    except (S.ScenarioError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    matrix = J.load_matrix(MODELS)
    if args.rehearsal:
        # A rehearsal cuts every bound small; a flag may cut one further, never raise it.
        if args.repeat not in (None, 1):
            print(f"a rehearsal runs one repeat, and --repeat asks for {args.repeat}", file=sys.stderr)
            return 2
        if args.max_spend_usd is not None and args.max_spend_usd > RH.MAX_SPEND_USD:
            print(
                f"a rehearsal spends at most ${RH.MAX_SPEND_USD:g}; --max-spend-usd can lower that, "
                f"and {args.max_spend_usd:g} would raise it",
                file=sys.stderr,
            )
            return 2
        scn = RH.scenario(scn, matrix, len(P.members(flags)))
    # A flag that is not given takes the scenario's value. From here on,
    # args.repeat and args.max_spend_usd are what the run takes.
    if args.repeat is None:
        args.repeat = scn.repeat or REPEAT
    if args.max_spend_usd is None:
        args.max_spend_usd = S.spend_cap(scn, args.repeat, len(P.members(flags)))
    # The scenario says where it runs. A runtime it does not list is refused
    # here, before a run folder is made.
    runtime = args.runtime or scn.runtimes[0]
    if runtime not in scn.runtimes:
        print(f"scenario {scn.name} runs on {', '.join(scn.runtimes)}, not on {runtime}", file=sys.stderr)
        return NOT_LISTED
    effort = args.effort or scn.judges.effort
    own_target = scn.resolve(scn.subject.target)
    target = Path(args.target).resolve() if args.target else own_target
    if target is not None and not target.is_dir():
        print(f"the target {target} is not a folder", file=sys.stderr)
        return 2

    run_id, run_dir = new_run_dir(out, scn.name)

    config: dict = {}
    if args.runtime_config:
        path = Path(args.runtime_config)
        config = S.parse_text(path.read_text(encoding="utf-8"), path.suffix) or {}
    if runtime == "container":
        config.setdefault("keys", [n for n in subject_keys(scn) if os.environ.get(RT.SUBJECT_KEYS[n])])
    # The subject lives outside the checkout, with copies of the plugin
    # payload and of the target, so neither an answer key nor the
    # repository's CLAUDE.md is in its reach.
    rt = RT.build(runtime, run_dir, target, config, plugin=ROOT if scn.kind != "qa" else None, sandbox=RT.new_sandbox())
    try:
        # A run that can spend holds this machine awake until it ends; one that only resolves or checks does not.
        with PF.held_awake(not (args.dry_run or args.preflight)) as note:
            if note:
                print(note, file=sys.stderr)
            return execute(args, scn, rt, run_dir, run_id, target, own_target, config, flags, effort, matrix)
    finally:
        rt.teardown()


def preflight(
    args: argparse.Namespace,
    scn: S.Scenario,
    rt: RT.BaseRuntime,
    run_dir: Path,
    resolved: dict[str, Any],
    staged: RF.Staged,
    stage_error: str | None,
    flags: P.Provider,
) -> int:
    """Check what the run needs, in order, up to the first failure; 0 when every check passed, else PREFLIGHT_FAILED.

    Every check that ran goes into `run.json` under `preflight`, with the
    names of those that did not run. Nothing runs a subject or asks a
    judge, and the keys are checked against the providers' model lists,
    which cost nothing.
    """
    ctx = PF.Context(
        scenario=scn,
        runtime=rt,
        sessions=phases_of(scn) if scn.kind == "skill" else [],
        max_spend_usd=args.max_spend_usd,
        providers=flags,
        claude=args.claude,
        build=args.build,
        release=V.plugin_version(ROOT),
        checkout=resolved["versions"]["checkout"],
        references=staged.versions,
        stage_error=stage_error,
        run_dir=run_dir,
        rehearsal=args.rehearsal,
    )
    print(f"preflight of {scn.name} on the {rt.name} runtime:")
    record = PF.run(ctx)
    resolved["preflight"] = record
    (run_dir / "run.json").write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
    if record["passed"]:
        skipped = sum(1 for c in record["checks"] if c["status"] == PF.SKIP)
        passed = len(record["checks"]) - skipped
        print(f"preflight passed: {passed} checks passed and {skipped} did not apply; ", end="")
        print("nothing was run and no paid endpoint was called")
        return 0
    left = f"; not run: {', '.join(record['not_run'])}" if record["not_run"] else ""
    sys.stdout.flush()  # the checks first, then why the run may not start
    print(f"preflight failed at {record['failed']}{left}", file=sys.stderr)
    print("nothing was run and no paid endpoint was called", file=sys.stderr)
    return PREFLIGHT_FAILED


def execute(args, scn, rt, run_dir, run_id, target, own_target, config, flags, effort, matrix) -> int:
    """Everything after the runtime exists: the caller tears the runtime down whatever happens here."""
    model = subject_model(scn, args.subject_model, matrix)
    try:
        # A runtime config that cannot run is refused here, before anything is spent.
        rt.stage()
        argv_subject = subject_argv(scn, plugin_name(ROOT), rt.plugin_path(), rt.target_path(), args.claude, model)
        planned = planned_phases(scn, plugin_name(ROOT), rt.plugin_path(), rt.target_path(), args.claude, model)
    except (S.ScenarioError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    # The agentic judges' references, staged in the sandbox: a path the
    # checkout does not hold, or a tag that cannot be fetched, is refused
    # here too. Fetching a public repository spends nothing, so a dry run
    # does it. A preflight records the refusal as its failed check instead.
    staged = RF.Staged()
    stage_error: str | None = None
    if scn.judges.agentic:
        try:
            staged = RF.stage(scn.judges.references, ROOT, rt.sandbox / "references", V.plugin_version(ROOT))
        except RF.StageError as exc:
            if not (args.preflight or args.rehearsal):
                print(exc, file=sys.stderr)
                return 2
            stage_error = str(exc)

    # The evidence the judges get. The source is read from the target as
    # the runtime staged it, the copy the subject reads, so the judges and
    # the subject see the same files. The expected findings describe the
    # scenario's own target; on any other target they would be wrong, so
    # they are dropped and the run says so. The source goes either way.
    source_text = E.source(rt.target, scn.evidence.files) if rt.target and scn.evidence.files else ""
    expected_path = scn.resolve(scn.evidence.expected)
    expected_note = None
    if expected_path and target != own_target:
        expected_note = f"expected findings dropped: they describe {own_target}, and the run is on {target}"
        expected_path = None
    expected_text: str | None = None
    expected_data = None
    if expected_path:
        expected_text = expected_path.read_text(encoding="utf-8")
        expected_data = S.parse_text(expected_text, expected_path.suffix)
    evidence_text = E.render(expected_text, source_text)
    resolved = {
        "run_id": run_id,
        # The scenario's content is inline; its path is a reference, so it is
        # recorded as the other paths are, relative to the repository.
        "scenario": {**scn.as_dict(), "path": V.shown(scn.path, ROOT)},
        "runtime": {"name": rt.name, "config": config, "target": V.shown(target, ROOT)},
        "providers": {"flags": int(flags), "names": [P.name(p) for p in P.members(flags)]},
        "effort": effort,
        "repeat": args.repeat,
        "models": {P.name(p): J.models_for(matrix, P.name(p)) for p in P.members(flags)},
        "subject_argv": argv_subject,
        "subject_model": model,
        "max_spend_usd": args.max_spend_usd,
        "guideline_sha": git_sha(ROOT),
        "target_sha": git_sha(target) if target else None,
        "evidence": {
            "files": list(scn.evidence.files),
            "source_chars": len(source_text),
            "expected": V.shown(expected_path, ROOT),
            "note": expected_note,
        },
        "versions": static_versions(target, rt.target, expected_path),
        "started_at": R.now(),
    }
    if scn.judges.agentic:
        resolved["versions"]["references"] = staged.versions
        resolved["notes"] = list(staged.notes)
    if planned:
        resolved["phases"] = planned
    if scn.subject.groups:
        # The optional groups this run takes; none is the scenario's default.
        resolved["groups"] = S.taken_groups(scn)
    if args.rehearsal:
        # Every bound above is the rehearsal's. A rehearsal is never checked in.
        resolved["rehearsal"] = True
    (run_dir / "run.json").write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
    print(f"run folder: {run_dir}")
    if expected_note:
        print(expected_note)
    for note in staged.notes:
        print(note)

    if args.dry_run:
        print(json.dumps(resolved, indent=2))
        print("dry run: nothing was executed and no provider was called")
        return 0
    if args.preflight or args.rehearsal:
        # A rehearsal runs its preflight first, and spends nothing when a check fails.
        checked = preflight(args, scn, rt, run_dir, resolved, staged, stage_error, flags)
        if args.preflight or checked != 0:
            return checked

    missing = [P.name(p) for p in P.members(flags) if not P.available(p)]
    if missing and args.strict:
        print(f"strict: no key for {', '.join(missing)}", file=sys.stderr)
        return 3
    no_subject_key = [RT.SUBJECT_KEYS[n] for n in subject_keys(scn) if not os.environ.get(RT.SUBJECT_KEYS[n])]
    if scn.kind == "skill" and no_subject_key and args.strict:
        print(f"strict: the subject has no key of its own; set {', '.join(no_subject_key)}", file=sys.stderr)
        return 3

    # A qa subject runs no command, so no image is built for it: a container
    # run of one needs no engine.
    if isinstance(rt, RT.ContainerRuntime) and args.build and scn.kind != "qa":
        with CliStream(run_dir / "streams" / "build.jsonl") as build_stream:
            status = rt.build(build_stream)
        if status.code != 0:
            print(f"the image build failed with {status.code}; see streams/build.jsonl", file=sys.stderr)
            return 4

    # The runtime is ready, so it can say what it carries. run.json is
    # written again with the answer, before the subject runs.
    probed, notes = probe_versions(scn, rt, args.claude)
    resolved["versions"].update(probed)
    (run_dir / "run.json").write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
    notes[:0] = staged.notes
    if expected_note:
        notes.insert(0, expected_note)
    screencast = None
    if args.screencast_port:
        from harness.capture import CdpScreencast

        screencast = CdpScreencast(FrameSink(run_dir / "streams" / "browser"), port=args.screencast_port)
        screencast.start(seconds=args.screencast_seconds)

    run = R.RunResult(
        run_id=run_id,
        scenario=scn.name,
        runtime=rt.name,
        started_at=resolved["started_at"],
        guideline_sha=resolved["guideline_sha"],
        target_sha=resolved["target_sha"],
        versions=resolved["versions"],
        subject={
            "kind": scn.kind,
            "skill": scn.subject.skill,
            "prompt": scn.subject.prompt,
            "argv": argv_subject,
            "model": model,
            "provider": scn.subject.provider,
            "target": V.shown(target, ROOT),
            "plugin": rt.plugin_path(),
            "max_turns": scn.subject.max_turns,
            "allowed_tools": list(scn.subject.allowed_tools),
            **subject_bounds(scn),
        },
        weights=scn.judges.weights if scn.judges.agentic else {},
    )

    env = subject_env(scn)
    budget = Budget(args.max_spend_usd)
    plan = Plan(
        plugin_name(ROOT),
        rt.plugin_path(),
        rt.target_path(),
        args.claude,
        model,
        subject_prices(matrix),
        budget,
        env,
    )

    streams = CliStream(run_dir / "streams" / "cli.jsonl")
    # What the harness runs where the subject runs: the checkpoints, the archive, the gates.
    harness = CliStream(run_dir / "streams" / "harness.jsonl") if scn.subject.output else None
    failed_subjects: list[int] = []
    # Each repeat's archive command, for a rehearsal's outcome.
    archived: dict[int, bool | None] = {}
    # A repeat starts only when what is left of the cap covers every phase it runs, so the cap
    # never cuts short a repeat whose first phases it let spend. Judges count toward the spend.
    need = repeat_need(scn)
    held = False
    try:
        for index in range(args.repeat):
            if budget.reached():
                notes.append(f"{budget.says()}; repeat {index} and after did not run")
                held = True
                break
            if not budget.covers(need):
                left = (budget.cap or 0.0) - budget.spent
                notes.append(
                    f"repeat {index} and after did not run: ${left:.4f} of the run's ${budget.cap:g} spend cap is left, "
                    f"and the phases of a repeat may spend ${need:g}"
                )
                held = True
                break
            mark = streams.count
            streams.note(f"[repeat {index}] start")
            rt.prepare_repeat(index)  # every repeat starts in an empty workspace of its own
            done: SkillRepeat | None = None
            if scn.kind == "qa":
                status, artifact, subject_usage = run_subject_qa(scn, streams, dict(os.environ), matrix, effort, model or "")
                models = [model] if model else []
                qa_provider = P.name(P.parse(scn.subject.provider or "anthropic"))
                subject_cost = J.cost_usd(matrix, qa_provider, model or "", subject_usage) if subject_usage else None
                budget.spent += subject_cost or 0.0
            elif scn.kind == "skill":
                done = run_skill(scn, rt, streams, harness, plan, index)
                status, artifact, models = done.status, done.answer, done.models
                subject_usage, subject_cost = done.usage, done.cost
                notes.extend(done.notes)
                archived[index] = done.archived
            else:
                status = rt.run(argv_subject, rt.workspace, env, streams, timeout_s=scn.subject.timeout_s)
                lines = [r["line"] for r in CliStream.read(run_dir / "streams" / "cli.jsonl")[mark:] if r.get("s") == "out"]
                artifact, models, is_error = read_envelope("\n".join(lines))
                subject_usage, subject_cost = read_envelope_spend("\n".join(lines))
                budget.spent += subject_cost or 0.0
                if is_error:
                    status = dataclasses.replace(status, is_error=True)
            if scn.kind != "qa" and model and models and not any(m.startswith(model) for m in models):
                notes.append(f"repeat {index}: the subject was pinned to {model}, and its result reports {', '.join(models)}")
            streams.note(f"[repeat {index}] exit {status.code}")

            art_dir = run_dir / "artifacts" / str(index)
            art_dir.mkdir(parents=True, exist_ok=True)
            paths = []
            if scn.artifact.stdout:
                (art_dir / "answer.md").write_text(artifact + "\n", encoding="utf-8")
                paths.append(f"artifacts/{index}/answer.md")
            parts = [artifact] if scn.artifact.stdout else []
            # One collection, so another machine's workspace is fetched once: the files and the archive.
            wanted = [*scn.artifact.files, *([PH.ARCHIVE] if scn.subject.output else [])]
            found = rt.collect(wanted) if wanted else []
            zip_file = rt.workspace / PH.ARCHIVE
            file_paths, file_parts = collect_files(rt, [f for f in found if f != zip_file], art_dir, index)
            paths += file_paths
            parts += file_parts
            archive = None
            if zip_file in found:
                archive, kept_note = keep_archive(zip_file, art_dir, run_dir, done.commit if done else None)
                if kept_note:
                    notes.append(f"repeat {index}: {kept_note}")
                if archive:
                    paths += [archive["path"], archive["manifest"]]
            blob = "\n\n".join(p for p in parts if p.strip()) or "(the subject produced nothing)"

            if done is not None and done.cut_short:
                # The run's spend cap kept phases from running. The output is
                # not the one the scenario measures, and a judge would spend
                # past the cap, so the repeat is kept and not judged.
                print(f"  repeat {index} cut short by the run's spend cap; not judged")
                run.repeats.append(
                    R.RepeatResult(
                        index=index,
                        exit_status=status.as_dict(),
                        artifact_paths=paths,
                        subject_models=models,
                        subject_usage=subject_usage,
                        subject_cost_usd=subject_cost,
                        phases=done.phases,
                        archive=archive,
                        gates=done.gates,
                        cut_short=done.cut_short,
                    )
                )
                continue

            ended = done.ended_early if done is not None else None
            if not status.ok or ended:
                # A subject that failed, ran out of time, left no tree, or
                # left a subagent unanswered produced nothing worth a
                # judge's money, and a score of it would be a score of the
                # failure. The repeat is recorded with no judgement.
                failed_subjects.append(index)
                reason = "timed out" if status.timed_out else "is_error" if status.is_error else f"exit {status.code}"
                if ended and status.ok:
                    why = "left no tree" if ended["reason"] == "no_tree" else "ended with an Agent call unanswered"
                    reason = f"phase {ended['phase']} {why}"
                print(f"  repeat {index} subject failed ({reason}); not judged")
                run.repeats.append(
                    R.RepeatResult(
                        index=index,
                        exit_status=status.as_dict(),
                        artifact_paths=paths,
                        subject_models=models,
                        subject_usage=subject_usage,
                        subject_cost_usd=subject_cost,
                        phases=done.phases if done else None,
                        archive=archive,
                        gates=done.gates if done else None,
                        ended_early=ended,
                    )
                )
                if ended and index + 1 < args.repeat:
                    notes.append(f"repeat {index} ended early, so the run ends: repeat {index + 1} and after did not run")
                    break
                continue

            expected = E.named(expected_data, blob)
            if expected is not None:
                print(f"  repeat {index} names {len(expected['named'])} of {expected['expected']} planted findings")
            judgements: list[R.AnyJudgement]
            if scn.judges.agentic:
                judgements = [
                    *judge_agentic(scn, rt.sandbox, staged, art_dir, run_dir, index, argv_subject, flags, effort, matrix, notes)
                ]
            else:
                prompt = J.build_prompt(scn.rubric, describe_subject(scn, argv_subject), blob, evidence=evidence_text)
                (art_dir / "judge-prompt.md").write_text(prompt, encoding="utf-8")
                judgements = [*J.judge_all(flags, prompt, effort, matrix)]
            budget.spent += sum(j.cost_usd or 0.0 for j in judgements)
            keep_judgements(run_dir, index, judgements)
            run.repeats.append(
                R.RepeatResult(
                    index=index,
                    exit_status=status.as_dict(),
                    artifact_paths=paths,
                    judgements=judgements,
                    expected=expected,
                    subject_models=models,
                    subject_usage=subject_usage,
                    subject_cost_usd=subject_cost,
                    phases=done.phases if done else None,
                    archive=archive,
                    gates=done.gates if done else None,
                )
            )
    finally:
        streams.close()
        if harness is not None:
            harness.close()
        if screencast is not None:
            result = screencast.stop()
            notes.append(f"screencast: {result.frames} frame(s)" + (f", {result.error}" if result.error else ""))

    if failed_subjects:
        notes.append(f"subject failed in repeat(s) {', '.join(map(str, failed_subjects))}; not judged")
    if isinstance(rt, RT.VmRuntime):
        # The other machine is given back before the results are written,
        # so a folder that stayed there, or a fetch that failed, is noted.
        notes.extend(rt.release())
    run.notes = notes
    if args.rehearsal:
        run.rehearsal = RH.outcome(scn, run.repeats, bool(failed_subjects), budget.cap, archived, held=held)
    data, problems = write_record(run, run_dir)
    summary = data["summary"]
    if problems:
        return 5
    if failed_subjects:
        print(f"the subject failed in {len(failed_subjects)} of {len(run.repeats)} repeat(s)", file=sys.stderr)
        return 6
    if run.rehearsal and run.rehearsal["status"] in ("capped", "incomplete"):
        why = (
            "the run's spend cap cut it short"
            if run.rehearsal["status"] == "capped"
            else "a step it exists to prove did not happen"
        )
        print(f"the rehearsal did not prove the pipeline to its end: {why}", file=sys.stderr)
        return REHEARSAL_UNPROVEN
    if args.strict and summary["skipped"]:
        return 3
    return 0


# Judging a run's output again ----------------------------------------------

# The flags `judge` does not take: the subject's and the runtime's, and the groups, which are the source run's.
NOT_FOR_JUDGE = {
    "scenario": "--scenario",
    "groups": "--with",
    "repeat": "--repeat",
    "runtime": "--runtime",
    "runtime_config": "--runtime-config",
    "target": "--target",
    "subject_model": "--subject-model",
    "preflight": "--preflight",
    "rehearsal": "--rehearsal",
    "build": "--build",
    "screencast_port": "--screencast-port",
}


@dataclasses.dataclass
class SourceRepeat:
    """A repeat of the run judged again: its index, the commit its archive holds, and why it is refused, when it is."""

    index: int
    commit: str | None = None
    refused: str | None = None
    # The phases the source run's results.json lists as run in this repeat; None when it lists none.
    ran: list[str] | None = None


def read_record(path: Path) -> dict[str, Any] | None:
    """A JSON object a run folder holds, or None when the file is not there or holds no object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def source_repeats(source: Path, results: dict[str, Any] | None) -> list[SourceRepeat]:
    """Every repeat the source run recorded or kept artifacts for, in order, each refused when its output cannot be judged.

    A repeat is refused when it kept no archive, when its archive does not
    open as a zip or holds no file, and when the archive's SHA-256 is not
    the one the source run recorded: that output is not the one the run
    made. A repeat that was never judged, because a phase failed, the run
    ended early, or a judge missed, is judged like any other. Each repeat
    carries the phases the source run's record lists as run in it.
    """
    recorded: dict[int, dict[str, Any]] = {}
    ran: dict[int, list[str]] = {}
    listed = (results or {}).get("repeats")
    for repeat in listed if isinstance(listed, list) else []:
        if isinstance(repeat, dict) and isinstance(repeat.get("index"), int):
            archive = repeat.get("archive")
            recorded[repeat["index"]] = archive if isinstance(archive, dict) else {}
            phases = repeat.get("phases")
            names = (
                [p["name"] for p in phases if isinstance(p, dict) and isinstance(p.get("name"), str)]
                if isinstance(phases, list)
                else []
            )
            if names:
                ran[repeat["index"]] = names
    folder = source / "artifacts"
    kept = {int(d.name) for d in folder.iterdir() if d.is_dir() and d.name.isdigit()} if folder.is_dir() else set()
    out: list[SourceRepeat] = []
    for index in sorted({*kept, *recorded}):
        zip_file = folder / str(index) / A.ZIP
        was = recorded.get(index, {})
        if not zip_file.is_file():
            out.append(SourceRepeat(index, refused="the source run kept no archive of its output"))
        elif not A.readable(zip_file):
            out.append(SourceRepeat(index, refused="its archive does not open as a zip"))
        elif not (found := A.record(zip_file, source))["files"]:
            out.append(SourceRepeat(index, refused="its archive holds no file"))
        elif was.get("sha256") and was["sha256"] != found["sha256"]:
            why = f"its archive's SHA-256 is {found['sha256']}, and the source run recorded {was['sha256']}"
            out.append(SourceRepeat(index, refused=why))
        else:
            commit = was.get("commit")
            out.append(SourceRepeat(index, commit=commit if isinstance(commit, str) else None, ran=ran.get(index)))
    return out


def command_judge(args: argparse.Namespace) -> int:
    """Judge an earlier run's archived output again, with this checkout's judges and no subject run.

    The scenario is this checkout's, found by the name the source run
    records, with the groups the source run took. Each repeat's judges are
    told of the phases that ran in it, and its rubric takes the sentence of
    a group only when one of the group's phases ran in it. Every repeat
    whose archive can be judged is staged as a run stages it before its
    judges. The others are refused with their reason, and a source with
    nothing to judge makes no run folder. The run's spend cap is the
    judges' budgets over the repeats it judges, unless `--max-spend-usd`
    names one.
    """
    given = [flag for dest, flag in NOT_FOR_JUDGE.items() if getattr(args, dest) not in (None, False)]
    if given:
        told = ", ".join(given)
        print(f"judge takes its scenario, groups, and output from the source run; {told} is not for it", file=sys.stderr)
        return 2
    if not args.source:
        print("judge needs --source, the run folder whose archived output it judges again", file=sys.stderr)
        return 2
    source = Path(args.source).resolve()
    resolved = read_record(source / "run.json") or {}
    scenario, runtime, groups = resolved.get("scenario"), resolved.get("runtime"), resolved.get("groups")
    name = scenario.get("name") if isinstance(scenario, dict) else None
    ran_on = runtime.get("name") if isinstance(runtime, dict) else None
    if not isinstance(name, str) or ran_on not in RT.NAMES:
        print(f"{source} is not a run folder: it holds no run.json that names its scenario and its runtime", file=sys.stderr)
        return 2
    taken = [str(g) for g in groups] if isinstance(groups, list) else []
    try:
        base = S.load(S.find(name, SCENARIOS))
        scn = S.select(base, taken)
        flags = P.parse(args.providers if args.providers is not None else scn.judges.providers)
    except (S.ScenarioError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if not (scn.subject.output and scn.judges.agentic):
        print(
            f"scenario {scn.name}: judge has agentic judges read the archive of an output folder, "
            "and this scenario builds no output folder or has no agentic judges",
            file=sys.stderr,
        )
        return 2
    repeats = source_repeats(source, read_record(source / "results.json"))
    for repeat in repeats:
        if repeat.refused:
            print(f"repeat {repeat.index} is not judged: {repeat.refused}", file=sys.stderr)
    judged = [r for r in repeats if not r.refused]
    if not judged:
        print(f"{source} holds no output to judge: no run folder was made and no judge started", file=sys.stderr)
        return 2
    if resolved.get("rehearsal"):
        # A rehearsal's output is judged within a rehearsal's bounds: each judge's budget cut as a rehearsal
        # cuts it, and the run capped at a rehearsal's cap, which a flag can lower and never raise.
        if args.max_spend_usd is not None and args.max_spend_usd > RH.MAX_SPEND_USD:
            print(
                f"the source is a rehearsal, whose judges spend at most ${RH.MAX_SPEND_USD:g}; --max-spend-usd can lower that, "
                f"and {args.max_spend_usd:g} would raise it",
                file=sys.stderr,
            )
            return 2
        base = dataclasses.replace(base, judges=dataclasses.replace(base.judges, budget=RH.budget(base.judges.budget)))
        scn = S.select(base, taken)
    # What each repeat's judges are told: the phases that ran in it, and the sentence of each group one of them is in.
    per_repeat = {r.index: S.as_ran(base, taken, r.ran) for r in judged}
    cap = args.max_spend_usd if args.max_spend_usd is not None else S.judging_cap(scn, len(judged), len(P.members(flags)))
    if resolved.get("rehearsal") and cap is not None:
        cap = min(cap, RH.MAX_SPEND_USD)
    out = Path(args.out).resolve() if args.out else source.parent
    run_id, run_dir = new_run_dir(out, scn.name)
    sandbox = RT.new_sandbox()
    try:
        # A run that can spend holds this machine awake until it ends; a dry run does not.
        with PF.held_awake(not args.dry_run) as note:
            if note:
                print(note, file=sys.stderr)
            return judge_again(
                args, scn, per_repeat, source, resolved, repeats, run_id, run_dir, sandbox, flags, cap, str(ran_on)
            )
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def judge_again(
    args: argparse.Namespace,
    scn: S.Scenario,
    per_repeat: dict[int, S.Scenario],
    source: Path,
    ran: dict[str, Any],
    repeats: list[SourceRepeat],
    run_id: str,
    run_dir: Path,
    sandbox: Path,
    flags: P.Provider,
    cap: float | None,
    runtime: str,
) -> int:
    """Everything after the run folder of `judge` exists: the caller removes the sandbox whatever happens here.

    Each repeat judged gets the source's artifacts but its judge prompt: the
    archive, its manifest, the answer, and the collected files. The judges
    read the archive's tree, as a run's judges do, and their prompt is made
    of `per_repeat`, the scenario as that repeat ran it. The repeat records the
    archive and the judgements, and no session, since no subject ran.
    """
    effort = args.effort or scn.judges.effort
    matrix = J.load_matrix(MODELS)
    try:
        staged = RF.stage(scn.judges.references, ROOT, sandbox / "references", V.plugin_version(ROOT))
    except RF.StageError as exc:
        print(exc, file=sys.stderr)
        return 2
    # The repeats to judge; once the run ends, the repeats judged, and the rest under `capped`.
    origin: dict[str, Any] = {
        "run_id": str(ran.get("run_id") or source.name),
        "path": V.shown(source, ROOT),
        "repeats": [r.index for r in repeats if not r.refused],
        "refused": [{"repeat": r.index, "reason": r.refused} for r in repeats if r.refused],
        "capped": [],
    }
    if scn.subject.groups:
        # The groups each repeat's rubric takes: those the source run took with a phase that ran in the repeat.
        origin["rubric_groups"] = [{"repeat": i, "groups": S.taken_groups(per_repeat[i])} for i in origin["repeats"]]
    notes = list(staged.notes)
    resolved: dict[str, Any] = {
        "run_id": run_id,
        "source": origin,
        "scenario": {**scn.as_dict(), "path": V.shown(scn.path, ROOT)},
        "runtime": {"name": runtime},
        "providers": {"flags": int(flags), "names": [P.name(p) for p in P.members(flags)]},
        "effort": effort,
        "models": {P.name(p): J.models_for(matrix, P.name(p)) for p in P.members(flags)},
        "subject_model": ran.get("subject_model"),
        "max_spend_usd": cap,
        "guideline_sha": git_sha(ROOT),
        "versions": {**static_versions(None, None, None), "references": staged.versions},
        "notes": list(notes),
        "started_at": R.now(),
    }
    if scn.subject.groups:
        resolved["groups"] = S.taken_groups(scn)
    if ran.get("rehearsal"):
        # A rehearsal's output stays a rehearsal's, whoever judges it, and is never checked in.
        resolved["rehearsal"] = True
    (run_dir / "run.json").write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
    print(f"run folder: {run_dir}")
    for note in notes:
        print(note)
    if args.dry_run:
        print(json.dumps(resolved, indent=2))
        print("dry run: no judge was called")
        return 0
    missing = [P.name(p) for p in P.members(flags) if not P.available(p)]
    if missing and args.strict:
        print(f"strict: no key for {', '.join(missing)}", file=sys.stderr)
        return 3

    run = R.RunResult(
        run_id=run_id,
        scenario=scn.name,
        runtime=runtime,
        started_at=resolved["started_at"],
        guideline_sha=resolved["guideline_sha"],
        versions=resolved["versions"],
        subject={
            "kind": scn.kind,
            "skill": scn.subject.skill,
            "prompt": scn.subject.prompt,
            "model": resolved["subject_model"],
            **subject_bounds(scn),
        },
        weights=scn.judges.weights,
        source=origin,
    )
    budget = Budget(cap)
    # A repeat's judges start only when what is left of the cap covers their budgets, so no judge is handed
    # dollars the cap does not hold. The judges of a repeat that started still judge it.
    need = S.judges_budget(scn, len(P.members(flags)))
    to_judge = [r.index for r in repeats if not r.refused]
    for repeat in (r for r in repeats if not r.refused):
        if not budget.covers(need):
            origin["capped"] = to_judge[to_judge.index(repeat.index) :]
            left = (budget.cap or 0.0) - budget.spent
            notes.append(
                f"repeat {repeat.index} and after were not judged: ${left:.4f} of the run's ${budget.cap:g} spend cap is left, "
                f"and the judges of a repeat may spend ${need:g}"
            )
            break
        index = repeat.index
        kept = source / "artifacts" / str(index)
        art_dir = run_dir / "artifacts" / str(index)
        shutil.copytree(kept, art_dir, symlinks=True)
        (art_dir / "judge-prompt.md").unlink(missing_ok=True)  # the source's own; this run writes its own
        paths = sorted(p.relative_to(run_dir).as_posix() for p in art_dir.rglob("*") if p.is_file())
        archive = {**A.record(art_dir / A.ZIP, run_dir), "commit": repeat.commit}
        judgements = judge_agentic(per_repeat[index], sandbox, staged, art_dir, run_dir, index, [], flags, effort, matrix, notes)
        budget.spent += sum(j.cost_usd or 0.0 for j in judgements)
        keep_judgements(run_dir, index, [*judgements])
        # No subject ran, so the repeat carries no session and no subject spend, and its exit status is 0.
        run.repeats.append(
            R.RepeatResult(index=index, exit_status={"code": 0}, artifact_paths=paths, judgements=[*judgements], archive=archive)
        )
    origin["repeats"] = [r.index for r in run.repeats]
    if "rubric_groups" in origin:
        origin["rubric_groups"] = [g for g in origin["rubric_groups"] if g["repeat"] in origin["repeats"]]
    run.notes = notes
    data, problems = write_record(run, run_dir)
    if problems:
        return 5
    if args.strict and data["summary"]["skipped"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
