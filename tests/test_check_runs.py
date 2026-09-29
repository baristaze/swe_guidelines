"""scripts/check_runs.py: the index of the benchmark runs names every run folder once, in its scenario's section."""

import io
import json
import zipfile

import pytest

HEAD = "| Run | Scenario |\n|---|---|\n"


def row(name):
    return f"| [{name}]({name}/report.md) | one |\n"


def section(scenario, *names):
    return f"\n## {scenario}\n\nIt measures {scenario}.\n\n" + HEAD + "".join(row(name) for name in names)


@pytest.fixture
def runs(repo):
    return repo.script("check_runs")


def a_run(repo, name, started="2026-01-01T00:00:00Z", scenario=None, runtime=None):
    repo.write(f"benchmark/runs/{name}/report.md", "# Benchmark run\n")
    results = {"started_at": started} | ({"scenario": scenario} if scenario else {}) | ({"runtime": runtime} if runtime else {})
    repo.write(f"benchmark/runs/{name}/results.json", json.dumps(results) + "\n")


def a_scenario(repo, name, runtimes, file=None):
    scenario = {"name": name, "kind": "qa", "subject": {"prompt": "Why?"}, "rubric": "r", "runtimes": runtimes}
    repo.write(f"benchmark/scenarios/{file or name}.json", json.dumps(scenario) + "\n")


def test_no_runs_and_no_index_pass(runs, capsys):
    assert runs.main() == 0
    assert "runs ok: 0 run folder(s)" in capsys.readouterr().out


def test_every_run_with_one_row_passes(repo, runs, capsys):
    a_run(repo, "20260101-000000-one-aa", "2026-01-01T08:00:00Z")
    a_run(repo, "20260102-000000-two-bb", "2026-01-02T08:00:00Z")
    repo.write("benchmark/runs/README.md", "# Runs\n\n" + HEAD + row("20260102-000000-two-bb") + row("20260101-000000-one-aa"))
    assert runs.main() == 0
    assert "runs ok: 2 run folder(s), one row each" in capsys.readouterr().out


def test_a_run_without_a_row_fails(repo, runs, capsys):
    a_run(repo, "20260101-000000-one-aa")
    a_run(repo, "20260102-000000-two-bb")
    repo.write("benchmark/runs/README.md", HEAD + row("20260101-000000-one-aa"))
    assert runs.main() == 1
    assert "benchmark/runs/README.md: no row for the run folder 20260102-000000-two-bb" in capsys.readouterr().out


def test_a_row_without_its_run_fails(repo, runs, capsys):
    a_run(repo, "20260101-000000-one-aa")
    repo.write("benchmark/runs/README.md", HEAD + row("20260101-000000-one-aa") + row("20260103-000000-gone-cc"))
    assert runs.main() == 1
    assert "README.md:4: links 20260103-000000-gone-cc/report.md, and there is no such run" in capsys.readouterr().out


def test_a_run_with_two_rows_fails(repo, runs, capsys):
    a_run(repo, "20260101-000000-one-aa")
    repo.write("benchmark/runs/README.md", HEAD + row("20260101-000000-one-aa") + row("20260101-000000-one-aa"))
    assert runs.main() == 1
    assert "20260101-000000-one-aa has 2 rows; a run has one" in capsys.readouterr().out


def test_runs_without_an_index_fail(repo, runs, capsys):
    a_run(repo, "20260101-000000-one-aa")
    assert runs.main() == 1
    assert "benchmark/runs/README.md: missing, so no run folder has a row: 20260101-000000-one-aa" in capsys.readouterr().out


def test_a_link_outside_a_table_is_not_a_row(repo, runs):
    a_run(repo, "20260101-000000-one-aa")
    repo.write(
        "benchmark/runs/README.md",
        "See [the first run](20260101-000000-one-aa/report.md).\n\n" + HEAD + row("20260101-000000-one-aa"),
    )
    assert runs.main() == 0


