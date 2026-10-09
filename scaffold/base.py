#!/usr/bin/env python3
"""Commit the scaffold at a ref of its source onto a repository's `scaffold`
branch: under a copy's name, or unchanged for a layer.

    python3 scaffold/base.py v0.54.0
    python3 scaffold/base.py <commit> --repo ~/code/pressroom --name pressroom
    python3 scaffold/base.py v0.54.0 --layer --repo ~/code/<layer>
    python3 scaffold/base.py <ref> --source https://github.com/<owner>/<layer>

A copy of the scaffold keeps its base in git. Its `scaffold` branch holds the
scaffold as the copy took it: each commit there is the scaffold at one commit
of the source, renamed by that commit's own `new.py`, and its parent is the
render before it. The copy's main branch merges the branch, so the render it
merged last is the merge base of the next move, and `git merge scaffold` brings
in what the scaffold changed since, three ways, and keeps what the copy
changed.

A layer is a repository whose own scaffold builds on its source's. It keeps
the source's `scaffold/` folder at `scaffold/`, under the name `acme`, and
changes and adds to it there. With `--layer`, each commit on its `scaffold`
branch is the source's `scaffold/` folder at one commit, unchanged, and the
layer merges it as a copy does. A layer's render records the name `acme`,
which `new.py` refuses for a copy, so the script never moves a copy's base as
a layer's, or a layer's as a copy's.

The render's parent is the last render: the newest of the `scaffold` branch,
origin's, and the last render the checkout merged, so a stale branch never
wins, and a base whose branch was never pushed is still found.

The source is this guideline, or a layer built on it (`--source`). Without
`--source`, a move takes the source its base records, and a `--source` that
names another is refused: a base keeps one source. The ref is
a release tag, a branch, or a commit of the source on GitHub. The script
fetches it as one tarball, never a clone, and reads only `scaffold/` and
`.claude-plugin/` from it. A public source comes from codeload. On a 404 the
script asks GitHub's API with the token `gh auth token` gives, which reads a
private source; the token goes to the API alone, never to where it
redirects. Without such a token, fetch the tarball where you can read it and
pass the file with `--tarball`:

    gh api repos/<owner>/<repo>/tarball/<ref> > source.tar.gz

The commit the script writes names the source commit the tarball holds and
the name the render took. A render whose tree is the branch head's commits
nothing. The script moves only `scaffold`: it never touches the working tree,
the index, or another branch, and it pushes nothing.

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
"""The guideline a repository takes its scaffold from, unless `--source` names
a layer built on it, or a fork."""
BRANCH = "scaffold"
"""The repository's branch that holds its base."""
SCAFFOLD = "scaffold"
"""The source's folder that holds the scaffold, `new.py`, and this script. A
layer takes it whole, at the same path."""
TAKEN = (SCAFFOLD, ".claude-plugin")
"""What the script reads from the tarball: the scaffold and `new.py`, and the
plugin manifest `new.py` reads the release to pin from."""
LAYER_NAME = "acme"
"""The name a layer's render keeps: the scaffold's own, which `new.py` refuses
for a copy, so it marks a layer's base."""
CODELOAD = "https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}"
"""Where anyone fetches a public repository's tarball."""
API = "https://api.github.com/repos/{owner}/{repo}/tarball/{ref}"
"""Where a token fetches a private repository's tarball. GitHub answers with a
redirect to a link of its own, which carries no token of the caller's."""

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


class Missing(Refused):
    """GitHub answered 404: no such ref, or a repository the request cannot read."""


@dataclass(frozen=True)
class Render:
    commit: str
    """The source commit the tarball holds."""
    folder: Path
    """The folder the render's tree is read from."""
    taken: str
    """What of the folder the render commits: all of it for a copy, `scaffold/`
    for a layer."""


