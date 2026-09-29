"""The deploy's two helper scripts, run against a fake `aws` and `terraform`.

`scripts/deploy_static.sh` publishes a static build: it uploads the entry
points whose content differs from the bucket's copy, invalidates those alone,
and skips the invalidation when none differs. `pre_rollout.sh` runs the
migration's commands in one one-off task, in order, stopping at the first
that fails, and runs the task again when it asks to, a bounded number of
times. No cloud is needed: the fakes record every call and answer from a
JSON file the test writes.
"""

import hashlib
import json
import os
import shlex
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_STATIC = ROOT / "scripts" / "deploy_static.sh"
TASK = "arn:aws:ecs:us-west-2:123456789012:task/acme-test/abc"
DESCRIBE = f"aws ecs describe-tasks --cluster c --tasks {TASK} --query tasks[0]"
PRE_ROLLOUT = ROOT / "deployment" / "terraform" / "modules" / "service" / "pre_rollout.sh"

# One fake for both tools: it appends its argv to calls.jsonl and answers
# from answers.json, keyed by the first words of the call. A list answers
# each matching call in turn, and its last entry every call after.
FAKE = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]
calls = os.path.join(state, "calls.jsonl")
with open(calls, "a") as log:
    log.write(json.dumps([tool, *args]) + "\\n")
answers = json.load(open(os.path.join(state, "answers.json")))
for prefix, answer in answers.items():
    if " ".join([tool, *args]).startswith(prefix):
        if isinstance(answer, list):
            made = [" ".join(json.loads(line)) for line in open(calls)]
            seen = sum(1 for call in made if call.startswith(prefix))
            answer = answer[min(seen, len(answer)) - 1]
        sys.stdout.write(answer)
        break
"""


def _tools(tmp_path: Path, answers: dict[str, str | list[str]]) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("aws", "terraform"):
        path = bin_dir / tool
        path.write_text(FAKE)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state"
    state.mkdir()
    (state / "answers.json").write_text(json.dumps(answers))
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "FAKE_STATE": str(state),
    }


def _calls(env: dict[str, str]) -> list[list[str]]:
    log = Path(env["FAKE_STATE"]) / "calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def _md5(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()


def _build(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<script src=/assets/index-abc.js></script>")
    (dist / "robots.txt").write_text("User-agent: *\n")
    (dist / "assets" / "index-abc.js").write_text("console.log(1)")
    return dist


def _publish(
    tmp_path: Path, remote: dict[str, str]
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    etags = "".join(f'{key}\t"{etag}"\n' for key, etag in remote.items()) or "None\n"
    env = _tools(
        tmp_path,
        {
            "terraform -chdir=root output -raw portal_bucket": "acme-test-portal",
            "terraform -chdir=root output -raw portal_distribution_id": "E123",
            "terraform -chdir=root output -raw portal_url": "https://app.example.test",
            "aws s3api list-objects-v2": etags,
            "aws cloudfront create-invalidation": "I456\n",
        },
    )
    result = subprocess.run(
        ["bash", str(DEPLOY_STATIC), "portal", "root", str(_build(tmp_path))],
        capture_output=True,
        text=True,
        env=env,
    )
    return result, _calls(env)


def _uploaded(calls: list[list[str]]) -> list[str]:
    return [call[4].split("/", 3)[3] for call in calls if call[:3] == ["aws", "s3", "cp"]]


def _invalidated(calls: list[list[str]]) -> list[str] | None:
    for call in calls:
        if call[:3] == ["aws", "cloudfront", "create-invalidation"]:
            start = call.index("--paths") + 1
            end = call.index("--query")
            return call[start:end]
    return None


def test_an_unchanged_build_uploads_no_entry_point_and_invalidates_nothing(tmp_path: Path) -> None:
    dist = _build(tmp_path / "expected")
    remote = {
        "index.html": _md5((dist / "index.html").read_bytes()),
        "robots.txt": _md5((dist / "robots.txt").read_bytes()),
        "config.json": "anything",
    }
    result, calls = _publish(tmp_path, remote)
    assert result.returncode == 0, result.stderr
    assert _uploaded(calls) == []
    assert _invalidated(calls) is None
    assert not any(call[:3] == ["aws", "cloudfront", "wait"] for call in calls)
    assert "nothing to upload or invalidate" in result.stdout


def test_a_changed_index_uploads_it_and_invalidates_it_and_the_root_alone(tmp_path: Path) -> None:
    dist = _build(tmp_path / "expected")
    remote = {
        "index.html": _md5(b"the previous build's page"),
        "robots.txt": _md5((dist / "robots.txt").read_bytes()),
    }
    result, calls = _publish(tmp_path, remote)
    assert result.returncode == 0, result.stderr
    assert _uploaded(calls) == ["index.html"]
    assert _invalidated(calls) == ["/", "/index.html"]
    wait = [call for call in calls if call[:3] == ["aws", "cloudfront", "wait"]]
    assert wait and wait[0][-1] == "I456"
    cp = next(call for call in calls if call[:3] == ["aws", "s3", "cp"])
    assert cp[cp.index("--cache-control") + 1] == "no-cache"


def test_an_empty_bucket_gets_every_entry_point_and_never_config_json(tmp_path: Path) -> None:
    result, calls = _publish(tmp_path, {})
    assert result.returncode == 0, result.stderr
    assert _uploaded(calls) == ["index.html", "robots.txt"]
    assert _invalidated(calls) == ["/", "/index.html", "/robots.txt"]


def test_a_multipart_etag_counts_as_a_difference(tmp_path: Path) -> None:
    dist = _build(tmp_path / "expected")
    remote = {
        "index.html": _md5((dist / "index.html").read_bytes()) + "-2",
        "robots.txt": _md5((dist / "robots.txt").read_bytes()),
    }
    result, calls = _publish(tmp_path, remote)
    assert result.returncode == 0, result.stderr
    assert _uploaded(calls) == ["index.html"]


def test_hashed_assets_are_compared_by_size_not_by_time(tmp_path: Path) -> None:
    result, calls = _publish(tmp_path, {})
    assert result.returncode == 0, result.stderr
    sync = next(call for call in calls if call[:3] == ["aws", "s3", "sync"])
    assert "--size-only" in sync
    assert sync[sync.index("--cache-control") + 1] == "public, max-age=31536000, immutable"


def _pre_rollout(
    tmp_path: Path, commands: list[list[str]], exit_code: str | list[str]
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """`exit_code` is the container's, or one per run of the task in turn."""
    codes = [exit_code] if isinstance(exit_code, str) else exit_code
    env = _tools(
        tmp_path,
        {
            "aws ecs run-task": f"{TASK}\n",
            f"{DESCRIBE}.containers": [f"{code}\n" for code in codes],
            f"{DESCRIBE}.stoppedReason": "Essential container in task exited\n",
        },
    )
    env |= {
        "CLUSTER": "c",
        "TASK_DEFINITION": "arn:aws:ecs:us-west-2:123456789012:task-definition/migrate:7",
        "SUBNETS": "subnet-1,subnet-2",
        "SECURITY_GROUPS": "sg-1",
        "CONTAINER": "migrate",
        "COMMANDS": json.dumps(commands),
    }
    result = subprocess.run(["bash", str(PRE_ROLLOUT)], capture_output=True, text=True, env=env)
    return result, _calls(env)


