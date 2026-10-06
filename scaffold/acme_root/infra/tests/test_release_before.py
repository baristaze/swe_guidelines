"""`scripts/release_before.sh`, the release before's suite on a branch's schema,
run in a throwaway repository against a fake `make` and `uv`.

A branch that changes no migration ends green with no call to either, so the
required check costs a pull request nothing. One that changes a migration
migrates once, then for each release before (the merge base, and the tip of
origin/release when it exists and differs) stamps every role to that
release's heads and runs its integration suite without its own migration
tests and the tests the deselect file names. A failure fails the run and
names the release, and the records are stamped back to the branch either way.
The fakes record every call; the real run over the compose stack is CI's.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_before.sh"
DESELECT = ROOT / "scripts" / "release_before_deselect.txt"
JOB = "the release before passes on this schema"

# One fake for both tools: it appends [tool, cwd, the .env it finds, *argv]
# to calls.jsonl, and a pytest run whose directory names FAKE_FAILS exits 1.
FAKE = """#!/usr/bin/env python3
import json, os, sys
tool = os.path.basename(sys.argv[0])
found = open(".env").read() if os.path.exists(".env") else None
with open(os.path.join(os.environ["FAKE_STATE"], "calls.jsonl"), "a") as log:
    log.write(json.dumps([tool, os.getcwd(), found, *sys.argv[1:]]) + "\\n")
fails = os.environ.get("FAKE_FAILS")
sys.exit(1 if "pytest" in sys.argv and fails and fails in os.getcwd() else 0)
"""

# Commits under a fixed identity, away from the machine's git configuration.
GIT = {
    "GIT_AUTHOR_NAME": "a",
    "GIT_AUTHOR_EMAIL": "a@acme.example",
    "GIT_COMMITTER_NAME": "a",
    "GIT_COMMITTER_EMAIL": "a@acme.example",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GIT},
    ).stdout.strip()


def commit(repo: Path, path: str, text: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "-qm", path)
    return git(repo, "rev-parse", "HEAD")


def repository(tmp_path: Path) -> tuple[Path, dict[str, str], str]:
    """A repository whose main holds the script, the deselect file with one
    line, and one migration; the environment that runs it with the fakes."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    env = {
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "FAKE_STATE": str(tmp_path),
        **GIT,
    }
    (tmp_path / "bin").mkdir()
    for tool in ("make", "uv"):
        fake = tmp_path / "bin" / tool
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    git(repo, "init", "-q", "-b", "main")
    (repo / "scripts" / "release_before.sh").write_bytes(SCRIPT.read_bytes())
    (repo / "scripts" / "release_before.sh").chmod(0o755)
    (repo / "scripts" / "release_before_deselect.txt").write_text(
        f"{DESELECT.read_text()}\n# A dropped column its test still writes.\n"
        "om/tests/integration/test_x.py::test_writes_x\n"
    )
    (repo / ".env.example").write_text("ACME_DATABASE_URL=example\n")
    (repo / ".env").write_text("ACME_DATABASE_URL=branch\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "the scripts")
    main = commit(repo, "om/migrations/versions/core/1_first.py", "revision = '1'\n")
    git(repo, "checkout", "-qb", "branch")
    return repo, env, main


def run(repo: Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "release_before.sh"), "main"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    log = Path(env["FAKE_STATE"]) / "calls.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls


def stamps(calls: list[list[str]]) -> list[str]:
    return [call[-1] for call in calls if "stamp" in call]


def suites(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if "pytest" in call]


def test_a_branch_that_changes_no_migration_ends_green_with_no_database(tmp_path: Path) -> None:
    repo, env, _ = repository(tmp_path)
    commit(repo, "README.md", "a change beside the migrations\n")
    result, calls = run(repo, env)
    assert result.returncode == 0, result.stderr
    assert "no migration changed since" in result.stdout
    assert calls == []


def test_the_merge_base_runs_alone_when_there_is_no_release_branch(tmp_path: Path) -> None:
    repo, env, main = repository(tmp_path)
    commit(repo, "om/migrations/versions/core/2_second.py", "revision = '2'\n")
    result, calls = run(repo, env)
    assert result.returncode == 0, result.stderr
    assert "there is no origin/release yet, so the merge base runs alone" in result.stdout
    assert [call[3:] for call in calls if call[0] == "make"] == [
        ["--no-print-directory", "infra-up"],
        ["--no-print-directory", "migrate"],
    ]
    tree = stamps(calls)[0]
    assert Path(tree).name == main[:12]
    # Stamped to the release before, and back to the branch on the way out.
    assert stamps(calls) == [tree, str(repo)]
    [suite] = suites(calls)
    assert Path(suite[1]).name == main[:12]
    # Its own defaults, then the branch's .env, which wins where both set one.
    assert suite[2] == "ACME_DATABASE_URL=example\nACME_DATABASE_URL=branch\n"
    assert suite[suite.index("pytest") :] == [
        "pytest",
        "-q",
        "-m",
        "integration",
        "-p",
        "no:cacheprovider",
        "--deselect",
        "om/tests/integration/test_migrations.py",
        "--deselect",
        "om/tests/integration/test_x.py::test_writes_x",
    ]
    assert not Path(tree).exists()


def test_the_release_branch_runs_too_and_a_failure_names_it(tmp_path: Path) -> None:
    repo, env, main = repository(tmp_path)
    git(repo, "checkout", "-q", "main")
    release = git(repo, "rev-parse", "HEAD~1")
    git(repo, "update-ref", "refs/remotes/origin/release", release)
    commit(repo, "README.md", "main moves past the release\n")
    main = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "branch")
    git(repo, "merge", "-q", "main")
    commit(repo, "om/migrations/versions/core/2_second.py", "revision = '2'\n")
    result, calls = run(repo, {**env, "FAKE_FAILS": release[:12]})
    assert result.returncode == 1
    assert "the merge base passes on this schema" in result.stdout
    assert "failed on this schema: origin/release." in result.stderr
    ran = [Path(suite[1]).name for suite in suites(calls)]
    assert ran == [main[:12], release[:12]]
    assert stamps(calls)[-1] == str(repo.resolve())


def test_a_release_branch_at_the_merge_base_runs_once(tmp_path: Path) -> None:
    repo, env, main = repository(tmp_path)
    git(repo, "update-ref", "refs/remotes/origin/release", main)
    commit(repo, "om/migrations/versions/core/2_second.py", "revision = '2'\n")
    result, calls = run(repo, env)
    assert result.returncode == 0, result.stderr
    assert "origin/release is the merge base, so one release runs" in result.stdout
    assert len(suites(calls)) == 1


def test_the_job_always_reports_and_the_main_ruleset_requires_it() -> None:
    """A path filter never reports on a pull request it filters out, so a
    required check behind one blocks that pull request for good."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    for trigger in ("pull_request", "merge_group"):
        assert "paths" not in (workflow[True][trigger] or {})
    job = workflow["jobs"]["release-before"]
    assert job["name"] == JOB
    assert "needs" not in job
    assert f'"{JOB}"' in (ROOT / "scripts" / "cloud_create.sh").read_text()
