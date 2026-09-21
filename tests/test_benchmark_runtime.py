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


def test_a_subject_that_runs_too_long_is_killed_and_marked(tmp_path):
    rt = RT.build("host", tmp_path)
    rt.prepare()
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run(
            [sys.executable, "-c", "import time; time.sleep(30)"], rt.workspace, {"PATH": "/usr/bin:/bin"}, stream, timeout_s=1
        )
    assert status.timed_out and not status.ok


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
    mount = box.command(["claude"], box.workspace)[4]
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