def git(repo: Path, *args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, input=stdin, check=False)
    if done.returncode != 0:
        raise Refused(f"git {args[0]} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip()


def located(source: str, ref: str) -> tuple[str, str, str]:
    """The source's owner and repository, and the ref quoted for a URL; refuses
    a source that is not a repository on GitHub, and a ref that is not one."""
    match = SOURCE_URL.match(source)
    if match is None:
        raise Refused(f"{source!r} is not a repository on GitHub, such as {SOURCE}")
    if not REF.match(ref) or ".." in ref or "//" in ref or ref.endswith(("/", ".lock")):
        raise Refused(f"{ref!r} is not a tag, a branch, or a commit")
    owner, repo = match.groups()
    return owner, repo, urllib.parse.quote(ref, safe="/")


def download(request: urllib.request.Request) -> bytes:
    url = request.full_url
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise Missing(f"{url} answered 404") from error
        raise Refused(f"{url} answered {error.code}") from error
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
        raise Refused(f"{url} could not be fetched: {error!r}") from error


def gh_token() -> str | None:
    """The token `gh auth token` prints for github.com, or None when gh is
    missing or holds none."""
    try:
        done = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"], capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = done.stdout.strip()
    return token if done.returncode == 0 and token else None


def fetch(source: str, ref: str) -> bytes:
    """The tarball of `ref`: from codeload, as anyone reads a public source; on
    a 404, from the API with the token `gh auth token` gives, which reads a
    private one. The token rides as a header no redirect carries, so the API
    alone sees it. Every refusal after the first 404 names `--tarball`."""
    owner, repo, quoted = located(source, ref)
    try:
        return download(urllib.request.Request(CODELOAD.format(owner=owner, repo=repo, ref=quoted)))
    except Missing:
        pass
    by_hand = (
        f"fetch it where you can read it, `gh api repos/{owner}/{repo}/tarball/{quoted} > source.tar.gz`, "
        "and pass the file with --tarball"
    )
    token = gh_token()
    if token is None:
        raise Refused(
            f"{ref!r} is not a tag, a branch, or a commit of {source}, "
            f"or {source} is private and `gh auth token` gives no token; {by_hand}"
        )
    api = API.format(owner=owner, repo=repo, ref=quoted)
    request = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json"})
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        return download(request)
    except Missing as error:
        raise Refused(
            f"{ref!r} is not a tag, a branch, or a commit of {source} that the token `gh auth token` gives can read; {by_hand}"
        ) from error
    except Refused as error:
        raise Refused(f"{error}; {by_hand}") from error


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
    if not (root / SCAFFOLD / "new.py").is_file() or not (root / SCAFFOLD / "acme_root").is_dir():
        raise Refused("that commit has no scaffold/acme_root to copy; the guideline added it in v0.39.0")
    return commit


def render(tarball: Path, name: str, work: Path, layer: bool) -> Render:
    """The source's `scaffold/` folder as it is, for a layer; else the copy its
    own `new.py` makes under `name`."""
    source = work / "source"
    source.mkdir()
    commit = unpack(tarball, source)
    if layer:
        return Render(commit, source, SCAFFOLD)
    folder = work / "render" / name
    done = subprocess.run(
        [sys.executable, str(source / SCAFFOLD / "new.py"), str(folder)], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise Refused(f"new.py at {commit[:9]} refused: {done.stderr.strip()}")
    return Render(commit, folder, ".")


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


def repository_of(source: str) -> tuple[str, str] | str:
    """What tells two spellings of one source apart from another source: its
    owner and repository, as GitHub compares them, or the text when it is not
    a repository on GitHub."""
    match = SOURCE_URL.match(source)
    return (match.group(1).lower(), match.group(2).lower()) if match else source


def source_of(given: str | None, recorded: str | None) -> str:
    """The source the render comes from: the one given, else the one the base
    records, else this guideline. A base keeps one source, so a copy of a layer
    never takes this guideline's scaffold over what the layer added."""
    if given and recorded and repository_of(given) != repository_of(recorded):
        raise Refused(f"the base came from {recorded}; a base keeps one source: give that --source, or none")
    return given or recorded or SOURCE


def name_of(repo: Path, given: str | None, recorded: str | None, layer: bool) -> str:
    """The name the render takes: `acme` for a layer; else the one given, the
    one the base records, or the arch-check package. A layer's base and a
    copy's are told apart by the name the base records."""
    if layer:
        if recorded and recorded != LAYER_NAME:
            raise Refused(f"the base is a copy's, rendered as {recorded!r}; move it without --layer")
        return LAYER_NAME
    if recorded == LAYER_NAME:
        raise Refused(f"the base is a layer's, the scaffold unchanged as {LAYER_NAME!r}; move it with --layer")
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
        git(made.folder, *tree_of, "add", "--all", "--force", made.taken, env=env)
        tree = git(made.folder, *tree_of, "write-tree", env=env)
    if head is not None and git(repo, "rev-parse", f"{head}^{{tree}}") == tree:
        point(repo, head)
        return None
    label = made.commit[:9] if made.commit.startswith(ref) else f"{ref} ({made.commit[:9]})"
    message = (
        f"The scaffold at {label}, {taken_as(made, name)}\n\n"
        f"{TRAILER_SOURCE}: {source}\n{TRAILER_COMMIT}: {made.commit}\n{TRAILER_NAME}: {name}\n"
    )
    parents = ["-p", head] if head else []
    new = git(repo, "commit-tree", tree, *parents, "-F", "-", stdin=message)
    point(repo, new)
    return new


def taken_as(made: Render, name: str) -> str:
    return "unchanged" if made.taken == SCAFFOLD else f"as {name}"


def point(repo: Path, commit: str) -> None:
    """Moves the local branch to `commit`, from where it was or from nothing,
    so `scaffold` names the newest render even in a clone that had only
    origin's."""
    local = answer(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}")
    if local != commit:
        git(repo, "update-ref", f"refs/heads/{BRANCH}", commit, local or "0" * 40)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="base.py", description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("ref", help="a release tag, a branch, or a commit of the source")
    parser.add_argument("--repo", default=".", help="the repository's checkout (default: here)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--name", help="the copy's name (default: the one its base records, else its arch-check package)")
    mode.add_argument(
        "--layer",
        action="store_true",
        help="the repository is a layer: take the source's scaffold/ folder unchanged, at scaffold/, rather than render a copy",
    )
    parser.add_argument(
        "--source", help=f"the source on GitHub, public or private (default: the one the base records, else {SOURCE})"
    )
    parser.add_argument("--tarball", help="read this tarball of the ref rather than fetch it")
    args = parser.parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    try:
        git(repo, "rev-parse", "--git-dir")
        head = base_of(repo)
        recorded = trailers(git(repo, "log", "-1", "--format=%B", head)) if head else {}
        source = source_of(args.source, recorded.get(TRAILER_SOURCE))
        located(source, args.ref)
        name = name_of(repo, args.name, recorded.get(TRAILER_NAME), args.layer)
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch)
            tarball = Path(args.tarball).expanduser() if args.tarball else work / "source.tar.gz"
            if not args.tarball:
                tarball.write_bytes(fetch(source, args.ref))
            made = render(tarball, name, work, args.layer)
            new = commit_render(repo, made, name, args.ref, source, head)
    except Refused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    if new is None:
        print(f"{BRANCH} is unchanged: the scaffold at {args.ref} ({made.commit[:9]}) is its head's tree")
        return 0
    parent = f"on {head[:9]}" if head else "its first commit"
    print(f"{BRANCH} is {new[:9]}, {parent}: the scaffold at {args.ref} ({made.commit[:9]}), {taken_as(made, name)}")
    if head:
        print(f"next: on a branch cut from the main branch, git merge {BRANCH}")
    elif args.layer:
        print(
            f"next: on a branch cut from the main branch, git merge --allow-unrelated-histories {BRANCH}; "
            "when the layer holds this scaffold/ already, graft it with -s ours"
        )
    else:
        print(f"next: when the copy holds this release already, graft it: git merge -s ours --allow-unrelated-histories {BRANCH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
