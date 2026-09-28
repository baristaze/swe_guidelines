"""A subject in phases: fresh and resumed sessions, checkpoints, the archive, the bounds, and the gates.

The subject is `benchmark_fake_claude.py` on the host runtime, and on
the vm runtime through a prefix that runs here. Its prompt says what it
does, and its answer says what it saw.
"""

import json
import sys
import zipfile
from pathlib import Path

import pytest

from harness import archive as A
from harness import phases as PH
from harness import scenario as S
from harness.capture import CliStream
from test_benchmark_run import run, vm_config

FAKE = Path(__file__).resolve().parent / "benchmark_fake_claude.py"


def do(action: dict) -> str:
    return f"DO {json.dumps(action)}"


def phase(name: str, action: dict | None = None, **extra) -> dict:
    return {"name": name, "prompt": do(action or {}), "max_turns": 5, "max_usd": 1, "timeout_s": 60, **extra}


def phased(*phases: dict, **subject) -> dict:
    return {
        "name": "system",
        "kind": "skill",
        "subject": {"skill": "arch-scaffold-new", "output": "site", "phases": list(phases), **subject},
        "rubric": "r",
        "runtimes": ["host", "vm"],
        "judges": {"providers": "anthropic"},
    }


def fake_claude(tmp_path: Path) -> str:
    path = tmp_path / "claude"
    path.write_text(f"#!{sys.executable}\n" + FAKE.read_text(encoding="utf-8"), encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def run_phases(tmp_path, monkeypatch):
    """Run a scenario once with the fake Claude Code; its exit code and its run folder."""
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])

    def go(scenario: dict, *extra: str) -> tuple[int, Path]:
        path = tmp_path / "scenario.json"
        path.write_text(json.dumps(scenario), encoding="utf-8")
        out = tmp_path / "runs" / str(len(list((tmp_path / "runs").glob("*"))) if (tmp_path / "runs").exists() else 0)
        argv = ["--scenario", str(path), "--out", str(out), "--claude", fake_claude(tmp_path)]
        code = run.main([*argv, "--subject-model", "claude-opus-5-5", *(extra or ("--repeat", "1"))])
        (run_dir,) = out.iterdir()
        return code, run_dir

    return go


def results(run_dir: Path) -> dict:
    return json.loads((run_dir / "results.json").read_text(encoding="utf-8"))


def seen(run_dir: Path) -> list[dict | None]:
    """What each session that wrote a result saw, in order."""
    out: list[dict | None] = []
    for record in CliStream.read(run_dir / "streams" / "cli.jsonl"):
        event = PH.parse(record["line"]) if record["s"] == "out" else None
        if event and event.get("type") == "result":
            out.append(json.loads(event["result"]) if "result" in event else None)
    return out


def test_each_fresh_phase_gets_a_new_home_and_a_resumed_one_continues_its_session(run_phases):
    code, run_dir = run_phases(phased(phase("scaffold"), phase("mvp", session="resume"), phase("review")))
    assert code == 0
    first, second, third = seen(run_dir)
    assert first and second and third
    assert first["home_files"] == [] and first["resumed"] is None
    # The resumed session continues the one before it, in its HOME.
    sessions = [p["session_id"] for p in results(run_dir)["repeats"][0]["phases"]]
    assert second["resumed"] == sessions[0] == sessions[1]
    assert second["home"] == first["home"] and len(second["home_files"]) == 1
    # A fresh one finds nothing any session left in a HOME.
    assert third["home"] != first["home"] and third["home_files"] == [] and third["resumed"] is None
    assert "--resume" not in third["args"]


