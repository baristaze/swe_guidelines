"""Where the subject runs: the host, a container, or another machine.

One protocol, three implementations, the same streams in the run folder
either way. A runtime decides two things: the command this machine
actually executes, and the environment that command sees. Everything
else, the workspace and the collected artifact, is the same.

Isolation is a choice, and the choice is named:

- `host` isolates by convention only. A private `HOME` and a private
  `TMPDIR` under the run folder keep a subject from writing into the
  operator's account by accident. Nothing stops a subject that means to.
- `container` isolates with Docker: the plugin checkout and the target
  read-only, the workspace read-write, the keys passed one by one.
- `vm` runs the command on another machine through a configured prefix.
  The harness provisions nothing; it composes the prefix and the sync
  command, and the tests cover that composition with a fake prefix.

A path on this machine means nothing inside a container or on another
machine. So a runtime also answers where the plugin checkout and the
target are as the subject sees them, and the subject is told those
paths, never the ones on this machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .capture import CliStream

NAMES = ("host", "container", "vm")
DEFAULT_IMAGE = "swe-guidelines-benchmark:latest"
# Where the container mounts what it is given. Both are read-only.
CONTAINER_PLUGIN = "/plugin"
CONTAINER_TARGET = "/target"


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

    def __init__(self, run_dir: Path, target: Path | None = None, config: dict | None = None, plugin: Path | None = None) -> None:
        # Absolute, because the subject's working directory is the workspace:
        # a relative HOME, TMPDIR, or mount source would be read from there.
        self.run_dir = Path(run_dir).resolve()
        self.target = Path(target).resolve() if target else None
        self.plugin = Path(plugin).resolve() if plugin else None
        self.config = dict(config or {})
        self.workspace = self.run_dir / "workspace"
        self.slot: str | None = None
        self.prepared = False

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
        return self.prepare(self.run_dir / "workspace" / self.slot)

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
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            # A binary that is not there is a repeat that failed, recorded as
            # the shell records it, never a run that leaves no results.
            streams.note(f"[{self.name}] could not start: {type(exc).__name__}: {exc}")
            return ExitStatus(code=127, duration_s=time.monotonic() - started)
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
            proc.kill()
            proc.wait()
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
        """Nothing by default: the run folder is the record and it stays."""
        return None


def _pipe(handle, stream: str, streams: CliStream) -> None:
    """Read one pipe to its end, one line at a time, into the stream file."""
    if handle is None:
        return
    with handle:
        for line in handle:
            streams.write(stream, line)


class HostRuntime(BaseRuntime):
    """This machine, with a private HOME and TMPDIR under the run folder."""

    name = "host"

    def private(self, name: str) -> Path:
        """The private HOME or TMPDIR, one per repeat once a repeat is prepared."""
        base = self.run_dir / name
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

    def __init__(self, run_dir: Path, target: Path | None = None, config: dict | None = None, plugin: Path | None = None) -> None:
        super().__init__(run_dir, target, config, plugin)
        self.image = self.config.get("image", DEFAULT_IMAGE)
        self.dockerfile = Path(self.config.get("dockerfile", Path(__file__).resolve().parent.parent / "runtime" / "Dockerfile"))
        self.docker = self.config.get("docker", "docker")
        self.keys = list(self.config.get("keys", []))

    def build_command(self) -> list[str]:
        """The image build, for a caller that wants to build before it runs."""
        return [self.docker, "build", "-t", self.image, "-f", str(self.dockerfile), str(self.dockerfile.parent)]

    def build(self, streams: CliStream | None = None) -> ExitStatus:
        """Build the image, writing the build output into the stream when given."""
        command = self.build_command()
        started = time.monotonic()
        proc = subprocess.run(command, capture_output=True, text=True)
        if streams is not None:
            for line in (proc.stdout + proc.stderr).splitlines():
                streams.write("err", line)
        return ExitStatus(code=proc.returncode, duration_s=time.monotonic() - started)

    def plugin_path(self) -> str | None:
        return CONTAINER_PLUGIN if self.plugin else None

    def target_path(self) -> str | None:
        return CONTAINER_TARGET if self.target else None

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        out = [self.docker, "run", "--rm", "-v", f"{self.workspace}:/workspace:rw", "-w", "/workspace"]
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
    replaced with the two workspace paths. The harness provisions no
    machine and starts none, and copies neither the plugin checkout nor
    the target there: `remote_plugin` and `remote_target` say where the
    operator put them, and a run that needs one and is not told is
    refused before it starts.
    """

    name = "vm"

    def __init__(self, run_dir: Path, target: Path | None = None, config: dict | None = None, plugin: Path | None = None) -> None:
        super().__init__(run_dir, target, config, plugin)
        raw = self.config
        self.vm = VmConfig(
            exec_prefix=list(raw.get("exec_prefix", [])),
            sync=list(raw.get("sync", [])),
            remote_workspace=str(raw.get("remote_workspace", "/tmp/benchmark-workspace")),
            fetch=list(raw.get("fetch", [])),
            remote_plugin=raw.get("remote_plugin"),
            remote_target=raw.get("remote_target"),
        )

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

    def _fill(self, words: list[str]) -> list[str]:
        return [w.replace("{local}", str(self.workspace)).replace("{remote}", self.vm.remote_workspace) for w in words]

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
            subprocess.run(self.sync_command(), check=False)
        return path

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        return list(self.vm.exec_prefix) + list(argv)

    def collect(self, globs: list[str]) -> list[Path]:
        if self.vm.fetch:
            subprocess.run(self.fetch_command(), check=False)
        return super().collect(globs)


def build(
    name: str, run_dir: Path, target: Path | None = None, config: dict | None = None, plugin: Path | None = None
) -> BaseRuntime:
    """The runtime one of the three names asks for."""
    if name == "host":
        return HostRuntime(run_dir, target, config, plugin)
    if name == "container":
        return ContainerRuntime(run_dir, target, config, plugin)
    if name == "vm":
        return VmRuntime(run_dir, target, config, plugin)
    raise ValueError(f"runtime {name!r} is not one of {', '.join(NAMES)}")


def docker_available(docker: str = "docker") -> bool:
    """Whether a docker command is on the path."""
    return shutil.which(docker) is not None


def passthrough_env(names: list[str], env: dict[str, str] | None = None) -> dict[str, str]:
    """Only the variables named, from the environment: keys are passed on purpose."""
    source = os.environ if env is None else env
    return {n: source[n] for n in names if source.get(n)}
