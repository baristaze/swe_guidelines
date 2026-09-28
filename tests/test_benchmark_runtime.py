"""benchmark/harness/runtime.py: what each runtime executes and where."""

import json
import os
import subprocess
import sys
import threading
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
    rt = RT.build("vm", tmp_path / "run-1", None, config)
    assert isinstance(rt, RT.VmRuntime)
    rt.workspace = tmp_path / "workspace"
    command = rt.command(["claude", "-p", "hi"], rt.workspace)
    assert command[:5] == ["fake-shell", "station", "--", "sh", "-c"]
    # The folders are arguments, not script text: the keys, HOME, TMPDIR, the workspace, and the group file.
    folders = [f"/opt/work/run-1/{name}" for name in ("keys", "home", "tmp", "workspace", "group")]
    assert command[6:] == ["sh", *folders, "0", "claude", "-p", "hi"]  # no key name handed, so none exported
    assert rt.sync_command() == ["fake-copy", f"{rt.workspace}/", "station:/opt/work/run-1/workspace/"]
    assert rt.fetch_command() == ["fake-copy", "station:/opt/work/run-1/workspace/", f"{rt.workspace}/"]


def test_the_vm_gives_each_repeat_its_own_remote_workspace(tmp_path, monkeypatch):
    ran: list[list[str]] = []

    def helper(self, argv, stdin=None, timeout_s=None):
        ran.append(list(argv))
        return 0

    monkeypatch.setattr(RT.VmRuntime, "helper", helper)
    config = {
        "exec_prefix": ["fake-shell", "--"],
        "sync": ["fake-copy", "{local}/", "station:{remote}/"],
        "remote_workspace": "/opt/work/",
        "fetch": ["fake-copy", "station:{remote}/", "{local}/"],
    }
    rt = RT.build("vm", tmp_path / "run-1", None, config)
    assert isinstance(rt, RT.VmRuntime)
    first = rt.prepare_repeat(0)
    assert ran == [
        ["fake-shell", "--", "sh", "-c", RT.LOCK, "sh", "/opt/work", "run-1"],
        ["fake-shell", "--", "sh", "-c", RT.PREPARE, "sh", "/opt/work/run-1"],
        ["fake-shell", "--", "mkdir", "-p", "/opt/work/run-1/workspace/0"],
        ["fake-copy", f"{first}/", "station:/opt/work/run-1/workspace/0/"],
    ]
    assert rt.command(["claude"], first)[-2:] == ["0", "claude"]
    assert "/opt/work/run-1/workspace/0" in rt.command(["claude"], first)
    second = rt.prepare_repeat(1)
    assert ran[4:] == [  # the lock is taken once, for every repeat
        ["fake-shell", "--", "sh", "-c", RT.PREPARE, "sh", "/opt/work/run-1"],
        ["fake-shell", "--", "mkdir", "-p", "/opt/work/run-1/workspace/1"],
        ["fake-copy", f"{second}/", "station:/opt/work/run-1/workspace/1/"],
    ]
    assert rt.sync_command() == ["fake-copy", f"{second}/", "station:/opt/work/run-1/workspace/1/"]
    assert rt.fetch_command() == ["fake-copy", "station:/opt/work/run-1/workspace/1/", f"{second}/"]
    assert "/opt/work/run-1/workspace/1" in rt.command(["claude"], second)


def test_two_vm_runs_never_share_a_remote_workspace_and_each_removes_its_own(tmp_path, monkeypatch):
    # `env` stands in for the prefix: it runs its words on this machine, as a remote shell would there.
    remote = tmp_path / "remote"
    script = "import os, pathlib; print(len(os.listdir('.'))); pathlib.Path('left.md').write_text('x')"
    seen = []
    for name in ("run-1", "run-2"):
        rt = RT.build("vm", tmp_path / name, None, {"exec_prefix": ["env"], "remote_workspace": str(remote)})
        rt.prepare_repeat(0)
        with CliStream(tmp_path / f"{name}.jsonl") as stream:
            assert rt.run([sys.executable, "-c", script], tmp_path, {"PATH": "/usr/bin:/bin"}, stream).ok
        seen += [r["line"] for r in CliStream.read(tmp_path / f"{name}.jsonl") if r["s"] == "out"]
        assert (remote / name / "workspace" / "0" / "left.md").is_file()
        rt.teardown()
        assert not (remote / name).exists()
    assert seen == ["0", "0"]  # the second run found nothing the first left
    assert list(remote.iterdir()) == []  # and each gave the machine back