def test_a_checkpoint_is_committed_after_every_phase_and_the_last_is_archived_whole(run_phases):
    scenario = phased(
        phase("scaffold", {"write": {"site/README.md": "one\n"}}),
        phase("mvp", {"write": {"site/README.md": "two\n", "site/app/main.py": "print(2)\n", "stray.txt": "x"}}),
    )
    code, run_dir = run_phases(scenario)
    assert code == 0
    repeat = results(run_dir)["repeats"][0]
    first, second = (p["checkpoint"] for p in repeat["phases"])
    assert first and second and first != second
    archive = repeat["archive"]
    assert archive["commit"] == second
    assert archive["path"] == "artifacts/0/output.zip" and archive["manifest"] == "artifacts/0/MANIFEST.txt"
    zipped = run_dir / archive["path"]
    assert archive["sha256"] == A.digest(zipped) and archive["bytes"] == zipped.stat().st_size and archive["files"] == 2
    with zipfile.ZipFile(zipped) as zf:
        assert sorted(zf.namelist()) == ["README.md", "app/", "app/main.py"]
        assert zf.read("README.md") == b"two\n"  # the last commit, and nothing outside the output folder
        assert zf.comment.decode() == second
    manifest = (run_dir / archive["manifest"]).read_text(encoding="utf-8").splitlines()
    assert [line.split("  ")[1:] for line in manifest] == [["4", "README.md"], ["9", "app/main.py"]]
    assert {"artifacts/0/output.zip", "artifacts/0/MANIFEST.txt"} <= set(repeat["artifact_paths"])
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## Phases" in report and "## Output" in report and "2 file(s)" in report


def test_a_failed_phase_ends_the_repeat_and_its_checkpoints_are_kept(run_phases):
    scenario = phased(
        phase("scaffold", {"write": {"site/a.txt": "a"}}),
        phase("mvp", {"write": {"site/b.txt": "b"}, "result": False, "exit": 1}),
        phase("review", {"write": {"site/c.txt": "c"}}),
    )
    code, run_dir = run_phases(scenario)
    assert code == 6  # the subject failed, so the repeat is not judged
    repeat = results(run_dir)["repeats"][0]
    assert [(p["name"], p["status"]) for p in repeat["phases"]] == [("scaffold", "ok"), ("mvp", "failed")]
    assert repeat["exit_status"]["code"] == 1
    with zipfile.ZipFile(run_dir / repeat["archive"]["path"]) as zf:
        assert sorted(zf.namelist()) == ["a.txt", "b.txt"]  # what the failed phase wrote is kept too
    assert any("phase mvp failed; the phases after it did not run" in n for n in results(run_dir)["notes"])


def test_a_phase_that_hits_a_bound_is_capped_and_the_next_one_runs_unless_it_says_stop(run_phases):
    turns = {"subtype": "error_max_turns", "is_error": True, "exit": 1}
    code, run_dir = run_phases(phased(phase("scaffold", turns), phase("mvp")))
    assert code == 0  # a bound is not a failure: the repeat goes on and is judged
    phases = results(run_dir)["repeats"][0]["phases"]
    assert [(p["status"], p["capped"]) for p in phases] == [("capped", "turns"), ("ok", None)]
    assert "| 0 | scaffold | fresh | capped | turns |" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_a_phase_that_says_stop_ends_the_repeat_at_its_bound(run_phases):
    budget = {"subtype": "error_max_budget_usd", "is_error": True, "exit": 1}
    code, run_dir = run_phases(phased(phase("scaffold", budget, on_cap="stop"), phase("mvp")))
    assert code == 0
    data = results(run_dir)
    assert [(p["name"], p["capped"]) for p in data["repeats"][0]["phases"]] == [("scaffold", "spend")]
    assert any("ended at its spend cap, and it stops the repeat there" in n for n in data["notes"])


