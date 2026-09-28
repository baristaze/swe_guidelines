"""Preflight: what a run needs, checked before it spends anything.

`run.py --preflight` resolves a run as `--dry-run` does and writes its
`run.json`. Then it runs the checks below, in order, and stops at the
first that fails. A failed check says what is wrong and how to fix it.
Every check that ran is written to `run.json` under `preflight`, with
the names of those that did not run. No check calls a paid endpoint: a
key is checked against its provider's model list, which costs nothing,
and no subject runs and no judge is asked.

- `budgets`: the run has its spend cap. Every session of a skill
  subject has its turn cap, its spend cap, and its timeout, and each
  phase its gate-rerun cap. A session with no spend bound fails.
- `checkout`: the checkout holds no change a commit does not, in what
  decides a score: the `dirty` that `versions.checkout` records is
  false.
- `references`: every reference of agentic judges was staged, and
  every repository reference, fetched at its tag, pins this checkout's
  release.
- `awake`: on macOS, `caffeinate` is there to hold this machine awake
  for the run, and the machine is on AC power, where it holds off
  system sleep.
- `subject_key`: the subject's key is set, is not a judge's, and its
  provider's model list takes it.
- `judge_keys`: every selected judge has a key, and its provider's
  model list takes it.
- `runtime`: the runtime is up. The other machine answers through its
  prefix and passes its check; the container engine answers, and has
  the image or builds it.
- `workspace`: on the other machine, the folder runs work in holds
  nothing: no run holds the machine, and none left anything there.
- `tools`: every tool the runtime pins answers where the subject runs,
  at its pinned version.
- `resources`: the free disk and the available memory where the subject
  works, against what the scenario needs.
- `network`: the model API a skill calls, and the registries the
  scenario names, answer from where the subject runs.
- `requires`: what the scenario requires works there. For `docker`, a
  Compose stack starts, turns healthy within 120 seconds, and stops.

A check that does not apply to the run is a `skip`, with the reason.

A run that spends holds this machine awake while it runs: on macOS the
harness starts `caffeinate -i -s -w <its own process id>` and stops it
at the end (`held_awake`).

Like every harness module, this one imports the standard library only.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import providers as P
from . import references as RF
from . import runtime as RT
from .capture import CliStream
from .scenario import Phase, Scenario

PASS, FAIL, SKIP = "pass", "fail", "skip"
# How long a model list may take to answer, and a command where the subject runs.
HTTP_TIMEOUT_S = 20
PROBE_TIMEOUT_S = 120
# How long each registry may take to answer from where the subject runs.
REACH_TIMEOUT_S = 20
# How long the Compose stack may take to turn healthy, and the whole check, its pull and its stop included.
COMPOSE_WAIT_S = 120
COMPOSE_TIMEOUT_S = 600
COMPOSE_IMAGE = "busybox:1.37"
# The model API a skill subject's Claude Code calls.
MODEL_API = "https://api.anthropic.com"
MEMINFO = "/proc/meminfo"
GIB = 1024**3
# What the preflight sends as its user agent, since some APIs refuse the library's own.
USER_AGENT = "swe-guidelines-benchmark-preflight"

# Each provider's model list: free to call, and it takes a key only when
# the key is valid. The key goes in a header, never in the URL.
MODEL_LISTS: dict[P.Provider, tuple[str, Callable[[str], dict[str, str]]]] = {
    P.Provider.ANTHROPIC: (
        "https://api.anthropic.com/v1/models",
        lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
    ),
    P.Provider.OPENAI: ("https://api.openai.com/v1/models", lambda key: {"Authorization": f"Bearer {key}"}),
    P.Provider.GEMINI: ("https://generativelanguage.googleapis.com/v1beta/models", lambda key: {"x-goog-api-key": key}),
    P.Provider.XAI: ("https://api.x.ai/v1/models", lambda key: {"Authorization": f"Bearer {key}"}),
}

# The tools the container runtime's Dockerfile pins, and how to find each
# pin there, for a container run whose runtime config names no `tools`.
DOCKERFILE_PINS: tuple[tuple[tuple[str, ...], re.Pattern[str]], ...] = (
    (("claude", "--version"), re.compile(r"@anthropic-ai/claude-code@([0-9][0-9A-Za-z.+-]*)")),
    (("uv", "--version"), re.compile(r"astral-sh/uv:([0-9][0-9A-Za-z.+-]*)")),
    (("node", "--version"), re.compile(r"\bNODE_MAJOR=([0-9]+)")),
)
VERSION = re.compile(r"\d+(?:\.\d+)*")

# The scripts below run where the subject runs, each with its values as
# arguments, never spliced into the script, and each on one line, as a
# prefix hands its words on.
#
# The entries of the folder runs work in there, when it is there.
LISTING = '[ -d "$1" ] || exit 0; ls -A -- "$1"'
# The free disk, in KiB, of the folder the subject works in, or of the
# nearest folder above it that is there; and the available memory, in
# KiB, when the machine reports it.
RESOURCES = (
    'p=$1; while [ ! -e "$p" ]; do p=$(dirname -- "$p"); done; '
    'df -Pk -- "$p" | awk \'NR == 2 { print "disk", $4 }\'; '
    'if [ -r "$2" ]; then awk \'$1 == "MemAvailable:" { print "memory", $2 }\' "$2"; fi'
)
# The HTTP status each URL answers to a HEAD request, so no body is
# fetched, or 000 when it answers none. Exit 127: no curl there.
REACH = (
    "command -v curl >/dev/null 2>&1 || exit 127; "
    'for url in "$@"; do '
    f'code=$(curl -sS --head -o /dev/null --max-time {REACH_TIMEOUT_S} -w "%{{http_code}}" -- "$url" 2>/dev/null); '
    'printf "%s %s\\n" "${code:-000}" "$url"; done'
)
# A Compose stack of one service with a health check: up until it is
# healthy, then down, whatever up did. Arguments: the project, the wait
# in seconds, the image. Exit 3: it did not turn healthy in time. Exit 4:
# it did not stop.
COMPOSE = (
    "project=$1 wait=$2 image=$3; dir=$(mktemp -d) || exit 2; "
    "printf '%s\\n' 'services:' '  probe:' \"    image: $image\" '    init: true' "
    '\'    command: ["sleep", "600"]\' \'    healthcheck:\' \'      test: ["CMD", "true"]\' '
    "'      interval: 1s' '      retries: 60' > \"$dir/compose.yaml\" || exit 2; "
    'docker compose -p "$project" -f "$dir/compose.yaml" up -d --quiet-pull --wait --wait-timeout "$wait"; up=$?; '
    'docker compose -p "$project" -f "$dir/compose.yaml" down -v --remove-orphans --timeout 10 >/dev/null 2>&1; down=$?; '
    'rm -rf -- "$dir"; [ "$up" -eq 0 ] || exit 3; [ "$down" -eq 0 ] || exit 4'
)


@dataclass
class Check:
    """One check's outcome, `pass`, `fail`, or `skip`: what it found, how to fix a failure, and the facts it read."""

    name: str
    status: str
    detail: str
    fix: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "status": self.status, "detail": self.detail, "fix": self.fix}
        if self.facts:
            out["facts"] = self.facts
        return out


