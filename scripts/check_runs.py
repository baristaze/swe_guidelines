#!/usr/bin/env python3
"""Check that the index of the benchmark runs names every run folder once.

`benchmark/runs/README.md` is written by hand: the pull request that adds
a run folder adds its row. This holds the two together:
- every run folder, a folder directly under `benchmark/runs/`, has
  exactly one row in the index;
- every row links to a run folder that is there, with its `report.md`.

A row is a table line of the index, and its run is the folder its
`](<folder>/report.md)` link names. With no run folder and no index there
is nothing to check.

Exit status is non-zero on any mismatch. Standard library only.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Sequence

from _common import ROOT, parser

RUNS = ROOT / "benchmark" / "runs"
INDEX = RUNS / "README.md"
ROW_LINK = re.compile(r"\]\(([^()/\s]+)/report\.md\)")


def rows(text: str) -> list[tuple[int, str]]:
    """The line number and the run folder of every row, in order."""
    out = []
    for ln, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("|"):
            out += [(ln, m.group(1)) for m in ROW_LINK.finditer(line)]
    return out


def check(errors: list[str]) -> int:
    """Add every mismatch to `errors` and return the number of run folders."""
    folders = sorted(p.name for p in RUNS.iterdir() if p.is_dir()) if RUNS.is_dir() else []
    index = INDEX.relative_to(ROOT)
    if not INDEX.is_file():
        if folders:
            errors.append(f"{index}: missing, so no run folder has a row: {', '.join(folders)}")
        return len(folders)
    found = rows(INDEX.read_text(encoding="utf-8"))
    counts = Counter(name for _, name in found)
    for name in folders:
        if counts[name] == 0:
            errors.append(f"{index}: no row for the run folder {name}")
    reported: set[str] = set()
    for ln, name in found:
        if not (RUNS / name / "report.md").is_file():
            errors.append(f"{index}:{ln}: links {name}/report.md, and there is no such run")
        elif counts[name] > 1 and name not in reported:
            reported.add(name)
            errors.append(f"{index}:{ln}: {name} has {counts[name]} rows; a run has one")
    return len(folders)


def main(argv: Sequence[str] = ()) -> int:
    parser(__doc__).parse_args(list(argv))
    errors: list[str] = []
    count = check(errors)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} run index mismatch(es)")
        return 1
    print(f"runs ok: {count} run folder(s), one row each")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