def test_the_harness_stops_a_phase_whose_stream_passes_its_spend_cap(run_phases):
    usage = {"input_tokens": 150_000, "output_tokens": 0}  # $0.60 at the matrix's price
    act = {"messages": [["m1", "claude-opus-5-5", usage], ["m2", "claude-opus-5-5-20260901", usage]], "sleep": 30}
    code, run_dir = run_phases(phased(phase("scaffold", act), phase("mvp")))
    assert code == 0
    repeat = results(run_dir)["repeats"][0]
    first = repeat["phases"][0]
    assert (first["status"], first["capped"]) == ("capped", "spend")
    assert first["exit_status"]["stopped"] is True and first["exit_status"]["duration_s"] < 20
    # Each message counts once, though the stream carried it twice, and a dated release takes its model's price.
    assert first["estimated_usd"] == 1.2 and first["cost_usd"] is None
    assert repeat["subject_cost_usd"] == pytest.approx(1.2 + 0.25)
    assert repeat["phases"][1]["status"] == "ok"


def test_a_gate_that_fails_past_its_reruns_stops_the_phase_and_is_recorded_failing(run_phases):
    failing = [["cd site && make check 2>&1", True]] * 4 + [["make checks", True]]
    code, run_dir = run_phases(phased(phase("scaffold", {"bash": failing, "sleep": 30}), gates=["make check"]))
    assert code == 0
    first = results(run_dir)["repeats"][0]["phases"][0]
    assert (first["status"], first["capped"]) == ("capped", "gate_reruns")
    assert first["gate_runs"] == {"make check": {"runs": 4, "failed": 4, "failing": True}}


def test_a_passing_gate_run_starts_the_count_again(run_phases):
    # A failed run and two reruns that fail would pass the bound; a pass between them ends the streak.
    runs = [["make check", True]] * 2 + [["make check", False]] + [["make check", True]] * 2
    code, run_dir = run_phases(phased(phase("scaffold", {"bash": runs}, max_gate_reruns=2), gates=["make check"]))
    assert code == 0
    first = results(run_dir)["repeats"][0]["phases"][0]
    assert (first["status"], first["capped"]) == ("ok", None)
    assert first["gate_runs"]["make check"] == {"runs": 5, "failed": 4, "failing": True}


def test_the_handoff_note_reaches_only_a_hinted_phase(run_phases):
    scenario = phased(
        phase("scaffold", {"note": "from scaffold", "write": {"site/x.txt": "x"}}, hint=True),
        phase("review", cwd="output"),
        phase("fix", cwd="output", hint=True),
    )
    code, run_dir = run_phases(scenario)
    assert code == 0
    scaffold, review, fix = seen(run_dir)
    assert scaffold and review and fix
    assert scaffold["note_path"] == "HANDOFF.md" and scaffold["note"] is None
    # The phase with no hint is not told of the note, and the note is not where it could find it.
    assert review["note_path"] is None and review["handoff_in_reach"] == []
    assert "handoff" not in " ".join(review["args"]).lower()
    assert review["cwd"].endswith("/site")
    assert fix["note_path"] == "../HANDOFF.md" and fix["note"] == "from scaffold"
    with zipfile.ZipFile(run_dir / results(run_dir)["repeats"][0]["archive"]["path"]) as zf:
        assert zf.namelist() == ["x.txt"]


def test_the_gates_run_on_the_final_tree_and_are_recorded_beside_the_scores(run_phases):
    scenario = phased(phase("scaffold", {"write": {"site/README.md": "r"}}), gates=["test -f README.md", "exit 3"])
    code, run_dir = run_phases(scenario)
    assert code == 0  # a failing gate caps no score and fails no run
    gates = results(run_dir)["repeats"][0]["gates"]
    assert [(g["command"], g["passed"], g["exit_code"]) for g in gates] == [("test -f README.md", True, 0), ("exit 3", False, 3)]
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "- repeat 0: `test -f README.md` passed" in report and "- repeat 0: `exit 3` failed, exit 3" in report
    harness = (run_dir / "streams" / "harness.jsonl").read_text(encoding="utf-8")
    assert "[gate] exit 3" in harness and "[checkpoint] checkpoint 1: scaffold, ok" in harness


