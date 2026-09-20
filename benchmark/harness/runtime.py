"""Where the subject runs: the host, a container, or another machine.

One protocol, three implementations, the same streams in the run folder
either way. A runtime decides two things: the command this machine
actually executes, and the environment that command sees. Everything
else, the workspace and the collected artifact, is the same.

Isolation is a choice, and the choice is named:

- `host` isolates by convention only. A private `HOME` and a private
  `TMPDIR` under the run folder keep a subject from writing into the
  operator's account by accident. Nothing stops a subject that means to.
- `container` isolates with Docker: the target read-only, the workspace
  read-write, the keys passed one by one.
- `vm` runs the command on another machine through a configured prefix.
  The harness provisions nothing; it composes the prefix and the sync
  command, and the tests cover that composition with a fake prefix.
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

    def run(self, argv: list[str], cwd: Path, env: dict[str, str], streams: CliStream, timeout_s: int = 900) -> ExitStatus: ...

    def collect(self, globs: list[str]) -> list[Path]: ...

    def teardown(self) -> None: ...


class BaseRuntime:
    """What the three share: the workspace, the collection, the local spawn."""

    name = "base"

    def __init__(self, run_dir: Path, target: Path | None = None, config: dict | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.target = Path(target).resolve() if target else None
        self.config = dict(config or {})
        self.workspace = self.run_dir / "workspace"
        self.prepared = False

    def prepare(self, workspace: Path | None = None) -> Path:
        """Make the workspace the subject works in and return it."""
        self.workspace = Path(workspace) if workspace else self.workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.prepared = True
        return self.workspace

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
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=self.environment(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
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
        """Every workspace file one of the globs names, once, in path order."""
        found: set[Path] = set()
        for pattern in globs:
            for path in self.workspace.glob(pattern):
                if path.is_file():
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

    def prepare(self, workspace: Path | None = None) -> Path:
        path = super().prepare(workspace)
        (self.run_dir / "home").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "tmp").mkdir(parents=True, exist_ok=True)
        return path

    def environment(self, env: dict[str, str]) -> dict[str, str]:
        out = dict(env)
        out["HOME"] = str(self.run_dir / "home")
        out["TMPDIR"] = str(self.run_dir / "tmp")
        return out


class ContainerRuntime(BaseRuntime):
    """`docker run --rm` from the image `runtime/Dockerfile` builds."""

    name = "container"

    def __init__(self, run_dir: Path, target: Path | None = None, config: dict | None = None) -> None:
        super().__init__(run_dir, target, config)
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

    def command(self, argv: list[str], cwd: Path) -> list[str]:
        out = [self.docker, "run", "--rm", "-v", f"{self.workspace}:/workspace:rw", "-w", "/workspace"]
        if self.target:
            out += ["-v", f"{self.target}:/target:ro"]
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


class VmRuntime(BaseRuntime):
    """Another machine, reached through a command prefix.

    The prefix is configuration, for example
    `["limactl", "shell", "default", "--"]`. The sync command is
    configuration too; `{local}` and `{remote}` in any of its words are
    replaced with the two workspace paths. The harness provisions no
    machine and starts none.
    """

    name = "vm"

    def __init__(self, run_dir: Path, target: Path | None = None, config: dict | None = None) -> None:
        super().__init__(run_dir, target, config)
        raw = self.config
        self.vm = VmConfig(
            exec_prefix=list(raw.get("exec_prefix", [])),
            sync=list(raw.get("sync", [])),
            remote_workspace=str(raw.get("remote_workspace", "/tmp/benchmark-workspace")),
            fetch=list(raw.get("fetch", [])),
        )

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


def build(name: str, run_dir: Path, target: Path | None = None, config: dict | None = None) -> BaseRuntime:
    """The runtime one of the three names asks for."""
    if name == "host":
        return HostRuntime(run_dir, target, config)
    if name == "container":
        return ContainerRuntime(run_dir, target, config)
    if name == "vm":
        return VmRuntime(run_dir, target, config)
    raise ValueError(f"runtime {name!r} is not one of {', '.join(NAMES)}")


def docker_available(docker: str = "docker") -> bool:
    """Whether a docker command is on the path."""
    return shutil.which(docker) is not None


def passthrough_env(names: list[str], env: dict[str, str] | None = None) -> dict[str, str]:
    """Only the variables named, from the environment: keys are passed on purpose."""
    source = os.environ if env is None else env
    return {n: source[n] for n in names if source.get(n)}