@dataclass
class Context:
    """The run as it was resolved: what every check reads."""

    scenario: Scenario
    runtime: RT.BaseRuntime
    # The sessions a skill subject runs: its phases, or its one session.
    sessions: list[Phase] = field(default_factory=list)
    max_spend_usd: float | None = None
    providers: P.Provider = P.ALL
    claude: str = "claude"
    build: bool = False
    # This checkout's release, and the checkout as `versions.checkout` records it.
    release: str | None = None
    checkout: dict[str, Any] = field(default_factory=dict)
    # The references as the run staged them, or why one could not be.
    references: dict[str, dict[str, Any]] = field(default_factory=dict)
    stage_error: str | None = None
    run_dir: Path | None = None
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))


@dataclass(frozen=True)
class Answer:
    """What a command answered: its exit code and its lines of output and of error."""

    code: int
    out: list[str]
    err: list[str]

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def first(self) -> str:
        return self.out[0] if self.out else ""

    def why(self) -> str:
        """The exit code and the last line of error, as a failure names them."""
        return f"exit {self.code}" + (f": {self.err[-1]}" if self.err else "")


@dataclass(frozen=True)
class Tool:
    """A tool the runtime carries: how to ask it its version, and the version pinned, if one is."""

    argv: tuple[str, ...]
    version: str | None = None


# Running what the checks ask ----------------------------------------------


def _lines(text: str | None) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def here(command: list[str], env: dict[str, str], timeout_s: float = PROBE_TIMEOUT_S) -> Answer:
    """Run a command on this machine and return what it answered.

    It reads no input. One past its timeout is stopped with its whole
    group and answers exit 124; one that cannot start answers exit 127.
    """
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return Answer(127, [], [f"{command[0]} could not start: {exc.strerror or type(exc).__name__}"])
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        RT.kill_group(proc.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=5)
        return Answer(124, [], [f"no answer in {timeout_s:g} s"])
    except BaseException:
        RT.kill_group(proc.pid)
        proc.wait()
        raise
    return Answer(proc.returncode, _lines(out), _lines(err))


def ask(rt: RT.BaseRuntime, argv: list[str], timeout_s: float = PROBE_TIMEOUT_S, network: bool = False) -> Answer:
    """What a command answers where the subject runs: through the prefix, in a container of the image, or here.

    It runs as the runtime's probes do, with no judge's key. In a container
    it has no network unless `network` asks for it.
    """
    return here(rt.probe_command(argv, network=network), rt.probe_env(), timeout_s)