def test_the_run_s_spend_cap_is_checked_before_each_phase_and_each_repeat(run_phases):
    scenario = phased(phase("scaffold"), phase("mvp"), phase("review"))
    code, run_dir = run_phases(scenario, "--repeat", "2", "--max-spend-usd", "0.3")
    assert code == 0
    data = results(run_dir)
    assert [[p["name"] for p in r["phases"]] for r in data["repeats"]] == [["scaffold", "mvp"]]
    assert any("$0.3 was reached at $0.5000; phase review and after did not run" in n for n in data["notes"])
    assert any("repeat 1 and after did not run" in n for n in data["notes"])
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["max_spend_usd"] == 0.3


def test_a_one_phase_skill_takes_its_spend_cap_the_same_way(run_phases):
    one = {
        "name": "one",
        "kind": "skill",
        "subject": {"skill": "arch-explain", "prompt": do({}), "max_usd": 0.75, "max_turns": 4},
        "rubric": "r",
        "runtimes": ["host"],
        "judges": {"providers": "anthropic"},
    }
    code, run_dir = run_phases(one)
    assert code == 0
    (only,) = seen(run_dir)
    assert only
    args = only["args"]
    assert args[args.index("--max-budget-usd") + 1] == "0.75" and args[args.index("--max-turns") + 1] == "4"
    assert args[args.index("--output-format") + 1] == "stream-json" and "--verbose" in args
    (phase_record,) = results(run_dir)["repeats"][0]["phases"]
    assert phase_record["name"] == "subject" and phase_record["status"] == "ok"
    assert not (run_dir / "streams" / "harness.jsonl").exists()  # no output folder, so nothing to commit


def test_a_dry_run_resolves_the_phases_and_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    scenario = phased(phase("scaffold", hint=True), phase("mvp", session="resume"), phase("review", cwd="output"))
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--dry-run", "--max-spend-usd", "20"]
    assert run.main(argv) == 0
    (run_dir,) = (tmp_path / "runs").iterdir()
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    scaffold, mvp, review = resolved["phases"]
    assert [p["name"] for p in (scaffold, mvp, review)] == ["scaffold", "mvp", "review"]
    assert scaffold["argv"] == resolved["subject_argv"] and "handoff note at HANDOFF.md" in scaffold["argv"][2]
    assert mvp["argv"][mvp["argv"].index("--resume") + 1] == "<the session of scaffold>"
    assert review["argv"][:5] == ["sh", "-c", PH.IN_FOLDER, "sh", "site"]
    assert resolved["max_spend_usd"] == 20
    assert sorted(p.name for p in run_dir.iterdir()) == ["run.json"]


def test_a_phase_is_told_of_the_target_only_where_its_prompt_names_it():
    scn = S.from_data(phased(phase("read", prompt="Read {target}/spec.md."), phase("review", prompt="Review it.")))
    read, review = scn.subject.phases
    argv = run.phase_argv(scn, read, "swe-guidelines", "/plugin", "/target")
    assert argv[argv.index("-p") + 1] == "Read /target/spec.md." and argv[argv.index("--add-dir") + 1] == "/target"
    argv = run.phase_argv(scn, review, "swe-guidelines", "/plugin", "/target")
    assert argv[argv.index("-p") + 1] == "Review it." and "--add-dir" not in argv
    with pytest.raises(S.ScenarioError, match=r"phase read names \{target\} and the run has no target"):
        run.phase_argv(scn, read, "swe-guidelines", "/plugin", None)


