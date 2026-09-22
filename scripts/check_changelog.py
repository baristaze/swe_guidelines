#!/usr/bin/env python3
"""Refuse a pull request that edits CHANGELOG.md unless it is a release.

The changelog is written once per release, in the release pull request
(`CONTRIBUTING.md`, Versioning). A change that edits it puts every other
open pull request in conflict, so CI refuses it. A release pull request
is titled `Release X.Y.Z: ...` or comes from a branch named
`release-X-Y-Z` (or `release-X.Y.Z`); either marks it.

Usage: `check_changelog.py --base <ref> --title <title> --branch <name>`,
where `--base` is the ref the pull request merges into
(`origin/main`), and the title and the branch are the pull request's.
The files it changes are `git diff --name-only <base>...HEAD`. Standard
library only.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence

from _common import parser

CHANGELOG = "CHANGELOG.md"
RELEASE_TITLE = re.compile(r"^Release \d+\.\d+\.\d+\b")
RELEASE_BRANCH = re.compile(r"^release-\d+[-.]\d+[-.]\d+$")


def is_release(title: str, branch: str) -> bool:
    """Whether the title or the branch marks a release pull request."""
    return bool(RELEASE_TITLE.match(title.strip()) or RELEASE_BRANCH.match(branch.strip()))


def refusal(changed: Sequence[str], title: str, branch: str) -> str | None:
    """Why a pull request that changes `changed` may not merge, or None when it may."""
    if CHANGELOG not in changed or is_release(title, branch):
        return None
    return (
        f"this pull request edits {CHANGELOG}, and it is not a release: the release pull request writes the "
        "changelog, titled `Release X.Y.Z: ...` or from a branch named `release-X-Y-Z`. "
        "Put what the release section needs in the pull request description instead."
    )


def changed_files(base: str) -> list[str]:
    """The paths the current branch changes against the merge base with `base`."""
    run = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], capture_output=True, text=True, check=True)
    return [line for line in run.stdout.splitlines() if line.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    p = parser(__doc__)
    p.add_argument("--base", required=True, help="the ref the pull request merges into, e.g. origin/main")
    p.add_argument("--title", required=True, help="the pull request's title")
    p.add_argument("--branch", required=True, help="the pull request's head branch")
    args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    message = refusal(changed_files(args.base), args.title, args.branch)
    if message is not None:
        print(f"check_changelog: {message}", file=sys.stderr)
        return 1
    print("changelog ok: this pull request leaves CHANGELOG.md alone, or is a release")
    return 0


if __name__ == "__main__":
    sys.exit(main())