def test_the_vm_subject_runs_in_the_remote_workspace_not_the_shell_default(tmp_path):
    # `env` stands in for the prefix: it runs its words on this machine, as a remote shell would there.
    remote = tmp_path / "remote dir"
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": ["env"], "remote_workspace": str(remote)})
    rt.prepare_repeat(0)
    script = "import pathlib; pathlib.Path('out.md').write_text('x')"
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run([sys.executable, "-c", script], tmp_path, {"PATH": "/usr/bin:/bin"}, stream)
    assert status.ok
    assert (remote / "run" / "workspace" / "0" / "out.md").read_text(encoding="utf-8") == "x"

    assert not (tmp_path / "out.md").exists()


def test_the_vm_without_a_prefix_is_refused(tmp_path):
    with pytest.raises(ValueError, match="exec_prefix"):
        RT.build("vm", tmp_path, None, {}).prepare()


def payload_checkout(tmp_path):
    """A checkout with a skill, the guideline, and a fixture whose answers sit beside it."""
    root = tmp_path / "checkout"
    for name in ("skills/a", "benchmark/fixtures/x"):
        (root / name).mkdir(parents=True)
    (root / "architecture.md").write_text("g", encoding="utf-8")
    (root / "benchmark" / "fixtures" / "x.expected.yaml").write_text("k", encoding="utf-8")
    (root / "benchmark" / "fixtures" / "x" / "a.py").write_text("print()", encoding="utf-8")
    return root, root / "benchmark" / "fixtures" / "x"


def fake_prefix(tmp_path, name="prefix", new_session=False):
    """A prefix that logs its words and its environment's names, then runs the words here, as a shell there would.

    With `new_session`, it runs them in a session of their own and waits,
    as `ssh` does: killing the prefix here leaves them running.
    """
    log = tmp_path / f"{name}.log"
    path = tmp_path / name
    run = (
        "import subprocess\nsys.exit(subprocess.Popen(sys.argv[1:], start_new_session=True).wait())\n"
        if new_session
        else "os.execvp(sys.argv[1], sys.argv[1:])\n"
    )
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"with open({str(log)!r}, 'a') as fh:\n"
        "    fh.write(json.dumps({'argv': sys.argv[1:], 'env': sorted(os.environ)}) + '\\n')\n" + run,
        encoding="utf-8",
    )
    path.chmod(0o755)
    return str(path), log


def logged(log):
    """Every call a fake prefix logged."""
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_the_vm_copies_what_it_staged_afresh_before_every_repeat(tmp_path):
    # `cp -R` stands in for the copy: it runs here, as the copy would there.
    root, target = payload_checkout(tmp_path)
    remote = tmp_path / "remote"
    prefix, log = fake_prefix(tmp_path)
    config = {"exec_prefix": [prefix], "copy": [prefix, "cp", "-R", "{local}", "{remote}"], "remote_workspace": str(remote)}
    rt = RT.build("vm", tmp_path / "run", target, config, plugin=root, sandbox=RT.new_sandbox())
    rt.stage()
    plugin, copied = remote / "run" / "plugin", remote / "run" / "target"
    assert (rt.plugin_path(), rt.target_path()) == (str(plugin), str(copied))
    assert not remote.exists()  # naming the copies copies nothing, so a dry run touches nothing there
    rt.prepare_repeat(0)
    assert (plugin / "skills" / "a").is_dir() and (plugin / "architecture.md").is_file()
    assert not (plugin / "benchmark").exists()  # the payload only, no fixture and no answer
    assert (copied / "a.py").is_file() and list(copied.parent.glob("*.expected.yaml")) == []
    assert (remote / "run").stat().st_mode & 0o777 == 0o700
    # A repeat that changes its copies changes nothing the next repeat reads.
    (plugin / "architecture.md").write_text("changed", encoding="utf-8")
    (copied / "planted.py").write_text("x", encoding="utf-8")
    rt.prepare_repeat(1)
    assert (plugin / "architecture.md").read_text(encoding="utf-8") == "g" and not (copied / "planted.py").exists()
    assert sum(c["argv"][0] == "cp" for c in logged(log)) == 4  # the plugin and the target, before each repeat
    rt.teardown()
    assert list(remote.iterdir()) == []  # the run's folder and the lock are gone


