"""scaffold/base.py: the render it commits, the branch it moves, and what it refuses."""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCAFFOLD = ROOT / "scaffold"


def load():
    spec = importlib.util.spec_from_file_location("scaffold_base", SCAFFOLD / "base.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["scaffold_base"] = module
    spec.loader.exec_module(module)
    return module


base = load()


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def guideline(tmp_path: Path) -> Path:
    """A guideline repository with the real new.py, a small scaffold, and a release."""
    repo = tmp_path / "swe_guidelines"
    root = repo / "scaffold" / "acme_root"
    (root / "specs").mkdir(parents=True)
    (root / ".agents/skills/acme-watch").mkdir(parents=True)
    (root / ".claude").mkdir()
    shutil.copy(SCAFFOLD / "new.py", repo / "scaffold" / "new.py")
    (root / "README.md").write_text("# Acme\n", encoding="utf-8")
    (root / "specs/architecture.md").write_text("(pinned at release `v0.1.0`)\n", encoding="utf-8")
    (root / ".agents/skills/acme-watch/SKILL.md").write_text("# acme-watch\n", encoding="utf-8")
    (root / ".claude/skills").symlink_to("../.agents/skills")
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin/plugin.json").write_text(json.dumps({"version": "0.1.0"}), encoding="utf-8")
    (repo / "architecture.md").write_text("# The guideline\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "one")
    return repo


def tarball(repo: Path, out: Path) -> tuple[Path, str]:
    """The tarball GitHub serves for the repository's head, and the head's commit."""
    commit = git(repo, "rev-parse", "HEAD")
    with out.open("wb") as handle:
        subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar.gz", f"--prefix=swe_guidelines-{commit[:7]}/", commit],
            stdout=handle,
            check=True,
        )
    return out, commit


def copy(tmp_path: Path, pyproject: str | None = None) -> Path:
    repo = tmp_path / "pressroom"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "notes.md").write_text("mine\n", encoding="utf-8")
    if pyproject is not None:
        (repo / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "the copy")
    return repo


def run(repo: Path, tar: Path, *extra: str, ref: str = "v0.1.0") -> int:
    return base.main([ref, "--repo", str(repo), "--tarball", str(tar), *extra])


def test_the_first_render_starts_the_branch_and_names_its_commit(tmp_path, capsys):
    tar, commit = tarball(guideline(tmp_path), tmp_path / "one.tar.gz")
    repo = copy(tmp_path)
    assert run(repo, tar, "--name", "pressroom") == 0
    message = git(repo, "log", "-1", "--format=%B", "scaffold")
    assert message.startswith(f"The scaffold at v0.1.0 ({commit[:9]}), as pressroom")
    assert f"Scaffold-Commit: {commit}" in message and "Scaffold-Name: pressroom" in message
    assert git(repo, "rev-list", "--count", "scaffold") == "1"
    files = set(git(repo, "ls-tree", "-r", "--name-only", "scaffold").splitlines())
    assert {"README.md", "specs/architecture.md", ".agents/skills/pressroom-watch/SKILL.md", ".claude/skills"} <= files
    assert git(repo, "show", "scaffold:README.md") == "# Pressroom"
    assert "pinned at release `v0.1.0`" in git(repo, "show", "scaffold:specs/architecture.md")
    assert "scaffold is" in capsys.readouterr().out


def test_a_later_render_is_a_child_of_the_one_before(tmp_path):
    source = guideline(tmp_path)
    one, _ = tarball(source, tmp_path / "one.tar.gz")
    repo = copy(tmp_path)
    assert run(repo, one, "--name", "pressroom") == 0
    first = git(repo, "rev-parse", "scaffold")
    (source / "scaffold/acme_root/README.md").write_text("# Acme\n\nMore.\n", encoding="utf-8")
    git(source, "commit", "-q", "-am", "two")
    two, commit = tarball(source, tmp_path / "two.tar.gz")
    assert run(repo, two, ref="v0.2.0") == 0
    assert git(repo, "rev-parse", "scaffold^") == first
    assert f"Scaffold-Commit: {commit}" in git(repo, "log", "-1", "--format=%B", "scaffold")
    assert git(repo, "show", "scaffold:README.md") == "# Pressroom\n\nMore."


def test_a_render_with_the_heads_tree_commits_nothing(tmp_path, capsys):
    tar, _ = tarball(guideline(tmp_path), tmp_path / "one.tar.gz")
    repo = copy(tmp_path)
    assert run(repo, tar, "--name", "pressroom") == 0
    head = git(repo, "rev-parse", "scaffold")
    assert run(repo, tar) == 0
    assert git(repo, "rev-parse", "scaffold") == head
    assert "unchanged" in capsys.readouterr().out


