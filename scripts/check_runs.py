#!/usr/bin/env python3
"""Check that the index of the benchmark runs names every run folder once, in its scenario's section.

`benchmark/runs/README.md` is written by hand: the pull request that adds
a run folder adds its row. The index has one section per scenario, a
`## <scenario>` heading over that scenario's table. This holds the index
and the run folders together:
- every run folder, a folder directly under `benchmark/runs/`, has
  exactly one row in the index;
- every row links to a run folder that is there, with its `report.md`;
- every row sits in the section of its run's scenario. The scenario is
  the one the run's `results.json` records, or its `run.json` when that
  records none;
- every scenario a run folder ran has a section, and no section heading
  is there twice;
- within a section, the rows run from the newest run at the top to the
  oldest at the bottom, by the `started_at` each run's `results.json`
  records. The folder names carry the local time of the machine that
  ran them, so they do not order runs from two machines. A row of
  another scenario's run is named as out of place, and it is left out
  of the order of the section it sits in;
- every run that records its versions ran on a clean checkout. The
  skills are staged from the working tree, so a run on uncommitted
  changes names a commit that does not hold what ran. A run recorded
  before the harness kept its versions has none to check;
- every run that records its runtime ran on one its scenario lists. The
  scenario is the file of that name under `benchmark/scenarios/`, as it
  is now: a run on a runtime the scenario no longer lists measured
  something the scenario no longer stands behind. A run of a scenario
  with no file there, or with one that does not load, fails too, since
  nothing says where it runs.

A row is a table line of the index, and its run is the folder its
`](<folder>/report.md)` link names. Its section is the nearest `## `
heading above it, and a closing sequence of `#` is not part of the
heading's name. A line of fenced code is neither a row nor a heading. A
run that records no scenario is held to one row but to no section, and
a run that records no start is left out of the order. With no run
folder and no index there is nothing to check.

Exit status is non-zero on any mismatch. The scenarios are read through
the benchmark harness, which imports the standard library only; a YAML
scenario needs pyyaml, which `make runs` brings.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence

from _common import CLOSING, HEADING, ROOT, parser, unfenced

# The harness reads a scenario as a run reads it, so the runtimes checked
# here are the ones run.py admits.
if str(ROOT / "benchmark") not in sys.path:
    sys.path.insert(0, str(ROOT / "benchmark"))
from harness import scenario as S

RUNS = ROOT / "benchmark" / "runs"
SCENARIOS = ROOT / "benchmark" / "scenarios"
INDEX = RUNS / "README.md"
ROW_LINK = re.compile(r"\]\(([^()/\s]+)/report\.md\)")


def section_name(line: str) -> str | None:
    """The name a `## ` heading line gives its section, less a closing `#` sequence; None for any other line."""
    m = HEADING.match(line)
    if not m or len(m.group(1)) != 2:
        return None
    return CLOSING.sub("", m.group(2)) or None


def sections(text: str) -> list[tuple[int, str]]:
    """The line number and the name of every section heading outside fenced code, in order."""
    lines = enumerate(unfenced(text).splitlines(), start=1)
    return [(ln, name) for ln, line in lines if (name := section_name(line))]


def rows(text: str) -> list[tuple[int, str, str]]:
    """The line number, the run folder, and the section of every row outside fenced code, in order.

    The section is empty above the first heading.
    """
    out = []
    section = ""
    for ln, line in enumerate(unfenced(text).splitlines(), start=1):
        name = section_name(line)
        if name:
            section = name
        elif line.lstrip().startswith("|"):
            out += [(ln, m.group(1), section) for m in ROW_LINK.finditer(line)]
    return out


def record(name: str, file: str = "results.json") -> dict:
    """A JSON file of a run folder, `results.json` by default, or an empty record when it has none that reads."""
    path = RUNS / name / file
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def started_at(name: str) -> str:
    """When a run started, as its `results.json` records it in UTC; empty when it records nothing."""
    return str(record(name).get("started_at") or "")


def scenario(name: str) -> str:
    """The scenario a run ran, from its `results.json`, else its `run.json`; empty when neither records one."""
    recorded = record(name).get("scenario")
    if isinstance(recorded, str) and recorded:
        return recorded
    resolved = record(name, "run.json").get("scenario")
    if isinstance(resolved, dict):
        resolved = resolved.get("name")
    return resolved if isinstance(resolved, str) else ""


def runtime(name: str) -> str:
    """The runtime a run ran on, from its `results.json`, else its `run.json`; empty when neither records one."""
    recorded = record(name).get("runtime")
    if isinstance(recorded, str) and recorded:
        return recorded
    resolved = record(name, "run.json").get("runtime")
    if isinstance(resolved, dict):
        resolved = resolved.get("name")
    return resolved if isinstance(resolved, str) else ""


def runtimes(scenario_name: str, cache: dict[str, list[str] | str]) -> list[str] | str:
    """The runtimes a scenario lists, or why none can be read: its file under `SCENARIOS` is missing or does not load."""
    if scenario_name not in cache:
        files = {path.stem: path for path in S.catalog(SCENARIOS)}
        if scenario_name not in files:
            cache[scenario_name] = f"no scenario {scenario_name} in {SCENARIOS.relative_to(ROOT)} says where it runs"
        else:
            try:
                cache[scenario_name] = S.load(files[scenario_name]).runtimes
            except S.ScenarioError as exc:
                cache[scenario_name] = f"its scenario does not load, so nothing says where it runs: {exc}"
    return cache[scenario_name]


def unlisted(name: str, scenario_name: str, cache: dict[str, list[str] | str]) -> str | None:
    """Why a run's runtime is not one its scenario lists, or None when it is or the run records no runtime or no scenario."""
    ran_on = runtime(name)
    if not ran_on or not scenario_name:
        return None
    listed = runtimes(scenario_name, cache)
    if isinstance(listed, str):
        return listed
    if ran_on in listed:
        return None
    return f"ran on {ran_on}, and {scenario_name} runs on {', '.join(listed)}; a checked-in run ran where its scenario runs"