def test_the_copies_are_named_plugin_and_target_whatever_the_folders_here_are_called(tmp_path):
    plugin, target = tmp_path / "keys", tmp_path / "workspace"
    (plugin / "skills").mkdir(parents=True)
    (target / "om").mkdir(parents=True)
    remote = tmp_path / "remote"
    config = {"exec_prefix": ["env"], "copy": ["cp", "-R", "{local}", "{remote}"], "remote_workspace": str(remote)}
    rt = RT.build("vm", tmp_path / "run", target, config, plugin=plugin)
    rt.prepare_repeat(0)
    assert sorted(p.name for p in (remote / "run").iterdir()) == ["plugin", "target"]
    assert (remote / "run" / "plugin" / "skills").is_dir() and (remote / "run" / "target" / "om").is_dir()


def test_a_copy_that_fails_fails_the_repeat_with_a_note_and_the_subject_never_runs(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    config = {"exec_prefix": ["env"], "copy": ["false"], "remote_workspace": str(tmp_path / "remote")}
    rt = RT.build("vm", tmp_path / "run", None, config, plugin=plugin)
    rt.prepare_repeat(0)
    marker = tmp_path / "ran"
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run(["touch", str(marker)], tmp_path, {"PATH": os.environ["PATH"]}, stream)
    assert status.code == 1 and not status.ok and not marker.exists()
    assert "the copy of the plugin to the other machine failed" in (tmp_path / "cli.jsonl").read_text(encoding="utf-8")


def test_a_machine_that_does_not_answer_fails_every_repeat_with_a_note(tmp_path):
    missing = str(tmp_path / "no-such-prefix")
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": [missing], "remote_workspace": "/opt/work"})
    assert isinstance(rt, RT.VmRuntime)
    with CliStream(tmp_path / "cli.jsonl") as stream:
        for index in (0, 1):
            rt.prepare_repeat(index)
            status = rt.run(["true"], tmp_path, {"PATH": os.environ["PATH"]}, stream)
            assert status.code == 127 and not status.ok
    lines = [r["line"] for r in CliStream.read(tmp_path / "cli.jsonl")]
    assert sum("the other machine did not answer" in line for line in lines) == 2
    assert rt.release() == []  # the run never took the machine, so it made nothing there to remove
    rt.teardown()  # a machine that is gone is no error here


def test_a_second_run_on_the_machine_is_refused_until_the_first_gives_it_back(tmp_path):
    remote = tmp_path / "remote"
    config = {"exec_prefix": ["env"], "remote_workspace": str(remote)}
    first = RT.build("vm", tmp_path / "run-1", None, config)
    second = RT.build("vm", tmp_path / "run-2", None, config)
    first.prepare_repeat(0)
    assert (remote / ".lock" / "run").read_text(encoding="utf-8") == "run-1\n"
    second.prepare_repeat(0)
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = second.run(["true"], tmp_path, {"PATH": os.environ["PATH"]}, stream)
        assert status.code == 3 and not status.ok
        first.teardown()
        assert not (remote / ".lock").exists()
        second.prepare_repeat(1)
        assert second.run(["true"], tmp_path, {"PATH": os.environ["PATH"]}, stream).ok
    assert "another run holds the other machine" in (tmp_path / "cli.jsonl").read_text(encoding="utf-8")
    second.teardown()


KEY = "subject-key-for-the-test"


def test_the_vm_hands_the_key_through_a_file_never_a_command_line_or_a_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "judge-openai")
    monkeypatch.setenv("GH_TOKEN", "a-token-of-this-machine")
    monkeypatch.setenv("HOME", str(tmp_path / "operator"))
    prefix, log = fake_prefix(tmp_path)
    remote = tmp_path / "remote"
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": [prefix], "remote_workspace": str(remote)})
    script = "import os; print(len(os.environ.get('ANTHROPIC_API_KEY', '')))"
    keys = [remote / "run" / "keys" / str(index) / "ANTHROPIC_API_KEY" for index in (0, 1)]
    with CliStream(tmp_path / "cli.jsonl") as stream:
        for index, key in enumerate(keys):
            rt.prepare_repeat(index)
            env = {"PATH": os.environ["PATH"], "ANTHROPIC_API_KEY": KEY}
            assert rt.run([sys.executable, "-c", script], tmp_path, env, stream).ok
            assert key.read_text(encoding="utf-8") == KEY and key.stat().st_mode & 0o777 == 0o600
    assert not keys[0].exists()  # the next repeat's preparation removed it with the rest of the run's folder
    assert [r["line"] for r in CliStream.read(tmp_path / "cli.jsonl") if r["s"] == "out"] == [str(len(KEY))] * 2
    calls = logged(log)
    assert sum(c["argv"][-1] in map(str, keys) for c in calls) == 2  # written for each repeat, into its own folder
    assert all(KEY not in " ".join(c["argv"]) for c in calls)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GH_TOKEN"):
        assert all(name not in c["env"] for c in calls)  # the prefix here holds no key and no token of this machine
    assert all("HOME" in c["env"] for c in calls)  # a prefix such as limactl finds its machine through HOME
    assert KEY not in (tmp_path / "cli.jsonl").read_text(encoding="utf-8")
    rt.teardown()
    assert not any(key.exists() for key in keys)