def test_an_older_run_above_a_newer_one_fails(repo, runs, capsys):
    a_run(repo, "20260101-000000-one-aa", "2026-01-01T08:00:00Z")
    a_run(repo, "20260102-000000-two-bb", "2026-01-02T08:00:00Z")
    repo.write("benchmark/runs/README.md", HEAD + row("20260101-000000-one-aa") + row("20260102-000000-two-bb"))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert "README.md:4: 20260102-000000-two-bb started 2026-01-02T08:00:00Z, after 20260101-000000-one-aa above it" in out


def test_runs_that_started_together_keep_either_order(repo, runs):
    a_run(repo, "20260101-000000-one-aa", "2026-01-01T08:00:00Z")
    a_run(repo, "20260101-000000-two-bb", "2026-01-01T08:00:00Z")
    repo.write("benchmark/runs/README.md", HEAD + row("20260101-000000-one-aa") + row("20260101-000000-two-bb"))
    assert runs.main() == 0


def test_a_run_that_records_less_is_listed_and_left_out_of_the_order(repo, runs):
    a_run(repo, "20260102-000000-two-bb", "2026-01-02T08:00:00Z")
    repo.write("benchmark/runs/20260101-000000-old-aa/report.md", "# Benchmark run\n")
    repo.write("benchmark/runs/20260103-000000-bare-cc/report.md", "# Benchmark run\n")
    repo.write("benchmark/runs/20260103-000000-bare-cc/results.json", "{}\n")
    repo.write(
        "benchmark/runs/README.md",
        HEAD + row("20260101-000000-old-aa") + row("20260102-000000-two-bb") + row("20260103-000000-bare-cc"),
    )
    assert runs.main() == 0


def a_versioned_run(repo, name, dirty, paths=()):
    a_run(repo, name)
    checkout = {"commit": "c" * 40, "plugin_version": "1.0.0", "dirty": dirty, "dirty_paths": list(paths), "dirty_sha256": None}
    results = {"started_at": "2026-01-01T00:00:00Z", "versions": {"checkout": checkout}}
    repo.write(f"benchmark/runs/{name}/results.json", json.dumps(results) + "\n")
    repo.write("benchmark/runs/README.md", HEAD + row(name))


def test_a_run_on_a_clean_checkout_passes(repo, runs):
    a_versioned_run(repo, "20260101-000000-one-aa", False)
    assert runs.main() == 0


def test_a_run_on_changes_no_commit_holds_fails(repo, runs, capsys):
    a_versioned_run(repo, "20260101-000000-one-aa", True, ["skills/one/SKILL.md"])
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert "benchmark/runs/20260101-000000-one-aa: ran on changes no commit holds (skills/one/SKILL.md)" in out


def test_a_run_on_no_git_checkout_fails(repo, runs, capsys):
    a_versioned_run(repo, "20260101-000000-one-aa", None)
    assert runs.main() == 1
    assert "ran on no git checkout" in capsys.readouterr().out


def test_a_run_recorded_before_versions_has_none_to_check(repo, runs):
    a_run(repo, "20260101-000000-one-aa")
    repo.write("benchmark/runs/README.md", HEAD + row("20260101-000000-one-aa"))
    assert runs.main() == 0


@pytest.mark.parametrize(("file", "marker"), [("results.json", {"status": "completed"}), ("run.json", True)])
def test_a_rehearsal_is_never_checked_in(repo, runs, capsys, file, marker):
    name = "20260101-000000-one-aa"
    a_versioned_run(repo, name, False)
    path = repo.root / "benchmark" / "runs" / name / file
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    repo.write(f"benchmark/runs/{name}/{file}", json.dumps({**data, "rehearsal": marker}) + "\n")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/{name}: is a rehearsal, whose scores mean nothing; a rehearsal is never checked in" in out


EARLIER = "/tmp/swe_guidelines/benchmark/runs/20260927-204817-create-full-system-f263cfa8/report.md"


