"""Where the subject runs: the host, a container, or another machine.

One protocol, three implementations, the same streams in the run folder
either way. A runtime decides two things: the command this machine
actually executes, and the environment that command sees. Everything
else, the workspace and the collected artifact, is the same.

Everything a subject can reach lives in a sandbox outside the checkout:
the workspace, the private HOME and TMPDIR, and a staged copy of the
plugin and of the target. The staged plugin carries the payload a skill
reads (the manifest, the skills, the agents, the lenses, the guideline,
the checker) and nothing of the benchmark, so no answer key and no
earlier repeat's judge prompt is in reach, and no parent folder holds a
CLAUDE.md for the subject to load. The run folder keeps the record; the
sandbox is removed when the run ends.

Isolation is a choice, and the choice is named:

- `host` isolates by convention only. A private `HOME` and a private
  `TMPDIR` in the sandbox keep a subject from writing into the
  operator's account by accident. Nothing stops a subject that means to.
  The subject runs as the harness's own user, so it can read what that
  user reads: the harness's environment, the judges' keys in it (on
  Linux, through `/proc/<pid>/environ` of the harness), and the answer
  files at their fixed paths in the checkout. The sandbox keeps those out
  of the paths the subject is given, not out of its reach. A scenario
  whose subject must not reach them does not list `host`.
- `container` isolates with Docker: the plugin checkout and the target
  read-only, the workspace read-write, the keys passed one by one, every
  capability dropped, and memory, processor, and process count bounded.
  Nothing of the harness's machine is in the container but the three
  mounts, so the judges' keys and the answer files are out of reach.
- `vm` runs the command on another machine through a configured prefix.
  The harness provisions no machine. It copies the staged plugin and
  target into the run's folder there, hands the subject its key through
  a file, and composes the prefix; the tests cover that with a fake
  prefix. The machine is the boundary: `runtime/lima/benchmark.yaml`
  makes one that holds nothing of this machine, so the judges' keys and
  the answer files are out of the subject's reach.

A subject is stopped whole, however it ends: past its timeout, on a clean
exit that left children behind, or when the harness itself is
interrupted. The host runtime starts it in a process group of its own and
kills the group every time. The container runtime names its container
and kills the container, because killing the `docker run` client leaves
the container running and paying. The vm runtime kills the subject's
process group on the other machine through the prefix, for the same
reason: killing the prefix here leaves the subject running there.

A path on this machine means nothing inside a container or on another
machine. So a runtime also answers where the plugin checkout and the
target are as the subject sees them, and the subject is told those
paths, never the ones on this machine. For the same reason a runtime
answers what it carries, such as `claude --version`, by a probe that
runs where the subject runs.

A subject in phases runs several sessions in one workspace. Each session
gets a HOME and a TMPDIR of its own, named by `use_session`, so a fresh
session finds nothing an earlier one left there, and a resumed one finds
its own. The container runtime starts every command in a new container,
whose HOME is new each time. The harness can stop a command before it
ends, through the `stop` event `run` takes, and it can move a path of the
workspace out of the subject's reach and back (`hide`, `show`).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal as signals
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import providers as P
from . import versions as V
from .capture import CliStream

NAMES = ("host", "container", "vm")
# What a scenario may require of its runtime (`requires`), and which
# runtimes can provide each. `docker` is a Docker engine the subject runs
# containers on. The vm runtime provides it: the subject runs as that
# machine's user, and `runtime/lima/benchmark.yaml` makes a machine whose
# engine listens at the default socket, which needs no setting. The container
# runtime runs no engine and drops every capability. The host runtime hands
# the subject a private HOME and the variables run.py passes through, so an
# engine's settings (DOCKER_HOST, DOCKER_CONTEXT, a context under
# ~/.docker) never reach it.
REQUIREMENTS = ("docker",)
PROVIDES: dict[str, tuple[str, ...]] = {"host": (), "container": (), "vm": ("docker",)}
DEFAULT_IMAGE = "swe-guidelines-benchmark:latest"
# Where the container mounts what it is given. Both are read-only.
CONTAINER_PLUGIN = "/plugin"
CONTAINER_TARGET = "/target"
# What a subject may read of the plugin checkout: the payload the skills
# reference, and nothing else. The benchmark, its fixtures and their
# answer keys, the docs, and the repository's own CLAUDE.md stay out.
PLUGIN_PAYLOAD = (".claude-plugin", "skills", "agents", "lenses", "architecture.md", "checkers", "LICENSE")
# What no staged copy carries: the caches a tool leaves behind.
STAGE_IGNORE = shutil.ignore_patterns(*V.CACHES)
# The subject's own key, by the name the subject reads it under and the
# name the harness reads it from. `claude -p` reads ANTHROPIC_API_KEY; its
# value comes from SUBJECT_ANTHROPIC_API_KEY, never from a judge's key, so
# a subject that leaks its key leaks no judge's.
SUBJECT_KEYS = {"ANTHROPIC_API_KEY": "SUBJECT_ANTHROPIC_API_KEY"}


def judge_key_values(source: dict[str, str] | None = None) -> set[str]:
    """The values of every provider key in the harness's environment."""
    source = dict(os.environ) if source is None else source
    return {source[n] for n in P.JUDGE_KEY_NAMES if source.get(n)}