def test_a_phased_run_on_another_machine_commits_hides_the_note_and_fetches_the_zip(tmp_path, run_phases):
    scenario = phased(
        phase("scaffold", {"note": "n", "write": {"site/x.txt": "x"}}, hint=True),
        phase("review", cwd="output"),
        phase("fix", hint=True, cwd="output"),
    )
    config = vm_config(tmp_path, fetch=["cp", "-R", "{remote}/.", "{local}"])
    config_path = tmp_path / "vm.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    code, run_dir = run_phases(scenario, "--repeat", "1", "--runtime", "vm", "--runtime-config", str(config_path))
    assert code == 0
    scaffold, review, fix = seen(run_dir)
    assert scaffold and review and fix
    there = str(tmp_path / "remote" / run_dir.name)
    assert scaffold["home"] == f"{there}/home/0/scaffold" and review["home"] == f"{there}/home/0/review"
    assert review["handoff_in_reach"] == [] and fix["note"] == "n"
    repeat = results(run_dir)["repeats"][0]
    assert all(p["checkpoint"] for p in repeat["phases"])
    with zipfile.ZipFile(run_dir / repeat["archive"]["path"]) as zf:
        assert zf.namelist() == ["x.txt"]
    assert not (tmp_path / "remote" / run_dir.name).exists()  # the run's folder there went at the end


PRICES = {"claude-opus-5-5": {"input": 4.0, "output": 20.0}, "claude-sonnet-5": {"input": 2.0, "output": 10.0}}


def line(event: dict) -> str:
    return json.dumps(event)


def test_the_result_is_the_last_result_line_of_the_stream():
    stream = [
        line({"type": "system", "subtype": "init", "session_id": "s-1"}),
        "not json",
        line({"type": "assistant", "message": {"id": "m", "content": []}, "session_id": "s-1"}),
        line(
            {"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "session_id": "s-1", "modelUsage": {"m": {}}}
        ),
    ]
    result = PH.final_result(stream)
    assert result is not None and PH.cap_of(result) == "spend" and PH.session_id(stream) == "s-1"
    assert PH.cap_of({"subtype": "error_max_turns"}) == "turns" and PH.cap_of({"subtype": "success"}) is None
    # A result a bound ended carries no answer, and says it is an error.
    assert run.read_envelope("\n".join(stream)) == ("", ["m"], True)
    done = [*stream[:3], line({"type": "result", "subtype": "success", "result": "the answer", "total_cost_usd": 0.5})]
    assert run.read_envelope("\n".join(done)) == ("the answer", [], False)
    assert run.read_envelope_spend("\n".join(done))[1] == 0.5
    assert PH.final_result(stream[:3]) is None and PH.session_id(["{}"]) is None


def test_the_estimate_prices_cache_reads_and_writes_and_counts_a_message_once():
    watch = PH.Watch(None, PRICES)
    usage = {
        "input_tokens": 1000,
        "cache_read_input_tokens": 10_000,
        "cache_creation_input_tokens": 2000,
        "cache_creation": {"ephemeral_5m_input_tokens": 1500, "ephemeral_1h_input_tokens": 500},
        "output_tokens": 50,
    }
    event = {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage}}
    watch.feed("out", line(event))
    later = {**usage, "output_tokens": 100}  # the same message again, its output grown
    watch.feed("out", line({"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": later}}))
    watch.feed("err", line(event))  # the harness's own lines are not the session's
    # 1000 x 4 + 10000 x 0.4 + 1500 x 5 + 500 x 8 + 100 x 20, per million
    assert watch.estimated_usd == pytest.approx(0.0215)
    assert watch.usage() == {
        "input_tokens": 13_000,
        "output_tokens": 100,
        "cache_read_input_tokens": 10_000,
        "cache_creation_input_tokens": 2000,
    }
    assert not watch.stop.is_set() and watch.capped is None


def test_a_model_the_matrix_does_not_price_is_priced_at_its_dearest():
    watch = PH.Watch(0.01, PRICES)
    message = {"id": "m", "model": "claude-haiku-9", "usage": {"input_tokens": 1000, "output_tokens": 1000}}
    watch.feed("out", line({"type": "assistant", "message": message}))
    assert watch.estimated_usd == pytest.approx(0.024) and watch.unpriced == {"claude-haiku-9"}
    assert watch.capped == "spend" and watch.stop.is_set()


