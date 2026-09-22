"""benchmark/harness/runtime.py: what each runtime executes and where."""

import sys
from pathlib import Path

import pytest

from harness import runtime as RT
from harness.capture import CliStream


def test_the_host_runs_the_command_itself_with_a_private_home(tmp_path):
    rt = RT.build("host", tmp_path)
    rt.prepare()
    assert rt.command(["echo", "hi"], rt.workspace) == ["echo", "hi"]
    env = rt.environment({"PATH": "/usr/bin"})
    assert env["HOME"] == str(tmp_path / "home")
    assert env["TMPDIR"] == str(tmp_path / "tmp")
    assert (tmp_path / "home").is_dir() and (tmp_path / "tmp").is_dir()


def test_the_host_writes_both_streams_and_the_exit_status(tmp_path):
    rt = RT.build("host", tmp_path)
    rt.prepare()
    script = "import sys; print('out line'); print('err line', file=sys.stderr); sys.exit(3)"
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run([sys.executable, "-c", script], rt.workspace, {"PATH": "/usr/bin:/bin"}, stream)
    records = CliStream.read(tmp_path / "cli.jsonl")
    assert status.code == 3 and not status.ok and not status.timed_out
    assert status.as_dict()["duration_s"] >= 0
    assert "out line" in [r["line"] for r in records if r["s"] == "out"]
    assert "err line" in [r["line"] for r in records if r["s"] == "err"]


def test_a_byte_that_is_not_utf8_is_replaced_and_the_reader_goes_on(tmp_path):
    rt = RT.build("host", tmp_path)
    rt.prepare()
    script = (
        "import sys\n"
        "for fh in (sys.stdout.buffer, sys.stderr.buffer):\n"
        "    fh.write(b'bad \\xff byte\\n' + 'one\\u2028two\\n'.encode() + b'after\\n')\n"
        "    fh.flush()\n"
    )
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run([sys.executable, "-c", script], rt.workspace, {"PATH": "/usr/bin:/bin"}, stream)
    records = CliStream.read(tmp_path / "cli.jsonl")
    assert status.ok
    for name in ("out", "err"):
        lines = [r["line"] for r in records if r["s"] == name and not r["line"].startswith("[host]")]
        assert lines == ["bad \ufffd byte", "one\u2028two", "after"], lines


def test_a_subject_that_runs_too_long_is_killed_and_marked(tmp_path):
    rt = RT.build("host", tmp_path)
    rt.prepare()
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run(
            [sys.executable, "-c", "import time; time.sleep(30)"], rt.workspace, {"PATH": "/usr/bin:/bin"}, stream, timeout_s=1
        )
    assert status.timed_out and not status.ok


def test_a_child_that_outlives_a_clean_exit_is_stopped(tmp_path):
    # The subject exits 0 and leaves a child running; the run stops it.
    import os
    import time

    rt = RT.build("host", tmp_path)
    rt.prepare()
    pidfile = tmp_path / "child.pid"
    script = (
        "import subprocess, sys; "
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open({str(pidfile)!r}, 'w').write(str(c.pid))"
    )
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run([sys.executable, "-c", script], rt.workspace, {"PATH": "/usr/bin:/bin"}, stream)
    assert status.ok
    child = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"the child {child} is still running")


def test_the_sandbox_holds_only_the_payload_and_goes_at_teardown(tmp_path):
    root = tmp_path / "checkout"
    for name in ("skills/a", "lenses", "benchmark/fixtures", "docs"):
        (root / name).mkdir(parents=True)
    (root / "architecture.md").write_text("g", encoding="utf-8")
    (root / "CLAUDE.md").write_text("c", encoding="utf-8")
    (root / "benchmark" / "fixtures" / "x.expected.yaml").write_text("k", encoding="utf-8")
    target = root / "benchmark" / "fixtures" / "x"
    target.mkdir()
    (target / "a.py").write_text("print()", encoding="utf-8")
    sandbox = RT.new_sandbox()
    rt = RT.build("host", tmp_path / "run", target, plugin=root, sandbox=sandbox)
    rt.stage()
    plugin_path, target_path = rt.plugin_path(), rt.target_path()
    assert plugin_path is not None and target_path is not None
    staged = Path(plugin_path)
    assert staged.is_relative_to(sandbox) and (staged / "skills" / "a").is_dir() and (staged / "architecture.md").exists()
    assert not (staged / "benchmark").exists() and not (staged / "CLAUDE.md").exists() and not (staged / "docs").exists()
    staged_target = Path(target_path)
    assert (staged_target / "a.py").exists() and not list(staged_target.parent.glob("*.expected.yaml"))
    rt.teardown()
    assert not sandbox.exists()