def unclean(name: str) -> str | None:
    """Why a run's checkout was not clean, or None when it was or the run records no versions."""
    versions = record(name).get("versions")
    if not isinstance(versions, dict) or not versions:
        return None
    checkout = versions.get("checkout")
    if not isinstance(checkout, dict):
        return "records versions but no checkout"
    if checkout.get("dirty") is False:
        return None
    if checkout.get("dirty") is None:
        return "ran on no git checkout, so no commit holds what ran"
    paths = ", ".join(checkout.get("dirty_paths") or [])
    return f"ran on changes no commit holds ({paths})"


def check(errors: list[str]) -> int:
    """Add every mismatch to `errors` and return the number of run folders."""
    folders = sorted(p.name for p in RUNS.iterdir() if p.is_dir()) if RUNS.is_dir() else []
    index = INDEX.relative_to(ROOT)
    if not INDEX.is_file():
        if folders:
            errors.append(f"{index}: missing, so no run folder has a row: {', '.join(folders)}")
        return len(folders)
    text = INDEX.read_text(encoding="utf-8")
    found = rows(text)
    counts = Counter(name for _, name, _ in found)
    ran = {name: scenario(name) for name in folders}
    headed: set[str] = set()
    for ln, heading in sections(text):
        if heading in headed:
            errors.append(f"{index}:{ln}: a second section for {heading}; a scenario has one")
        headed.add(heading)
    unheaded: dict[str, list[str]] = defaultdict(list)
    declared: dict[str, list[str] | str] = {}
    for name in folders:
        if counts[name] == 0:
            errors.append(f"{index}: no row for the run folder {name}")
        reason = unclean(name)
        if reason:
            errors.append(f"{RUNS.relative_to(ROOT)}/{name}: {reason}; a checked-in run names a commit that holds what ran")
        reason = unlisted(name, ran[name], declared)
        if reason:
            errors.append(f"{RUNS.relative_to(ROOT)}/{name}: {reason}")
        if ran[name] and ran[name] not in headed:
            unheaded[ran[name]].append(name)
    for missing in sorted(unheaded):
        errors.append(
            f"{index}: no section for the scenario {missing}, which {', '.join(unheaded[missing])} ran; "
            f"add `## {missing}` with one line on what it measures, and put its rows there"
        )
    reported: set[str] = set()
    above: dict[str, tuple[str, str]] = {}
    for ln, name, section in found:
        if not (RUNS / name / "report.md").is_file():
            errors.append(f"{index}:{ln}: links {name}/report.md, and there is no such run")
        elif counts[name] > 1 and name not in reported:
            reported.add(name)
            errors.append(f"{index}:{ln}: {name} has {counts[name]} rows; a run has one")
        scenario_of = ran.get(name, "")
        if scenario_of and scenario_of != section:
            if scenario_of in headed:
                where = f"under `## {section}`" if section else "above every section"
                errors.append(
                    f"{index}:{ln}: {name} is a run of {scenario_of}, and its row sits {where}; it goes under `## {scenario_of}`"
                )
            continue
        started = started_at(name)
        if started and section in above and started > above[section][1]:
            errors.append(
                f"{index}:{ln}: {name} started {started}, after {above[section][0]} above it; "
                "the newest run of a section comes first"
            )
        if started:
            above[section] = (name, started)
    return len(folders)


def main(argv: Sequence[str] = ()) -> int:
    parser(__doc__).parse_args(list(argv))
    errors: list[str] = []
    count = check(errors)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} run index mismatch(es)")
        return 1
    print(f"runs ok: {count} run folder(s), one row each, in its scenario's section, on a runtime it lists")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
