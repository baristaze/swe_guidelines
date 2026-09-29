#!/usr/bin/env python3
"""Check that every checked-in benchmark run sits in its scenario's folder and is named by one row, and what it holds.

`benchmark/runs/` holds a folder per scenario, and each scenario's folder
holds its run folders and its `README.md`, whose table has one row per
run. A run and its resumes are one run: run folders chained by
`source.run_id` (`benchmark/harness/chain.py`), and one row names the
newest of them. `benchmark/runs/README.md` is the index: one line per
scenario, linking its README. Every README is written by hand: the pull
request that adds a run folder adds its row, or moves the row of the run
it resumes onto it. This holds them together:
- every run folder sits in the folder of the scenario it ran. A folder
  directly under `benchmark/runs/` that holds a `run.json`, a
  `results.json`, or a `report.md` is a run folder out of place. The
  scenario is the one the run's `results.json` records, or its
  `run.json` when that records none;
- every scenario's folder has a `README.md`, and the index links each
  scenario's folder once, as `<scenario>/README.md`, and no folder that
  is not there;
- every row names a run folder of its scenario's folder that holds its
  `report.md`, and that folder's chain is whole: each folder's source is
  beside it;
- every run folder is named by exactly one row, as that row's run or as
  a part of its chain. So a folder of a chain with a row of its own
  fails, and so does a folder no row and no chain names;
- no chain forks: no run folder is the source of two. A fork cannot be
  one row, so it is named once, and a run resumes or is judged again
  from its chain's newest folder, which reaches every milestone before
  it;
- every row's Cost (USD) is its chain's total: what the chain's folders
  spent, each its `spend.total_usd`, to the cent. It reads "—" when no
  folder of the chain recorded a spend, and "at least" when one did not
  or a model had no price;
- within a README, the rows run from the newest run at the top to the
  oldest at the bottom, by the `started_at` the first folder of each
  chain records. The folder names carry the local time of the machine
  that ran them, so they do not order runs from two machines;
- every run that records its versions ran on a clean checkout. The
  skills are staged from the working tree, so a run on uncommitted
  changes names a commit that does not hold what ran. A run recorded
  before the harness kept its versions has none to check;
- no run folder is a rehearsal: its `results.json` or its `run.json`
  is marked `rehearsal`. A rehearsal ran with every bound cut small, so
  its scores mean nothing, and it may have run on changes no commit
  holds;
- no repeat of a run folder is marked: its `results.json` names, under
  `read_runs`, a tool call whose input named the benchmark's run
  folders. That subject had an earlier run's answers in its reach;
- every run that records its runtime ran on one its scenario lists. The
  scenario is the file under `benchmark/scenarios/` whose `name` is the
  one the run records, as the file is now: a run on a runtime the
  scenario no longer lists measured something the scenario no longer
  stands behind. A run of a scenario no file there names, or whose file
  does not load, or that two files name, fails too, since nothing says
  where it runs;
- no file in a run folder, plain or compressed, holds a string shaped
  like a key, or an account id or a limit's figures that a provider's
  error names: the strings `run.py redact` replaces by their shape. A
  plain file is scanned as bytes. A zip, a tar, and a gzip, bzip2, or xz stream are
  read the way `run.py redact` reads them, member by member and down the
  levels, since a compressed member hides its text from a scan of the
  bytes. A compressed form the scan cannot read, a part of one it cannot
  unpack, and a `.zip` that does not open fail too, since no one can say
  they hold no key. So does a `.git` folder in a run folder: its objects
  are compressed, and the output's zip is the record of the output.

A row is a body line of a table whose header's first cell is `Run`, and
its run is the folder its first cell's `](<folder>/report.md)` link
names. A stage table, whose first column is the stage, holds no row. A
line of fenced code is neither a row nor a link of the index. A run
that records no start is left out of the order. With no scenario folder
and no index there is nothing to check.

Exit status is non-zero on any mismatch. The scenarios and the chains
are read through the benchmark harness, which imports the standard
library only; a YAML scenario needs pyyaml, which `make runs` brings.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from _common import ROOT, parser, unfenced

# The harness reads a scenario as a run reads it, so the runtimes checked
# here are the ones run.py admits, and a chain as run.py records it.
if str(ROOT / "benchmark") not in sys.path:
    sys.path.insert(0, str(ROOT / "benchmark"))
from harness import chain as CH
from harness import redact as X
from harness import scenario as S

RUNS = ROOT / "benchmark" / "runs"
SCENARIOS = ROOT / "benchmark" / "scenarios"
INDEX = RUNS / "README.md"
README = "README.md"
ROW_LINK = re.compile(r"\]\(([^()/\s]+)/report\.md\)")
SCENARIO_LINK = re.compile(r"\]\(([^()/\s]+)/README\.md\)")
DELIMITER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
# What only a run folder holds: `run.py` writes `run.json` first, then `results.json` and `report.md`.
RUN_FILES = ("run.json", "results.json", "report.md")
COST = "Cost (USD)"


def shown(path: Path) -> str:
    """A path as a message names it: from the repository's root."""
    return path.relative_to(ROOT).as_posix()