def test_the_staged_target_keeps_its_tests(tmp_path):
    # The subject reviews the whole target, tests included, as the judges do.
    target = tmp_path / "target"
    (target / "tests").mkdir(parents=True)
    (target / "tests" / "test_a.py").write_text("def test_a(): ...", encoding="utf-8")
    (target / "__pycache__").mkdir()
    (target / "__pycache__" / "a.cpython-314.pyc").write_bytes(b"")
    staged = RT.stage_target(target, tmp_path / "staged")
    assert (staged / "tests" / "test_a.py").is_file()
    assert not (staged / "__pycache__").exists()


def test_the_container_mounts_the_target_read_only_and_names_the_keys(tmp_path):
    target = tmp_path / "checkout"
    target.mkdir()
    rt = RT.build("container", tmp_path, target, {"keys": ["ANTHROPIC_API_KEY"], "image": "img:1"})
    rt.prepare()
    command = rt.command(["claude", "-p", "hi"], rt.workspace)
    assert command[:3] == ["docker", "run", "--rm"]
    assert f"{rt.workspace}:/workspace:rw" in command
    assert f"{target.resolve()}:/target:ro" in command
    assert command[command.index("-e") + 1] == "ANTHROPIC_API_KEY"
    assert command[-3:] == ["claude", "-p", "hi"]
    assert command[command.index("img:1") + 1] == "claude"


def test_the_container_user_can_write_the_workspace_whatever_its_uid(tmp_path):
    # A bind mount keeps this machine's owner, and the image's user is not
    # this machine's user on a Linux runner, so the workspace is opened to it.
    rt = RT.build("container", tmp_path / "run", sandbox=tmp_path / "sandbox")
    workspace = rt.prepare_repeat(0)
    assert workspace.stat().st_mode & 0o777 == 0o777


def test_the_container_build_command_names_the_dockerfile(tmp_path):
    rt = RT.build("container", tmp_path, None, {"image": "img:1", "dockerfile": tmp_path / "runtime" / "Dockerfile"})
    assert isinstance(rt, RT.ContainerRuntime)
    assert rt.build_command()[:5] == ["docker", "build", "-t", "img:1", "-f"]


def test_the_vm_runs_behind_the_prefix_and_fills_the_sync_paths(tmp_path):
    config = {
        "exec_prefix": ["fake-shell", "station", "--"],
        "sync": ["fake-copy", "{local}/", "station:{remote}/"],
        "remote_workspace": "/opt/work",
        "fetch": ["fake-copy", "station:{remote}/", "{local}/"],
    }
    rt = RT.build("vm", tmp_path, None, config)
    assert isinstance(rt, RT.VmRuntime)
    rt.workspace = tmp_path / "workspace"
    command = rt.command(["claude", "-p", "hi"], rt.workspace)
    assert command[:5] == ["fake-shell", "station", "--", "sh", "-c"]
    assert command[6:] == ["sh", "/opt/work", "claude", "-p", "hi"]  # the folder is an argument, not script text
    assert rt.sync_command() == ["fake-copy", f"{rt.workspace}/", "station:/opt/work/"]
    assert rt.fetch_command() == ["fake-copy", "station:/opt/work/", f"{rt.workspace}/"]