def test_a_repeat_exports_only_the_keys_it_is_handed_whatever_an_earlier_one_left(tmp_path):
    remote = tmp_path / "remote"
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": ["env"], "remote_workspace": str(remote)})
    keys = remote / "run" / "keys"
    # Repeat 0 removes its own key and plants files for repeat 1, as a subject there could.
    plant = (
        "import os, pathlib\n"
        f"pathlib.Path({str(keys / '0' / 'ANTHROPIC_API_KEY')!r}).unlink()\n"
        f"later = pathlib.Path({str(keys / '1')!r}); later.mkdir(parents=True)\n"
        "for name in ('ANTHROPIC_BASE_URL', 'NODE_OPTIONS', 'ANTHROPIC_API_KEY~'):\n"
        "    (later / name).write_text('planted')\n"
    )
    show = "import os; e = os.environ; print(len(e.get('ANTHROPIC_API_KEY', '')), 'ANTHROPIC_BASE_URL' in e, 'NODE_OPTIONS' in e)"
    env = {"PATH": os.environ["PATH"], "ANTHROPIC_API_KEY": KEY}
    with CliStream(tmp_path / "cli.jsonl") as stream:
        rt.prepare_repeat(0)
        assert rt.run([sys.executable, "-c", plant], tmp_path, env, stream).ok
        rt.prepare_repeat(1)
        assert rt.run([sys.executable, "-c", show], tmp_path, env, stream).ok
    assert [r["line"] for r in CliStream.read(tmp_path / "cli.jsonl") if r["s"] == "out"] == [f"{len(KEY)} False False"]


def test_a_repeat_finds_nothing_an_earlier_one_left_anywhere_in_the_run_folder(tmp_path):
    remote = tmp_path / "remote"
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": ["env"], "remote_workspace": str(remote)})
    run = remote / "run"
    # Repeat 0 plants what a later Claude Code would load: settings in repeat 1's HOME, and a CLAUDE.md in
    # repeat 1's workspace, in the folder above every workspace, and in the run's folder.
    plant = (
        "import pathlib\n"
        f"run = pathlib.Path({str(run)!r})\n"
        "home = run / 'home' / '1' / '.claude'; home.mkdir(parents=True)\n"
        '(home / \'settings.json\').write_text(\'{"env": {"ANTHROPIC_BASE_URL": "http://planted"}}\')\n'
        "(run / 'workspace' / '1').mkdir(parents=True)\n"
        "for folder in (run / 'workspace' / '1', run / 'workspace', run):\n"
        "    (folder / 'CLAUDE.md').write_text('planted')\n"
        "(run / 'tmp' / '1').mkdir(parents=True)\n"
    )
    show = (
        "import os, pathlib; here = pathlib.Path('.').resolve(); "
        "print(sorted(os.listdir(os.environ['HOME'])), sorted(os.listdir('.')), os.listdir(os.environ['TMPDIR']), "
        "[str(p.relative_to(here.parents[1])) for p in (here.parent, here.parents[1]) if (p / 'CLAUDE.md').exists()])"
    )
    with CliStream(tmp_path / "cli.jsonl") as stream:
        rt.prepare_repeat(0)
        assert rt.run([sys.executable, "-c", plant], tmp_path, {"PATH": os.environ["PATH"]}, stream).ok
        rt.prepare_repeat(1)
        assert run.stat().st_mode & 0o777 == 0o700 and (remote / ".lock").is_dir()  # made again, and the lock stays
        assert rt.run([sys.executable, "-c", show], tmp_path, {"PATH": os.environ["PATH"]}, stream).ok
    assert [r["line"] for r in CliStream.read(tmp_path / "cli.jsonl") if r["s"] == "out"] == ["[] [] [] []"]
    rt.teardown()