def _run_tasks(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if call[:3] == ["aws", "ecs", "run-task"]]


def _task_command(calls: list[list[str]]) -> list[str]:
    run_tasks = _run_tasks(calls)
    assert len(run_tasks) == 1, "every command runs in one task, so the cold start is paid once"
    overrides = json.loads(run_tasks[0][run_tasks[0].index("--overrides") + 1])
    (container,) = overrides["containerOverrides"]
    assert container["name"] == "migrate"
    return container["command"]


def test_the_commands_run_in_one_task_in_order(tmp_path: Path) -> None:
    commands = [
        ["acme-api", "migrate", "ensure-logins"],
        ["acme-api", "migrate", "--all"],
    ]
    result, calls = _pre_rollout(tmp_path, commands, "0")
    assert result.returncode == 0, result.stderr
    command = _task_command(calls)
    assert command[:2] == ["sh", "-c"]
    lines = command[2].splitlines()
    assert lines[0] == "set -eu"
    assert [shlex.split(line) for line in lines[1:]] == commands


def test_the_task_script_stops_at_the_first_command_that_fails(tmp_path: Path) -> None:
    """The script the task runs, run here by sh with a stand-in binary: the
    second command fails, so the third never runs and the exit code is the
    failing one's."""
    stand_in = tmp_path / "stand-in"
    stand_in.mkdir()
    log = tmp_path / "ran.txt"
    binary = stand_in / "acme-api"
    binary.write_text(f'#!/bin/sh\necho "$*" >> {log}\n[ "$2" != "--all" ] || exit 3\n')
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    commands = [
        ["acme-api", "migrate", "ensure-logins"],
        ["acme-api", "migrate", "--all"],
        ["acme-api", "migrate", "it's quoted"],
    ]
    (tmp_path / "fake").mkdir()
    result, calls = _pre_rollout(tmp_path / "fake", commands, "0")
    assert result.returncode == 0, result.stderr
    command = _task_command(calls)
    ran = subprocess.run(
        command,
        env={"PATH": f"{stand_in}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )
    assert ran.returncode == 3
    assert log.read_text().splitlines() == ["migrate ensure-logins", "migrate --all"]


def test_a_failed_task_fails_the_apply(tmp_path: Path) -> None:
    result, calls = _pre_rollout(tmp_path, [["acme-api", "migrate", "--all"]], "1")
    assert result.returncode == 1
    assert "pre-rollout task failed" in result.stderr
    assert "the service keeps its current tasks" in result.stderr
    assert len(_run_tasks(calls)) == 1, "a failure that is not a lock wait is not run again"


MIGRATE = [["acme-api", "migrate", "ensure-logins"], ["acme-api", "migrate", "--all"]]


def test_a_task_that_asks_to_run_again_runs_again(tmp_path: Path) -> None:
    """75 is what the migrate command exits with when a lock was not granted
    within its bound: nothing of that role was applied, and the same task,
    run again, resumes where the first stopped."""
    result, calls = _pre_rollout(tmp_path, MIGRATE, ["75", "0"])
    assert result.returncode == 0, result.stderr
    assert "asked to run again" in result.stdout and "run 2 of 3" in result.stdout
    runs = _run_tasks(calls)
    assert len(runs) == 2 and runs[0] == runs[1]


def test_a_task_that_keeps_asking_fails_the_apply_after_three_runs(tmp_path: Path) -> None:
    result, calls = _pre_rollout(tmp_path, MIGRATE, ["75"])
    assert result.returncode == 1
    assert "exited 75" in result.stderr and "the service keeps its current tasks" in result.stderr
    assert len(_run_tasks(calls)) == 3
