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
  scenario is the file under `benchmark/scenarios/` whose `name` is the
  one the run records, as the file is now: a run on a runtime the
  scenario no longer lists measured something the scenario no longer
  stands behind. A run of a scenario no file there names, or whose file
  does not load, or that two files name, fails too, since nothing says
  where it runs;
- no compressed file in a run folder holds a string shaped like a key:
  a zip, a tar, and a gzip, bzip2, or xz stream are read the way
  `run.py redact` reads them, member by member and down the levels, since
  a compressed member hides its text from a scan of the bytes. A
  compressed form the scan cannot read, a part of one it cannot unpack,
  and a `.zip` that does not open fail too, since no one can say they
  hold no key. So does a `.git` folder in a run folder: its objects are
  compressed, and the output's zip is the record of the output.

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
from harness import redact as X
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


def declared() -> dict[str, list[str] | str]:
    """Every scenario's runtimes by the name a run records, or why they cannot be read.

    A run records the scenario's `name`, which need not be its file's
    stem, so each file under `SCENARIOS` is loaded and keyed by its name.
    A file that does not load has no name to read, so it is keyed by its
    stem, the name it takes when it sets none. Two files of one name, or
    a file of that name and one that does not load with that stem, leave
    no way to tell which one a run ran.
    """
    loaded: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    out: dict[str, list[str] | str] = {}
    broken: dict[str, str] = {}
    for path in S.catalog(SCENARIOS):
        try:
            scn = S.load(path)
        except S.ScenarioError as exc:
            out[path.stem] = f"its scenario does not load, so nothing says where it runs: {exc}"
            broken[path.stem] = path.name
        else:
            loaded[scn.name].append((path.name, scn.runtimes))
    for name, found in loaded.items():
        files = ", ".join(file for file, _ in found)
        if name in broken:
            out[name] = (
                f"{name} is the name of {files} and the stem of {broken[name]}, which does not load, so none says where it runs"
            )
        elif len(found) > 1:
            out[name] = f"the scenario files {files} are all named {name}, so none says where it runs"
        else:
            out[name] = found[0][1]
    return out


def unlisted(name: str, scenario_name: str, scenarios: dict[str, list[str] | str]) -> str | None:
    """Why a run's runtime is not one its scenario lists, or None when it is or the run records no runtime or no scenario."""
    ran_on = runtime(name)
    if not ran_on or not scenario_name:
        return None
    listed = scenarios.get(scenario_name)
    if listed is None:
        return f"no scenario named {scenario_name} in {SCENARIOS.relative_to(ROOT)} says where it runs"
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


def packed_keys(name: str) -> list[str]:
    """Why a run folder's compressed files fail: a key, a part the scan cannot read, a `.zip` that does not open, a `.git`."""
    out = []
    for path in sorted((RUNS / name).rglob("*")):
        shown = path.relative_to(ROOT)
        if path.name == ".git":
            out.append(f"{shown}: a run folder holds no .git; its objects are compressed, and the output's zip is the record")
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            data = path.read_bytes()
            kind = X.form(data)
            if path.suffix == ".zip" and kind != "zip":
                out.append(f"{shown}: does not open as a zip, so no one can say it holds no key")
                continue
            places = X.keys_in(data) if kind else []
        except (*X.READ_ERRORS, MemoryError) as exc:
            out.append(f"{shown}: could not be read, so no one can say it holds no key ({type(exc).__name__}: {exc})")
            continue
        for place in places:
            if "(not scanned:" in place:
                out.append(f"{shown}: {place}; no one can say it holds no key, and `run.py redact` replaces it")
            else:
                where = f"{shown}: {place}" if place else str(shown)
                out.append(f"{where} holds a string shaped like a key; run `run.py redact`")
    return out


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
    scenarios = declared() if folders else {}
    for name in folders:
        if counts[name] == 0:
            errors.append(f"{index}: no row for the run folder {name}")
        reason = unclean(name)
        if reason:
            errors.append(f"{RUNS.relative_to(ROOT)}/{name}: {reason}; a checked-in run names a commit that holds what ran")
        reason = unlisted(name, ran[name], scenarios)
        if reason:
            errors.append(f"{RUNS.relative_to(ROOT)}/{name}: {reason}")
        errors.extend(packed_keys(name))
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
    print(
        f"runs ok: {count} run folder(s), one row each, in its scenario's section, on a runtime it lists, "
        "no key in a compressed file"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