def http_status(url: str, headers: dict[str, str], timeout_s: float = HTTP_TIMEOUT_S) -> int | str:
    """The HTTP status a GET of `url` answers, or why it answered none. The reason never holds a header's value."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except urllib.error.URLError as exc:
        return f"no answer: {exc.reason}"
    except (OSError, ValueError) as exc:
        return f"no answer: {type(exc).__name__}"


def model_list(provider: P.Provider, key: str) -> int | str:
    """What a provider's model list answers to a key: 200 when it takes the key."""
    url, headers = MODEL_LISTS[provider]
    return http_status(url, headers(key))


def key_name(provider: P.Provider, env: dict[str, str]) -> str | None:
    """The name of the variable the run reads a provider's key from, or None when none is set."""
    return next((n for n in P.KEY_NAMES[provider] if env.get(n)), None)


def refused(provider: P.Provider, variable: str, answered: int | str) -> tuple[str, str]:
    """What a model list that did not take a key means, and how to fix it."""
    host = MODEL_LISTS[provider][0].split("/")[2]
    if isinstance(answered, int):
        return f"{P.name(provider)}'s model list answered {answered} to {variable}", f"set {variable} to a valid key"
    return f"{P.name(provider)}'s model list gave {variable} {answered}", f"check that this machine reaches {host}"


def prefix_of(rt: RT.BaseRuntime) -> str:
    """How the fix of a check on another machine names the prefix."""
    return shlex.join(rt.vm.exec_prefix) if isinstance(rt, RT.VmRuntime) else ""


# The checks ----------------------------------------------------------------


def budgets(ctx: Context) -> Check:
    """The run's spend cap, and every bound of every session a skill subject runs."""
    scn = ctx.scenario
    problems: list[str] = []
    fixes: list[str] = []
    if ctx.max_spend_usd is None:
        problems.append("the run has no spend cap")
        fixes.append("set max_spend_usd in the scenario, or pass --max-spend-usd")
    sessions: list[dict[str, Any]] = []
    unbounded = False
    if scn.kind == "skill":
        for s in ctx.sessions:
            bounds: dict[str, float | int | None] = {"max_turns": s.max_turns, "max_usd": s.max_usd, "timeout_s": s.timeout_s}
            if scn.subject.gates:
                bounds["max_gate_reruns"] = s.max_gate_reruns
            # A rerun cap of 0 is a bound: no rerun. Every other bound is above 0.
            missing = [n for n, v in bounds.items() if v is None or (v < 0 if n == "max_gate_reruns" else v <= 0)]
            if missing:
                unbounded = True
                problems.append(f"session {s.name} has no {', '.join(missing)}")
            sessions.append({"name": s.name, **bounds})
    if unbounded:
        fixes.append("give every session its max_turns, max_usd, and timeout_s, and every phase its max_gate_reruns")
    facts: dict[str, Any] = {"max_spend_usd": ctx.max_spend_usd}
    if sessions:
        facts["sessions"] = sessions
    if scn.kind == "command":
        facts["timeout_s"] = scn.subject.timeout_s
    if scn.judges.agentic and scn.judges.budget is not None:
        facts["judgement_budget"] = scn.judges.budget.as_dict()
    if problems:
        return Check("budgets", FAIL, "; ".join(problems), "; ".join(fixes), facts)
    detail = f"the run starts nothing more past ${ctx.max_spend_usd:g}"
    if sessions:
        caps = sum(s["max_usd"] for s in sessions)
        bounds_named = (
            "turn cap, spend cap, timeout, and gate-rerun cap" if scn.subject.gates else "turn cap, spend cap, and timeout"
        )
        detail += f"; {len(sessions)} session(s), each with its {bounds_named}, their spend caps summing to ${caps:g}"
    return Check("budgets", PASS, detail, facts=facts)


def checkout(ctx: Context) -> Check:
    """The checkout holds no change a commit does not, in what decides a score, as `versions.checkout` records it."""
    dirty = ctx.checkout.get("dirty")
    facts = {"commit": ctx.checkout.get("commit"), "dirty": dirty}
    if dirty is False:
        return Check(
            "checkout", PASS, f"what decides a score is as commit {str(ctx.checkout.get('commit'))[:12]} holds it", facts=facts
        )
    if dirty is None:
        return Check(
            "checkout",
            FAIL,
            "git could not say whether the checkout holds changes no commit holds, so the run could not name what it measured",
            "run from a git checkout of the repository, with git on the path",
            facts,
        )
    paths = list(ctx.checkout.get("dirty_paths") or [])
    shown = ", ".join(paths[:5]) + (f", and {len(paths) - 5} more" if len(paths) > 5 else "")
    return Check(
        "checkout",
        FAIL,
        f"the checkout holds changes no commit holds, in what decides a score: {shown}. "
        "The skills are staged from the working tree, and a run whose checkout is dirty is never checked in",
        "commit the changes, or set them aside, and run again",
        {**facts, "dirty_paths": paths},
    )


