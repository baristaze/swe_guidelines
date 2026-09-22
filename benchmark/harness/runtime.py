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
  of the paths the subject is given, not out of its reach. A run whose
  subject must not reach them uses `container`.
- `container` isolates with Docker: the plugin checkout and the target
  read-only, the workspace read-write, the keys passed one by one, every
  capability dropped, and memory, processor, and process count bounded.
  Nothing of the harness's machine is in the container but the three
  mounts, so the judges' keys and the answer files are out of reach.
- `vm` runs the command on another machine through a configured prefix.
  The harness provisions nothing; it composes the prefix and the sync
  command, and the tests cover that composition with a fake prefix.

A subject is stopped whole, however it ends: past its timeout, on a clean
exit that left children behind, or when the harness itself is
interrupted. The host runtime starts it in a process group of its own and
kills the group every time. The container runtime names its container
and kills the container, because killing the `docker run` client leaves
the container running and paying.

A path on this machine means nothing inside a container or on another
machine. So a runtime also answers where the plugin checkout and the
target are as the subject sees them, and the subject is told those
paths, never the ones on this machine.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal as signals
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .capture import CliStream

NAMES = ("host", "container", "vm")
DEFAULT_IMAGE = "swe-guidelines-benchmark:latest"
# Where the container mounts what it is given. Both are read-only.
CONTAINER_PLUGIN = "/plugin"
CONTAINER_TARGET = "/target"
# What a subject may read of the plugin checkout: the payload the skills
# reference, and nothing else. The benchmark, its fixtures and their
# answer keys, the docs, and the repository's own CLAUDE.md stay out.
PLUGIN_PAYLOAD = (".claude-plugin", "skills", "agents", "lenses", "architecture.md", "checkers", "LICENSE")
# What no staged copy carries: the caches a tool leaves behind.
STAGE_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".ruff_cache")


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

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "signal": self.signal,
            "duration_s": round(self.duration_s, 3),
            "timed_out": self.timed_out,
        }


class Runtime(Protocol):
    """What every runtime does."""

    name: str

    def prepare(self, workspace: Path) -> Path: ...

    def plugin_path(self) -> str | None: ...

    def target_path(self) -> str | None: ...

    def run(self, argv: list[str], cwd: Path, env: dict[str, str], streams: CliStream, timeout_s: int = 900) -> ExitStatus: ...

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
        return self.prepare(self.sandbox / "workspace" / self.slot)

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        """The command this machine runs. The host runs the subject itself."""
        return list(argv)

    def environment(self, env: dict[str, str]) -> dict[str, str]:
        """The environment the command sees on this machine."""
        return dict(env)

    def run(self, argv: list[str], cwd: Path, env: dict[str, str], streams: CliStream, timeout_s: int = 900) -> ExitStatus:
        """Spawn the composed command and write both its streams as they come."""
        command = self.command(argv, cwd)
        streams.note(f"[{self.name}] {' '.join(command)}")
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=self.environment(env),
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
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            streams.note(f"[{self.name}] timed out after {timeout_s}s; stopping the subject")
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
        )

    def stop(self, proc: subprocess.Popen) -> None:
        """Kill the subject's whole process group; a group already gone is no error."""
        kill_group(proc.pid)
        if proc.poll() is None:
            proc.kill()

    def collect(self, globs: list[str]) -> list[Path]:
        """Every workspace file one of the globs names, once, in path order.

        A file outside the workspace, reached through `..` or a symlink, is
        not the subject's output and is left out.
        """
        root = self.workspace.resolve()
        found: set[Path] = set()
        for pattern in globs:
            for path in self.workspace.glob(pattern):
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
        """The private HOME or TMPDIR, one per repeat once a repeat is prepared."""
        base = self.sandbox / name
        return base / self.slot if self.slot is not None else base

    def prepare(self, workspace: Path | None = None) -> Path:
        path = super().prepare(workspace)
        self.private("home").mkdir(parents=True, exist_ok=True)
        self.private("tmp").mkdir(parents=True, exist_ok=True)
        return path

    def environment(self, env: dict[str, str]) -> dict[str, str]:
        out = dict(env)
        out["HOME"] = str(self.private("home"))
        out["TMPDIR"] = str(self.private("tmp"))
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
            proc = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
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

    def stop(self, proc: subprocess.Popen) -> None:
        """Kill the container by name, then the client."""
        if self.container_name:
            subprocess.run([self.docker, "kill", self.container_name], capture_output=True, check=False)
        super().stop(proc)