@pytest.mark.parametrize("where", ["repeat", "phase"])
def test_a_run_whose_subject_named_the_run_folders_is_never_checked_in(repo, runs, capsys, where):
    name = "20260101-000000-one-aa"
    a_versioned_run(repo, name, False)
    path = repo.root / "benchmark" / "runs" / name / "results.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    read = {"tool": "Read", "id": "toolu_1", "key": "file_path", "value": EARLIER}
    phases = [{"name": "scaffold"}, {"name": "review", "read_runs": [read]}]
    repeat = {"index": 0, "phases": phases} | ({"read_runs": [{"phase": "review", **read}]} if where == "repeat" else {})
    repo.write(f"benchmark/runs/{name}/results.json", json.dumps({**data, "repeats": [repeat]}) + "\n")
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert (
        f"benchmark/runs/{name}: repeat 0, Read with file_path {EARLIER!r} named the benchmark's run folders, "
        "so its subject had an earlier run's answers in reach; a marked run is never checked in"
    ) in out


def test_a_run_whose_repeats_name_no_run_folder_passes(repo, runs, capsys):
    name = "20260101-000000-one-aa"
    a_versioned_run(repo, name, False)
    path = repo.root / "benchmark" / "runs" / name / "results.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    repeat = {"index": 0, "phases": [{"name": "scaffold"}, {"name": "review"}]}
    repo.write(f"benchmark/runs/{name}/results.json", json.dumps({**data, "repeats": [repeat]}) + "\n")
    assert runs.main() == 0
    assert "no rehearsal, no marked repeat" in capsys.readouterr().out


ONE_A = "20260101-000000-alpha-aa"
TWO_A = "20260102-000000-alpha-bb"
ONE_B = "20260101-000000-beta-cc"
TWO_B = "20260102-000000-beta-dd"


def test_every_row_in_its_scenarios_section_passes(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    a_run(repo, ONE_B, "2026-01-01T08:00:00Z", "beta")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A) + section("beta", ONE_B))
    assert runs.main() == 0
    assert "runs ok: 2 run folder(s), one row each, in its scenario's section" in capsys.readouterr().out


def test_the_newest_run_comes_first_within_a_section_not_across_the_file(repo, runs):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    a_run(repo, TWO_A, "2026-01-02T08:00:00Z", "alpha")
    a_run(repo, TWO_B, "2026-01-02T09:00:00Z", "beta")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", TWO_A, ONE_A) + section("beta", TWO_B))
    assert runs.main() == 0


def test_an_older_run_above_a_newer_one_in_a_section_fails(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    a_run(repo, TWO_A, "2026-01-02T08:00:00Z", "alpha")
    a_run(repo, TWO_B, "2026-01-02T09:00:00Z", "beta")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A, TWO_A) + section("beta", TWO_B))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"README.md:10: {TWO_A} started 2026-01-02T08:00:00Z, after {ONE_A} above it" in out
    assert "the newest run of a section comes first" in out
    assert "1 run index mismatch(es)" in out