def references(ctx: Context) -> Check:
    """Every reference was staged, and every repository reference pins this checkout's release."""
    scn = ctx.scenario
    if not scn.judges.agentic:
        return Check("references", SKIP, "the judges are one-shot and read no reference")
    if ctx.stage_error:
        return Check(
            "references",
            FAIL,
            ctx.stage_error,
            "check that the repository is public and reached from here, and that the tag is on it "
            "(`git ls-remote --tags <url> <tag>`)",
        )
    ours = f"v{ctx.release.removeprefix('v')}" if ctx.release else None
    problems: list[str] = []
    for ref in scn.judges.references:
        if not ref.repository:
            continue
        pins = ctx.references.get(ref.name, {}).get("pins")
        if pins is None:
            problems.append(f"reference `{ref.name}` names no guideline release in its {RF.SPEC}")
        elif ours and pins.removeprefix("v") != ours.removeprefix("v"):
            problems.append(f"reference `{ref.name}` pins the guideline at {pins}, and this checkout is at {ours}")
    facts = {"references": ctx.references}
    if problems:
        return Check(
            "references",
            FAIL,
            "; ".join(problems),
            f"move each repository reference's tag to a release of it that pins {ours or 'this checkout'}",
            facts,
        )
    repos = [r for r in scn.judges.references if r.repository]
    detail = "every reference was staged"
    if repos:
        detail += f"; each repository, fetched at its tag, pins {ours}"
    return Check("references", PASS, detail, facts=facts)


def system() -> str:
    """The platform this machine runs, as `sys.platform` names it."""
    return sys.platform


def power_source() -> str | None:
    """What this Mac draws its power from, as `pmset -g batt` says: `AC Power`, `Battery Power`, or None when it does not say."""
    answer = here(["pmset", "-g", "batt"], RT.clean_env(), 10)
    found = re.search(r"'([^']+)'", answer.first) if answer.ok else None
    return found.group(1) if found else None


def awake(ctx: Context) -> Check:
    """On macOS, the run can hold this machine awake: caffeinate is there, and the machine is on AC power."""
    if system() != "darwin":
        return Check(
            "awake", SKIP, "the harness holds a machine awake on macOS only; keep this one from sleeping until the run ends"
        )
    if shutil.which("caffeinate") is None:
        return Check(
            "awake",
            FAIL,
            "caffeinate is not on the path, so nothing would hold this machine awake while the run runs",
            "put /usr/bin, where macOS keeps caffeinate, on PATH",
        )
    source = power_source()
    if source == "Battery Power":
        return Check(
            "awake",
            FAIL,
            "this machine runs on its battery, and caffeinate holds off system sleep on AC power only",
            "plug it in, and keep it plugged in until the run ends",
            {"power": source},
        )
    return Check(
        "awake",
        PASS,
        "a run holds this machine awake with `caffeinate -i -s` until it ends; a closed lid still sleeps a laptop",
        facts={"power": source},
    )


def subject_key(ctx: Context) -> Check:
    """The subject's key: set, the subject's own, and taken by its provider's model list."""
    scn, env = ctx.scenario, ctx.env
    if scn.kind == "qa":
        provider = P.parse(scn.subject.provider or "anthropic")
        variable = key_name(provider, env)
        names = " or ".join(P.KEY_NAMES[provider])
        if variable is None:
            return Check(
                "subject_key",
                FAIL,
                f"the qa subject is {P.name(provider)} answering itself, and no key for it is set ({names})",
                f"set {names}",
            )
        answered = model_list(provider, env[variable])
        facts = {"key": variable, "answered": answered}
        if answered != 200:
            detail, fix = refused(provider, variable, answered)
            return Check("subject_key", FAIL, detail, fix, facts)
        return Check(
            "subject_key",
            PASS,
            f"{P.name(provider)}'s model list takes {variable}, which the qa subject answers with",
            facts=facts,
        )
    source = RT.SUBJECT_KEYS["ANTHROPIC_API_KEY"]
    value = env.get(source)
    if not value:
        if scn.kind == "command":
            return Check("subject_key", SKIP, f"{source} is not set, and a command subject runs without it")
        return Check(
            "subject_key",
            FAIL,
            f"the subject has no key of its own: {source} is not set",
            f"set {source} to an Anthropic key of the subject's own, one you can cap and revoke on its own",
        )
    if value in RT.judge_key_values(env):
        return Check(
            "subject_key",
            FAIL,
            f"{source} holds a judge's key, and the run drops it rather than hand it to the subject",
            f"set {source} to a key of the subject's own",
        )
    answered = model_list(P.Provider.ANTHROPIC, value)
    facts = {"key": source, "answered": answered}
    if answered != 200:
        detail, fix = refused(P.Provider.ANTHROPIC, source, answered)
        return Check("subject_key", FAIL, detail, fix, facts)
    return Check("subject_key", PASS, f"{source} is the subject's own, and Anthropic's model list takes it", facts=facts)


