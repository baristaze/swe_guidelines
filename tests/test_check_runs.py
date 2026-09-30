"""scripts/check_runs.py: each run folder sits in its scenario's folder, and one row names it, as its run or in its chain."""

import gzip
import io
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from test_benchmark_redact import ANTHROPIC, ANTHROPIC_429, ANTHROPIC_429_WITHOUT_ID, XAI_429

HEAD = "| Run | Started (UTC) | Cost (USD) |\n|---|---|---|\n"
ONE_A = "20260101-000000-alpha-aa"
TWO_A = "20260102-000000-alpha-bb"
ONE_B = "20260101-000000-beta-cc"


def row(name, cost="—"):
    return f"| [{name}]({name}/report.md) | one | {cost} |\n"


def page(repo, scenario, *rows, head=HEAD, above=""):
    """A scenario's README: a paragraph, then its table of runs with these rows."""
    text = f"# {scenario}\n\nIt measures {scenario}.\n{above}\n" + head + "".join(rows)
    repo.write(f"benchmark/runs/{scenario}/README.md", text)


def index(repo, *scenarios):
    lines = "".join(f"- [{s}]({s}/README.md): what {s} measures.\n" for s in scenarios)
    repo.write("benchmark/runs/README.md", "# Runs\n\n" + lines)


def a_run(repo, name, started="2026-01-01T00:00:00Z", scenario="alpha", runtime=None, folder=None, **recorded):
    """A run folder in its scenario's folder, or in `folder`: its report, and its results.json with what it records."""
    where = f"benchmark/runs/{folder or scenario}/{name}"
    repo.write(f"{where}/report.md", "# Benchmark run\n")
    results = {"started_at": started, "scenario": scenario} | ({"runtime": runtime} if runtime else {}) | recorded
    repo.write(f"{where}/results.json", json.dumps(results) + "\n")


def a_scenario(repo, name, runtimes, file=None):
    scenario = {"name": name, "kind": "qa", "subject": {"prompt": "Why?"}, "rubric": "r", "runtimes": runtimes}
    repo.write(f"benchmark/scenarios/{file or name}.json", json.dumps(scenario) + "\n")


@pytest.fixture
def runs(repo):
    return repo.script("check_runs")


def test_no_runs_and_no_index_pass(runs, capsys):
    assert runs.main() == 0
    assert "runs ok: 0 run folder(s)" in capsys.readouterr().out