@dataclass
class VmConfig:
    """How to reach the other machine and how to get the workspace there."""

    exec_prefix: list[str] = field(default_factory=list)
    sync: list[str] = field(default_factory=list)
    remote_workspace: str = "/tmp/benchmark-workspace"
    fetch: list[str] = field(default_factory=list)
    remote_plugin: str | None = None
    remote_target: str | None = None


class VmRuntime(BaseRuntime):
    """Another machine, reached through a command prefix.

    The prefix is configuration, for example
    `["limactl", "shell", "default", "--"]`. The sync command is
    configuration too; `{local}` and `{remote}` in any of its words are
    replaced with the two workspace paths. Each repeat gets its own
    remote folder under `remote_workspace`, as it gets its own local
    one, and the subject runs inside it. The prefix has to hand its
    words on as words (`limactl shell`, `docker exec`); one that joins
    them into a remote shell line, as `ssh` does, needs a wrapper. The harness provisions no
    machine and starts none, and copies neither the plugin checkout nor
    the target there: `remote_plugin` and `remote_target` say where the
    operator put them, and a run that needs one and is not told is
    refused before it starts.
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
            sync=list(raw.get("sync", [])),
            remote_workspace=str(raw.get("remote_workspace", "/tmp/benchmark-workspace")),
            fetch=list(raw.get("fetch", [])),
            remote_plugin=raw.get("remote_plugin"),
            remote_target=raw.get("remote_target"),
        )

    def stage(self) -> None:
        """Nothing to copy here: the operator placed the plugin and the target on the other machine."""
        return None

    def plugin_path(self) -> str | None:
        if not self.plugin:
            return None
        if not self.vm.remote_plugin:
            raise ValueError("the vm runtime needs remote_plugin in its runtime config to run a skill")
        return str(self.vm.remote_plugin)

    def target_path(self) -> str | None:
        if not self.target:
            return None
        if not self.vm.remote_target:
            raise ValueError("the vm runtime needs remote_target in its runtime config to run on a target")
        return str(self.vm.remote_target)

    def remote(self) -> str:
        """The workspace on the other machine: one folder per repeat once a repeat is prepared."""
        base = self.vm.remote_workspace.rstrip("/") or "/"
        return f"{base}/{self.slot}" if self.slot is not None else base

    def _fill(self, words: list[str]) -> list[str]:
        return [w.replace("{local}", str(self.workspace)).replace("{remote}", self.remote()) for w in words]

    def sync_command(self) -> list[str]:
        """The configured sync, with the two workspace paths filled in."""
        return self._fill(self.vm.sync)

    def fetch_command(self) -> list[str]:
        """The configured way to bring the workspace back, filled in."""
        return self._fill(self.vm.fetch)

    def prepare(self, workspace: Path | None = None) -> Path:
        path = super().prepare(workspace)
        if not self.vm.exec_prefix:
            raise ValueError("the vm runtime needs exec_prefix in its runtime config")
        if self.vm.sync:
            # The repeat's remote folder is new, and a sync may not make its parents.
            subprocess.run([*self.vm.exec_prefix, "mkdir", "-p", self.remote()], check=False)
            subprocess.run(self.sync_command(), check=False)
        return path

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        """The subject runs in the remote workspace, so what it writes is what fetch brings back.

        The folder and the words travel as arguments of `sh -c`, never
        spliced into its script, so a space or a quote in them stays
        one word.
        """
        script = 'mkdir -p "$1" && cd "$1" && shift && exec "$@"'
        return [*self.vm.exec_prefix, "sh", "-c", script, "sh", self.remote(), *argv]

    def collect(self, globs: list[str]) -> list[Path]:
        if self.vm.fetch:
            subprocess.run(self.fetch_command(), check=False)
        return super().collect(globs)


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