def judge_keys(ctx: Context) -> Check:
    """Every selected judge's key: set, and taken by its provider's model list."""
    problems: list[str] = []
    fixes: list[str] = []
    rows: list[dict[str, Any]] = []
    for provider in P.members(ctx.providers):
        name = P.name(provider)
        variable = key_name(provider, ctx.env)
        if variable is None:
            names = " or ".join(P.KEY_NAMES[provider])
            problems.append(f"{name} has no key ({names})")
            fixes.append(f"set {names}, or leave {name} out of --providers")
            rows.append({"provider": name, "key": None, "answered": None})
            continue
        answered = model_list(provider, ctx.env[variable])
        rows.append({"provider": name, "key": variable, "answered": answered})
        if answered != 200:
            detail, fix = refused(provider, variable, answered)
            problems.append(detail)
            fixes.append(fix)
    facts = {"judges": rows}
    if problems:
        return Check("judge_keys", FAIL, "; ".join(problems), "; ".join(fixes), facts)
    names = ", ".join(r["provider"] for r in rows)
    return Check("judge_keys", PASS, f"every judge's model list takes its key: {names}", facts=facts)


def runtime(ctx: Context) -> Check:
    """The runtime is up: the other machine answers and passes its check, or the engine answers and has the image."""
    scn, rt = ctx.scenario, ctx.runtime
    if scn.kind == "qa":
        return Check("runtime", SKIP, "a qa subject runs no command: the harness asks the model itself")
    if isinstance(rt, RT.VmRuntime):
        prefix = prefix_of(rt)
        answer = ask(rt, ["true"], min(PROBE_TIMEOUT_S, rt.vm.helper_timeout_s))
        if not answer.ok:
            return Check(
                "runtime",
                FAIL,
                f"the other machine did not answer `{prefix} true` ({answer.why()})",
                "start the machine the runtime config's exec_prefix reaches; for the Lima machine, `limactl start swe-benchmark`",
            )
        if rt.vm.check:
            answer = ask(rt, list(rt.vm.check), rt.vm.helper_timeout_s)
            if not answer.ok:
                return Check(
                    "runtime",
                    FAIL,
                    f"the machine's check fails there, `{shlex.join(rt.vm.check)}` ({answer.why()}), "
                    "so no repeat would run its subject",
                    "restore what the check holds. The Lima machine's check compares its firewall with the listing saved when "
                    "it loaded, and a restart loads it again: `limactl stop swe-benchmark`, then `limactl start swe-benchmark`",
                )
        return Check(
            "runtime", PASS, "the other machine answers through its prefix" + (", and its check passes" if rt.vm.check else "")
        )
    if isinstance(rt, RT.ContainerRuntime):
        answer = here([rt.docker, "version", "--format", "{{.Server.Version}}"], RT.clean_env())
        if not answer.ok:
            return Check("runtime", FAIL, f"no container engine answers here ({answer.why()})", "start Docker on this machine")
        engine = answer.first
        if ctx.build:
            with CliStream((ctx.run_dir or rt.run_dir) / "streams" / "build.jsonl") as stream:
                built = rt.build(stream)
            if not built.ok:
                return Check(
                    "runtime",
                    FAIL,
                    f"the image build failed with {built.code}; see streams/build.jsonl",
                    "fix what the build output names",
                    {"engine": engine},
                )
        image = rt.image_version() or {"name": rt.image, "id": None}
        if image["id"] is None:
            return Check(
                "runtime",
                FAIL,
                f"the image {rt.image} is not here",
                "pass --build, which builds it before the run",
                {"engine": engine},
            )
        return Check(
            "runtime",
            PASS,
            f"the container engine answers, at {engine}, and has {rt.image}",
            facts={"engine": engine, "image": image},
        )
    return Check("runtime", PASS, "the subject runs on this machine")