def test_a_row_under_another_scenarios_section_fails(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    a_run(repo, ONE_B, "2026-01-01T08:00:00Z", "beta")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha") + section("beta", ONE_A, ONE_B))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"README.md:16: {ONE_A} is a run of alpha, and its row sits under `## beta`; it goes under `## alpha`" in out
    assert "1 run index mismatch(es)" in out


def test_a_row_above_every_section_fails(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    repo.write("benchmark/runs/README.md", "# Runs\n\n" + HEAD + row(ONE_A) + section("alpha"))
    assert runs.main() == 1
    assert f"README.md:5: {ONE_A} is a run of alpha, and its row sits above every section" in capsys.readouterr().out


def test_a_run_whose_scenario_has_no_section_fails(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    a_run(repo, ONE_B, "2026-01-01T08:00:00Z", "beta")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A, ONE_B))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/README.md: no section for the scenario beta, which {ONE_B} ran" in out
    assert "add `## beta` with one line on what it measures, and put its rows there" in out
    assert "1 run index mismatch(es)" in out


def test_a_second_section_for_a_scenario_fails(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    a_run(repo, TWO_A, "2026-01-02T08:00:00Z", "alpha")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", TWO_A) + section("alpha", ONE_A))
    assert runs.main() == 1
    assert "README.md:11: a second section for alpha; a scenario has one" in capsys.readouterr().out


def index_with_one_row(heading, name):
    """An index with a section for alpha and one for beta, the one row under `heading`."""
    other = "beta" if heading == "alpha" else "alpha"
    return "# Runs\n" + section(heading, name) + section(other)


@pytest.mark.parametrize(("heading", "status"), [("alpha", 0), ("beta", 1)])
def test_a_run_without_results_is_held_to_the_scenario_its_run_json_names(repo, runs, capsys, heading, status):
    repo.write(f"benchmark/runs/{ONE_A}/report.md", "# Benchmark run\n")
    repo.write(f"benchmark/runs/{ONE_A}/run.json", json.dumps({"scenario": {"name": "alpha"}}) + "\n")
    repo.write("benchmark/runs/README.md", index_with_one_row(heading, ONE_A))
    assert runs.main() == status
    refused = f"{ONE_A} is a run of alpha, and its row sits under `## beta`"
    assert (refused in capsys.readouterr().out) == bool(status)


@pytest.mark.parametrize(("heading", "status"), [("alpha", 0), ("beta", 1)])
def test_the_scenario_results_json_records_decides_over_run_json(repo, runs, capsys, heading, status):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    repo.write(f"benchmark/runs/{ONE_A}/run.json", json.dumps({"scenario": {"name": "beta"}}) + "\n")
    repo.write("benchmark/runs/README.md", index_with_one_row(heading, ONE_A))
    assert runs.main() == status
    refused = f"{ONE_A} is a run of alpha, and its row sits under `## beta`"
    assert (refused in capsys.readouterr().out) == bool(status)


def fenced(text):
    return "\n```markdown\n" + text + "```\n"


def test_a_heading_and_a_row_in_fenced_code_are_neither(repo, runs):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    example = fenced("## alpha\n\n" + HEAD + row(ONE_A))
    repo.write("benchmark/runs/README.md", "# Runs\n" + example + section("alpha", ONE_A))
    assert runs.main() == 0


def test_a_heading_in_fenced_code_opens_no_section(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    repo.write("benchmark/runs/README.md", "# Runs\n" + fenced("## alpha\n") + section("beta", ONE_A))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"no section for the scenario alpha, which {ONE_A} ran" in out
    assert "1 run index mismatch(es)" in out


def test_a_closing_sequence_is_not_part_of_a_sections_name(repo, runs):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    a_run(repo, ONE_B, "2026-01-01T08:00:00Z", "beta")
    index = "# Runs\n\n## alpha ##\n\n" + HEAD + row(ONE_A) + "\n## beta #\n\n" + HEAD + row(ONE_B)
    repo.write("benchmark/runs/README.md", index)
    assert runs.main() == 0


def test_a_row_under_a_heading_with_a_closing_sequence_is_held_to_its_name(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    repo.write("benchmark/runs/README.md", "# Runs\n\n## alpha ##\n\n## beta ##\n\n" + HEAD + row(ONE_A))
    assert runs.main() == 1
    assert f"{ONE_A} is a run of alpha, and its row sits under `## beta`; it goes under `## alpha`" in capsys.readouterr().out


def test_a_row_of_another_scenario_is_left_out_of_the_sections_order(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha")
    a_run(repo, TWO_A, "2026-01-02T08:00:00Z", "alpha")
    a_run(repo, ONE_B, "2026-01-01T09:00:00Z", "beta")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_B, TWO_A, ONE_A) + section("beta"))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"{ONE_B} is a run of beta, and its row sits under `## alpha`; it goes under `## beta`" in out
    assert TWO_A not in out
    assert "1 run index mismatch(es)" in out


def test_a_run_on_a_runtime_its_scenario_lists_passes(repo, runs, capsys):
    a_scenario(repo, "alpha", ["host", "container"])
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "container")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 0
    assert "on a runtime it lists" in capsys.readouterr().out


def test_a_run_on_a_runtime_its_scenario_does_not_list_fails(repo, runs, capsys):
    a_scenario(repo, "alpha", ["container"])
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "host")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert (
        f"benchmark/runs/{ONE_A}: ran on host, and alpha runs on container; a checked-in run ran where its scenario runs" in out
    )
    assert "1 run index mismatch(es)" in out


def test_the_runtime_run_json_records_counts_when_results_json_records_none(repo, runs, capsys):
    a_scenario(repo, "alpha", ["container"])
    repo.write(f"benchmark/runs/{ONE_A}/report.md", "# Benchmark run\n")
    repo.write(f"benchmark/runs/{ONE_A}/run.json", json.dumps({"scenario": {"name": "alpha"}, "runtime": {"name": "vm"}}) + "\n")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 1
    assert f"{ONE_A}: ran on vm, and alpha runs on container" in capsys.readouterr().out


def test_a_run_whose_scenario_has_no_file_or_one_that_does_not_load_fails(repo, runs, capsys):
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "container")
    a_run(repo, ONE_B, "2026-01-01T08:00:00Z", "beta", "container")
    a_run(repo, "20260101-000000-gamma-ee", "2026-01-01T08:00:00Z", "gamma", "container")
    repo.write("benchmark/scenarios/beta.json", json.dumps({"name": "beta", "kind": "qa", "subject": {"prompt": "?"}}) + "\n")
    repo.write("benchmark/scenarios/gamma.json", '{"name": "gamma",\n')  # does not parse
    index = section("alpha", ONE_A) + section("beta", ONE_B) + section("gamma", "20260101-000000-gamma-ee")
    repo.write("benchmark/runs/README.md", "# Runs\n" + index)
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/{ONE_A}: no scenario named alpha in benchmark/scenarios says where it runs" in out
    assert f"benchmark/runs/{ONE_B}: its scenario does not load, so nothing says where it runs: scenario beta:" in out
    assert "benchmark/runs/20260101-000000-gamma-ee: its scenario does not load" in out
    assert "gamma.json: does not parse as JSON" in out
    assert "3 run index mismatch(es)" in out