def test_a_repeat_whose_check_fails_there_runs_no_subject(tmp_path):
    remote = tmp_path / "remote"
    flag = tmp_path / "firewall"
    flag.write_text("up", encoding="utf-8")
    # The check stands in for the firewall's: it passes while the flag is there.
    config = {"exec_prefix": ["env"], "remote_workspace": str(remote), "check": ["test", "-f", str(flag)]}
    rt = RT.build("vm", tmp_path / "run", None, config)
    marker = tmp_path / "ran"
    with CliStream(tmp_path / "cli.jsonl") as stream:
        rt.prepare_repeat(0)
        assert rt.run(["touch", str(marker)], tmp_path, {"PATH": os.environ["PATH"]}, stream).ok
        marker.unlink()
        flag.unlink()  # a subject with sudo removed the firewall
        rt.prepare_repeat(1)
        status = rt.run(["touch", str(marker)], tmp_path, {"PATH": os.environ["PATH"]}, stream)
    assert status.code == 4 and not status.ok and not marker.exists()
    assert f"the machine's check failed there: test -f {flag}" in (tmp_path / "cli.jsonl").read_text(encoding="utf-8")
    rt.teardown()


def test_a_vm_subject_key_that_is_a_judge_key_is_never_written(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "shared")
    remote = tmp_path / "remote"
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": ["env"], "remote_workspace": str(remote)})
    rt.prepare_repeat(0)
    script = "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'none'))"
    env = {"PATH": os.environ["PATH"], "ANTHROPIC_API_KEY": "shared"}
    with CliStream(tmp_path / "cli.jsonl") as stream:
        assert rt.run([sys.executable, "-c", script], tmp_path, env, stream).ok
    lines = [r["line"] for r in CliStream.read(tmp_path / "cli.jsonl")]
    assert "none" in lines
    assert any("ANTHROPIC_API_KEY holds a judge's key" in line for line in lines)
    assert not (remote / "run" / "keys").exists()


def test_a_key_that_cannot_be_written_there_fails_the_repeat_and_the_subject_never_runs(tmp_path):
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": ["env"], "remote_workspace": str(tmp_path / "remote")})
    rt.prepare_repeat(0)
    assert isinstance(rt, RT.VmRuntime)
    rt.vm.exec_prefix = [str(tmp_path / "no-such-prefix")]
    marker = tmp_path / "ran"
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run(["touch", str(marker)], tmp_path, {"PATH": os.environ["PATH"], "ANTHROPIC_API_KEY": KEY}, stream)
    assert status.code == 127 and not status.ok and not marker.exists()
    text = (tmp_path / "cli.jsonl").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY could not be written" in text and KEY not in text


def test_each_vm_repeat_gets_its_own_home_and_tmp_there(tmp_path):
    remote = tmp_path / "remote"
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": ["env"], "remote_workspace": str(remote)})
    script = "import os; print(os.environ['HOME']); print(os.environ['TMPDIR'])"
    with CliStream(tmp_path / "cli.jsonl") as stream:
        for index in (0, 1):
            rt.prepare_repeat(index)
            env = {"PATH": "/usr/bin:/bin", "HOME": "/elsewhere"}
            assert rt.run([sys.executable, "-c", script], tmp_path, env, stream).ok
    lines = [r["line"] for r in CliStream.read(tmp_path / "cli.jsonl") if r["s"] == "out"]
    run = remote / "run"
    assert lines == [str(run / "home" / "0"), str(run / "tmp" / "0"), str(run / "home" / "1"), str(run / "tmp" / "1")]