def cells(line: str) -> list[str]:
    """The cells of a table line, stripped, without the outer pipes."""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [c.strip() for c in text.split("|")]


@dataclass
class Row:
    """A row of a scenario's table: its line, the run folder it names, and its Cost (USD) cell, None when its table has none."""

    line: int
    run: str
    cost: str | None


def rows(text: str) -> list[Row]:
    """Every row of the tables of runs outside fenced code, in order."""
    lines = unfenced(text).splitlines()
    out: list[Row] = []
    header: list[str] | None = None
    for at, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            header = None
            continue
        if DELIMITER.match(line):
            continue
        if at + 1 < len(lines) and DELIMITER.match(lines[at + 1]):
            header = cells(line)
            continue
        if header is None or header[0] != "Run":
            continue
        row = cells(line)
        found = ROW_LINK.search(row[0])
        if not found:
            continue
        cost = row[header.index(COST)] if COST in header and header.index(COST) < len(row) else None
        out.append(Row(at + 1, found.group(1), cost))
    return out


def record(folder: Path, file: str = "results.json") -> dict:
    """A JSON file of a run folder, `results.json` by default, or an empty record when it has none that reads."""
    return CH.record(folder, file)


def started_at(folder: Path) -> str:
    """When a run started, as its `results.json` records it in UTC; empty when it records nothing."""
    return str(record(folder).get("started_at") or "")


def scenario(folder: Path) -> str:
    """The scenario a run ran, from its `results.json`, else its `run.json`; empty when neither records one."""
    recorded = record(folder).get("scenario")
    if isinstance(recorded, str) and recorded:
        return recorded
    resolved = record(folder, "run.json").get("scenario")
    if isinstance(resolved, dict):
        resolved = resolved.get("name")
    return resolved if isinstance(resolved, str) else ""


def runtime(folder: Path) -> str:
    """The runtime a run ran on, from its `results.json`, else its `run.json`; empty when neither records one."""
    recorded = record(folder).get("runtime")
    if isinstance(recorded, str) and recorded:
        return recorded
    resolved = record(folder, "run.json").get("runtime")
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


def rehearsed(folder: Path) -> bool:
    """Whether a run folder is a rehearsal: its `results.json` or its `run.json` is marked `rehearsal`."""
    return bool(record(folder).get("rehearsal") or record(folder, "run.json").get("rehearsal"))


def read_runs(folder: Path) -> list[str]:
    """Each marked repeat of a run folder, with the first call that marks it: the repeat's `read_runs`, or a phase's."""
    listed = record(folder).get("repeats")
    out = []
    for repeat in listed if isinstance(listed, list) else []:
        if not isinstance(repeat, dict):
            continue
        calls = list(repeat.get("read_runs") or [])
        phases = repeat.get("phases")
        for phase in phases if isinstance(phases, list) else []:
            calls += (phase.get("read_runs") or []) if isinstance(phase, dict) else []
        first = next((c for c in calls if isinstance(c, dict)), None)
        if first is not None:
            out.append(f"repeat {repeat.get('index')}, {first.get('tool')} with {first.get('key')} {first.get('value')!r}")
    return out