def test_a_run_s_scenario_is_the_file_that_bears_its_name_not_its_stem(repo, runs, capsys):
    a_scenario(repo, "alpha", ["container"], file="first")  # named alpha, in first.json
    a_scenario(repo, "first", ["host"], file="second")  # named first, in second.json
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "container")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 0
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "first", "container")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("first", ONE_A))
    assert runs.main() == 1
    assert f"{ONE_A}: ran on container, and first runs on host" in capsys.readouterr().out


def test_a_run_of_a_name_two_scenario_files_bear_fails(repo, runs, capsys):
    a_scenario(repo, "alpha", ["container"], file="one")
    a_scenario(repo, "alpha", ["container"], file="two")
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "container")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 1
    assert "the scenario files one.json, two.json are all named alpha, so none says where it runs" in capsys.readouterr().out


def test_a_scenario_file_no_run_names_is_not_held_to_loading(repo, runs, capsys):
    a_scenario(repo, "alpha", ["container"])
    bad = {"name": "beta", "kind": "qa", "subject": {"prompt": "?", "timeout_s": "900s"}, "rubric": "r", "runtimes": ["host"]}
    repo.write("benchmark/scenarios/beta.json", json.dumps(bad) + "\n")
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "container")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 0  # beta does not load, and no run is held to it


