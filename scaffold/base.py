#!/usr/bin/env python3
"""Commit the scaffold at a ref of the guideline, under a copy's name, onto the
copy's `scaffold` branch.

    python3 scaffold/base.py v0.42.0
    python3 scaffold/base.py <commit> --repo ~/code/pressroom --name pressroom

A copy of the scaffold keeps its base in git. Its `scaffold` branch holds the
scaffold as the copy took it: each commit there is the scaffold at one commit
of the guideline, renamed by that commit's own `new.py`, and its parent is the
render before it. The copy's main branch merges the branch, so the render it
merged last is the merge base of the next move, and `git merge scaffold` brings
in what the scaffold changed since, three ways, and keeps what the copy
changed.

The render's parent is the copy's last render: the newest of its `scaffold`
branch, origin's, and the last render the checkout merged, so a stale branch
never wins, and a base whose branch was never pushed is still found.

The ref is a release tag, a branch, or a commit of the guideline on GitHub. The
script fetches it as one tarball, never a clone, and reads only `scaffold/` and
`.claude-plugin/` from it. The commit it writes names the guideline commit the
tarball holds and the name the render took. A render whose tree is the branch
head's commits nothing. The script moves only `scaffold`: it never touches the
working tree, the index, or another branch, and it pushes nothing.

Standard library only, as `new.py` is.
"""

from __future__ import annotations

import argparse
import http.client
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SOURCE = "https://github.com/baristaze/swe_guidelines"
"""The guideline a copy takes its scaffold from, unless `--source` names a fork."""
BRANCH = "scaffold"
"""The copy's branch that holds its base."""
TAKEN = ("scaffold", ".claude-plugin")
"""What the script reads from the tarball: the scaffold and `new.py`, and the
plugin manifest `new.py` reads the release to pin from."""

SOURCE_URL = re.compile(r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)?$")
"""One or two snake_case words, the names `new.py` takes."""
ARCH_CHECK_PACKAGE = re.compile(r'^\[tool\.arch-check\]\s*$(?:(?!^\[).)*?^package\s*=\s*"([^"]+)"', re.M | re.S)
"""The copy's package under `[tool.arch-check]` in its root `pyproject.toml`,
which is its name."""

TRAILER_SOURCE = "Scaffold-Source"
TRAILER_COMMIT = "Scaffold-Commit"
TRAILER_NAME = "Scaffold-Name"
TIMEOUT = 120
"""Seconds the tarball's download may take."""


class Refused(Exception):
    """Why the render is not committed. The branch stays where it was."""


@dataclass(frozen=True)
class Render:
    commit: str
    """The guideline commit the tarball holds."""
    folder: Path
    """The rendered copy."""