def scrub(env: dict[str, str], source: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """The environment without any judge key, and the names that held one.

    A variable goes when its value is a judge's key, whatever its name, so
    a subject key set to a judge's key is dropped rather than handed on.
    Every process a runtime starts gets its environment through here.
    """
    judged = judge_key_values(source)
    named = (set(P.JUDGE_KEY_NAMES) | set(SUBJECT_KEYS.values())) - set(SUBJECT_KEYS)
    dropped = sorted(k for k, v in env.items() if v in judged or k in named)
    return {k: v for k, v in env.items() if k not in dropped}, dropped


def clean_env() -> dict[str, str]:
    """The harness's own environment with every judge key out: for the helper commands."""
    return scrub(dict(os.environ))[0]


def new_sandbox() -> Path:
    """A fresh folder outside every checkout, for one run."""
    return Path(tempfile.mkdtemp(prefix="swe-guidelines-benchmark-")).resolve()


def stage_plugin(root: Path, dest: Path) -> Path:
    """Copy the plugin payload of `root` into `dest` and return `dest`."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in PLUGIN_PAYLOAD:
        source = root / name
        if source.is_dir():
            shutil.copytree(source, dest / name, symlinks=False, ignore=STAGE_IGNORE, dirs_exist_ok=True)
        elif source.is_file():
            shutil.copy2(source, dest / name)
    return dest


def stage_target(target: Path, dest: Path) -> Path:
    """Copy the target folder alone into `dest`, so no sibling of it is in reach.

    The copy is the whole target, its tests included, less the caches: the
    subject reviews what the judges read as evidence, and the judges read
    this copy.
    """
    shutil.copytree(target, dest, symlinks=True, ignore=STAGE_IGNORE, dirs_exist_ok=True)
    return dest


@dataclass(frozen=True)
class ExitStatus:
    """How a subject ended."""

    code: int
    signal: int | None = None
    duration_s: float = 0.0
    timed_out: bool = False
    # The subject exited cleanly and said it failed: `is_error` in its result.
    is_error: bool = False
    # The harness stopped it at a bound before it ended.
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out and not self.is_error and not self.stopped

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "signal": self.signal,
            "duration_s": round(self.duration_s, 3),
            "timed_out": self.timed_out,
            "is_error": self.is_error,
            "stopped": self.stopped,
        }


class Runtime(Protocol):
    """What every runtime does."""

    name: str

    def prepare(self, workspace: Path) -> Path: ...

    def plugin_path(self) -> str | None: ...

    def target_path(self) -> str | None: ...

    def run(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        streams: CliStream,
        timeout_s: int = 900,
        stop: threading.Event | None = None,
    ) -> ExitStatus: ...

    def collect(self, globs: list[str]) -> list[Path]: ...

    def teardown(self) -> None: ...


class BaseRuntime:
    """What the three share: the workspace, the collection, the local spawn."""

    name = "base"

    def __init__(
        self,
        run_dir: Path,
        target: Path | None = None,
        config: dict | None = None,
        plugin: Path | None = None,
        sandbox: Path | None = None,
    ) -> None:
        # Absolute, because the subject's working directory is the workspace:
        # a relative HOME, TMPDIR, or mount source would be read from there.
        self.run_dir = Path(run_dir).resolve()
        # Where the subject lives. The harness passes a fresh folder outside
        # the checkout; a caller that passes none keeps it in the run folder.
        self.sandbox = Path(sandbox).resolve() if sandbox else self.run_dir
        self.owns_sandbox = sandbox is not None
        self.target = Path(target).resolve() if target else None
        self.plugin = Path(plugin).resolve() if plugin else None
        self.config = dict(config or {})
        self.workspace = self.sandbox / "workspace"
        self.slot: str | None = None
        # The session within the repeat whose HOME and TMPDIR the next command gets; None for the repeat's own.
        self.session: str | None = None
        self.prepared = False
        self.live: set[int] = set()

    def stage(self) -> None:
        """Replace the plugin and the target with copies in the sandbox."""
        if not self.owns_sandbox:
            return
        if self.plugin:
            self.plugin = stage_plugin(self.plugin, self.sandbox / "plugin")
        if self.target:
            self.target = stage_target(self.target, self.sandbox / "target")

    def plugin_path(self) -> str | None:
        """The plugin checkout as the subject sees it. On this machine, where it is."""
        return str(self.plugin) if self.plugin else None

    def target_path(self) -> str | None:
        """The target as the subject sees it. On this machine, where it is."""
        return str(self.target) if self.target else None

    def prepare(self, workspace: Path | None = None) -> Path:
        """Make the workspace the subject works in and return it."""
        self.workspace = Path(workspace) if workspace else self.workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.prepared = True
        return self.workspace

    def prepare_repeat(self, index: int) -> Path:
        """A fresh workspace for one repeat, so no repeat sees another's files."""
        self.slot = str(index)
        self.session = None
        return self.prepare(self.sandbox / "workspace" / self.slot)

    def use_session(self, name: str | None) -> None:
        """Give the next commands the HOME and TMPDIR of the named session of this repeat; None for the repeat's own."""
        self.session = name

    def hidden(self, rel: str) -> Path:
        """Where `hide` keeps a path of the workspace: outside it, in the repeat's own folder."""
        return self.sandbox / "hidden" / (self.slot or "run") / rel

    def hide(self, rel: str) -> None:
        """Move a path of the workspace out of it, when it is there; the subject is not given where it goes."""
        _move(self.workspace / rel, self.hidden(rel))

    def show(self, rel: str) -> None:
        """Move a hidden path back into the workspace, when one is hidden."""
        _move(self.hidden(rel), self.workspace / rel)

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        """The command this machine runs. The host runs the subject itself."""
        return list(argv)

    def environment(self, env: dict[str, str]) -> dict[str, str]:
        """The environment the command sees on this machine."""
        return dict(env)

    def run(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        streams: CliStream,
        timeout_s: int = 900,
        stop: threading.Event | None = None,
    ) -> ExitStatus:
        """Spawn the composed command and write both its streams as they come.

        A `stop` event the harness sets ends the command as a timeout
        does: its whole group is killed, and the status says `stopped`.
        """
        command = self.command(argv, cwd)
        streams.note(f"[{self.name}] {' '.join(command)}")
        environment, dropped = scrub(self.environment(env))
        for name in dropped:
            streams.note(f"[{self.name}] {name} holds a judge's key or names one; the subject is not handed it")
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # UTF-8 whatever the locale says, and a byte that is not
                # UTF-8 becomes U+FFFD: a decode error would end the reader
                # and leave the pipe full, with the subject blocked on it.
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                # A group of its own, so a timeout reaches every process the
                # subject started, not only the first.
                start_new_session=True,
            )
        except OSError as exc:
            # A binary that is not there is a repeat that failed, recorded as
            # the shell records it, never a run that leaves no results.
            streams.note(f"[{self.name}] could not start: {type(exc).__name__}: {exc}")
            return ExitStatus(code=127, duration_s=time.monotonic() - started)
        self.live.add(proc.pid)
        readers = [
            threading.Thread(target=_pipe, args=(proc.stdout, "out", streams), daemon=True),
            threading.Thread(target=_pipe, args=(proc.stderr, "err", streams), daemon=True),
        ]
        for r in readers:
            r.start()
        timed_out = stopped = False
        deadline = started + timeout_s
        try:
            while True:
                left = deadline - time.monotonic()
                try:
                    proc.wait(timeout=max(0.0, min(left, 0.2) if stop is not None else left))
                    break
                except subprocess.TimeoutExpired:
                    if stop is not None and stop.is_set():
                        stopped = True
                        streams.note(f"[{self.name}] stopped by the harness at a bound")
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        streams.note(f"[{self.name}] timed out after {timeout_s}s; stopping the subject")
                        break
        finally:
            # Every way out stops the whole group: a timeout, a clean exit
            # that left a child running, and an interrupt of the harness.
            self.stop(proc)
            proc.wait()
            self.live.discard(proc.pid)
        for r in readers:
            r.join(timeout=5)
        code = proc.returncode or 0
        signal = -code if code < 0 else None
        return ExitStatus(
            code=code,
            signal=signal,
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
            stopped=stopped,
        )

    def probe_command(self, argv: list[str], network: bool = False) -> list[str]:
        """The command a probe runs on this machine. The host runs it as it is, with this machine's network.

        `network` asks for the network where a probe has none by default: in a container.
        """
        return list(argv)

    def probe(self, argv: list[str], timeout_s: int = 120) -> str | None:
        """The first line a short command prints where the subject runs, or None when it fails.

        A probe asks the runtime what it carries, such as `claude --version`.
        It runs with the harness's environment less every judge key, and
        its output is never part of a repeat.
        """
        try:
            out = subprocess.run(
                self.probe_command(argv),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_s,
                env=self.probe_env(),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        return lines[0] if out.returncode == 0 and lines else None

    def probe_env(self) -> dict[str, str]:
        """The environment a probe runs in here: the harness's own less every judge key."""
        return clean_env()

    def image_version(self) -> dict | None:
        """The image the subject runs in, by name and id; None where there is no image."""
        return None

    def stop(self, proc: subprocess.Popen) -> None:
        """Kill the subject's whole process group; a group already gone is no error."""
        kill_group(proc.pid)
        if proc.poll() is None:
            proc.kill()

    def collect(self, globs: list[str]) -> list[Path]:
        """Every workspace file one of the globs names, once, in path order.

        A file outside the workspace, reached through `..` or a symlink, is
        not the subject's output and is left out. So is anything under a
        `.git`: its objects are compressed where no redaction reads them,
        and the output's zip is the record of the output.
        """
        root = self.workspace.resolve()
        found: set[Path] = set()
        for pattern in globs:
            for path in self.workspace.glob(pattern):
                if ".git" in path.relative_to(self.workspace).parts:
                    continue
                if path.is_file() and path.resolve().is_relative_to(root):
                    found.add(path)
        return sorted(found)

    def teardown(self) -> None:
        """Stop whatever is still running, then remove the sandbox the harness made.

        The run folder is the record and it stays; the sandbox held the
        subject's copies and its workspace, whose files were collected.
        """
        for pgid in list(self.live):
            kill_group(pgid)
            self.live.discard(pgid)
        if self.owns_sandbox and self.sandbox != self.run_dir:
            shutil.rmtree(self.sandbox, ignore_errors=True)


def _move(source: Path, dest: Path) -> None:
    """Move a file or a folder to a path, replacing what is there; nothing when the source is not there."""
    if not (source.exists() or source.is_symlink()):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir() and not dest.is_symlink():
        shutil.rmtree(dest)
    elif dest.exists() or dest.is_symlink():
        dest.unlink()
    shutil.move(str(source), str(dest))


def kill_group(pgid: int) -> None:
    """SIGKILL a process group, quietly when it is already gone."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signals.SIGKILL)


def _pipe(handle, stream: str, streams: CliStream) -> None:
    """Read one pipe to its end, one line at a time, into the stream file."""
    if handle is None:
        return
    with handle:
        for line in handle:
            streams.write(stream, line)


class HostRuntime(BaseRuntime):
    """This machine, with a private HOME and TMPDIR in the sandbox.

    No boundary: the subject runs as the harness's user and can read what
    that user reads, the harness's environment with the judges' keys and
    the answer files in the checkout among it. `container` is the runtime
    that keeps them out of reach.
    """

    name = "host"

    def private(self, name: str) -> Path:
        """The private HOME or TMPDIR, one per repeat once a repeat is prepared, and one per session in it."""
        base = self.sandbox / name
        if self.slot is None:
            return base
        return base / self.slot / self.session if self.session else base / self.slot

    def prepare(self, workspace: Path | None = None) -> Path:
        path = super().prepare(workspace)
        self.private("home").mkdir(parents=True, exist_ok=True)
        self.private("tmp").mkdir(parents=True, exist_ok=True)
        return path

    def environment(self, env: dict[str, str]) -> dict[str, str]:
        out = dict(env)
        for name, variable in (("home", "HOME"), ("tmp", "TMPDIR")):
            self.private(name).mkdir(parents=True, exist_ok=True)
            out[variable] = str(self.private(name))
        return out


class ContainerRuntime(BaseRuntime):
    """`docker run --rm` from the image `runtime/Dockerfile` builds."""

    name = "container"

    def __init__(
        self,
        run_dir: Path,
        target: Path | None = None,
        config: dict | None = None,
        plugin: Path | None = None,
        sandbox: Path | None = None,
    ) -> None:
        super().__init__(run_dir, target, config, plugin, sandbox)
        self.image = self.config.get("image", DEFAULT_IMAGE)
        self.dockerfile = Path(self.config.get("dockerfile", Path(__file__).resolve().parent.parent / "runtime" / "Dockerfile"))
        self.docker = self.config.get("docker", "docker")
        self.keys = list(self.config.get("keys", []))
        # The bounds on what one subject may take; the config can raise them.
        self.memory = str(self.config.get("memory", "4g"))
        self.cpus = str(self.config.get("cpus", "2"))
        self.pids = str(self.config.get("pids", "512"))
        self.container_name: str | None = None

    def build_command(self) -> list[str]:
        """The image build, for a caller that wants to build before it runs."""
        return [self.docker, "build", "-t", self.image, "-f", str(self.dockerfile), str(self.dockerfile.parent)]

    def build(self, streams: CliStream | None = None) -> ExitStatus:
        """Build the image, writing the build output into the stream when given."""
        command = self.build_command()
        started = time.monotonic()
        try:
            proc = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", env=clean_env())
        except OSError as exc:
            # No container engine: the build failed, recorded as the shell records it.
            if streams is not None:
                streams.note(f"[build] could not start: {type(exc).__name__}: {exc}")
            return ExitStatus(code=127, duration_s=time.monotonic() - started)
        if streams is not None:
            for line in (proc.stdout + proc.stderr).splitlines():
                streams.write("err", line)
        return ExitStatus(code=proc.returncode, duration_s=time.monotonic() - started)

    def prepare(self, workspace: Path | None = None) -> Path:
        """The workspace, open to the image's user.

        A bind mount keeps this machine's owner and mode, and the image's
        user is not this machine's user on a Linux runner. The workspace
        is the one folder the subject writes, so it is opened to every
        user; the sandbox around it stays private to this one.
        """
        path = super().prepare(workspace)
        path.chmod(0o777)
        return path

    def plugin_path(self) -> str | None:
        return CONTAINER_PLUGIN if self.plugin else None

    def target_path(self) -> str | None:
        return CONTAINER_TARGET if self.target else None

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        # A name per run, so a timeout can kill this container and no other.
        self.container_name = f"swe-guidelines-benchmark-{uuid.uuid4().hex[:12]}"
        out = [
            self.docker,
            "run",
            "--rm",
            "--name",
            self.container_name,
            "--init",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--pids-limit",
            self.pids,
            "-v",
            f"{self.workspace}:/workspace:rw",
            "-w",
            "/workspace",
        ]
        if self.plugin:
            out += ["-v", f"{self.plugin}:{CONTAINER_PLUGIN}:ro"]
        if self.target:
            out += ["-v", f"{self.target}:{CONTAINER_TARGET}:ro"]
        for key in self.keys:
            out += ["-e", key]
        out.append(self.image)
        return out + list(argv)

    def environment(self, env: dict[str, str]) -> dict[str, str]:
        """Docker carries the keys by name, so the local environment holds them."""
        return dict(env)

    def probe_command(self, argv: list[str], network: bool = False) -> list[str]:
        """A probe runs in a container of the same image, with no mount, no key, and no network unless it asks for one."""
        return [self.docker, "run", "--rm", *([] if network else ["--network", "none"]), self.image, *argv]

    def image_version(self) -> dict | None:
        """The image's name and the id the engine gives it; the id is None when the engine does not answer.

        A tag names whatever was built last under it. The id names the
        image that ran.
        """
        try:
            out = subprocess.run(
                [self.docker, "image", "inspect", "--format", "{{.Id}}", self.image],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                env=clean_env(),
            )
        except (OSError, subprocess.SubprocessError):
            return {"name": self.image, "id": None}
        found = out.stdout.strip() if out.returncode == 0 else ""
        return {"name": self.image, "id": found or None}

    def stop(self, proc: subprocess.Popen) -> None:
        """Kill the container by name, then the client."""
        if self.container_name:
            subprocess.run([self.docker, "kill", self.container_name], capture_output=True, check=False, env=clean_env())
        super().stop(proc)


@dataclass
class VmConfig:
    """How to reach the other machine, and how to get files there and back."""

    exec_prefix: list[str] = field(default_factory=list)
    copy: list[str] = field(default_factory=list)
    sync: list[str] = field(default_factory=list)
    remote_workspace: str = "/tmp/benchmark-workspace"
    fetch: list[str] = field(default_factory=list)
    remote_plugin: str | None = None
    remote_target: str | None = None
    # The names the prefix takes from this machine's environment besides
    # PATH and the subject's own: `limactl` does not start without HOME.
    prefix_env: list[str] = field(default_factory=lambda: ["HOME"])
    # How long a command other than the subject may take: a copy, a fetch,
    # a key, a kill, the removal.
    helper_timeout_s: int = 600
    # A command run there before every repeat; a repeat whose check fails
    # runs no subject. The Lima config checks that the firewall is there.
    check: list[str] = field(default_factory=list)


# The scripts below run there, with every path as an argument, never
# spliced into the script, so a space or a quote in a path stays one word.
#
# Removal goes through sudo where sudo answers without a password, because
# a container the subject ran as root leaves files its user cannot remove.
REMOVE = 'remove() { sudo -n rm -rf -- "$@" 2>/dev/null || rm -rf -- "$@"; }; '
# The machine is this run's alone: the lock is a folder, made or refused
# in one step, and it names the run that holds it. Exit 3: another run
# holds it.
LOCK = 'umask 077 && mkdir -p "$1" || exit 2; mkdir "$1/.lock" 2>/dev/null || exit 3; printf "%s\n" "$2" > "$1/.lock/run"'
# Before every repeat: the run's folder, removed whole and made again,
# private to that machine's user, so nothing an earlier repeat left in it
# reaches this one; a CLAUDE.md anywhere in it would, since Claude Code
# loads every CLAUDE.md from its working folder up. The lock lives beside
# it and stays. Fetch has already brought each earlier workspace back.
# Then the machine's check. Arguments: the run's folder, then the check's
# words. Exit 3: something stayed. Exit 4: the check failed.
PREPARE = REMOVE + (
    'run=$1; shift; remove "$run"; [ ! -e "$run" ] || exit 3; '
    'umask 077 && mkdir -p "$run" || exit 2; '
    'if [ "$#" -gt 0 ]; then "$@" >/dev/null 2>&1 || exit 4; fi'
)
# A key goes through stdin into a file of the repeat's own that only that
# user can read.
WRITE_KEY = 'umask 077 && mkdir -p "$1" && cat > "$2"'
# What runs before the subject: each named key file becomes that variable
# and nothing else does, HOME and TMPDIR become the repeat's own, and the
# subject starts in its workspace, in a process group of its own whose id
# goes into a file, so a stop reaches every process it started. `setsid`
# makes the group; where there is none, the command's own group, which a
# remote shell makes new, is the one written. Exit 125: the wrapper failed.
WRAPPER = (
    "keys=$1 home=$2 tmp=$3 work=$4 group=$5 count=$6; shift 6; "
    'while [ "$count" -gt 0 ]; do value=$(cat "$keys/$1") || exit 125; export "$1=$value"; shift; count=$((count - 1)); done; '
    'mkdir -p "$home" "$tmp" "$work" "${group%/*}" && cd "$work" || exit 125; '
    'export HOME="$home" TMPDIR="$tmp"; '
    'if command -v setsid >/dev/null 2>&1; then exec setsid -w sh -c \'echo "$$" > "$0" && exec "$@"\' "$group" "$@"; fi; '
    'ps -o pgid= -p "$$" | tr -d " " > "$group" && exec "$@"'
)
# A stop: kill the subject's process group, then wait for it to be gone.
# `kill -0` takes no `--`: dash, Ubuntu's sh, reads it as a process id.
KILL = (
    'group=$(cat "$1" 2>/dev/null) || exit 0; [ -n "$group" ] || exit 0; '
    'kill -s KILL -- "-$group" 2>/dev/null; n=0; '
    'while kill -0 "-$group" 2>/dev/null && [ "$n" -lt 50 ]; do sleep 0.1; n=$((n + 1)); done; exit 0'
)
# The end of a run: its folder goes, then the lock it holds.
RELEASE = REMOVE + 'remove "$1"; rm -rf -- "$2"; [ ! -e "$1" ]'
# A path moved to another, replacing what is there; nothing when it is not there.
MOVE = '[ -e "$1" ] || [ -L "$1" ] || exit 0; mkdir -p -- "${2%/*}" && rm -rf -- "$2" && mv -- "$1" "$2"'
# What the machine's Docker holds: one line per container, network, and
# volume, each its kind, its id, and its name; a volume's id is its name.
# A machine with no Docker holds none. Argument: the Docker command. Exit
# 3: Docker is there and did not answer.
DOCKER_LIST = (
    'docker=$1; command -v "$docker" >/dev/null 2>&1 || exit 0; '
    '"$docker" ps -a --no-trunc --format "container {{.ID}} {{.Names}}" || exit 3; '
    '"$docker" network ls --no-trunc --format "network {{.ID}} {{.Name}}" || exit 3; '
    '"$docker" volume ls --format "volume {{.Name}} {{.Name}}" || exit 3'
)
# The removal of what a subject's Docker made: the containers first, with
# their anonymous volumes, so nothing holds a network or a volume; then the
# networks; then the volumes. Then each item that is still there, one per
# line. Arguments: the Docker command, then each item as `<kind>:<id>`.
DOCKER_REMOVE = (
    'docker=$1; shift; for kind in container network volume; do for item in "$@"; do '
    '[ "${item%%:*}" = "$kind" ] || continue; id=${item#*:}; case $kind in '
    'container) "$docker" rm -f -v "$id";; network) "$docker" network rm "$id";; volume) "$docker" volume rm -f "$id";; '
    "esac >/dev/null 2>&1; done; done; "
    'for item in "$@"; do "$docker" "${item%%:*}" inspect "${item#*:}" >/dev/null 2>&1 && printf "%s\\n" "$item"; done; exit 0'
)
DOCKER_KINDS = ("container", "network", "volume")
# The command that reaches the machine's Docker there. A test points it at
# a stand-in: a test's other machine runs on the machine the test runs on,
# and no test may reach that machine's Docker.
DOCKER = "docker"

# One item a machine's Docker holds: its kind, its id, and its name.
DockerItem = tuple[str, str, str]


def docker_items(lines: list[str]) -> list[DockerItem]:
    """What a listing of the machine's Docker names, in its order: each item's kind, id, and name."""
    out: list[DockerItem] = []
    for line in lines:
        kind, _, rest = line.strip().partition(" ")
        ident, _, name = rest.partition(" ")
        if kind in DOCKER_KINDS and ident:
            out.append((kind, ident, name or ident))
    return out


def described(items: list[DockerItem]) -> str:
    """Items by kind, each kind with its count and its names: `2 containers (a, b), 1 volume (c)`."""
    parts = []
    for kind in DOCKER_KINDS:
        names = [name for k, _, name in items if k == kind]
        if names:
            parts.append(f"{len(names)} {kind}{'' if len(names) == 1 else 's'} ({', '.join(names)})")
    return ", ".join(parts)


def fill(words: list[str], local: str, remote: str) -> list[str]:
    """A configured command with `{local}` and `{remote}` replaced in every word."""
    return [w.replace("{local}", local).replace("{remote}", remote) for w in words]


class VmRuntime(BaseRuntime):
    """Another machine, reached through a command prefix.

    The prefix is configuration, for example
    `["limactl", "shell", "--workdir", "/", "swe-benchmark", "--"]`. It
    has to hand its words on as words (`limactl shell`, `docker exec`);
    one that joins them into a remote shell line, as `ssh` does, needs a
    wrapper. The harness provisions no machine and starts none.

    A run takes the machine alone: a lock under `remote_workspace` refuses
    a second run there until the first gives it back. Each run gets its
    own folder under `remote_workspace`, named after the run folder, made
    with mode 0700, and removed at the end. It mirrors the sandbox:
    `plugin/` and `target/` hold the staged copies, and `workspace/`,
    `home/`, `tmp/`, and `keys/` hold one folder per repeat. Before every
    repeat the run's folder is removed whole and made again, and the copy
    command makes `plugin/` and `target/` in it, so no repeat reads what
    an earlier one left there: `{local}` is the staged folder here and
    `{remote}` the path its copy takes there. The folders above it
    outlast the repeat and the run. The sync
    and fetch commands take the repeat's workspace here and there.
    `remote_plugin` and `remote_target` override the copies with paths
    the operator placed on that machine, which nothing copies and no
    version names.

    The subject's key never travels in a command line, and the prefix
    never holds it here. Before every repeat it goes through stdin into a
    file of mode 0600 under the repeat's `keys/`, and the wrapper exports
    the names the harness hands it and no other. The prefix runs here
    with the subject's environment and the names `prefix_env` lists, and
    nothing else of this machine's.

    A step that fails there, the machine stopped or taken, fails the
    repeat with a note, as a key that cannot be written does, and the run
    still writes its results. A stop kills the subject's process group
    there before the prefix here, because the prefix going does not stop
    what it started there. Every command other than the subject has a
    timeout, and a timeout is noted.

    A container the subject starts is a child of Docker's daemon there,
    so no stop reaches it. When the run takes the machine, the harness
    lists what Docker holds there. After each repeat (`remove_docker`),
    and when the run gives the machine back, it removes every container,
    network, and volume that was not on that list, and notes what went.
    """

    name = "vm"

    def __init__(
        self,
        run_dir: Path,
        target: Path | None = None,
        config: dict | None = None,
        plugin: Path | None = None,
        sandbox: Path | None = None,
    ) -> None:
        super().__init__(run_dir, target, config, plugin, sandbox)
        raw = self.config
        self.vm = VmConfig(
            exec_prefix=list(raw.get("exec_prefix", [])),
            copy=list(raw.get("copy", [])),
            sync=list(raw.get("sync", [])),
            remote_workspace=str(raw.get("remote_workspace", "/tmp/benchmark-workspace")),
            fetch=list(raw.get("fetch", [])),
            remote_plugin=raw.get("remote_plugin"),
            remote_target=raw.get("remote_target"),
            prefix_env=list(raw.get("prefix_env", ["HOME"])),
            helper_timeout_s=int(raw.get("helper_timeout_s", 600)),
            check=list(raw.get("check", [])),
        )
        self.locked = False
        self.released = False
        # What the machine's Docker held when the run took the machine, by
        # kind and id: none of it is the run's to remove. None when Docker
        # did not answer then, and `docker_unread` says how.
        self.docker_before: set[tuple[str, str]] | None = None
        self.docker_unread: str | None = None
        # Why the prepared repeat cannot run, with the exit code that said so.
        self.failure: tuple[int, str] | None = None
        # The key names the wrapper exports for the repeat that runs.
        self.names: list[str] = []
        # What went wrong outside a subject's stream, for the next note.
        self.notes: list[str] = []

    def stage(self) -> None:
        """Refuse a config that reaches no machine, then stage here as every runtime does."""
        if not self.vm.exec_prefix:
            raise ValueError("the vm runtime needs exec_prefix in its runtime config")
        super().stage()

    def probe_command(self, argv: list[str], network: bool = False) -> list[str]:
        """A probe runs on the other machine, through the prefix, outside any workspace, with that machine's network."""
        return [*self.vm.exec_prefix, *argv]

    def plugin_path(self) -> str | None:
        if not self.plugin:
            return None
        if self.vm.remote_plugin:
            return str(self.vm.remote_plugin)
        return self.copied("plugin", "remote_plugin", "to run a skill")

    def target_path(self) -> str | None:
        if not self.target:
            return None
        if self.vm.remote_target:
            return str(self.vm.remote_target)
        return self.copied("target", "remote_target", "to run on a target")

    def copied(self, name: str, override: str, purpose: str) -> str:
        """Where the copy of a staged folder is there: `plugin/` or `target/` in the run's folder."""
        if not self.vm.copy:
            raise ValueError(f"the vm runtime needs copy, or {override}, in its runtime config {purpose}")
        return f"{self.remote_run()}/{name}"

    def copies(self) -> list[tuple[Path, str]]:
        """The staged folders the copy command takes there, each with its name there; none an override replaces."""
        out = []
        if self.plugin and not self.vm.remote_plugin:
            out.append((self.plugin, "plugin"))
        if self.target and not self.vm.remote_target:
            out.append((self.target, "target"))
        return out

    def remote_base(self) -> str:
        """The folder there that holds the run folders and the lock."""
        return self.vm.remote_workspace.rstrip("/")

    def remote_run(self) -> str:
        """This run's folder on the other machine, named after the run folder.

        Two runs never share one, so no run finds what an earlier one left.
        """
        return f"{self.remote_base()}/{self.run_dir.name}"

    def remote_part(self, name: str) -> str:
        """The workspace, HOME, TMPDIR, keys, or group file there: one per repeat once a repeat is prepared.

        HOME and TMPDIR are one per session of the repeat, when a session is named.
        """
        base = f"{self.remote_run()}/{name}"
        if self.slot is None:
            return base
        if self.session and name in ("home", "tmp"):
            return f"{base}/{self.slot}/{self.session}"
        return f"{base}/{self.slot}"

    def hide(self, rel: str) -> None:
        """Move a path of the workspace there into the run's folder there, outside the workspace."""
        self.move(f"{self.remote()}/{rel}", f"{self.remote_part('hidden')}/{rel}")

    def show(self, rel: str) -> None:
        """Move a hidden path there back into the workspace there."""
        self.move(f"{self.remote_part('hidden')}/{rel}", f"{self.remote()}/{rel}")

    def move(self, source: str, dest: str) -> None:
        """Move a path there, noting a move that failed."""
        if self.failure is not None:
            return
        code = self.helper(self.there(MOVE, source, dest))
        if code != 0:
            self.notes.append(f"[{self.name}] moving {source} to {dest} there failed (exit {code})")

    def remote(self) -> str:
        """The workspace on the other machine."""
        return self.remote_part("workspace")

    def sync_command(self) -> list[str]:
        """The configured sync, with the two workspace paths filled in."""
        return fill(self.vm.sync, str(self.workspace), self.remote())

    def fetch_command(self) -> list[str]:
        """The configured way to bring the workspace back, filled in."""
        return fill(self.vm.fetch, str(self.workspace), self.remote())

    def there(self, script: str, *args: str) -> list[str]:
        """A script run there through the prefix, its arguments as words."""
        return [*self.vm.exec_prefix, "sh", "-c", script, "sh", *args]

    def own_env(self) -> dict[str, str]:
        """What the prefix and the helpers take from this machine's environment: PATH and `prefix_env`, no judge key."""
        names = ["PATH", *self.vm.prefix_env]
        return scrub({n: os.environ[n] for n in names if os.environ.get(n)})[0]

    def probe_env(self) -> dict[str, str]:
        """A probe's prefix runs with what the helpers get, nothing more of this machine's."""
        return self.own_env()

    def helper(
        self, argv: list[str], stdin: str | None = None, timeout_s: int | None = None, out: list[str] | None = None
    ) -> int:
        """Run a command other than the subject here and return its exit code.

        It reads `stdin` or nothing, never the harness's own input. When
        `out` is given, the lines it prints go there. One that cannot start
        is exit 127, as the shell records it. One past its timeout is
        stopped with its whole group, noted, and exit 124, as `timeout`
        records it.
        """
        limit = timeout_s or self.vm.helper_timeout_s
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE if out is not None else None,
                encoding="utf-8",
                errors="replace",
                env=self.own_env(),
                start_new_session=True,
            )
        except OSError:
            return 127
        try:
            printed, _ = proc.communicate(stdin, timeout=limit)
        except subprocess.TimeoutExpired:
            kill_group(proc.pid)
            proc.wait()
            self.notes.append(f"[{self.name}] {' '.join(argv[:3])} ... did not finish in {limit}s and was stopped")
            return 124
        except BaseException:
            # An interrupt of the harness: the helper runs in a session of
            # its own, so nothing else stops it.
            kill_group(proc.pid)
            proc.wait()
            raise
        if out is not None:
            out.extend(line for line in (printed or "").splitlines() if line.strip())
        return proc.returncode

    def take_notes(self) -> list[str]:
        """The notes kept so far, once."""
        out, self.notes = self.notes, []
        return out

    def prepare(self, workspace: Path | None = None) -> Path:
        path = super().prepare(workspace)
        if not self.vm.exec_prefix:
            raise ValueError("the vm runtime needs exec_prefix in its runtime config")
        self.failure = self.setup()
        return path

    def setup(self) -> tuple[int, str] | None:
        """Everything a repeat needs there before its subject; the exit code and the reason when a step fails."""
        if not self.locked:
            code = self.helper(self.there(LOCK, self.remote_base(), self.run_dir.name))
            if code == 3:
                lock = f"{self.remote_base()}/.lock"
                return code, f"another run holds the other machine ({lock} there names it; remove it if no run is using it)"
            if code != 0:
                return code, f"the other machine did not answer: taking {self.remote_base()} failed with exit {code}"
            self.locked = True
            # What Docker holds before the run's first subject, which the run did not make and never removes.
            held, code = self.docker_held()
            self.docker_before = None if held is None else {(kind, ident) for kind, ident, _ in held}
            self.docker_unread = None if held is not None else f"exit {code}"
        code = self.helper(self.there(PREPARE, self.remote_run(), *self.vm.check))
        if code == 4:
            return code, f"the machine's check failed there: {' '.join(self.vm.check)}"
        if code != 0:
            return code, f"the run's folder there could not be made ready (exit {code})"
        for local, name in self.copies():
            code = self.helper(fill(self.vm.copy, str(local), f"{self.remote_run()}/{name}"))
            if code != 0:
                return code, f"the copy of the {name} to the other machine failed (exit {code})"
        if self.vm.sync:
            # The repeat's remote folder is new, and a sync may not make its parents.
            self.helper([*self.vm.exec_prefix, "mkdir", "-p", self.remote()])
            self.helper(self.sync_command())
        return None

    def hand_keys(self, keys: dict[str, str], streams: CliStream) -> ExitStatus | None:
        """Write each key into the repeat's own file there; a failure is the repeat's, with a note.

        The value goes through stdin, so no command line and no stream
        line holds it. The note names the variable and never its value.
        """
        folder = self.remote_part("keys")
        for name, value in keys.items():
            code = self.helper(self.there(WRITE_KEY, folder, f"{folder}/{name}"), stdin=value)
            if code != 0:
                streams.note(
                    f"[{self.name}] {name} could not be written on the other machine (exit {code}); the subject does not run"
                )
                return ExitStatus(code=code)
            streams.note(f"[{self.name}] {name} reaches the subject through a file of the repeat's, not the command line")
        return None

    def run(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        streams: CliStream,
        timeout_s: int = 900,
        stop: threading.Event | None = None,
    ) -> ExitStatus:
        """Hand the subject its keys through files there, then run the command with none of them here.

        A repeat whose preparation failed there does not run, and says
        why. A subject key that holds a judge's key is not handed on; it
        stays in the environment the base scrubs, which drops it and says
        so.
        """
        for note in self.take_notes():
            streams.note(note)
        if self.failure is not None:
            code, reason = self.failure
            streams.note(f"[{self.name}] {reason}; the subject does not run")
            return ExitStatus(code=code)
        kept = scrub(env)[0]
        keys = {n: kept[n] for n in SUBJECT_KEYS if kept.get(n)}
        failed = self.hand_keys(keys, streams)
        if failed is not None:
            return failed
        self.names = list(keys)
        return super().run(argv, cwd, {k: v for k, v in env.items() if k not in keys}, streams, timeout_s, stop)

    def environment(self, env: dict[str, str]) -> dict[str, str]:
        """The prefix's environment here: the subject's, with PATH and `prefix_env` from this machine's.

        A prefix such as `limactl shell` hands none of it to the other
        machine.
        """
        return {**self.own_env(), **env}

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        """The wrapper, then the subject, in the repeat's workspace, so what it writes is what fetch brings back."""
        parts = [self.remote_part(n) for n in ("keys", "home", "tmp", "workspace", "group")]
        return self.there(WRAPPER, *parts, str(len(self.names)), *self.names, *argv)

    def stop(self, proc: subprocess.Popen) -> None:
        """Kill the subject's process group there, through the prefix, then the prefix here."""
        self.helper(self.there(KILL, self.remote_part("group")), timeout_s=min(60, self.vm.helper_timeout_s))
        super().stop(proc)

    def collect(self, globs: list[str]) -> list[Path]:
        if self.vm.fetch and self.failure is None:
            code = self.helper(self.fetch_command())
            if code != 0:
                self.notes.append(f"[{self.name}] fetching the workspace of repeat {self.slot} failed (exit {code})")
        return super().collect(globs)

    def docker_held(self) -> tuple[list[DockerItem] | None, int]:
        """What the machine's Docker holds there, and the listing's exit code; None when Docker did not answer."""
        out: list[str] = []
        code = self.helper(self.there(DOCKER_LIST, DOCKER), out=out)
        return (docker_items(out) if code == 0 else None), code

    def remove_docker(self) -> list[str]:
        """Remove every container, network, and volume Docker holds there that it did not hold when the run took the machine.

        The subject's Docker is the machine's, and what it makes outlives
        the subject: a stack whose ports stay held and whose volumes keep a
        database. So the run removes what it made, and nothing that was
        there before it. Returns the notes that record it: what went, what
        stayed, or why nothing could be told apart. A run that never took
        the machine made nothing there.
        """
        if not self.locked:
            return []
        if self.docker_before is None:
            if self.docker_unread is None:
                return []  # said once already
            unread, self.docker_unread = self.docker_unread, None
            return [
                f"the machine's Docker did not answer when the run took the machine ({unread}), "
                "so the harness cannot tell what the run's subjects made there, and removes none of it"
            ]
        held, code = self.docker_held()
        if held is None:
            return [f"the machine's Docker did not answer (exit {code}), so what the subject made there stays"]
        before = self.docker_before
        made = [item for item in held if (item[0], item[1]) not in before]
        if not made:
            return []
        out: list[str] = []
        code = self.helper(self.there(DOCKER_REMOVE, DOCKER, *(f"{kind}:{ident}" for kind, ident, _ in made)), out=out)
        if code != 0:
            return [f"removing what the subject's Docker made there failed (exit {code}), and it may stay: {described(made)}"]
        stayed = set(out)
        gone = [item for item in made if f"{item[0]}:{item[1]}" not in stayed]
        kept = [item for item in made if f"{item[0]}:{item[1]}" in stayed]
        notes = [f"removed what the subject's Docker made on the other machine: {described(gone)}"] if gone else []
        if kept:
            notes.append(f"what the subject's Docker made on the other machine could not be removed: {described(kept)}")
        return notes

    def release(self) -> list[str]:
        """Remove what the run made there and give the machine back, once; the notes on what it did and what went wrong.

        The run records these notes. Docker's containers, networks, and
        volumes go first, then the run's folder. What stays is named,
        because it holds what the subject wrote. A run that never took the
        machine made nothing there, and removes nothing.
        """
        if self.locked and self.vm.exec_prefix and not self.released:
            self.released = True
            for note in self.remove_docker():
                self.notes.append(f"[{self.name}] when the run gave the machine back, {note}")
            code = self.helper(self.there(RELEASE, self.remote_run(), f"{self.remote_base()}/.lock"))
            if code != 0:
                self.notes.append(f"[{self.name}] the run's folder {self.remote_run()} was not removed there (exit {code})")
            self.locked = False
        return self.take_notes()

    def teardown(self) -> None:
        """Give the machine back if the run did not, then remove what is here.

        A run that ended has recorded the notes already; a run that was
        interrupted prints them.
        """
        for note in self.release():
            print(note, file=sys.stderr)
        super().teardown()


def build(
    name: str,
    run_dir: Path,
    target: Path | None = None,
    config: dict | None = None,
    plugin: Path | None = None,
    sandbox: Path | None = None,
) -> BaseRuntime:
    """The runtime one of the three names asks for."""
    if name == "host":
        return HostRuntime(run_dir, target, config, plugin, sandbox)
    if name == "container":
        return ContainerRuntime(run_dir, target, config, plugin, sandbox)
    if name == "vm":
        return VmRuntime(run_dir, target, config, plugin, sandbox)
    raise ValueError(f"runtime {name!r} is not one of {', '.join(NAMES)}")


def docker_available(docker: str = "docker") -> bool:
    """Whether a docker command is on the path."""
    return shutil.which(docker) is not None


def passthrough_env(names: list[str], env: dict[str, str] | None = None) -> dict[str, str]:
    """Only the variables named, from the environment: keys are passed on purpose."""
    source = os.environ if env is None else env
    return {n: source[n] for n in names if source.get(n)}
