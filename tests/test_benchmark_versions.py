"""benchmark/harness/versions.py: every version a run ran, as a reference."""

import json
import os
import shutil
import subprocess

import pytest

from harness import versions as V

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(root, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@b", GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@b")
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def checkout(tmp_path):
    """A committed checkout with a plugin manifest, a skill, and a run folder."""
    root = tmp_path / "repo"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "p", "version": "1.2.3"}), encoding="utf-8")
    (root / "skills").mkdir()
    (root / "skills" / "one.md").write_text("one\n", encoding="utf-8")
    (root / "benchmark" / "runs").mkdir(parents=True)
    (root / "benchmark" / "runs" / "README.md").write_text("runs\n", encoding="utf-8")
    (root / "docs.md").write_text("docs\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "one")
    return root


SCOPE = (".claude-plugin", "skills", "benchmark", ":(exclude)benchmark/runs")


@needs_git
def test_a_clean_checkout_names_its_commit_and_plugin_version(checkout):
    found = V.checkout(checkout, SCOPE)
    assert len(found["commit"]) == 40
    assert found["plugin_version"] == "1.2.3"
    assert found["dirty"] is False and found["dirty_paths"] == [] and found["dirty_sha256"] is None


@needs_git
def test_a_change_no_commit_holds_is_named_with_one_hash_over_its_content(checkout):
    (checkout / "skills" / "one.md").write_text("changed\n", encoding="utf-8")
    (checkout / "skills" / "two.md").write_text("new\n", encoding="utf-8")
    found = V.checkout(checkout, SCOPE)
    assert found["dirty"] is True
    assert found["dirty_paths"] == ["skills/one.md", "skills/two.md"]
    first = found["dirty_sha256"]
    assert len(first) == 64
    (checkout / "skills" / "two.md").write_text("newer\n", encoding="utf-8")
    assert V.checkout(checkout, SCOPE)["dirty_sha256"] != first  # the same paths, other content


@needs_git
def test_a_change_outside_the_scope_leaves_the_checkout_clean(checkout):
    (checkout / "docs.md").write_text("changed\n", encoding="utf-8")
    (checkout / "benchmark" / "runs" / "20260101-000000-one-aa").mkdir()
    (checkout / "benchmark" / "runs" / "20260101-000000-one-aa" / "run.json").write_text("{}\n", encoding="utf-8")
    assert V.checkout(checkout, SCOPE)["dirty"] is False


@needs_git
def test_a_deleted_and_a_renamed_file_are_named(checkout):
    git(checkout, "mv", "skills/one.md", "skills/renamed.md")
    assert V.checkout(checkout, SCOPE)["dirty_paths"] == ["skills/renamed.md"]
    (checkout / "skills" / "renamed.md").unlink()
    found = V.checkout(checkout, SCOPE)
    assert found["dirty"] is True and found["dirty_paths"] == ["skills/renamed.md"]


def test_a_folder_that_is_no_checkout_cannot_say_whether_it_was_clean(tmp_path):
    found = V.checkout(tmp_path, SCOPE)
    assert found["commit"] == "" and found["dirty"] is None and found["plugin_version"] is None


def test_the_porcelain_paths_leave_out_a_rename_s_origin():
    porcelain = "R  new.md\0old.md\0 M a.md\0?? b/c.md\0"
    assert V.changed_paths(porcelain) == ["a.md", "b/c.md", "new.md"]


def test_a_tree_hash_follows_the_content_and_leaves_out_the_caches(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    first = V.sha256_tree(tmp_path)
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"\0")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "x").write_text("x", encoding="utf-8")
    assert V.sha256_tree(tmp_path) == first
    (tmp_path / "src" / "a.py").write_text("a = 2\n", encoding="utf-8")
    assert V.sha256_tree(tmp_path) != first


def test_a_tree_hash_counts_a_path_not_only_the_bytes(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "a.py").write_text("same\n", encoding="utf-8")
    (tmp_path / "two" / "b.py").write_text("same\n", encoding="utf-8")
    assert V.sha256_tree(tmp_path / "one") != V.sha256_tree(tmp_path / "two")


def test_a_path_inside_the_repository_is_recorded_relative_to_it(tmp_path):
    root = tmp_path / "repo"
    (root / "benchmark" / "fixtures").mkdir(parents=True)
    assert V.shown(root / "benchmark" / "fixtures", root) == "benchmark/fixtures"
    assert V.shown(tmp_path / "elsewhere", root) == str((tmp_path / "elsewhere").resolve())
    assert V.shown(None, root) is None


def test_content_hashes_the_staged_copy_and_records_the_original_path(tmp_path):
    root = tmp_path / "repo"
    fixture = root / "benchmark" / "fixtures" / "one"
    fixture.mkdir(parents=True)
    (fixture / "a.py").write_text("a\n", encoding="utf-8")
    staged = tmp_path / "sandbox" / "target"
    shutil.copytree(fixture, staged)
    found = V.content(fixture, root, staged)
    assert found == {"path": "benchmark/fixtures/one", "sha256": V.sha256_tree(fixture)}
    answers = root / "benchmark" / "fixtures" / "one.expected.yaml"
    answers.write_text("findings: []\n", encoding="utf-8")
    assert V.content(answers, root) == {"path": "benchmark/fixtures/one.expected.yaml", "sha256": V.sha256_file(answers)}
    assert V.content(None, root) is None