def test_the_render_leaves_the_working_tree_the_index_and_the_other_branches_alone(tmp_path):
    tar, _ = tarball(guideline(tmp_path), tmp_path / "one.tar.gz")
    repo = copy(tmp_path)
    (repo / "draft.md").write_text("draft\n", encoding="utf-8")
    git(repo, "add", "draft.md")
    before = (git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"))
    assert run(repo, tar, "--name", "pressroom") == 0
    assert (git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain")) == before
    assert not (repo / "README.md").exists()


def test_a_clone_builds_on_origins_branch(tmp_path):
    tar, _ = tarball(guideline(tmp_path), tmp_path / "one.tar.gz")
    upstream = copy(tmp_path)
    assert run(upstream, tar, "--name", "pressroom") == 0
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
    (tmp_path / "swe_guidelines/scaffold/acme_root/README.md").write_text("# Acme\n\nMore.\n", encoding="utf-8")
    git(tmp_path / "swe_guidelines", "commit", "-q", "-am", "two")
    two, _ = tarball(tmp_path / "swe_guidelines", tmp_path / "two.tar.gz")
    assert run(clone, two, ref="v0.2.0") == 0
    assert git(clone, "rev-parse", "scaffold^") == git(clone, "rev-parse", "origin/scaffold")


def test_the_name_is_the_arch_check_package_when_no_base_records_one(tmp_path):
    tar, _ = tarball(guideline(tmp_path), tmp_path / "one.tar.gz")
    repo = copy(tmp_path, pyproject='[project]\nname = "x"\n\n[tool.arch-check]\npackage = "pressroom"\n')
    assert run(repo, tar) == 0
    assert "Scaffold-Name: pressroom" in git(repo, "log", "-1", "--format=%B", "scaffold")


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ((), "give it with --name"),
        (("--name", "Press Room"), "is not a name new.py takes"),
        (("--source", "https://gitlab.com/o/r"), "is not a repository on GitHub"),
    ],
)
def test_a_name_or_a_source_that_cannot_be_used_is_refused(tmp_path, capsys, extra, reason):
    tar, _ = tarball(guideline(tmp_path), tmp_path / "one.tar.gz")
    repo = copy(tmp_path)
    assert run(repo, tar, *extra) == 2
    assert reason in capsys.readouterr().err
    assert git(repo, "branch", "--list", "scaffold") == ""


def test_a_name_other_than_the_recorded_one_is_refused(tmp_path, capsys):
    tar, _ = tarball(guideline(tmp_path), tmp_path / "one.tar.gz")
    repo = copy(tmp_path)
    assert run(repo, tar, "--name", "pressroom") == 0
    assert run(repo, tar, "--name", "newsroom") == 2
    assert "keeps one name" in capsys.readouterr().err


@pytest.mark.parametrize("ref", ["a b", "../main", "v1..v2", "", "main/", "x.lock"])
def test_a_ref_that_is_not_one_is_refused(tmp_path, capsys, ref):
    tar, _ = tarball(guideline(tmp_path), tmp_path / "one.tar.gz")
    repo = copy(tmp_path)
    assert run(repo, tar, "--name", "pressroom", ref=ref) == 2
    assert "is not a tag, a branch, or a commit" in capsys.readouterr().err
    assert git(repo, "branch", "--list", "scaffold") == ""


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (urllib.error.HTTPError("u", 404, "Not Found", Message(), io.BytesIO()), "is not a tag, a branch, or a commit of"),
        (urllib.error.HTTPError("u", 503, "Unavailable", Message(), io.BytesIO()), "answered 503"),
        (urllib.error.URLError("no route"), "could not be fetched"),
    ],
)
def test_a_download_that_fails_is_refused(tmp_path, capsys, monkeypatch, error, reason):
    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(base.urllib.request, "urlopen", refuse)
    repo = copy(tmp_path)
    assert base.main(["v9.9.9", "--repo", str(repo), "--name", "pressroom"]) == 2
    assert reason in capsys.readouterr().err
    assert git(repo, "branch", "--list", "scaffold") == ""


def crafted(tmp_path: Path, members: list[tuple[str, str]], comment: str | None = "a" * 40) -> Path:
    """A tarball of (name, link target or file text) pairs; a link's text starts with '->'."""
    out = tmp_path / "crafted.tar.gz"
    headers = {"comment": comment} if comment else {}
    with tarfile.open(out, "w:gz", format=tarfile.PAX_FORMAT, pax_headers=headers) as archive:
        for name, body in members:
            info = tarfile.TarInfo(name)
            if body.startswith("->"):
                info.type, info.linkname = tarfile.SYMTYPE, body[2:]
                archive.addfile(info)
            else:
                data = body.encode()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return out