def gone(pid: int) -> bool:
    """Whether a process has ended, waiting up to five seconds for it."""
    import time

    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_a_timeout_stops_the_subject_there_not_only_the_prefix_here(tmp_path):
    # The prefix runs its words in a session of their own, as ssh does, so killing it here stops nothing there.
    prefix, log = fake_prefix(tmp_path, new_session=True)
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": [prefix], "remote_workspace": str(tmp_path / "remote")})
    rt.prepare_repeat(0)
    pids = tmp_path / "pids"
    child = "import time; time.sleep(60)"
    script = (
        "import os, subprocess, sys, time\n"
        f"c = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{c.pid}}')\n"
        "time.sleep(60)\n"
    )
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run([sys.executable, "-c", script], tmp_path, {"PATH": os.environ["PATH"]}, stream, timeout_s=3)
    assert status.timed_out
    subject, left = map(int, pids.read_text(encoding="utf-8").split())
    assert gone(subject) and gone(left)
    assert any(c["argv"][2] == RT.KILL and c["argv"][-1].endswith("/run/group/0") for c in logged(log))
    rt.teardown()


WAIT = 'kill -0 "-$group"'  # the stop's wait condition, as the script says it


@pytest.mark.parametrize("shell", ["sh", "dash", "bash"])
def test_the_stop_s_wait_condition_answers_for_a_live_group_in_every_shell(shell):
    # dash, Ubuntu's sh, reads a `--` after `kill -0` as a process id, so a wait written that way ends at once.
    import shutil

    if shutil.which(shell) is None:
        pytest.skip(f"no {shell} here")
    assert WAIT in RT.KILL
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        out = subprocess.run([shell, "-c", f"group=$1; {WAIT}", "sh", str(proc.pid)], capture_output=True, text=True)
        assert out.returncode == 0 and out.stderr == ""
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a zombie stays in its process group on Linux only")
def test_the_stop_waits_for_the_group_to_be_gone(tmp_path):
    import threading
    import time

    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    (tmp_path / "group").write_text(str(proc.pid), encoding="utf-8")
    # The killed process stays in its group, as a zombie, until it is reaped here a second later.
    reaper = threading.Timer(1.0, proc.wait)
    reaper.start()
    started = time.monotonic()
    out = subprocess.run(["sh", "-c", RT.KILL, "sh", str(tmp_path / "group")], capture_output=True, text=True, timeout=30)
    elapsed = time.monotonic() - started
    reaper.join()
    assert out.returncode == 0 and out.stderr == ""
    assert elapsed >= 0.8  # it waited for the group, not only sent the kill
    assert proc.returncode == -9