def git(repo: Path, *args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, input=stdin, check=False)
    if done.returncode != 0:
        raise Refused(f"git {args[0]} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip()


def tarball_url(source: str, ref: str) -> str:
    match = SOURCE_URL.match(source)
    if match is None:
        raise Refused(f"{source!r} is not a repository on GitHub, such as {SOURCE}")
    if not REF.match(ref) or ".." in ref or "//" in ref or ref.endswith(("/", ".lock")):
        raise Refused(f"{ref!r} is not a tag, a branch, or a commit")
    owner, repo = match.groups()
    return f"https://codeload.github.com/{owner}/{repo}/tar.gz/{urllib.parse.quote(ref, safe='/')}"


def download(url: str, ref: str, source: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise Refused(f"{ref!r} is not a tag, a branch, or a commit of {source}") from error
        raise Refused(f"{url} answered {error.code}") from error
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
        raise Refused(f"{url} could not be fetched: {error!r}") from error


def unpack(tarball: Path, into: Path) -> str:
    """Writes `scaffold/` and `.claude-plugin/` of the tarball into `into`;
    returns the commit the tarball holds. A member outside them is skipped; one
    that would land outside `into`, or that is neither a file, a folder, nor a
    link, is refused. A link is written as a link and never followed: a member
    whose place is a link, or lies under one, is refused, and so is a link that
    leaves `into` as the filesystem resolves it, once every member is
    written."""
    try:
        with tarfile.open(tarball, mode="r:*") as archive:
            return unpack_archive(archive, into)
    except (tarfile.TarError, EOFError, OSError) as error:
        raise Refused(f"the tarball could not be read: {error}") from error


def through_link(root: Path, rel: PurePosixPath) -> bool:
    """Whether a link stands at `rel` under `root`, or above it: a write there
    follows the link, to wherever it points."""
    return any(root.joinpath(*rel.parts[:depth]).is_symlink() for depth in range(1, len(rel.parts) + 1))


def unpack_archive(archive: tarfile.TarFile, into: Path) -> str:
    commit = str(archive.pax_headers.get("comment", "")).strip()
    if not COMMIT.match(commit):
        raise Refused("the tarball names no commit; fetch one GitHub made from a ref")
    root = into.resolve()
    links: list[tuple[str, Path]] = []
    for member in archive:
        parts = PurePosixPath(member.name).parts
        if len(parts) < 2 or parts[1] not in TAKEN:
            continue
        rel = PurePosixPath(*parts[1:])
        if rel.is_absolute() or ".." in rel.parts:
            raise Refused(f"the tarball's {member.name!r} climbs out of its folder")
        out = root.joinpath(*rel.parts)
        if through_link(root, rel):
            raise Refused(f"the tarball's {member.name!r} is written through a link")
        if member.isdir():
            out.mkdir(parents=True, exist_ok=True)
        elif member.issym():
            target = PurePosixPath(member.linkname)
            landing = os.path.normpath(out.parent / member.linkname)
            if target.is_absolute() or not landing.startswith(str(root) + os.sep):
                raise Refused(f"the tarball's link {member.name!r} points out of the scaffold")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.symlink_to(member.linkname)
            links.append((member.name, out))
        elif member.isfile():
            source = archive.extractfile(member)
            if source is None:
                raise Refused(f"the tarball's {member.name!r} could not be read")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(source.read())
            out.chmod(0o755 if member.mode & 0o111 else 0o644)
        else:
            raise Refused(f"the tarball's {member.name!r} is not a file, a folder, or a link")
    # A target is checked by its text when its link is made. What it resolves
    # to is known only now: a link on its way may have been made after it.
    for name, link in links:
        if not os.path.realpath(link).startswith(str(root) + os.sep):
            raise Refused(f"the tarball's link {name!r} points out of the scaffold")
    if not (root / "scaffold" / "new.py").is_file() or not (root / "scaffold" / "acme_root").is_dir():
        raise Refused("that commit has no scaffold/acme_root to copy; the guideline added it in v0.39.0")
    return commit


def render(tarball: Path, name: str, work: Path) -> Render:
    source = work / "source"
    source.mkdir()
    commit = unpack(tarball, source)
    folder = work / "render" / name
    done = subprocess.run(
        [sys.executable, str(source / "scaffold" / "new.py"), str(folder)], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise Refused(f"new.py at {commit[:9]} refused: {done.stderr.strip()}")
    return Render(commit, folder)


def trailers(message: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in message.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key in (TRAILER_SOURCE, TRAILER_COMMIT, TRAILER_NAME):
            found[key] = value.strip()
    return found


RENDER = r"^Scaffold-Commit: [0-9a-f]{40}$"
"""The trailer that marks a render in the copy's history."""


def answer(repo: Path, *args: str) -> str | None:
    """What a git query prints, or None when it answers nothing."""
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    return done.stdout.strip() or None if done.returncode == 0 else None


def base_of(repo: Path) -> str | None:
    """The copy's last render: the newest of its `scaffold` branch, origin's,
    and the last render its checkout merged. A stale branch never wins over a
    newer render, and a base whose branch was never pushed is still found."""
    found = [
        answer(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}^{{commit}}"),
        answer(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{BRANCH}^{{commit}}"),
        answer(repo, "log", "-1", "--no-merges", "-E", f"--grep={RENDER}", "--format=%H", "HEAD"),
    ]
    tip: str | None = None
    for render in filter(None, found):
        if tip is None or ancestor(repo, tip, render):
            tip = render
        elif not ancestor(repo, render, tip):
            raise Refused(f"the renders {tip[:9]} and {render[:9]} are on two lines; keep one on {BRANCH}")
    return tip


def ancestor(repo: Path, older: str, newer: str) -> bool:
    done = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer], check=False)
    return done.returncode == 0


def name_of(repo: Path, given: str | None, head: str | None) -> str:
    recorded = trailers(git(repo, "log", "-1", "--format=%B", head)).get(TRAILER_NAME) if head else None
    if given and recorded and given != recorded:
        raise Refused(f"the base was rendered as {recorded!r}; a copy keeps one name")
    name = given or recorded
    if name is None:
        pyproject = repo / "pyproject.toml"
        match = ARCH_CHECK_PACKAGE.search(pyproject.read_text(encoding="utf-8")) if pyproject.is_file() else None
        name = match.group(1) if match else None
    if name is None:
        raise Refused("the copy's name is not recorded; give it with --name")
    if not NAME.match(name):
        raise Refused(f"{name!r} is not a name new.py takes")
    return name


def commit_render(repo: Path, made: Render, name: str, ref: str, source: str, head: str | None) -> str | None:
    """Commits the render onto the branch, its parent the head; returns the new
    commit, or None when the render's tree is the head's."""
    git_dir = git(repo, "rev-parse", "--absolute-git-dir")
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        tree_of = ["--git-dir", git_dir, "--work-tree", str(made.folder)]
        git(made.folder, *tree_of, "add", "--all", "--force", ".", env=env)
        tree = git(made.folder, *tree_of, "write-tree", env=env)
    if head is not None and git(repo, "rev-parse", f"{head}^{{tree}}") == tree:
        point(repo, head)
        return None
    label = made.commit[:9] if made.commit.startswith(ref) else f"{ref} ({made.commit[:9]})"
    message = (
        f"The scaffold at {label}, as {name}\n\n"
        f"{TRAILER_SOURCE}: {source}\n{TRAILER_COMMIT}: {made.commit}\n{TRAILER_NAME}: {name}\n"
    )
    parents = ["-p", head] if head else []
    new = git(repo, "commit-tree", tree, *parents, "-F", "-", stdin=message)
    point(repo, new)
    return new


def point(repo: Path, commit: str) -> None:
    """Moves the local branch to `commit`, from where it was or from nothing,
    so `scaffold` names the newest render even in a clone that had only
    origin's."""
    local = answer(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}")
    if local != commit:
        git(repo, "update-ref", f"refs/heads/{BRANCH}", commit, local or "0" * 40)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="base.py", description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("ref", help="a release tag, a branch, or a commit of the guideline")
    parser.add_argument("--repo", default=".", help="the copy's checkout (default: here)")
    parser.add_argument("--name", help="the copy's name (default: the one its base records, else its arch-check package)")
    parser.add_argument("--source", default=SOURCE, help=f"the guideline on GitHub (default: {SOURCE})")
    parser.add_argument("--tarball", help="read this tarball of the ref rather than fetch it")
    args = parser.parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    try:
        url = tarball_url(args.source, args.ref)
        git(repo, "rev-parse", "--git-dir")
        head = base_of(repo)
        name = name_of(repo, args.name, head)
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch)
            tarball = Path(args.tarball).expanduser() if args.tarball else work / "source.tar.gz"
            if not args.tarball:
                tarball.write_bytes(download(url, args.ref, args.source))
            made = render(tarball, name, work)
            new = commit_render(repo, made, name, args.ref, args.source, head)
    except Refused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    if new is None:
        print(f"{BRANCH} is unchanged: the scaffold at {args.ref} ({made.commit[:9]}) is its head's tree")
        return 0
    parent = f"on {head[:9]}" if head else "its first commit"
    print(f"{BRANCH} is {new[:9]}, {parent}: the scaffold at {args.ref} ({made.commit[:9]}), as {name}")
    if head:
        print(f"next: on a branch cut from the main branch, git merge {BRANCH}")
    else:
        print(f"next: when the copy holds this release already, graft it: git merge -s ours --allow-unrelated-histories {BRANCH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