@pytest.mark.parametrize(
    ("command", "runs"),
    [
        ("make check", True),
        ("cd site && make check 2>&1 | tail -20", True),
        ("(make  check)", True),
        ("make checks", False),
        ("make check-fast", False),
        ("echo make-check", False),
    ],
)
def test_a_gate_is_found_as_whole_words_of_a_command(command, runs):
    assert bool(PH.Watch(None, PRICES, ["make check"]).gates_in({"command": command})) is runs


@pytest.mark.parametrize("cap", ["0", "-1", "inf", "nan"])
def test_a_run_s_spend_cap_is_an_amount_above_zero(tmp_path, monkeypatch, capsys, cap):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(phased(phase("scaffold"))), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--max-spend-usd", cap]) == 2
    assert "--max-spend-usd is an amount in US dollars above 0" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_a_resumed_phase_s_spend_is_what_it_adds_to_its_session_s_running_total(run_phases):
    # Claude Code's result for a resumed session carries the session's total so far.
    scenario = phased(
        phase("scaffold", {"cost": 0.25, "usage": {"input_tokens": 100, "output_tokens": 10}}),
        phase("mvp", {"cost": 0.6, "usage": {"input_tokens": 250, "output_tokens": 30}}, session="resume"),
        phase("review", {"cost": 0.1}),
    )
    code, run_dir = run_phases(scenario, "--repeat", "1", "--max-spend-usd", "0.8")
    assert code == 0
    repeat = results(run_dir)["repeats"][0]
    assert [p["cost_usd"] for p in repeat["phases"]] == [0.25, 0.35, 0.1]
    assert repeat["subject_cost_usd"] == pytest.approx(0.7)
    assert repeat["subject_usage"] == {
        "input_tokens": 260,
        "output_tokens": 50,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    # The run's cap saw 0.25 and 0.35 before the third phase, not 0.25 and 0.6.
    assert repeat["phases"][2]["status"] == "ok"


def test_nothing_under_a_git_folder_is_collected(run_phases):
    scenario = phased(phase("scaffold", {"write": {"site/README.md": "r\n"}}))
    scenario["artifact"] = {"stdout": False, "files": ["**/*"]}
    code, run_dir = run_phases(scenario)
    assert code == 0
    paths = results(run_dir)["repeats"][0]["artifact_paths"]
    assert "artifacts/0/workspace/site/README.md" in paths
    assert not [p for p in paths if "/.git/" in p]
    assert not list((run_dir / "artifacts").rglob(".git"))


def test_an_output_zip_that_cannot_be_scanned_is_kept_as_a_line_and_the_results_are_written(run_phases, monkeypatch):
    monkeypatch.setattr(run.X, "UNPACKED", 4)
    code, run_dir = run_phases(phased(phase("scaffold", {"write": {"site/README.md": "more than four bytes\n"}})))
    assert code == 0
    data = results(run_dir)
    archive = data["repeats"][0]["archive"]
    assert archive["files"] == 0 and (run_dir / archive["path"]).read_bytes() == run.X.UNREADABLE
    assert (run_dir / archive["manifest"]).read_text(encoding="utf-8") == ""
    assert any("does not open as a zip and was replaced by a line that says so" in n for n in data["notes"])
    assert (run_dir / "report.md").is_file()


def test_run_py_redact_names_a_file_it_cannot_redact_and_exits_1(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "runs" / "one"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text("fine\n", encoding="utf-8")
    (folder / "b.md").write_text("fine\n", encoding="utf-8")
    real = run.X.redact_file

    def redact_file(path, values):
        if path.name == "a.md":
            raise PermissionError("denied")
        return real(path, values)

    monkeypatch.setattr(run.X, "redact_file", redact_file)
    assert run.main(["redact", "--out", str(tmp_path / "runs")]) == 1
    assert "could not redact one/a.md: PermissionError: denied" in capsys.readouterr().err