def workspace(ctx: Context) -> Check:
    """On the other machine, the folder runs work in holds nothing: no lock, and nothing an earlier run left."""
    rt = ctx.runtime
    if ctx.scenario.kind == "qa":
        return Check("workspace", SKIP, "a qa subject runs no command and works in no folder")
    if not isinstance(rt, RT.VmRuntime):
        return Check("workspace", SKIP, "the run works in a sandbox of its own on this machine, made for it")
    base = rt.remote_base()
    prefix = prefix_of(rt)
    answer = ask(rt, ["sh", "-c", LISTING, "sh", base], rt.vm.helper_timeout_s)
    if not answer.ok:
        return Check(
            "workspace", FAIL, f"{base} there could not be listed ({answer.why()})", f"find why `{prefix} ls -A {base}` fails"
        )
    entries = answer.out
    if ".lock" in entries:
        holder = ask(rt, ["cat", f"{base}/.lock/run"], rt.vm.helper_timeout_s).first
        return Check(
            "workspace",
            FAIL,
            f"another run holds the machine: {base}/.lock is there" + (f" and names {holder}" if holder else ""),
            f"wait for that run to end. If no run is using the machine, a run that died left the lock: "
            f"`{prefix} rm -rf -- {base}/.lock`",
            {"entries": entries},
        )
    if entries:
        return Check(
            "workspace",
            FAIL,
            f"{base} there holds what earlier runs left, and the next subject can read it: {', '.join(entries)}",
            f"no run holds the machine, so remove it: `{prefix} sudo rm -rf -- {base}`",
            {"entries": entries},
        )
    return Check("workspace", PASS, f"{base} there holds nothing")


def parse_tools(raw: Any) -> list[Tool]:
    """The runtime config's `tools`: a list of `{argv, version}`, the version left out where none is pinned."""
    if not isinstance(raw, list):
        raise ValueError("tools is a list, each entry an argv and, where one is pinned, a version")
    tools: list[Tool] = []
    for index, item in enumerate(raw):
        argv = item.get("argv") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or set(item) - {"argv", "version"}
            or not isinstance(argv, list)
            or not argv
            or not all(isinstance(w, str) and w for w in argv)
        ):
            raise ValueError(f"tools[{index}] is a mapping of argv, a list of words, and, where one is pinned, version")
        version = item.get("version")
        tools.append(Tool(tuple(argv), None if version is None else str(version)))
    return tools


def dockerfile_pins(path: Path) -> list[Tool]:
    """The tools a Dockerfile pins: Claude Code, uv, and Node's major, each found by its install line."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    return [Tool(argv, m.group(1)) for argv, pattern in DOCKERFILE_PINS if (m := pattern.search(text))]


def tools_of(ctx: Context) -> list[Tool]:
    """The tools the check asks: the runtime config's, else the Dockerfile's for a container; and a skill's Claude Code."""
    rt = ctx.runtime
    raw = rt.config.get("tools")
    if raw is not None:
        tools = parse_tools(raw)
    elif isinstance(rt, RT.ContainerRuntime):
        tools = dockerfile_pins(rt.dockerfile)
    else:
        tools = []
    if ctx.scenario.kind == "skill" and not any(t.argv[0] == ctx.claude for t in tools):
        tools.insert(0, Tool((ctx.claude, "--version")))
    return tools


def version_in(line: str) -> str | None:
    """The first version a tool's answer names, such as 1.16.4 in `Terraform v1.16.4`."""
    found = VERSION.search(line)
    return found.group(0) if found else None


def matches(pin: str, found: str | None) -> bool:
    """Whether a version is the pinned one: equal, or within it, as 24.9.0 is within 24."""
    pin = pin.removeprefix("v")
    return found is not None and (found == pin or found.startswith(pin + "."))


def tools(ctx: Context) -> Check:
    """Every tool the runtime pins answers where the subject runs, at its pinned version."""
    rt = ctx.runtime
    if ctx.scenario.kind == "qa":
        return Check("tools", SKIP, "a qa subject runs no tool")
    try:
        wanted = tools_of(ctx)
    except ValueError as exc:
        return Check(
            "tools", FAIL, f"the runtime config's {exc}", "give each entry its argv and, where a version is pinned, its version"
        )
    if not wanted:
        return Check("tools", SKIP, "the runtime pins no tool, and the subject names none")
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for tool in wanted:
        answer = ask(rt, list(tool.argv))
        said = answer.first
        rows.append({"argv": list(tool.argv), "pinned": tool.version, "answered": said or None})
        if not answer.ok or not said:
            problems.append(f"`{shlex.join(tool.argv)}` did not answer ({answer.why()})")
        elif tool.version and not matches(tool.version, version_in(said)):
            problems.append(f"`{shlex.join(tool.argv)}` answered {said!r}, and {tool.version} is pinned")
    facts = {"tools": rows}
    if problems:
        if isinstance(rt, RT.VmRuntime):
            fix = (
                "the machine lacks a tool its template installs, or carries another version: make it again from this "
                "checkout's template (`limactl delete swe-benchmark`, then "
                "`limactl create --name swe-benchmark benchmark/runtime/lima/benchmark.yaml`)"
            )
        elif isinstance(rt, RT.ContainerRuntime):
            fix = "build the image again from this checkout's Dockerfile: pass --build"
        else:
            fix = "install the tool on this machine"
        return Check("tools", FAIL, "; ".join(problems), fix, facts)
    pinned = sum(1 for t in wanted if t.version)
    return Check("tools", PASS, f"{len(wanted)} tool(s) answer, {pinned} of them at their pinned versions", facts=facts)