def test_every_run_in_its_scenario_s_folder_with_one_row_passes(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z")
    a_run(repo, TWO_A, "2026-01-02T08:00:00Z")
    a_run(repo, ONE_B, "2026-01-01T08:00:00Z", "beta")
    page(repo, "alpha", row(TWO_A), row(ONE_A))
    page(repo, "beta", row(ONE_B))
    index(repo, "alpha", "beta")
    assert runs.main() == 0
    out = capsys.readouterr().out
    assert (
        "runs ok: 3 run folder(s), each in its scenario's folder and named by one row, each row's cost its chain's total" in out
    )


def test_a_run_without_a_row_fails(repo, runs, capsys):
    a_run(repo, ONE_A)
    a_run(repo, TWO_A)
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/alpha/README.md: no row names the run folder {TWO_A}, as its run or as a part of its chain" in out


def test_a_row_without_its_run_fails(repo, runs, capsys):
    a_run(repo, ONE_A)
    page(repo, "alpha", row(ONE_A), row("20260103-000000-alpha-gone"))
    index(repo, "alpha")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert "alpha/README.md:8: links 20260103-000000-alpha-gone/report.md, and alpha holds no such run" in out


def test_a_run_with_two_rows_fails(repo, runs, capsys):
    a_run(repo, ONE_A)
    page(repo, "alpha", row(ONE_A), row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"{ONE_A} is named by 2 rows (line 7, as its run; line 8, as its run); a run and its resumes are one row" in out


def test_a_run_folder_directly_under_the_runs_folder_fails(repo, runs, capsys):
    repo.write(f"benchmark/runs/{ONE_A}/report.md", "# Benchmark run\n")
    repo.write(f"benchmark/runs/{ONE_A}/results.json", json.dumps({"scenario": "alpha"}) + "\n")
    repo.write("benchmark/runs/README.md", "# Runs\n")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/{ONE_A}: a run folder sits in its scenario's folder; move it to benchmark/runs/alpha/" in out
    assert "1 run index mismatch(es)" in out


def test_a_run_in_another_scenario_s_folder_fails(repo, runs, capsys):
    a_run(repo, ONE_B, scenario="beta", folder="alpha")
    page(repo, "alpha", row(ONE_B))
    index(repo, "alpha")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/alpha/{ONE_B}: is a run of beta, and sits in alpha; it goes in benchmark/runs/beta/" in out
    assert "1 run index mismatch(es)" in out


def test_a_run_without_results_is_held_to_the_scenario_its_run_json_names(repo, runs, capsys):
    repo.write(f"benchmark/runs/alpha/{ONE_A}/report.md", "# Benchmark run\n")
    repo.write(f"benchmark/runs/alpha/{ONE_A}/run.json", json.dumps({"scenario": {"name": "beta"}}) + "\n")
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 1
    assert f"alpha/{ONE_A}: is a run of beta, and sits in alpha" in capsys.readouterr().out


def test_a_scenario_folder_without_a_readme_fails(repo, runs, capsys):
    a_run(repo, ONE_A)
    index(repo, "alpha")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/alpha/README.md: missing, so no run folder of it has a row: {ONE_A}" in out


def test_scenario_folders_without_an_index_fail(repo, runs, capsys):
    a_run(repo, ONE_A)
    page(repo, "alpha", row(ONE_A))
    assert runs.main() == 1
    assert "benchmark/runs/README.md: missing, so no scenario's folder is named: alpha" in capsys.readouterr().out


def test_the_index_names_each_scenario_folder_once_and_no_other(repo, runs, capsys):
    a_run(repo, ONE_A)
    a_run(repo, ONE_B, scenario="beta")
    page(repo, "alpha", row(ONE_A))
    page(repo, "beta", row(ONE_B))
    index(repo, "alpha", "alpha", "gamma")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert "benchmark/runs/README.md: links alpha/README.md on lines 3, 4; it names each scenario folder once" in out
    assert "benchmark/runs/README.md: no line names the scenario folder beta; add one that links beta/README.md" in out
    assert "benchmark/runs/README.md:5: links gamma/README.md, and benchmark/runs holds no such scenario folder" in out
    assert "3 run index mismatch(es)" in out


def test_a_link_outside_a_table_is_not_a_row(repo, runs):
    a_run(repo, ONE_A)
    page(repo, "alpha", row(ONE_A), above=f"\nSee [the first run]({ONE_A}/report.md).\n")
    index(repo, "alpha")
    assert runs.main() == 0


def test_a_stage_table_holds_no_row(repo, runs, capsys):
    a_run(repo, ONE_A)
    a_run(repo, TWO_A, "2026-01-02T00:00:00Z")
    stages = f"\n### alpha-bb\n\n| Stage | Folder | Cost (USD) |\n|---|---|---|\n| mvp | [{ONE_A}]({ONE_A}/report.md) | $1.00 |\n"
    page(repo, "alpha", row(TWO_A), row(ONE_A) + stages)
    index(repo, "alpha")
    assert runs.main() == 0
    page(repo, "alpha", row(TWO_A) + stages)
    assert runs.main() == 1
    assert f"no row names the run folder {ONE_A}" in capsys.readouterr().out


def fenced(text):
    return "\n```markdown\n" + text + "```\n"


def test_a_row_and_an_index_link_in_fenced_code_are_neither(repo, runs, capsys):
    a_run(repo, ONE_A)
    page(repo, "alpha", row(ONE_A), above=fenced(HEAD + row(ONE_A)))
    repo.write("benchmark/runs/README.md", "# Runs\n" + fenced("- [alpha](alpha/README.md)\n") + "- [alpha](alpha/README.md)\n")
    assert runs.main() == 0
    repo.write("benchmark/runs/README.md", "# Runs\n" + fenced("- [alpha](alpha/README.md)\n"))
    assert runs.main() == 1
    assert "no line names the scenario folder alpha" in capsys.readouterr().out


def test_an_older_run_above_a_newer_one_fails(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z")
    a_run(repo, TWO_A, "2026-01-02T08:00:00Z")
    page(repo, "alpha", row(ONE_A), row(TWO_A))
    index(repo, "alpha")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"alpha/README.md:8: {TWO_A} started 2026-01-02T08:00:00Z, after {ONE_A} above it; the newest run comes first" in out


def test_runs_that_started_together_keep_either_order(repo, runs):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z")
    a_run(repo, TWO_A, "2026-01-01T08:00:00Z")
    page(repo, "alpha", row(ONE_A), row(TWO_A))
    index(repo, "alpha")
    assert runs.main() == 0


def test_a_run_that_records_less_is_named_and_left_out_of_the_order(repo, runs):
    a_run(repo, TWO_A, "2026-01-02T08:00:00Z")
    repo.write("benchmark/runs/alpha/20260101-000000-alpha-old/report.md", "# Benchmark run\n")
    repo.write("benchmark/runs/alpha/20260103-000000-alpha-bare/report.md", "# Benchmark run\n")
    repo.write("benchmark/runs/alpha/20260103-000000-alpha-bare/results.json", "{}\n")
    page(repo, "alpha", row("20260101-000000-alpha-old"), row(TWO_A), row("20260103-000000-alpha-bare"))
    index(repo, "alpha")
    assert runs.main() == 0


# A run and its resumes ----------------------------------------------------------

RUN = "20260101-000000-alpha-run"
RESUMED = "20260102-000000-alpha-resumed"
JUDGED = "20260103-000000-alpha-judged"


def spend(total, unpriced=()):
    return {"judges": {}, "subject": {}, "total_usd": total, "unpriced": list(unpriced)}


def versions(commit, claude=None):
    checkout = {"commit": commit * 40, "plugin_version": "1.0.0", "dirty": False, "dirty_paths": [], "dirty_sha256": None}
    return {"checkout": checkout} | ({"claude_code": f"{claude} (Claude Code)"} if claude else {})


def chained(repo, first_spend=None):
    """A run, its resume after a phase, and a resume of its judges, chained by `source`, as the harness records them.

    The first two ran the subject on Claude Code 2.1.283, from two commits; the last ran only judges, from a third.
    """
    a_run(repo, RUN, "2026-01-01T08:00:00Z", spend=first_spend or spend(82.5281), versions=versions("a", "2.1.283"))
    a_run(
        repo,
        RESUMED,
        "2026-01-02T08:00:00Z",
        spend=spend(87.7648),
        source={"run_id": RUN, "path": f"benchmark/runs/{RUN}", "after": "scaffold", "repeats": [0], "refused": [], "capped": []},
        versions=versions("b", "2.1.283"),
    )
    judges = [{"repeat": 0, "run": ["openai"], "carried": ["anthropic"]}]
    a_run(
        repo,
        JUDGED,
        "2026-01-03T08:00:00Z",
        spend=spend(30.1202),
        source={"run_id": RESUMED, "path": "x", "repeats": [0], "refused": [], "capped": [], "judges": judges},
        versions=versions("c"),
    )
    index(repo, "alpha")


CHAIN_HEAD = "| Run | Started (UTC) | Cost (USD) | Commit | Claude Code |\n|---|---|---|---|---|\n"


def test_a_run_and_its_resumes_are_one_row_whose_cost_is_the_chain_s_total(repo, runs, capsys):
    chained(repo)
    # The row's Commit names each commit of the chain's folders, oldest first, and its Claude Code the one that ran
    # the subject: the resume of the judges ran none.
    chain_row = f"| [{JUDGED}]({JUDGED}/report.md) | 2026-01-01 08:00 | $200.41 | `aaaaaaa`, `bbbbbbb`, `ccccccc` | 2.1.283 |\n"
    page(repo, "alpha", chain_row, head=CHAIN_HEAD)
    assert runs.main() == 0
    assert "runs ok: 3 run folder(s)" in capsys.readouterr().out


def test_a_row_whose_cost_is_not_its_chain_s_total_fails(repo, runs, capsys):
    chained(repo)
    page(repo, "alpha", row(JUDGED, "$30.12"))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"alpha/README.md:7: {JUDGED} costs $30.12, and its chain spent $200.41 ({RUN} 82.5281, " in out
    assert "a row's cost is its chain's total" in out and "1 run index mismatch(es)" in out


def test_a_folder_of_a_chain_with_a_row_of_its_own_fails(repo, runs, capsys):
    chained(repo)
    page(repo, "alpha", row(JUDGED, "$200.41"), row(RUN, "$82.53"))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"{RUN} is named by 2 rows (line 7, as a part of the chain of {JUDGED}; line 8, as its run)" in out
    assert "1 run index mismatch(es)" in out


def test_a_folder_no_row_and_no_chain_names_fails(repo, runs, capsys):
    chained(repo)
    a_run(repo, ONE_A, "2025-12-01T08:00:00Z")
    page(repo, "alpha", row(RESUMED, "$170.29"))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"no row names the run folder {JUDGED}, as its run or as a part of its chain" in out
    assert f"no row names the run folder {ONE_A}" in out and "2 run index mismatch(es)" in out


AGAIN = "20260104-000000-alpha-again"


@pytest.mark.parametrize(
    "named",
    [
        [(AGAIN, "$84.53")],  # the second resume alone: the first one is left over
        [(AGAIN, "$84.53"), (RESUMED, "$170.29")],  # both: the run is named twice
        [(RESUMED, "$170.29")],  # the first resume alone: the second one is left over
    ],
)
def test_a_chain_that_forks_fails_with_one_line_to_resume_from(repo, runs, capsys, named):
    chained(repo)
    source = {"run_id": RUN, "path": "x", "after": "scaffold", "repeats": [0], "refused": [], "capped": []}
    a_run(repo, AGAIN, "2026-01-04T08:00:00Z", spend=spend(2.0), source=source)  # a second resume of the run
    shutil.rmtree(repo.root / "benchmark" / "runs" / "alpha" / JUDGED)
    page(repo, "alpha", *(row(name, cost) for name, cost in named))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert (
        f"benchmark/runs/alpha/{RUN}: {RUN} is already the source of {RESUMED} and {AGAIN}, beside it. A chain has one "
        "line, so a run resumes or is judged again from the chain's newest folder, which reaches every milestone before it"
    ) in out
    assert "is named by" not in out and "no row names" not in out and "1 run index mismatch(es)" in out


def test_a_dry_run_beside_a_run_does_not_fork_its_chain(repo, runs, capsys):
    chained(repo)
    repo.write(f"benchmark/runs/alpha/{AGAIN}/run.json", json.dumps({"source": {"run_id": RUN}}) + "\n")
    page(repo, "alpha", row(JUDGED, "$200.41"))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"no row names the run folder {AGAIN}" in out and "is already the source of" not in out


def test_a_chain_whose_source_is_not_beside_it_breaks(repo, runs, capsys):
    chained(repo)
    shutil.rmtree(repo.root / "benchmark" / "runs" / "alpha" / RUN)
    page(repo, "alpha", row(JUDGED, "at least $117.89"))  # what the folders it reaches spent
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"the chain of {JUDGED} breaks: {RESUMED} names {RUN} as its source, and alpha holds no such run folder" in out
    assert "1 run index mismatch(es)" in out


@pytest.mark.parametrize(
    ("first", "cost"),
    [
        (None, "at least $117.89"),  # the first folder recorded no spend
        (spend(82.5281, ["unknown-model"]), "at least $200.41"),  # a model with no price
    ],
)
def test_a_chain_s_total_reads_at_least_when_it_is_a_lower_bound(repo, runs, capsys, first, cost):
    chained(repo, first)
    if first is None:
        a_run(repo, RUN, "2026-01-01T08:00:00Z")
    page(repo, "alpha", row(JUDGED, "$200.41"))
    assert runs.main() == 1
    assert f"its chain spent {cost}" in capsys.readouterr().out
    page(repo, "alpha", row(JUDGED, cost))
    assert runs.main() == 0


def test_a_row_of_runs_that_recorded_no_spend_reads_a_dash(repo, runs):
    a_run(repo, ONE_A)
    page(repo, "alpha", row(ONE_A, "—"))
    index(repo, "alpha")
    assert runs.main() == 0
    page(repo, "alpha", row(ONE_A, "$0.00"))
    assert runs.main() == 1


def test_a_table_of_runs_without_a_cost_column_fails(repo, runs, capsys):
    a_run(repo, ONE_A)
    page(repo, "alpha", f"| [{ONE_A}]({ONE_A}/report.md) | one |\n", head="| Run | Started (UTC) |\n|---|---|\n")
    index(repo, "alpha")
    assert runs.main() == 1
    assert f"the table of {ONE_A} has no Cost (USD) column" in capsys.readouterr().out


def test_a_chain_is_ordered_by_when_its_first_folder_started(repo, runs, capsys):
    chained(repo)
    a_run(repo, ONE_A, "2026-01-01T12:00:00Z")  # after the chain's first folder, before its newest
    page(repo, "alpha", row(ONE_A), row(JUDGED, "$200.41"))
    assert runs.main() == 0
    page(repo, "alpha", row(JUDGED, "$200.41"), row(ONE_A))
    assert runs.main() == 1
    assert f"{ONE_A} started 2026-01-01T12:00:00Z, after {JUDGED} above it" in capsys.readouterr().out


# What a run folder holds ----------------------------------------------------------


def a_versioned_run(repo, name, dirty, paths=()):
    checkout = {"commit": "c" * 40, "plugin_version": "1.0.0", "dirty": dirty, "dirty_paths": list(paths), "dirty_sha256": None}
    a_run(repo, name, versions={"checkout": checkout})
    page(repo, "alpha", row(name))
    index(repo, "alpha")


def test_a_run_on_a_clean_checkout_passes(repo, runs):
    a_versioned_run(repo, ONE_A, False)
    assert runs.main() == 0


def test_a_run_on_changes_no_commit_holds_fails(repo, runs, capsys):
    a_versioned_run(repo, ONE_A, True, ["skills/one/SKILL.md"])
    assert runs.main() == 1
    assert f"benchmark/runs/alpha/{ONE_A}: ran on changes no commit holds (skills/one/SKILL.md)" in capsys.readouterr().out


def test_a_run_on_no_git_checkout_fails(repo, runs, capsys):
    a_versioned_run(repo, ONE_A, None)
    assert runs.main() == 1
    assert "ran on no git checkout" in capsys.readouterr().out


def test_a_run_recorded_before_versions_has_none_to_check(repo, runs):
    a_run(repo, ONE_A)
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 0


@pytest.mark.parametrize(("file", "marker"), [("results.json", {"status": "completed"}), ("run.json", True)])
def test_a_rehearsal_is_never_checked_in(repo, runs, capsys, file, marker):
    a_versioned_run(repo, ONE_A, False)
    path = repo.root / "benchmark" / "runs" / "alpha" / ONE_A / file
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    repo.write(f"benchmark/runs/alpha/{ONE_A}/{file}", json.dumps({**data, "rehearsal": marker}) + "\n")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/alpha/{ONE_A}: is a rehearsal, whose scores mean nothing; a rehearsal is never checked in" in out


EARLIER = "/tmp/swe_guidelines/benchmark/runs/create-full-system/20260927-204817-create-full-system-f263cfa8/report.md"


@pytest.mark.parametrize("where", ["repeat", "phase"])
def test_a_run_whose_subject_named_the_run_folders_is_never_checked_in(repo, runs, capsys, where):
    a_versioned_run(repo, ONE_A, False)
    path = repo.root / "benchmark" / "runs" / "alpha" / ONE_A / "results.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    read = {"tool": "Read", "id": "toolu_1", "key": "file_path", "value": EARLIER}
    phases = [{"name": "scaffold"}, {"name": "review", "read_runs": [read]}]
    repeat = {"index": 0, "phases": phases} | ({"read_runs": [{"phase": "review", **read}]} if where == "repeat" else {})
    repo.write(f"benchmark/runs/alpha/{ONE_A}/results.json", json.dumps({**data, "repeats": [repeat]}) + "\n")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert (
        f"benchmark/runs/alpha/{ONE_A}: repeat 0, Read with file_path {EARLIER!r} named the benchmark's run folders, "
        "so its subject had an earlier run's answers in reach; a marked run is never checked in"
    ) in out


def test_a_run_whose_repeats_name_no_run_folder_passes(repo, runs, capsys):
    a_versioned_run(repo, ONE_A, False)
    path = repo.root / "benchmark" / "runs" / "alpha" / ONE_A / "results.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    repeat = {"index": 0, "phases": [{"name": "scaffold"}, {"name": "review"}]}
    repo.write(f"benchmark/runs/alpha/{ONE_A}/results.json", json.dumps({**data, "repeats": [repeat]}) + "\n")
    assert runs.main() == 0
    assert "no rehearsal, no marked repeat" in capsys.readouterr().out


def test_a_run_on_a_runtime_its_scenario_lists_passes(repo, runs, capsys):
    a_scenario(repo, "alpha", ["host", "container"])
    a_run(repo, ONE_A, runtime="container")
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 0
    assert "on a runtime its scenario lists" in capsys.readouterr().out


def test_a_run_on_a_runtime_its_scenario_does_not_list_fails(repo, runs, capsys):
    a_scenario(repo, "alpha", ["container"])
    a_run(repo, ONE_A, runtime="host")
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 1
    out = capsys.readouterr().out
    expected = (
        f"benchmark/runs/alpha/{ONE_A}: ran on host, and alpha runs on container; a checked-in run ran where its scenario runs"
    )
    assert expected in out
    assert "1 run index mismatch(es)" in out


def test_the_runtime_run_json_records_counts_when_results_json_records_none(repo, runs, capsys):
    a_scenario(repo, "alpha", ["container"])
    repo.write(f"benchmark/runs/alpha/{ONE_A}/report.md", "# Benchmark run\n")
    repo.write(
        f"benchmark/runs/alpha/{ONE_A}/run.json", json.dumps({"scenario": {"name": "alpha"}, "runtime": {"name": "vm"}}) + "\n"
    )
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 1
    assert f"{ONE_A}: ran on vm, and alpha runs on container" in capsys.readouterr().out


def test_a_run_whose_scenario_has_no_file_or_one_that_does_not_load_fails(repo, runs, capsys):
    gamma = "20260101-000000-gamma-ee"
    a_run(repo, ONE_A, runtime="container")
    a_run(repo, ONE_B, scenario="beta", runtime="container")
    a_run(repo, gamma, scenario="gamma", runtime="container")
    repo.write("benchmark/scenarios/beta.json", json.dumps({"name": "beta", "kind": "qa", "subject": {"prompt": "?"}}) + "\n")
    repo.write("benchmark/scenarios/gamma.json", '{"name": "gamma",\n')  # does not parse
    page(repo, "alpha", row(ONE_A))
    page(repo, "beta", row(ONE_B))
    page(repo, "gamma", row(gamma))
    index(repo, "alpha", "beta", "gamma")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/alpha/{ONE_A}: no scenario named alpha in benchmark/scenarios says where it runs" in out
    assert f"benchmark/runs/beta/{ONE_B}: its scenario does not load, so nothing says where it runs: scenario beta:" in out
    assert f"benchmark/runs/gamma/{gamma}: its scenario does not load" in out
    assert "gamma.json: does not parse as JSON" in out
    assert "3 run index mismatch(es)" in out


def test_a_run_s_scenario_is_the_file_that_bears_its_name_not_its_stem(repo, runs, capsys):
    a_scenario(repo, "alpha", ["container"], file="first")  # named alpha, in first.json
    a_scenario(repo, "first", ["host"], file="second")  # named first, in second.json
    a_run(repo, ONE_A, runtime="container")
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 0
    shutil.rmtree(repo.root / "benchmark" / "runs" / "alpha")
    a_run(repo, ONE_A, scenario="first", runtime="container")
    page(repo, "first", row(ONE_A))
    index(repo, "first")
    assert runs.main() == 1
    assert f"{ONE_A}: ran on container, and first runs on host" in capsys.readouterr().out


def test_a_run_of_a_name_two_scenario_files_bear_fails(repo, runs, capsys):
    a_scenario(repo, "alpha", ["container"], file="one")
    a_scenario(repo, "alpha", ["container"], file="two")
    a_run(repo, ONE_A, runtime="container")
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 1
    assert "the scenario files one.json, two.json are all named alpha, so none says where it runs" in capsys.readouterr().out


def test_a_scenario_file_no_run_names_is_not_held_to_loading(repo, runs):
    a_scenario(repo, "alpha", ["container"])
    bad = {"name": "beta", "kind": "qa", "subject": {"prompt": "?", "timeout_s": "900s"}, "rubric": "r", "runtimes": ["host"]}
    repo.write("benchmark/scenarios/beta.json", json.dumps(bad) + "\n")
    a_run(repo, ONE_A, runtime="container")
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 0  # beta does not load, and no run is held to it


def test_a_name_a_loaded_file_bears_and_a_broken_file_s_stem_shares_fails(repo, runs, capsys):
    repo.write("benchmark/scenarios/alpha.json", '{"name": "alpha",\n')  # does not parse
    a_scenario(repo, "alpha", ["host"], file="beta")  # named alpha, in beta.json
    a_run(repo, ONE_A, runtime="host")
    page(repo, "alpha", row(ONE_A))
    index(repo, "alpha")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert "alpha is the name of beta.json and the stem of alpha.json, which does not load, so none says where it runs" in out


def a_zip(repo, name, members):
    path = repo.root / "benchmark" / "runs" / "alpha" / name / "artifacts" / "0" / "output.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for member, data in members.items():
            zf.writestr(member, data)
    return path


def a_vm_run(repo, *names):
    a_scenario(repo, "alpha", ["vm"])
    for name in names:
        a_run(repo, name, runtime="vm")
    page(repo, "alpha", *(row(name) for name in names))
    index(repo, "alpha")


def test_a_checked_in_zip_with_a_key_shaped_string_fails(repo, runs, capsys):
    a_vm_run(repo, ONE_A, TWO_A)
    a_zip(repo, ONE_A, {"README.md": "clean\n", "app/.env": "KEY=sk-ant-api03-" + "a1B2" * 12 + "\n"})
    a_zip(repo, TWO_A, {"README.md": "clean\n"})
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/alpha/{ONE_A}/artifacts/0/output.zip: app/.env holds a string shaped like a key" in out
    assert "1 run index mismatch(es)" in out  # the clean zip passes


def test_a_checked_in_stream_with_a_stripe_key_fails(repo, runs, capsys):
    a_vm_run(repo, ONE_A)
    stream = repo.root / "benchmark" / "runs" / "alpha" / ONE_A / "streams" / "cli.jsonl"
    stream.parent.mkdir(parents=True, exist_ok=True)
    stream.write_text('{"line": "API_KEY = \\"sk_test_' + "51Ab" * 6 + '\\""}\n', encoding="utf-8")
    assert runs.main() == 1
    assert f"benchmark/runs/alpha/{ONE_A}/streams/cli.jsonl holds a string shaped like a key" in capsys.readouterr().out


def test_a_checked_in_zip_that_does_not_open_fails(repo, runs, capsys):
    a_vm_run(repo, ONE_A)
    a_zip(repo, ONE_A, {"README.md": "clean\n"}).write_bytes(b"not a zip")
    assert runs.main() == 1
    assert "output.zip: does not open as a zip, so no one can say it holds no key" in capsys.readouterr().out


def test_a_clean_checked_in_zip_passes(repo, runs, capsys):
    a_vm_run(repo, ONE_A)
    a_zip(repo, ONE_A, {"README.md": "clean\n"})
    assert runs.main() == 0
    assert "no key or account id in any file" in capsys.readouterr().out


def test_a_key_in_a_zip_inside_a_checked_in_zip_fails(repo, runs, capsys):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr(".env", "KEY=ghp_" + "k" * 36)
    a_vm_run(repo, ONE_A)
    a_zip(repo, ONE_A, {"bundle.zip": inner.getvalue()})
    assert runs.main() == 1
    assert "output.zip: bundle.zip!.env holds a string shaped like a key" in capsys.readouterr().out


def test_a_git_folder_or_a_compressed_file_the_scan_cannot_read_in_a_run_folder_fails(repo, runs, capsys):
    a_vm_run(repo, ONE_A)
    folder = repo.root / "benchmark" / "runs" / "alpha" / ONE_A / "artifacts" / "0" / "workspace"
    (folder / "site" / ".git" / "objects").mkdir(parents=True)
    (folder / "site" / ".git" / "objects" / "ab").write_bytes(b"x")
    (folder / "env.json.gz").write_bytes(gzip.compress(b'{"key": "sk-ant-api03-' + b"a1B2" * 12 + b'"}'))
    (folder / "bundle.zst").write_bytes(b"\x28\xb5\x2f\xfd" + b"frame")
    (folder / "notes.md").write_text("plain text with nothing to redact passes\n", encoding="utf-8")
    assert runs.main() == 1
    out = capsys.readouterr().out
    workspace = f"benchmark/runs/alpha/{ONE_A}/artifacts/0/workspace"
    assert f"{workspace}/site/.git: a run folder holds no .git" in out
    assert f"{workspace}/env.json.gz holds a string shaped like a key" in out
    assert f"{workspace}/bundle.zst: (not scanned: a compressed form the scan cannot read)" in out
    assert "3 run index mismatch(es)" in out


ORGANIZATION = "org-" + "Qw3Er5Ty7Ui9" * 2


def test_an_organization_id_in_any_file_plain_or_compressed_fails_until_it_is_redacted(repo, runs, capsys):
    a_vm_run(repo, ONE_A)
    folder = repo.root / "benchmark" / "runs" / "alpha" / ONE_A
    said = f"Rate limit reached for gpt-6-sol in organization {ORGANIZATION} on tokens per min (TPM)."
    (folder / "judgements").mkdir()
    (folder / "judgements" / "0-openai.jsonl").write_text(json.dumps({"error": said}) + "\n", encoding="utf-8")
    (folder / "judgements" / "0-openai.jsonl.gz").write_bytes(gzip.compress(said.encode()))
    a_zip(repo, ONE_A, {"logs/judge.log": said})
    # A limit's figures alone fail too: they are the account's quota.
    (folder / "report.md").write_text("# Benchmark run\n\nLimit 30000, Used 28172, Requested 4096.\n", encoding="utf-8")
    assert runs.main() == 1
    out = capsys.readouterr().out
    shown = f"benchmark/runs/alpha/{ONE_A}"
    places = ("judgements/0-openai.jsonl", "judgements/0-openai.jsonl.gz", "artifacts/0/output.zip: logs/judge.log", "report.md")
    found = "a string shaped like a key, or an account id or a limit's figures from a provider's error"
    for where in places:
        assert f"{shown}/{where} holds {found}" in out
    assert "4 run index mismatch(es)" in out
    redacted = runs.X.redact_folder(folder, set())
    assert sorted(redacted) == sorted(path for path in folder.rglob("*") if path.suffix in (".jsonl", ".gz", ".zip", ".md"))
    assert runs.main() == 0
    assert "no key or account id in any file" in capsys.readouterr().out


@pytest.mark.parametrize("said", [ANTHROPIC_429, ANTHROPIC_429_WITHOUT_ID, XAI_429], ids=["anthropic", "anthropic-no-id", "xai"])
def test_the_account_the_other_judges_errors_name_fails_in_any_file_until_it_is_redacted(said, repo, runs, capsys):
    a_vm_run(repo, ONE_A)
    folder = repo.root / "benchmark" / "runs" / "alpha" / ONE_A
    (folder / "judgements").mkdir()
    (folder / "judgements" / "0-judge.jsonl").write_text(json.dumps({"error": said}) + "\n", encoding="utf-8")
    (folder / "judgements" / "0-judge.jsonl.gz").write_bytes(gzip.compress(said.encode()))
    a_zip(repo, ONE_A, {"logs/judge.log": said})
    assert runs.main() == 1
    out = capsys.readouterr().out
    shown = f"benchmark/runs/alpha/{ONE_A}"
    for where in ("judgements/0-judge.jsonl", "judgements/0-judge.jsonl.gz", "artifacts/0/output.zip: logs/judge.log"):
        assert f"{shown}/{where} holds a string shaped like a key, or an account id" in out
    assert "3 run index mismatch(es)" in out
    assert len(runs.X.redact_folder(folder, set())) == 3
    assert runs.main() == 0


# The browser benchmark ----------------------------------------------------------

SCHEMA = Path(__file__).resolve().parent.parent / "benchmark" / "schema" / "browser-session.schema.json"
BROWSER_HEAD = "| Run | Started (UTC) | chatgpt.com | claude.ai |\n|---|---|---|---|\n"
OLD = "20260920-224602"
NEW = "20260929-212716"


def a_session(site, **recorded):
    """A session as today's skill records it, its url redacted; `recorded` adds or replaces keys, and None drops one."""
    session = {
        "site": site,
        "url": "[redacted]",
        "model_label": "Latest",
        "effort_label": "High",
        "started_at": "2026-09-29T21:29:18Z",
        "finished_at": "2026-09-29T21:32:35Z",
        "score": 94,
        "read_version": "not stated",
        "polls": 1,
        "response_path": f"{site}.md",
        "status": "ok",
    } | recorded
    return {key: value for key, value in session.items() if value is not None}


def a_browser_run(repo, name, started, sessions=None, answer="Score: 94/100\n", **recorded):
    """A published browser run: its results.json and one answer per session, redacted."""
    sessions = [a_session("chatgpt.com"), a_session("claude.ai")] if sessions is None else sessions
    results = {
        "run_id": name,
        "started_at": started,
        "finished_at": started,
        "repository_head": "b" * 40,
        "prompt": "Evaluate the repository.",
        "contract": "Score first.",
        "sizes": {"model": "m", "effort": "m"},
        "sessions": sessions,
    } | recorded
    results = {key: value for key, value in results.items() if value is not None}
    repo.write(f"benchmark/runs/browser-judge-swe/{name}/results.json", json.dumps(results, indent=2) + "\n")
    for session in sessions:
        repo.write(
            f"benchmark/runs/browser-judge-swe/{name}/{session['response_path']}",
            f"# {session['site']}\n\n- URL: [redacted]\n\n## Answer\n\n{answer}",
        )


def browser_row(name):
    return f"| [{name}]({name}/results.json) | when | [94]({name}/chatgpt.com.md) Latest, High | [94]({name}/claude.ai.md) |\n"


def browser_page(repo, *names):
    """The browser page with a row per name, the index, and the schema the check holds each run to."""
    repo.write("benchmark/schema/browser-session.schema.json", SCHEMA.read_text(encoding="utf-8"))
    page(repo, "browser-judge-swe", *(browser_row(name) for name in names), head=BROWSER_HEAD)
    index(repo, "browser-judge-swe")


def two_browser_runs(repo):
    """Today's run, and one recorded before the schema required the head, a session's version read, and its polls."""
    a_browser_run(repo, NEW, "2026-09-29T21:27:16Z", answer="Score: 94/100\n\nSent at https://claude.ai/new.\n")
    older = [a_session(site, read_version=None, polls=None) for site in ("chatgpt.com", "claude.ai")]
    a_browser_run(repo, OLD, "2026-09-20T22:46:02Z", older, repository_head=None)
    browser_page(repo, NEW, OLD)


def test_browser_runs_in_their_schema_text_only_and_redacted_pass(repo, runs, capsys):
    two_browser_runs(repo)
    assert runs.main() == 0
    out = capsys.readouterr().out
    assert "runs ok: 2 run folder(s)" in out and "each browser run in its schema, text only, naming no conversation" in out


def test_a_browser_run_with_an_image_fails(repo, runs, capsys):
    two_browser_runs(repo)
    repo.write(f"benchmark/runs/browser-judge-swe/{NEW}/chatgpt.com.jpg", "\xff\xd8\xff not text")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert (
        f"benchmark/runs/browser-judge-swe/{NEW}/chatgpt.com.jpg: is not the run's results.json or a session's answer; "
        "a published browser run is text only, with no screenshot"
    ) in out
    assert "1 run index mismatch(es)" in out


@pytest.mark.parametrize(
    ("file", "said", "found"),
    [
        ("results.json", "https://gemini.google.com/app/0123456789abcdef", "gemini.google.com/app/0"),  # in a note
        ("chatgpt.com.md", "https://chatgpt.com/c/00000000-1111-2222-3333-444444444444", "chatgpt.com/c/"),
        ("claude.ai.md", "https://claude.ai/chat/55555555-6666-7777-8888-999999999999", "claude.ai/chat/"),
        ("chatgpt.com.md", "https://grok.com/c/aaaaaaaa-bbbb-cccc-dddd?rid=eeeeeeee", "grok.com/c/"),
        ("claude.ai.md", "https://chatgpt.com/share/00000000-1111", "chatgpt.com/share/"),
    ],
)
def test_a_browser_run_that_holds_a_conversation_s_address_fails(repo, runs, capsys, file, said, found):
    two_browser_runs(repo)
    if file == "results.json":
        a_browser_run(repo, NEW, "2026-09-29T21:27:16Z", [a_session("chatgpt.com", note=f"the first attempt, {said}, errored")])
    else:
        repo.write(f"benchmark/runs/browser-judge-swe/{NEW}/{file}", f"# {file}\n\n- URL: {said}\n")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/browser-judge-swe/{NEW}/{file}:" in out
    assert f"holds the address of a conversation ({found}...); a published run replaces it with [redacted]" in out


def test_a_browser_session_whose_url_is_not_redacted_fails(repo, runs, capsys):
    two_browser_runs(repo)
    a_browser_run(
        repo, NEW, "2026-09-29T21:27:16Z", [a_session("chatgpt.com"), a_session("claude.ai", url="https://claude.ai/new")]
    )
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"{NEW}/results.json: the claude.ai session's url is not [redacted]; a published run names no conversation" in out
    assert "1 run index mismatch(es)" in out  # a new-chat page is not a conversation's address


def test_a_browser_run_no_row_names_fails_and_so_does_a_row_with_no_run(repo, runs, capsys):
    two_browser_runs(repo)
    page(repo, "browser-judge-swe", browser_row(NEW), browser_row("20260101-000000"), head=BROWSER_HEAD)
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/browser-judge-swe/README.md: no row names the run folder {OLD}" in out
    assert "browser-judge-swe/README.md:8: links 20260101-000000/results.json, and browser-judge-swe holds no such run" in out
    page(repo, "browser-judge-swe", browser_row(NEW), browser_row(OLD), browser_row(OLD), head=BROWSER_HEAD)
    assert runs.main() == 1
    assert f"{OLD} is named by 2 rows (lines 8, 9); a run has one row" in capsys.readouterr().out


def test_browser_rows_run_from_the_newest_start(repo, runs, capsys):
    two_browser_runs(repo)
    page(repo, "browser-judge-swe", browser_row(OLD), browser_row(NEW), head=BROWSER_HEAD)
    assert runs.main() == 1
    assert f"browser-judge-swe/README.md:8: {NEW} started 2026-09-29T21:27:16Z, after {OLD} above it" in capsys.readouterr().out


def test_a_browser_run_outside_its_schema_fails(repo, runs, capsys):
    two_browser_runs(repo)
    sessions = [a_session("chatgpt.com", score=101, screenshot="chatgpt.com.jpg"), a_session("claude.ai", status=None)]
    a_browser_run(repo, NEW, "2026-09-29T21:27:16Z", sessions, sizes={"model": "m"})
    assert runs.main() == 1
    out = capsys.readouterr().out
    where = f"benchmark/runs/browser-judge-swe/{NEW}/results.json"
    assert f"{where}: sessions/0/score: 101 is greater than the maximum of 100" in out
    assert f"{where}: sessions/0: Additional properties are not allowed ('screenshot' was unexpected)" in out
    assert f"{where}: sessions/1: 'status' is a required property" in out
    assert f"{where}: sizes: 'effort' is a required property" in out
    # A key the schema required from the start is required of an older run too.
    a_browser_run(repo, OLD, "2026-09-20T22:46:02Z", [a_session("chatgpt.com", model_label=None)], repository_head=None)
    assert runs.main() == 1
    assert f"browser-judge-swe/{OLD}/results.json: sessions/0: 'model_label' is a required property" in capsys.readouterr().out


def test_a_browser_session_whose_answer_is_not_in_the_run_folder_fails(repo, runs, capsys):
    two_browser_runs(repo)
    (repo.root / "benchmark" / "runs" / "browser-judge-swe" / NEW / "claude.ai.md").unlink()
    a_browser_run(repo, OLD, "2026-09-20T22:46:02Z", [a_session("chatgpt.com", response_path="../chatgpt.com.md")])
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"browser-judge-swe/{NEW}: the claude.ai session's answer claude.ai.md is not in the run folder" in out
    assert f"{OLD}/results.json: the chatgpt.com session's response_path '../chatgpt.com.md' is not a file" in out


def test_a_browser_run_that_names_the_reference_implementation_fails(repo, runs, capsys):
    name = re.sub(r"\\b", "", runs.REFUSED_TERMS["reference"][0])  # the one list make leaks refuses it by
    two_browser_runs(repo)
    a_browser_run(repo, NEW, "2026-09-29T21:27:16Z", answer=f"Score: 94/100\n\nCloned someone/{name.title()} too.\n")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"{NEW}/chatgpt.com.md:9: names the reference implementation; a published run replaces the name" in out
    assert f"browser-judge-swe/{NEW}/claude.ai.md:9: names the reference implementation" in out


def test_a_key_in_a_browser_run_fails(repo, runs, capsys):
    two_browser_runs(repo)
    a_browser_run(repo, NEW, "2026-09-29T21:27:16Z", answer=f"Score: 94/100\n\nexport KEY={ANTHROPIC}\n")
    assert runs.main() == 1
    assert f"browser-judge-swe/{NEW}/chatgpt.com.md holds a string shaped like a key" in capsys.readouterr().out


def test_a_browser_run_without_jsonschema_fails(repo, runs, capsys, monkeypatch):
    two_browser_runs(repo)
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"browser-judge-swe/{NEW}: jsonschema is not installed, so no one can say the run is in its schema" in out