def test_an_interrupt_stops_a_helper_there(tmp_path):
    import signal
    import threading
    import time

    pidfile = tmp_path / "helper.pid"
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": ["env"], "remote_workspace": str(tmp_path / "remote")})
    assert isinstance(rt, RT.VmRuntime)

    def interrupt():
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.05)
        signal.raise_signal(signal.SIGINT)

    threading.Thread(target=interrupt, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        rt.helper(["sh", "-c", f'echo "$$" > {pidfile}; exec sleep 60'])
    assert gone(int(pidfile.read_text(encoding="utf-8")))


def test_a_vm_probe_runs_with_what_the_helpers_get(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "a-token-of-this-machine")
    monkeypatch.setenv("OPENAI_API_KEY", "judge-openai")
    prefix, log = fake_prefix(tmp_path)
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": [prefix], "remote_workspace": str(tmp_path / "remote")})
    assert rt.probe(["echo", "answered"]) == "answered"
    (call,) = logged(log)
    assert "GH_TOKEN" not in call["env"] and "OPENAI_API_KEY" not in call["env"] and "HOME" in call["env"]


def test_a_helper_past_its_timeout_is_stopped_and_noted(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    config = {
        "exec_prefix": ["env"],
        "copy": ["sleep", "30"],
        "remote_workspace": str(tmp_path / "remote"),
        "helper_timeout_s": 1,
    }
    rt = RT.build("vm", tmp_path / "run", None, config, plugin=plugin)
    rt.prepare_repeat(0)
    with CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run(["true"], tmp_path, {"PATH": os.environ["PATH"]}, stream)
    assert status.code == 124
    text = (tmp_path / "cli.jsonl").read_text(encoding="utf-8")
    assert "sleep 30 ... did not finish in 1s and was stopped" in text
    assert "the copy of the plugin to the other machine failed (exit 124)" in text


def test_the_end_of_a_run_removes_its_folder_with_sudo_there_and_notes_what_stays(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo_log = tmp_path / "sudo.log"
    sudo = bin_dir / "sudo"
    sudo.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {sudo_log}\nexit 1\n', encoding="utf-8")  # a sudo that answers no
    sudo.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    remote = tmp_path / "remote"
    rt = RT.build("vm", tmp_path / "run", None, {"exec_prefix": ["env"], "remote_workspace": str(remote)})
    assert isinstance(rt, RT.VmRuntime)
    rt.prepare_repeat(0)
    (remote / "run" / "workspace").mkdir(parents=True)
    (remote / "run" / "workspace" / "kept").write_text("x", encoding="utf-8")
    (remote / "run" / "workspace").chmod(0o500)  # what its user cannot remove, as a root container's files
    try:
        notes = rt.release()
        assert any(f"{remote / 'run'} was not removed there" in note for note in notes)
        assert f"-n rm -rf -- {remote / 'run'}" in sudo_log.read_text(encoding="utf-8")
        assert rt.release() == []  # once
    finally:
        (remote / "run" / "workspace").chmod(0o700)


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
    placed = RT.build(
        "vm", tmp_path, target, {"exec_prefix": ["x"], "remote_plugin": "/opt/p", "remote_target": "/opt/t"}, plugin=plugin
    )
    assert isinstance(placed, RT.VmRuntime)
    assert (placed.plugin_path(), placed.target_path()) == ("/opt/p", "/opt/t")
    assert placed.copies() == []  # what the operator placed there is not copied
    vm = RT.build(
        "vm", tmp_path / "run-1", target, {"exec_prefix": ["x"], "copy": ["x"], "remote_workspace": "/w"}, plugin=plugin
    )
    assert (vm.plugin_path(), vm.target_path()) == ("/w/run-1/plugin", "/w/run-1/target")
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
    monkeypatch.setenv("GH_TOKEN", "a-token-of-this-machine")
    prefix, log = fake_prefix(tmp_path)
    config = {
        "exec_prefix": [prefix],
        "sync": [prefix, "true"],
        "fetch": [prefix, "true"],
        "remote_workspace": str(tmp_path / "remote"),
    }
    rt = RT.build("vm", tmp_path / "run", None, config)
    rt.prepare_repeat(0)
    rt.collect(["*.md"])
    rt.teardown()
    calls = logged(log)
    assert len(calls) >= 5
    for call in calls:
        assert not {"OPENAI_API_KEY", "NOT_A_KEY", "GH_TOKEN"} & set(call["env"])


def test_each_runtime_probes_where_its_subject_runs(tmp_path):
    host = RT.build("host", tmp_path / "h")
    assert host.probe_command(["claude", "--version"]) == ["claude", "--version"]
    container = RT.build("container", tmp_path / "c", config={"image": "img:1", "keys": ["ANTHROPIC_API_KEY"]})
    assert container.probe_command(["claude", "--version"]) == [
        "docker", "run", "--rm", "--network", "none", "img:1", "claude", "--version",
    ]  # fmt: skip
    # A probe that asks for the network gets it in a container; elsewhere it has it anyway.
    assert container.probe_command(["curl", "-sS"], network=True) == ["docker", "run", "--rm", "img:1", "curl", "-sS"]
    assert host.probe_command(["curl"], network=True) == ["curl"]
    vm = RT.build("vm", tmp_path / "v", config={"exec_prefix": ["limactl", "shell", "default", "--"]})
    assert vm.probe_command(["claude", "--version"]) == ["limactl", "shell", "default", "--", "claude", "--version"]


def test_a_probe_answers_its_first_line_and_none_when_it_fails(tmp_path):
    rt = RT.build("host", tmp_path)
    assert rt.probe([sys.executable, "-c", "print(); print('2.1.0 (Claude Code)'); print('more')"]) == "2.1.0 (Claude Code)"
    assert rt.probe([sys.executable, "-c", "import sys; print('half'); sys.exit(1)"]) is None
    assert rt.probe([str(tmp_path / "no-such-binary"), "--version"]) is None


def test_a_probe_runs_without_the_judge_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "judge")
    rt = RT.build("host", tmp_path)
    assert rt.probe([sys.executable, "-c", "import os; print(os.environ.get('OPENAI_API_KEY', 'none'))"]) == "none"


def fake_docker(tmp_path, body):
    """A docker stand-in: a script that answers `image inspect` as the body says."""
    path = tmp_path / "docker"
    path.write_text(f"#!{sys.executable}\nimport sys\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_the_container_names_its_image_by_the_id_the_engine_gives(tmp_path):
    docker = fake_docker(tmp_path, "assert sys.argv[1:4] == ['image', 'inspect', '--format']\nprint('sha256:' + 'a' * 64)")
    rt = RT.build("container", tmp_path / "run", config={"docker": docker, "image": "img:latest"})
    assert rt.image_version() == {"name": "img:latest", "id": "sha256:" + "a" * 64}


def test_an_image_the_engine_does_not_know_has_no_id(tmp_path):
    docker = fake_docker(tmp_path, "sys.exit(1)")
    rt = RT.build("container", tmp_path / "run", config={"docker": docker, "image": "img:latest"})
    assert rt.image_version() == {"name": "img:latest", "id": None}
    assert RT.build("host", tmp_path / "h").image_version() is None


def test_a_session_gets_a_home_and_a_tmpdir_of_its_own_in_the_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(RT.VmRuntime, "helper", lambda self, argv, stdin=None, timeout_s=None: 0)
    rt = RT.build("vm", tmp_path / "run-1", None, {"exec_prefix": ["fake-shell", "--"], "remote_workspace": "/opt/work"})
    assert isinstance(rt, RT.VmRuntime)
    workspace = rt.prepare_repeat(0)
    rt.use_session("scaffold")
    parts = rt.command(["claude"], workspace)[6:11]
    assert parts == [f"/opt/work/run-1/{p}" for p in ("keys/0", "home/0/scaffold", "tmp/0/scaffold", "workspace/0", "group/0")]
    rt.prepare_repeat(1)  # a new repeat starts with the repeat's own again
    assert rt.remote_part("home") == "/opt/work/run-1/home/1"
    host = RT.build("host", tmp_path / "run-2", None, {}, sandbox=tmp_path / "sandbox")
    host.prepare_repeat(0)
    host.use_session("review")
    env = host.environment({})
    assert (
        env["HOME"] == str(tmp_path / "sandbox" / "home" / "0" / "review")
        and (tmp_path / "sandbox" / "tmp" / "0" / "review").is_dir()
    )


def test_the_harness_moves_a_path_out_of_the_workspace_and_back(tmp_path, monkeypatch):
    rt = RT.build("host", tmp_path / "run", None, {}, sandbox=tmp_path / "sandbox")
    workspace = rt.prepare_repeat(0)
    (workspace / "NOTE.md").write_text("n", encoding="utf-8")
    rt.hide("NOTE.md")
    assert not (workspace / "NOTE.md").exists() and (tmp_path / "sandbox" / "hidden" / "0" / "NOTE.md").is_file()
    (workspace / "NOTE.md").write_text("written meanwhile", encoding="utf-8")
    rt.show("NOTE.md")  # the hidden one comes back in its place
    assert (workspace / "NOTE.md").read_text(encoding="utf-8") == "n"
    rt.show("NOTE.md")  # nothing hidden: nothing moves
    assert (workspace / "NOTE.md").read_text(encoding="utf-8") == "n"
    ran: list[list[str]] = []

    def helper(self, argv, stdin=None, timeout_s=None):
        ran.append(argv)
        return 0

    monkeypatch.setattr(RT.VmRuntime, "helper", helper)
    vm = RT.build("vm", tmp_path / "run-1", None, {"exec_prefix": ["fake-shell", "--"], "remote_workspace": "/opt/work"})
    assert isinstance(vm, RT.VmRuntime)
    vm.prepare_repeat(0)
    vm.hide("NOTE.md")
    assert ran[-1] == [
        "fake-shell",
        "--",
        "sh",
        "-c",
        RT.MOVE,
        "sh",
        "/opt/work/run-1/workspace/0/NOTE.md",
        "/opt/work/run-1/hidden/0/NOTE.md",
    ]


def test_a_stop_the_harness_sets_ends_the_command_as_stopped(tmp_path):
    rt = RT.build("host", tmp_path / "run", None, {}, sandbox=tmp_path / "sandbox")
    workspace = rt.prepare_repeat(0)
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    with CliStream(tmp_path / "cli.jsonl") as streams:
        status = rt.run(["sleep", "30"], workspace, {"PATH": "/usr/bin:/bin"}, streams, timeout_s=60, stop=stop)
    assert status.stopped and not status.timed_out and not status.ok and status.duration_s < 10
    assert "stopped by the harness at a bound" in (tmp_path / "cli.jsonl").read_text(encoding="utf-8")