def working_folder(rt: RT.BaseRuntime) -> str:
    """Where the subject works, as the runtime sees it: the folder runs work in there, the image's workspace, or the sandbox."""
    if isinstance(rt, RT.VmRuntime):
        return rt.remote_base()
    if isinstance(rt, RT.ContainerRuntime):
        return "/workspace"
    return str(rt.sandbox)


def resources(ctx: Context) -> Check:
    """The free disk and the available memory where the subject works, against what the scenario needs."""
    rt, need = ctx.runtime, ctx.scenario.preflight
    if ctx.scenario.kind == "qa":
        return Check("resources", SKIP, "a qa subject runs no command and uses nothing of a runtime")
    folder = working_folder(rt)
    answer = ask(rt, ["sh", "-c", RESOURCES, "sh", folder, MEMINFO])
    figures: dict[str, float] = {}
    for line in answer.out:
        name, _, value = line.partition(" ")
        if value.strip().isdigit():
            figures[name] = int(value) * 1024 / GIB
    disk, memory = figures.get("disk"), figures.get("memory")
    facts = {
        "folder": folder,
        "disk_free_gib": None if disk is None else round(disk, 1),
        "memory_available_gib": None if memory is None else round(memory, 1),
        "needs": {"disk_gib": need.disk_gib, "memory_gib": need.memory_gib},
    }
    prefix = prefix_of(rt)
    problems: list[str] = []
    fixes: list[str] = []
    if need.disk_gib is not None:
        if disk is None:
            problems.append(f"the free disk at {folder} could not be read ({answer.why()})")
        elif disk < need.disk_gib:
            problems.append(f"{disk:.1f} GiB is free at {folder}, and the scenario needs {need.disk_gib:g}")
            fixes.append(
                "free disk there, such as the images and volumes earlier subjects left "
                f"(`{prefix} docker system prune --all --volumes`)"
                if prefix
                else "free disk there"
            )
    if need.memory_gib is not None:
        if memory is None:
            problems.append(f"the runtime reports no available memory, and the scenario needs {need.memory_gib:g} GiB")
        elif memory < need.memory_gib:
            problems.append(f"{memory:.1f} GiB of memory is available, and the scenario needs {need.memory_gib:g}")
            fixes.append(
                f"stop what runs there, such as the containers earlier subjects started (`{prefix} docker ps`), "
                "or restart the machine"
                if prefix
                else "stop what runs there"
            )
    if problems:
        return Check("resources", FAIL, "; ".join(problems), "; ".join(fixes) or "run where the figures can be read", facts)
    shown = [f"{disk:.1f} GiB free at {folder}" if disk is not None else f"no free disk read at {folder}"]
    shown.append(f"{memory:.1f} GiB of memory available" if memory is not None else "no available memory reported")
    against = [f"{v:g} GiB {k}" for k, v in (("of disk", need.disk_gib), ("of memory", need.memory_gib)) if v is not None]
    return Check(
        "resources",
        PASS,
        " and ".join(shown) + (f", against the {' and '.join(against)} the scenario needs" if against else ""),
        facts=facts,
    )


def network(ctx: Context) -> Check:
    """The model API a skill calls, and the registries the scenario names, answer from where the subject runs."""
    rt, scn = ctx.runtime, ctx.scenario
    if scn.kind == "qa":
        return Check("network", SKIP, "a qa subject runs no command; the harness calls its model from here")
    urls = ([MODEL_API] if scn.kind == "skill" else []) + list(scn.preflight.registries)
    if not urls:
        return Check("network", SKIP, "the subject reaches nothing the scenario names")
    answer = ask(rt, ["sh", "-c", REACH, "sh", *urls], (REACH_TIMEOUT_S + 5) * len(urls) + 30, network=True)
    if answer.code == 127:
        return Check("network", FAIL, "curl is not there, so nothing can be asked where the subject runs", "install curl there")
    codes: dict[str, str] = {}
    for line in answer.out:
        code, _, url = line.partition(" ")
        codes[url] = code
    rows = [{"url": u, "answered": codes.get(u, "000")} for u in urls]
    silent = [r["url"] for r in rows if not (r["answered"].isdigit() and 100 <= int(r["answered"]) <= 599)]
    facts = {"urls": rows}
    if silent:
        probe = shlex.join(rt.probe_command(["curl", "-sS", "-o", "/dev/null", silent[0]], network=True))
        return Check(
            "network",
            FAIL,
            f"{', '.join(silent)} answered nothing from where the subject runs",
            f"find why `{probe}` gets no answer",
            facts,
        )
    return Check("network", PASS, f"{len(urls)} URL(s) answer from where the subject runs", facts=facts)