def unlisted(folder: Path, scenario_name: str, scenarios: dict[str, list[str] | str]) -> str | None:
    """Why a run's runtime is not one its scenario lists, or None when it is or the run records no runtime or no scenario."""
    ran_on = runtime(folder)
    if not ran_on or not scenario_name:
        return None
    listed = scenarios.get(scenario_name)
    if listed is None:
        return f"no scenario named {scenario_name} in {shown(SCENARIOS)} says where it runs"
    if isinstance(listed, str):
        return listed
    if ran_on in listed:
        return None
    return f"ran on {ran_on}, and {scenario_name} runs on {', '.join(listed)}; a checked-in run ran where its scenario runs"


def unclean(folder: Path) -> str | None:
    """Why a run's checkout was not clean, or None when it was or the run records no versions."""
    versions = record(folder).get("versions")
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


def packed_keys(folder: Path) -> list[str]:
    """Why a run folder's files fail: a key or an id, a part the scan cannot read, a `.zip` that does not open, a `.git`."""
    out = []
    for path in sorted(folder.rglob("*")):
        where = shown(path)
        if path.name == ".git":
            out.append(f"{where}: a run folder holds no .git; its objects are compressed, and the output's zip is the record")
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            data = path.read_bytes()
            kind = X.form(data)
            if path.suffix == ".zip" and kind != "zip":
                out.append(f"{where}: does not open as a zip, so no one can say it holds no key")
                continue
            places = X.keys_in(data)
        except (*X.READ_ERRORS, MemoryError) as exc:
            out.append(f"{where}: could not be read, so no one can say it holds no key ({type(exc).__name__}: {exc})")
            continue
        for place in places:
            if "(not scanned:" in place:
                out.append(f"{where}: {place}; no one can say it holds no key, and `run.py redact` replaces it")
            else:
                at = f"{where}: {place}" if place else where
                found = "a string shaped like a key, or an account id or a limit's figures from a provider's error"
                out.append(f"{at} holds {found}; run `run.py redact`")
    return out


def is_run(folder: Path) -> bool:
    """Whether a folder is a run folder: it holds what only `run.py` writes into one."""
    return any((folder / name).is_file() for name in RUN_FILES)


def check_index(folders: list[Path], errors: list[str]) -> None:
    """The index links each scenario's folder once, as `<scenario>/README.md`, and links no folder that is not there."""
    index = shown(INDEX)
    if not INDEX.is_file():
        if folders:
            errors.append(f"{index}: missing, so no scenario's folder is named: {', '.join(f.name for f in folders)}")
        return
    linked: dict[str, list[int]] = defaultdict(list)
    for ln, line in enumerate(unfenced(INDEX.read_text(encoding="utf-8")).splitlines(), start=1):
        for found in SCENARIO_LINK.finditer(line):
            linked[found.group(1)].append(ln)
    for folder in folders:
        lines = linked.get(folder.name, [])
        if not lines:
            errors.append(f"{index}: no line names the scenario folder {folder.name}; add one that links {folder.name}/README.md")
        elif len(lines) > 1:
            errors.append(
                f"{index}: links {folder.name}/README.md on lines {', '.join(map(str, lines))}; "
                "it names each scenario folder once"
            )
    names = {f.name for f in folders}
    for name, lines in sorted(linked.items()):
        if name not in names:
            errors.append(f"{index}:{lines[0]}: links {name}/README.md, and benchmark/runs holds no such scenario folder")