def test_the_vm_gives_each_repeat_its_own_remote_workspace(tmp_path, monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(RT.subprocess, "run", lambda argv, **kwargs: ran.append(list(argv)))
    config = {
        "exec_prefix": ["fake-shell", "--"],
        "sync": ["fake-copy", "{local}/", "station:{remote}/"],
        "remote_workspace": "/opt/work/",
        "fetch": ["fake-copy", "station:{remote}/", "{local}/"],
    }
    rt = RT.build("vm", tmp_path, None, config)
    assert isinstance(rt, RT.VmRuntime)
    first = rt.prepare_repeat(0)
    assert ran == [["fake-shell", "--", "mkdir", "-p", "/opt/work/0"], ["fake-copy", f"{first}/", "station:/opt/work/0/"]]
    assert rt.command(["claude"], first)[-2:] == ["/opt/work/0", "claude"]
    second = rt.prepare_repeat(1)
    assert rt.sync_command() == ["fake-copy", f"{second}/", "station:/opt/work/1/"]
    assert rt.fetch_command() == ["fake-copy", "station:/opt/work/1/", f"{second}/"]
    assert rt.command(["claude"], second)[-2:] == ["/opt/work/1", "claude"]


def test_the_vm_subject_runs_in_the_remote_workspace_not_the_shell_default(tmp_path):
    # `env` stands in for the prefix: it runs its words on this machine, as a remote shell would there.
    remote = tmp_path / "remote dir"
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": ["env"], "remote_workspace": str(remote)})
    rt.prepare_repeat(0)
    script = "import pathlib; pathlib.Path('out.md').write_text('x')"
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run([sys.executable, "-c", script], tmp_path, {"PATH": "/usr/bin:/bin"}, stream)
    assert status.ok
    assert (remote / "0" / "out.md").read_text(encoding="utf-8") == "x"
    assert not (tmp_path / "out.md").exists()


def test_the_vm_without_a_prefix_is_refused(tmp_path):
    with pytest.raises(ValueError, match="exec_prefix"):
        RT.build("vm", tmp_path, None, {}).prepare()


def test_collect_takes_every_glob_once_in_path_order(tmp_path):
    rt = RT.build("host", tmp_path)
    rt.prepare()
    (rt.workspace / "sub").mkdir()
    (rt.workspace / "a.md").write_text("a", encoding="utf-8")
    (rt.workspace / "sub" / "b.md").write_text("b", encoding="utf-8")
    found = rt.collect(["*.md", "**/*.md"])
    assert [p.name for p in found] == ["a.md", "b.md"]


def test_an_unknown_runtime_name_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not one of"):
        RT.build("station", tmp_path)


def test_passthrough_takes_only_what_it_is_asked_for():
    env = {"PATH": "/usr/bin", "SECRET": "s", "EMPTY": ""}
    assert RT.passthrough_env(["PATH", "EMPTY", "MISSING"], env) == {"PATH": "/usr/bin"}


def test_each_runtime_names_the_plugin_and_the_target_as_the_subject_sees_them(tmp_path):
    plugin, target = tmp_path / "plugin", tmp_path / "checkout"
    plugin.mkdir()
    target.mkdir()
    host = RT.build("host", tmp_path, target, None, plugin=plugin)
    assert (host.plugin_path(), host.target_path()) == (str(plugin.resolve()), str(target.resolve()))
    box = RT.build("container", tmp_path, target, {"image": "img:1"}, plugin=plugin)
    assert (box.plugin_path(), box.target_path()) == ("/plugin", "/target")
    vm = RT.build(
        "vm", tmp_path, target, {"exec_prefix": ["x"], "remote_plugin": "/opt/p", "remote_target": "/opt/t"}, plugin=plugin
    )
    assert (vm.plugin_path(), vm.target_path()) == ("/opt/p", "/opt/t")
    bare = RT.build("container", tmp_path)
    assert (bare.plugin_path(), bare.target_path()) == (None, None)


def test_the_container_mounts_the_plugin_checkout_it_names(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    rt = RT.build("container", tmp_path, None, {"image": "img:1"}, plugin=plugin)
    rt.prepare()
    assert f"{plugin.resolve()}:/plugin:ro" in rt.command(["claude"], rt.workspace)


def test_the_vm_refuses_a_plugin_or_a_target_it_was_not_told_where_to_find(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    vm = RT.build("vm", tmp_path, tmp_path, {"exec_prefix": ["x"]}, plugin=plugin)
    with pytest.raises(ValueError, match="remote_plugin"):
        vm.plugin_path()
    with pytest.raises(ValueError, match="remote_target"):
        vm.target_path()


def test_a_relative_run_folder_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    host = RT.build("host", Path("runs/one"))
    host.prepare()
    env = host.environment({})
    assert env["HOME"] == str(tmp_path.resolve() / "runs" / "one" / "home")
    assert Path(env["TMPDIR"]).is_absolute()
    box = RT.build("container", Path("runs/one"), None, {"image": "img:1"})
    box.prepare()
    command = box.command(["claude"], box.workspace)
    mount = command[command.index("-v") + 1]
    assert mount == f"{tmp_path.resolve() / 'runs' / 'one' / 'workspace'}:/workspace:rw"


def test_each_repeat_gets_its_own_workspace_home_and_tmp(tmp_path):
    rt = RT.build("host", tmp_path)
    first = rt.prepare_repeat(0)
    (first / "left-behind.md").write_text("0", encoding="utf-8")
    home0 = rt.environment({})["HOME"]
    second = rt.prepare_repeat(1)
    assert first != second and rt.workspace == second
    assert list(second.iterdir()) == []
    env = rt.environment({})
    assert env["HOME"] != home0 and Path(env["HOME"]).is_dir() and Path(env["TMPDIR"]).is_dir()
    assert rt.collect(["*.md"]) == []


def test_a_binary_that_is_not_there_is_exit_127_with_a_note(tmp_path):
    rt = RT.build("host", tmp_path)
    rt.prepare()
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run(["no-such-binary-anywhere"], rt.workspace, {"PATH": str(tmp_path)}, stream)
    assert status.code == 127 and not status.ok
    lines = [r["line"] for r in CliStream.read(tmp_path / "cli.jsonl")]
    assert any("could not start" in line for line in lines)


def test_collect_leaves_out_a_file_outside_the_workspace(tmp_path):
    rt = RT.build("host", tmp_path)
    rt.prepare()
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    (rt.workspace / "inside.md").write_text("y", encoding="utf-8")
    assert [p.name for p in rt.collect(["*.md", "../*.md"])] == ["inside.md"]


def test_a_build_with_no_container_engine_is_a_recorded_failure(tmp_path):
    rt = RT.ContainerRuntime(tmp_path, config={"docker": str(tmp_path / "no-such-docker")})
    with CliStream(tmp_path / "build.jsonl") as streams:
        status = rt.build(streams)
    assert status.code == 127
    assert "could not start" in (tmp_path / "build.jsonl").read_text(encoding="utf-8")


def test_a_timeout_kills_the_whole_process_group(tmp_path):
    rt = RT.build("host", tmp_path)
    rt.prepare()
    marker = tmp_path / "child-alive"
    # The subject starts a child that outlives it unless its group is killed.
    child = f"import time, pathlib; time.sleep(3); pathlib.Path(r'{marker}').write_text('x')"
    script = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run([sys.executable, "-c", script], rt.workspace, {"PATH": "/usr/bin:/bin"}, stream, timeout_s=1)
    assert status.timed_out
    import time

    time.sleep(4)
    assert not marker.exists()


def test_the_container_is_named_bounded_and_killed_by_name_on_a_timeout(tmp_path):
    log = tmp_path / "docker.log"
    fake = tmp_path / "docker"
    # `run` sleeps like a subject that never ends; `kill` records its name.
    fake.write_text(
        f'#!/bin/sh\necho "$@" >> {log}\nif [ "$1" = run ]; then exec sleep 30; fi\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    rt = RT.build("container", tmp_path, None, {"docker": str(fake), "image": "img:1", "memory": "1g"})
    assert isinstance(rt, RT.ContainerRuntime)
    rt.prepare()
    command = rt.command(["claude"], rt.workspace)
    for flag in ("--init", "--name", "--pids-limit", "--cpus"):
        assert flag in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--memory") + 1] == "1g"
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run(["claude"], rt.workspace, {"PATH": "/usr/bin:/bin"}, stream, timeout_s=1)
    assert status.timed_out
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls[-1] == f"kill {rt.container_name}"


def test_the_vm_helpers_run_without_the_judge_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "judge-openai")
    monkeypatch.setenv("NOT_A_KEY", "judge-openai")  # the value is what counts, whatever the name
    envs: list[dict[str, str]] = []
    monkeypatch.setattr(RT.subprocess, "run", lambda argv, **kwargs: envs.append(kwargs.get("env")))
    config = {"exec_prefix": ["fake-shell", "--"], "sync": ["fake-copy", "{local}/", "{remote}/"], "fetch": ["x"]}
    rt = RT.build("vm", tmp_path, None, config)
    rt.prepare_repeat(0)
    rt.collect(["*.md"])
    rt.teardown()
    assert envs and all(env is not None for env in envs)
    for env in envs:
        assert "judge-openai" not in env.values()