def requires(ctx: Context) -> Check:
    """What the scenario requires works where the subject runs: for `docker`, a Compose stack starts, turns healthy, and stops."""
    rt, scn = ctx.runtime, ctx.scenario
    if not scn.requires:
        return Check("requires", SKIP, "the scenario requires nothing of its runtime")
    project = f"swe-benchmark-preflight-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    answer = ask(rt, ["sh", "-c", COMPOSE, "sh", project, str(COMPOSE_WAIT_S), COMPOSE_IMAGE], COMPOSE_TIMEOUT_S)
    seconds = round(time.monotonic() - started, 1)
    facts = {"docker": {"project": project, "image": COMPOSE_IMAGE, "wait_s": COMPOSE_WAIT_S, "seconds": seconds}}
    see = "run `docker info` and `docker compose version` there, and check that it pulls from Docker Hub"
    if answer.code == 124:
        # The script ran past its bound; the stack may be up, so it is taken down by its project name.
        ask(rt, ["docker", "compose", "-p", project, "down", "-v", "--remove-orphans", "--timeout", "10"])
        return Check("requires", FAIL, f"the Compose check did not finish in {COMPOSE_TIMEOUT_S} s there", see, facts)
    if answer.code == 3:
        return Check(
            "requires",
            FAIL,
            f"a Compose stack of {COMPOSE_IMAGE} did not start and turn healthy within {COMPOSE_WAIT_S} s there ({answer.why()})",
            see,
            facts,
        )
    if answer.code == 4:
        return Check("requires", FAIL, f"a Compose stack started there and did not stop ({answer.why()})", see, facts)
    if not answer.ok:
        return Check("requires", FAIL, f"the Compose check could not run there ({answer.why()})", see, facts)
    return Check(
        "requires",
        PASS,
        f"docker: a Compose stack started, turned healthy within {COMPOSE_WAIT_S} s, and stopped, in {seconds:g} s in all",
        facts=facts,
    )


# In the order they run: what this machine knows first, then the keys,
# then the runtime, the slowest last.
CHECKS: tuple[tuple[str, Callable[[Context], Check]], ...] = (
    ("budgets", budgets),
    ("checkout", checkout),
    ("references", references),
    ("awake", awake),
    ("subject_key", subject_key),
    ("judge_keys", judge_keys),
    ("runtime", runtime),
    ("workspace", workspace),
    ("tools", tools),
    ("resources", resources),
    ("network", network),
    ("requires", requires),
)


def line(check: Check) -> str:
    """A check as the console shows it: its status, its name, what it found, and the fix of a failure."""
    out = f"  {check.status.upper() if check.status == FAIL else check.status:4}  {check.name:12} {check.detail}"
    return out + (f"\n  {'':4}  {'':12} fix: {check.fix}" if check.fix and check.status == FAIL else "")


def run(ctx: Context, say: Callable[[str], None] = print) -> dict[str, Any]:
    """Every check in order, up to the first that fails; the record `run.json` keeps under `preflight`.

    A check that raises has failed, and says why.
    """
    done: list[Check] = []
    for name, check in CHECKS:
        try:
            found = check(ctx)
        except Exception as exc:
            found = Check(name, FAIL, f"the check could not run: {type(exc).__name__}: {exc}")
        done.append(found)
        say(line(found))
        if found.status == FAIL:
            break
    failed = next((c.name for c in done if c.status == FAIL), None)
    return {
        "passed": failed is None,
        "failed": failed,
        "checks": [c.as_dict() for c in done],
        "not_run": [name for name, _ in CHECKS[len(done) :]],
    }


@contextlib.contextmanager
def held_awake(hold: bool = True) -> Iterator[str | None]:
    """Hold this machine awake while the body runs; yields a note when it could not.

    On macOS it starts `caffeinate -i -s -w <this process>`: no idle sleep,
    and no system sleep while on AC power, until this process ends. It is
    stopped when the body ends. Elsewhere it holds nothing and says nothing.
    """
    binary = shutil.which("caffeinate") if hold and system() == "darwin" else None
    proc: subprocess.Popen | None = None
    note = None
    if binary:
        try:
            proc = subprocess.Popen(
                [binary, "-i", "-s", "-w", str(os.getpid())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            note = f"nothing holds this machine awake: caffeinate did not start ({exc.strerror or type(exc).__name__})"
    elif hold and system() == "darwin":
        note = "nothing holds this machine awake: caffeinate is not on the path"
    try:
        yield note
    finally:
        if proc is not None:
            proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
