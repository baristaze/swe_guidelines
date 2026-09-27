"""scripts/check_runs.py: the index of the benchmark runs names every run folder once."""

import pytest

HEAD = "| Run | Scenario |\n|---|---|\n"


def row(name):
    return f"| [{name}]({name}/report.md) | one |\n"


@pytest.fixture
def runs(repo):
    return repo.script("check_runs")


def a_run(repo, name):
    repo.write(f"benchmark/runs/{name}/report.md", "# Benchmark run\n")


def test_no_runs_and_no_index_pass(runs, capsys):
    assert runs.main() == 0
    assert "runs ok: 0 run folder(s)" in capsys.readouterr().out


def test_every_run_with_one_row_passes(repo, runs, capsys):
    a_run(repo, "20260101-000000-one-aa")
    a_run(repo, "20260102-000000-two-bb")
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
