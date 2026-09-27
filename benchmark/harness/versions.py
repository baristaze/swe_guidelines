"""What a run ran: every version that decides a score, as a reference.

A score compares with another only when the same things made both: the
same skills and harness, the same Claude Code, the same image, the same
target, and the same planted answers. So a run names each of them in a
form that resolves somewhere other than the machine that ran it:

- the checkout: its commit, the plugin's version, and whether the tree
  held changes no commit holds. The skills are staged from the working
  tree, so a commit alone would name code that did not run. A dirty
  checkout names the changed paths and one hash over their content.
- Claude Code: what `claude --version` answers inside the runtime the
  subject runs in, not on this machine, because a container carries its
  own.
- the image: the id the container engine gives the image, since a tag
  such as `latest` names whatever was built last.
- the target and the expected findings: a path inside the repository,
  and a hash of the content, so a fixture that changed under the same
  path is told apart.

Only the standard library is imported here.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

# What no hash counts: the caches a tool leaves behind, the same names
# `runtime.stage_target` leaves out of the staged copy.
CACHES = ("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".ruff_cache")


def sha256_file(path: Path) -> str:
    """The SHA-256 of one file's bytes, as hex."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def cached(rel: Path) -> bool:
    """Whether a path inside a tree is a tool's cache."""
    return any(fnmatch.fnmatch(part, pattern) for part in rel.parts for pattern in CACHES)


def sha256_tree(root: Path) -> str:
    """One SHA-256 over a folder: every file's path and content, in path order.

    A symlink counts as its link text, not what it points at, as the staged
    copy keeps it. Caches are left out, so a run that left a `__pycache__`
    behind does not change the hash of the tree it read.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if cached(rel):
            continue
        if path.is_symlink():
            content = "link:" + str(path.readlink())
        elif path.is_file():
            content = sha256_file(path)
        else:
            continue
        digest.update(f"{rel.as_posix()}\0{content}\n".encode())
    return digest.hexdigest()


def shown(path: Path | None, root: Path) -> str | None:
    """A path as a run records it: inside the repository, relative to it; outside, as it is."""
    if path is None:
        return None
    path = Path(path).resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def git(root: Path, *args: str) -> str | None:
    """What a git command prints in `root`, or None when it fails or git is not there."""
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def changed_paths(porcelain: str) -> list[str]:
    """The paths of `git status --porcelain=v1 -z`, a rename's origin left out."""
    fields = porcelain.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            index += 1  # the next field is where the path came from
    return sorted(set(paths))


def checkout(root: Path, scope: Sequence[str]) -> dict:
    """The commit, the plugin version, and the changes no commit holds, within `scope`.

    `scope` is a git pathspec: the paths whose content decides a score.
    `dirty` is None when `root` is not a git checkout, since then no one
    can say.
    """
    commit = (git(root, "rev-parse", "HEAD") or "").strip()
    status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *scope)
    paths = changed_paths(status) if status is not None else []
    digest = None
    if paths:
        hashed = hashlib.sha256()
        for rel in paths:
            file = root / rel
            content = sha256_file(file) if file.is_file() else "deleted"
            hashed.update(f"{rel}\0{content}\n".encode())
        digest = hashed.hexdigest()
    return {
        "commit": commit,
        "plugin_version": plugin_version(root),
        "dirty": None if status is None or not commit else bool(paths),
        "dirty_paths": paths,
        "dirty_sha256": digest,
    }


def plugin_version(root: Path) -> str | None:
    """The release version `.claude-plugin/plugin.json` carries, or None."""
    try:
        data = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else None


def content(path: Path | None, root: Path, tree: Path | None = None) -> dict | None:
    """A file or a folder as a run records it: its path and the hash of its content.

    `tree` is the copy to hash when it is not `path` itself, such as the
    staged target the subject read; the path recorded is always `path`.
    """
    if path is None:
        return None
    read = Path(tree) if tree is not None else Path(path)
    return {"path": shown(path, root), "sha256": sha256_tree(read) if read.is_dir() else sha256_file(read)}