def test_a_name_a_loaded_file_bears_and_a_broken_file_s_stem_shares_fails(repo, runs, capsys):
    repo.write("benchmark/scenarios/alpha.json", '{"name": "alpha",\n')  # does not parse
    a_scenario(repo, "alpha", ["host"], file="beta")  # named alpha, in beta.json
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "host")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert "alpha is the name of beta.json and the stem of alpha.json, which does not load, so none says where it runs" in out


def a_zip(repo, name, members):
    path = repo.root / "benchmark" / "runs" / name / "artifacts" / "0" / "output.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for member, data in members.items():
            zf.writestr(member, data)
    return path


def test_a_checked_in_zip_with_a_key_shaped_string_fails(repo, runs, capsys):
    a_scenario(repo, "alpha", ["vm"])
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "vm")
    a_run(repo, ONE_B, "2026-01-01T07:00:00Z", "alpha", "vm")
    a_zip(repo, ONE_A, {"README.md": "clean\n", "app/.env": "KEY=sk-ant-api03-" + "a1B2" * 12 + "\n"})
    a_zip(repo, ONE_B, {"README.md": "clean\n"})
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A, ONE_B))
    assert runs.main() == 1
    out = capsys.readouterr().out
    assert f"benchmark/runs/{ONE_A}/artifacts/0/output.zip: app/.env holds a string shaped like a key" in out
    assert "1 run index mismatch(es)" in out  # the clean zip passes


def test_a_checked_in_zip_that_does_not_open_fails(repo, runs, capsys):
    a_scenario(repo, "alpha", ["vm"])
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "vm")
    a_zip(repo, ONE_A, {"README.md": "clean\n"}).write_bytes(b"not a zip")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 1
    assert "output.zip: does not open as a zip, so no one can say it holds no key" in capsys.readouterr().out


def test_a_clean_checked_in_zip_passes(repo, runs, capsys):
    a_scenario(repo, "alpha", ["vm"])
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "vm")
    a_zip(repo, ONE_A, {"README.md": "clean\n"})
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 0
    assert "no key in a compressed file" in capsys.readouterr().out


def test_a_key_in_a_zip_inside_a_checked_in_zip_fails(repo, runs, capsys):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr(".env", "KEY=ghp_" + "k" * 36)
    a_scenario(repo, "alpha", ["vm"])
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "vm")
    a_zip(repo, ONE_A, {"bundle.zip": inner.getvalue()})
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 1
    assert "output.zip: bundle.zip!.env holds a string shaped like a key" in capsys.readouterr().out


def test_a_git_folder_or_a_compressed_file_the_scan_cannot_read_in_a_run_folder_fails(repo, runs, capsys):
    import gzip

    a_scenario(repo, "alpha", ["vm"])
    a_run(repo, ONE_A, "2026-01-01T08:00:00Z", "alpha", "vm")
    folder = repo.root / "benchmark" / "runs" / ONE_A / "artifacts" / "0" / "workspace"
    (folder / "site" / ".git" / "objects").mkdir(parents=True)
    (folder / "site" / ".git" / "objects" / "ab").write_bytes(b"x")
    (folder / "env.json.gz").write_bytes(gzip.compress(b'{"key": "sk-ant-api03-' + b"a1B2" * 12 + b'"}'))
    (folder / "bundle.zst").write_bytes(b"\x28\xb5\x2f\xfd" + b"frame")
    (folder / "notes.md").write_text("plain text is redact's to scan\n", encoding="utf-8")
    repo.write("benchmark/runs/README.md", "# Runs\n" + section("alpha", ONE_A))
    assert runs.main() == 1
    out = capsys.readouterr().out
    workspace = f"benchmark/runs/{ONE_A}/artifacts/0/workspace"
    assert f"{workspace}/site/.git: a run folder holds no .git" in out
    assert f"{workspace}/env.json.gz holds a string shaped like a key" in out
    assert f"{workspace}/bundle.zst: (not scanned: a compressed form the scan cannot read)" in out
    assert "3 run index mismatch(es)" in out