@pytest.mark.parametrize(
    ("members", "reason"),
    [
        ([("top/scaffold/../../evil.txt", "x")], "climbs out of its folder"),
        ([("top/scaffold/acme_root/x", "->/etc/passwd")], "points out of the scaffold"),
        ([("top/scaffold/acme_root/x", "->../../../../outside")], "points out of the scaffold"),
        ([("top/architecture.md", "x")], "has no scaffold/acme_root"),
    ],
)
def test_a_tarball_that_would_write_outside_the_scaffold_or_holds_none_is_refused(tmp_path, capsys, members, reason):
    repo = copy(tmp_path)
    assert run(repo, crafted(tmp_path, members), "--name", "pressroom") == 2
    assert reason in capsys.readouterr().err
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "outside").exists()
    assert git(repo, "branch", "--list", "scaffold") == ""


def test_a_tarball_that_names_no_commit_is_refused(tmp_path, capsys):
    repo = copy(tmp_path)
    assert run(repo, crafted(tmp_path, [("top/scaffold/new.py", "")], comment=None), "--name", "pressroom") == 2
    assert "names no commit" in capsys.readouterr().err


def test_a_file_that_is_not_a_tarball_is_refused(tmp_path, capsys):
    repo = copy(tmp_path)
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"<html>not found</html>")
    assert run(repo, junk, "--name", "pressroom") == 2
    assert "could not be read" in capsys.readouterr().err


def test_a_base_the_copy_merged_is_found_without_its_branch(tmp_path, capsys):
    source = guideline(tmp_path)
    one, _ = tarball(source, tmp_path / "one.tar.gz")
    repo = copy(tmp_path)
    assert run(repo, one, "--name", "pressroom") == 0
    first = git(repo, "rev-parse", "scaffold")
    assert "git merge -s ours --allow-unrelated-histories scaffold" in capsys.readouterr().out
    git(repo, "merge", "-q", "-s", "ours", "--allow-unrelated-histories", "scaffold", "-m", "graft")
    git(repo, "branch", "-D", "scaffold")
    (source / "scaffold/acme_root/README.md").write_text("# Acme\n\nMore.\n", encoding="utf-8")
    git(source, "commit", "-q", "-am", "two")
    two, _ = tarball(source, tmp_path / "two.tar.gz")
    assert run(repo, two, ref="v0.2.0") == 0
    assert git(repo, "rev-parse", "scaffold^") == first
    assert "next: on a branch cut from the main branch, git merge scaffold" in capsys.readouterr().out


def test_a_stale_branch_gives_way_to_the_newer_render(tmp_path):
    source = guideline(tmp_path)
    one, _ = tarball(source, tmp_path / "one.tar.gz")
    upstream = copy(tmp_path)
    assert run(upstream, one, "--name", "pressroom") == 0
    first = git(upstream, "rev-parse", "scaffold")
    (source / "scaffold/acme_root/README.md").write_text("# Acme\n\nMore.\n", encoding="utf-8")
    git(source, "commit", "-q", "-am", "two")
    two, _ = tarball(source, tmp_path / "two.tar.gz")
    assert run(upstream, two, ref="v0.2.0") == 0
    second = git(upstream, "rev-parse", "scaffold")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
    git(clone, "branch", "scaffold", first)
    (source / "scaffold/acme_root/README.md").write_text("# Acme\n\nMore still.\n", encoding="utf-8")
    git(source, "commit", "-q", "-am", "three")
    three, _ = tarball(source, tmp_path / "three.tar.gz")
    assert run(clone, three, ref="v0.3.0") == 0
    assert git(clone, "rev-parse", "scaffold^") == second


def test_renders_on_two_lines_are_refused(tmp_path, capsys):
    source = guideline(tmp_path)
    one, _ = tarball(source, tmp_path / "one.tar.gz")
    upstream = copy(tmp_path)
    assert run(upstream, one, "--name", "pressroom") == 0
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
    (source / "scaffold/acme_root/README.md").write_text("# Acme\n\nMore.\n", encoding="utf-8")
    git(source, "commit", "-q", "-am", "two")
    two, _ = tarball(source, tmp_path / "two.tar.gz")
    assert run(upstream, two, ref="v0.2.0") == 0
    (source / "scaffold/acme_root/README.md").write_text("# Acme\n\nOther.\n", encoding="utf-8")
    git(source, "commit", "-q", "-am", "three")
    three, _ = tarball(source, tmp_path / "three.tar.gz")
    assert run(clone, three, ref="v0.3.0") == 0
    git(clone, "fetch", "-q", "origin")
    head = git(clone, "rev-parse", "scaffold")
    assert run(clone, two, ref="v0.2.0") == 2
    assert "are on two lines" in capsys.readouterr().err
    assert git(clone, "rev-parse", "scaffold") == head