def check_scenario(folder: Path, errors: list[str]) -> list[Path]:
    """Hold one scenario's folder: its README, its rows, the chain each names, and each row's cost; return its run folders."""
    runs = sorted(p for p in folder.iterdir() if p.is_dir())
    readme = folder / README
    where = shown(readme)
    for run in runs:
        ran = scenario(run)
        if ran and ran != folder.name:
            errors.append(f"{shown(run)}: is a run of {ran}, and sits in {folder.name}; it goes in benchmark/runs/{ran}/")
    if not readme.is_file():
        held = f", so no run folder of it has a row: {', '.join(p.name for p in runs)}" if runs else ""
        errors.append(f"{where}: missing{held}; a scenario's folder has a README that says what it measures")
        return runs
    named: dict[str, list[tuple[int, str]]] = defaultdict(list)
    above: tuple[str, str] | None = None
    for row in rows(readme.read_text(encoding="utf-8")):
        path = folder / row.run
        if not (path / "report.md").is_file():
            errors.append(f"{where}:{row.line}: links {row.run}/report.md, and {folder.name} holds no such run")
            continue
        chain, broken = CH.lineage(path)
        if broken:
            errors.append(f"{where}:{row.line}: the chain of {row.run} breaks: {broken}")
        for part in chain:
            named[part.name].append((row.line, "its run" if part == path else f"a part of the chain of {row.run}"))
        found = CH.chain([(f.name, record(f)) for f in chain], broken)
        expected = CH.cost_text(found)
        if row.cost is None:
            errors.append(f"{where}:{row.line}: the table of {row.run} has no {COST} column; a row names its run's cost")
        elif row.cost.replace("**", "").strip() != expected:
            parts = ", ".join(f"{f['run_id']} {'—' if f['total_usd'] is None else f['total_usd']}" for f in found["folders"])
            errors.append(
                f"{where}:{row.line}: {row.run} costs {row.cost}, and its chain spent {expected} ({parts}); "
                "a row's cost is its chain's total"
            )
        started = started_at(chain[0])
        if started and above is not None and started > above[1]:
            errors.append(
                f"{where}:{row.line}: {row.run} started {started}, after {above[0]} above it; the newest run comes first"
            )
        if started:
            above = (row.run, started)
    # A folder two folders ran from forks its chain, and no set of rows holds a fork. The fork is named once, and the
    # rows it leaves over-named (it and the folders before it) or unnamed (the folders after it) are not named again.
    forks = {run.name: after for run in runs if len(after := CH.continued_by(run)) > 1}
    before = {part.name for name in forks for part in CH.lineage(folder / name)[0]}
    after_fork = {run.name for run in runs if any(part.name in forks for part in CH.lineage(run)[0][:-1])}
    for name, paths in sorted(forks.items()):
        errors.append(f"{shown(folder / name)}: {CH.one_line(folder / name, paths)}")
    for run in runs:
        roles = named.get(run.name, [])
        if not roles:
            if run.name not in after_fork:
                errors.append(f"{where}: no row names the run folder {run.name}, as its run or as a part of its chain")
        elif len(roles) > 1 and run.name not in before:
            said = "; ".join(f"line {ln}, as {role}" for ln, role in roles)
            errors.append(f"{where}: {run.name} is named by {len(roles)} rows ({said}); a run and its resumes are one row")
    return runs


def check(errors: list[str]) -> int:
    """Add every mismatch to `errors` and return the number of run folders."""
    entries = sorted(p for p in RUNS.iterdir() if p.is_dir()) if RUNS.is_dir() else []
    folders: list[Path] = []
    for entry in entries:
        if is_run(entry):
            ran = scenario(entry) or "<scenario>"
            errors.append(f"{shown(entry)}: a run folder sits in its scenario's folder; move it to benchmark/runs/{ran}/")
        else:
            folders.append(entry)
    check_index(folders, errors)
    runs = [run for folder in folders for run in check_scenario(folder, errors)]
    scenarios = declared() if runs else {}
    for run in runs:
        if rehearsed(run):
            errors.append(f"{shown(run)}: is a rehearsal, whose scores mean nothing; a rehearsal is never checked in")
        for marked in read_runs(run):
            errors.append(
                f"{shown(run)}: {marked} named the benchmark's run folders, so its subject had an "
                "earlier run's answers in reach; a marked run is never checked in"
            )
        reason = unclean(run)
        if reason:
            errors.append(f"{shown(run)}: {reason}; a checked-in run names a commit that holds what ran")
        reason = unlisted(run, scenario(run), scenarios)
        if reason:
            errors.append(f"{shown(run)}: {reason}")
        errors.extend(packed_keys(run))
    return len(runs)


def main(argv: Sequence[str] = ()) -> int:
    parser(__doc__).parse_args(list(argv))
    errors: list[str] = []
    count = check(errors)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} run index mismatch(es)")
        return 1
    print(
        f"runs ok: {count} run folder(s), each in its scenario's folder and named by one row, each row's cost its chain's "
        "total, on a runtime its scenario lists, no rehearsal, no marked repeat, no key or account id in any file"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
