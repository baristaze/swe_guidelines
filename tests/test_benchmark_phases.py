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


# What a first phase writes so the output folder holds a file; a phase after which it holds none ends the run.
TREE = {"write": {"site/README.md": "r\n"}}


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
    code, run_dir = run_phases(phased(phase("scaffold", TREE), phase("mvp", session="resume"), phase("review")))
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
    turns = {**TREE, "subtype": "error_max_turns", "is_error": True, "exit": 1}
    code, run_dir = run_phases(phased(phase("scaffold", turns), phase("mvp")))
    assert code == 0  # a bound is not a failure: the repeat goes on and is judged
    phases = results(run_dir)["repeats"][0]["phases"]
    assert [(p["status"], p["capped"]) for p in phases] == [("capped", "turns"), ("ok", None)]
    assert "| 0 | scaffold | fresh | capped | turns |" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_a_phase_that_says_stop_ends_the_repeat_at_its_bound(run_phases):
    budget = {**TREE, "subtype": "error_max_budget_usd", "is_error": True, "exit": 1}
    code, run_dir = run_phases(phased(phase("scaffold", budget, on_cap="stop"), phase("mvp")))
    assert code == 0
    data = results(run_dir)
    assert [(p["name"], p["capped"]) for p in data["repeats"][0]["phases"]] == [("scaffold", "spend")]
    assert any("ended at its spend cap, and it stops the repeat there" in n for n in data["notes"])


def test_the_harness_stops_a_phase_whose_stream_passes_its_spend_cap(run_phases):
    usage = {"input_tokens": 150_000, "output_tokens": 0}  # $0.60 at the matrix's price
    act = {**TREE, "messages": [["m1", "claude-opus-5-5", usage], ["m2", "claude-opus-5-5-20260901", usage]], "sleep": 30}
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
    code, run_dir = run_phases(phased(phase("scaffold", {**TREE, "bash": failing, "sleep": 30}), gates=["make check"]))
    assert code == 0
    first = results(run_dir)["repeats"][0]["phases"][0]
    assert (first["status"], first["capped"]) == ("capped", "gate_reruns")
    assert first["gate_runs"] == {"make check": {"runs": 4, "failed": 4, "unread": 0, "failing": True}}


def test_a_passing_gate_run_starts_the_count_again(run_phases):
    # A failed run and two reruns that fail would pass the bound; a pass between them ends the streak.
    runs = [["make check", True]] * 2 + [["make check", False]] + [["make check", True]] * 2
    code, run_dir = run_phases(phased(phase("scaffold", {**TREE, "bash": runs}, max_gate_reruns=2), gates=["make check"]))
    assert code == 0
    first = results(run_dir)["repeats"][0]["phases"][0]
    assert (first["status"], first["capped"]) == ("ok", None)
    assert first["gate_runs"]["make check"] == {"runs": 5, "failed": 4, "unread": 0, "failing": True}


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
    assert "[gate] exit 3" in harness and "[checkpoint 1] after scaffold, ok" in harness


def test_the_run_s_spend_cap_is_checked_before_each_phase_and_each_repeat(run_phases):
    # Each phase spends $0.25, past its own $0.10 cap, so the run's cap is reached inside the repeat.
    scenario = phased(*(phase(n, TREE if n == "scaffold" else None, max_usd=0.1) for n in ("scaffold", "mvp", "review")))
    code, run_dir = run_phases(scenario, "--repeat", "2", "--max-spend-usd", "0.3")
    assert code == 0
    data = results(run_dir)
    assert [[p["name"] for p in r["phases"]] for r in data["repeats"]] == [["scaffold", "mvp"]]
    assert any("$0.3 was reached at $0.5000; phase review and after did not run" in n for n in data["notes"])
    assert any("repeat 1 and after did not run" in n for n in data["notes"])
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["max_spend_usd"] == 0.3


def test_a_run_takes_the_scenario_s_repeats_and_spend_cap_unless_a_flag_overrides_them(run_phases):
    three = (phase(n, TREE if n == "scaffold" else None, max_usd=0.1) for n in ("scaffold", "mvp", "review"))
    scenario = dict(phased(*three), repeat=2, max_spend_usd=0.3)
    # No --repeat and no --max-spend-usd: the scenario's cap stops the run as the flag's would.
    code, run_dir = run_phases(scenario, "--runtime", "host")
    assert code == 0
    data = results(run_dir)
    assert [[p["name"] for p in r["phases"]] for r in data["repeats"]] == [["scaffold", "mvp"]]
    assert any("$0.3 was reached at $0.5000; phase review and after did not run" in n for n in data["notes"])
    assert any("repeat 1 and after did not run" in n for n in data["notes"])
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert (resolved["repeat"], resolved["max_spend_usd"]) == (2, 0.3)
    # The flags override both: one repeat, under a cap it does not reach.
    code, run_dir = run_phases(scenario, "--repeat", "1", "--max-spend-usd", "5")
    assert code == 0
    data = results(run_dir)
    assert [[p["name"] for p in r["phases"]] for r in data["repeats"]] == [["scaffold", "mvp", "review"]]
    assert not any("spend cap" in n for n in data["notes"])
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert (resolved["repeat"], resolved["max_spend_usd"]) == (1, 5.0)


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
    argv = scaffold["argv"]
    assert argv == resolved["subject_argv"] and "handoff note at HANDOFF.md" in argv[argv.index("-p") + 1]
    # The subject runs under env, with its subagents and commands in the foreground, on every runtime.
    assert argv[:3] == ["env", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1", "claude"]
    assert review["argv"][5:8] == ["env", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1", "claude"]
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
        phase("scaffold", {**TREE, "cost": 0.25, "usage": {"input_tokens": 100, "output_tokens": 10}}, max_usd=0.25),
        phase("mvp", {"cost": 0.6, "usage": {"input_tokens": 250, "output_tokens": 30}}, session="resume", max_usd=0.25),
        phase("review", {"cost": 0.1}, max_usd=0.25),
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


@pytest.mark.parametrize(
    ("command", "runs"),
    [
        ("make check", [("make check", True)]),
        ("cd site && make check 2>&1", [("make check", True)]),
        ("echo start; make check", [("make check", True)]),
        ("(make check)", [("make check", True)]),
        ("A=1 make check -j4", [("make check", True)]),
        ("make check && make test-integration", [("make check", True), ("make test-integration", True)]),
        ("make check 2>&1 | tail -30", [("make check", False)]),
        ("make check; echo done", [("make check", False)]),
        ("make check || true", [("make check", False)]),
        ("(make check) | tail", [("make check", False)]),
        ("make check &", [("make check", False)]),
        ("make check\nmake test-integration", [("make check", False), ("make test-integration", True)]),
        ("echo make check", []),
        ('git commit -m "fix: make check passes"', []),
        ("make checks", []),
        ("make check-fast", []),
        ("echo 'unclosed", []),
    ],
)
def test_a_gate_run_is_the_gate_as_a_command_of_its_own_and_its_outcome_is_read_only_when_it_is_the_call_s(command, runs):
    assert PH.gate_runs(command, ["make check", "make test-integration"]) == runs


def test_a_run_whose_outcome_is_not_read_neither_fails_nor_passes(run_phases):
    piped = [["make check 2>&1 | tail -30", True]] * 6
    code, run_dir = run_phases(phased(phase("scaffold", {**TREE, "bash": piped}), gates=["make check"]))
    assert code == 0
    first = results(run_dir)["repeats"][0]["phases"][0]
    assert (first["status"], first["gate_runs"]["make check"]) == ("ok", {"runs": 6, "failed": 0, "unread": 6, "failing": False})
    # A call that only names the gate is no gate run, so it ends no streak.
    named = [["make check", True]] * 2 + [["echo make check", False], ['git commit -m "make check passes"', False]]
    act = {**TREE, "bash": [*named, ["make check", True]], "sleep": 30}
    code, run_dir = run_phases(phased(phase("scaffold", act, max_gate_reruns=2), gates=["make check"]))
    first = results(run_dir)["repeats"][0]["phases"][0]
    assert (first["status"], first["capped"]) == ("capped", "gate_reruns")
    assert first["gate_runs"]["make check"] == {"runs": 3, "failed": 3, "unread": 0, "failing": True}


def test_a_checkpoint_leaves_the_branch_head_and_index_as_the_subject_left_them(run_phases):
    log = ["log", "--all", "--format=%an|%s"]
    scenario = phased(
        phase(
            "scaffold",
            {
                "write": {"site/README.md": "r\n", "site/draft.md": "d\n"},
                "git": [
                    ["-C", "site", "init", "-q"],
                    ["-C", "site", "add", "README.md"],
                    ["-C", "site", "-c", "user.name=builder", "-c", "user.email=b@localhost", "commit", "-qm", "own work"],
                ],
                # Left behind as a stopped git would leave it.
                "touch": ["site/.git/index.lock"],
            },
        ),
        phase(
            "review",
            {"write": {"notes.md": "n"}, "git": [log, ["log", "--format=%s"], ["add", "notes.md"], ["status", "--porcelain"]]},
            cwd="output",
        ),
    )
    code, run_dir = run_phases(scenario)
    assert code == 0
    _, review = seen(run_dir)
    assert review
    everything, branch, _, status = review["git"]
    # The branch holds the subject's own commit only; the checkpoints are named nothing but `checkpoint`.
    assert branch == "own work"
    assert sorted(everything.splitlines()) == ["builder|own work", "checkpoint|checkpoint"]
    # The index is the subject's, draft.md still untracked; and the stale lock went, so `git add` works.
    assert sorted(status.splitlines()) == ["?? draft.md", "A  notes.md"]
    repeat = results(run_dir)["repeats"][0]
    assert all(p["checkpoint"] for p in repeat["phases"])
    with zipfile.ZipFile(run_dir / repeat["archive"]["path"]) as zf:
        assert sorted(zf.namelist()) == ["README.md", "draft.md", "notes.md"]


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


def test_a_repeat_the_run_s_spend_cap_cuts_short_is_marked_and_not_judged(tmp_path, monkeypatch, run_phases):
    judged: list[str] = []

    def judge_all(*args, **kwargs):
        judged.append("called")
        return []

    monkeypatch.setattr(run.J, "judge_all", judge_all)
    three = (phase(n, TREE if n == "scaffold" else None, max_usd=0.1) for n in ("scaffold", "mvp", "review"))
    code, run_dir = run_phases(phased(*three), "--repeat", "1", "--max-spend-usd", "0.3")
    assert code == 0 and judged == []
    data = results(run_dir)
    assert data["repeats"][0]["cut_short"] == ["review"] and data["summary"]["cut_short"] == [0]
    assert data["summary"]["failed_repeats"] == [] and data["summary"]["overall_mean"] is None
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Repeat 0 was cut short by the run's spend cap: review did not run. It is not judged" in report


def test_a_listener_that_fails_on_a_line_does_not_end_the_reading(tmp_path):
    seen_lines: list[str] = []

    def listener(stream, line):
        if line == "bad":
            raise KeyError("boom")
        seen_lines.append(line)

    with CliStream(tmp_path / "cli.jsonl") as streams:
        streams.listener = listener
        for line in ("one", "bad", "two"):
            streams.write("out", line)
    assert seen_lines == ["one", "two"]
    lines = [r["line"] for r in CliStream.read(tmp_path / "cli.jsonl")]
    assert lines[:2] == ["one", "bad"] and lines[-1] == "two"
    assert any("listener failed on a line and read on: KeyError" in line for line in lines)


def test_a_usage_field_that_changes_shape_between_lines_is_read_and_the_total_kept_as_it_goes():
    watch = PH.Watch(None, PRICES)
    first = {"id": "m", "model": "claude-opus-5-5", "usage": {"input_tokens": 1000, "cache_creation": 5}}
    later = {
        "id": "m",
        "model": "claude-opus-5-5",
        "usage": {"input_tokens": 1000, "cache_creation": {"ephemeral_1h_input_tokens": 0}},
    }
    watch.feed("out", line({"type": "assistant", "message": first}))
    watch.feed("out", line({"type": "assistant", "message": later}))
    watch.feed("out", line({"type": "assistant", "message": {**later, "id": "n"}}))
    assert watch.estimated_usd == pytest.approx(0.008)  # two messages of 1000 input tokens, the first counted once


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


def test_an_agentic_judge_reads_the_archived_tree_as_the_output(run_phases, monkeypatch):
    seen_roots: dict[str, list[str]] = {}

    def judge_all(flags, prompt, roots, *args, **kwargs):
        for name, root in roots.items():
            seen_roots[name] = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        return []

    monkeypatch.setattr(run.RF, "judge_all", judge_all)
    scenario = phased(
        phase("scaffold", {"write": {"site/README.md": "one\n", "stray.txt": "x"}}),
        phase("mvp", {"write": {"site/app/main.py": "print(2)\n"}}),
    )
    scenario["judges"] = {
        "providers": "anthropic",
        "mode": "agentic",
        "references": [{"name": "guideline", "weight": 1, "paths": ["lenses/README.md"]}],
    }
    code, run_dir = run_phases(scenario)
    assert code == 0
    # The last commit of the output folder, and nothing else of the workspace.
    assert seen_roots == {"output": ["README.md", "app/main.py"], "guideline": ["lenses/README.md"]}
    prompt = (run_dir / "artifacts" / "0" / "judge-prompt.md").read_text(encoding="utf-8")
    assert "- `output`: the tree the subject built, its output folder as its last commit holds it." in prompt


# Optional groups of phases --------------------------------------------------


def grouped(**subject) -> dict:
    """The build by default, and a review in the group `extras`, whose sentence the rubric takes when a run takes it."""
    return phased(
        phase("scaffold", TREE),
        phase("review", {"write": {"notes.md": "n"}}, cwd="output", group="extras"),
        groups={"extras": {"rubric": "Then a review read the tree."}},
        **subject,
    )


def test_a_run_takes_a_group_only_with_the_flag_and_names_the_groups_it_took(run_phases):
    code, run_dir = run_phases(grouped())
    assert code == 0
    assert [p["name"] for p in results(run_dir)["repeats"][0]["phases"]] == ["scaffold"]
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["groups"] == [] and [p["name"] for p in resolved["phases"]] == ["scaffold"]
    assert results(run_dir)["subject"]["groups"] == []
    assert "Optional groups taken: none." in (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Then a review read the tree." not in (run_dir / "artifacts" / "0" / "judge-prompt.md").read_text(encoding="utf-8")

    code, run_dir = run_phases(grouped(), "--repeat", "1", "--with", "extras")
    assert code == 0
    data = results(run_dir)
    assert [p["name"] for p in data["repeats"][0]["phases"]] == ["scaffold", "review"]
    assert data["subject"]["groups"] == ["extras"] and [p["group"] for p in data["subject"]["phases"]] == [None, "extras"]
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["groups"] == ["extras"] and resolved["scenario"]["rubric"] == "r\n\nThen a review read the tree."
    assert "Optional groups taken: `extras`." in (run_dir / "report.md").read_text(encoding="utf-8")
    prompt = (run_dir / "artifacts" / "0" / "judge-prompt.md").read_text(encoding="utf-8")
    assert "r\n\nThen a review read the tree." in prompt and "2. review (fresh session)" in prompt


def test_a_scenario_with_no_groups_names_none_in_its_records(run_phases):
    code, run_dir = run_phases(phased(phase("scaffold", TREE)))
    assert code == 0
    assert "groups" not in json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert "groups" not in results(run_dir)["subject"]
    assert "Optional groups" not in (run_dir / "report.md").read_text(encoding="utf-8")


def test_a_group_the_scenario_does_not_declare_is_refused_before_a_run_folder_is_made(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(grouped()), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--with", "mvp", "--dry-run"]) == 2
    assert "scenario system declares no group mvp; its groups: extras" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    ("flags", "cap"),
    [
        ((), 1.0),  # the scaffold's cap
        (("--with", "extras"), 2.0),  # the scaffold's and the review's
        (("--with", "extras", "--repeat", "2"), 4.0),  # every phase of every repeat
        (("--with", "extras", "--max-spend-usd", "7"), 7.0),  # the flag names one for any path
    ],
)
def test_a_run_s_spend_cap_is_the_sum_of_the_caps_of_the_phases_it_runs_unless_a_flag_names_one(
    tmp_path, monkeypatch, flags, cap
):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(dict(grouped(), repeat=1)), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--dry-run", *flags]) == 0
    (run_dir,) = (tmp_path / "runs").iterdir()
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["max_spend_usd"] == cap


def test_the_scenario_s_own_spend_cap_holds_only_on_the_path_that_takes_no_group(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(dict(grouped(), repeat=1, max_spend_usd=0.5)), encoding="utf-8")
    caps: list[float] = []
    for flags in ((), ("--with", "extras")):
        out = tmp_path / "runs" / str(len(caps))
        assert run.main(["--scenario", str(path), "--out", str(out), "--dry-run", *flags]) == 0
        (run_dir,) = out.iterdir()
        caps.append(json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["max_spend_usd"])
    assert caps == [0.5, 2.0]


# A phase that leaves no tree ---------------------------------------------------


@pytest.mark.parametrize(
    "first",
    [
        {},  # no output folder at all
        {"git": [["init", "-q", "site"]]},  # a folder that holds a repository and no file
    ],
    ids=["no-folder", "empty-folder"],
)
def test_a_phase_that_leaves_no_tree_ends_the_run_unjudged(run_phases, monkeypatch, first):
    judged: list[str] = []

    def judge_all(*args, **kwargs):
        judged.append("called")
        return []

    monkeypatch.setattr(run.J, "judge_all", judge_all)
    scenario = phased(phase("scaffold", first), phase("mvp", TREE), phase("review", cwd="output"))
    code, run_dir = run_phases(scenario, "--repeat", "2")
    assert code == 6 and judged == []  # the subject built nothing, so the repeat failed and no judge was asked
    data = results(run_dir)
    (repeat,) = data["repeats"]  # the run ends: the second repeat never starts
    assert [p["name"] for p in repeat["phases"]] == ["scaffold"] and len(seen(run_dir)) == 1
    assert repeat["no_tree"] == {"phase": "scaffold", "not_run": ["mvp", "review"]}
    assert data["summary"]["failed_repeats"] == [0] and data["summary"]["cut_short"] == []
    assert any(
        "phase scaffold left no file in site; mvp, review did not run, and the repeat is not judged" in n for n in data["notes"]
    )
    assert any("repeat 0 left no tree, so the run ends: repeat 1 and after did not run" in n for n in data["notes"])
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Repeat 0 ended after phase scaffold, which left no file in the output folder. mvp, review did not run." in report


def test_a_last_phase_that_leaves_no_tree_fails_its_repeat_too(run_phases):
    code, run_dir = run_phases(phased(phase("scaffold")))
    assert code == 6
    assert results(run_dir)["repeats"][0]["no_tree"] == {"phase": "scaffold", "not_run": []}


def test_a_phase_s_wall_time_is_in_its_record_and_the_report(run_phases):
    code, run_dir = run_phases(phased(phase("scaffold", {**TREE, "sleep": 0.3})))
    assert code == 0
    (record,) = results(run_dir)["repeats"][0]["phases"]
    assert record["wall_s"] >= 0.3 and record["wall_s"] == record["exit_status"]["duration_s"]
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "| Repeat | Phase | Session | Status | Cap | Turns | Wall (s) | Cost (USD) | Checkpoint |" in report
    assert f"| 0 | scaffold | fresh | ok | - | 3 | {record['wall_s']:.1f} | $0.2500 |" in report


# The run's spend cap and a repeat ---------------------------------------------


def test_a_repeat_starts_only_when_what_is_left_of_the_cap_covers_its_phases(run_phases, monkeypatch):
    # Two phases capped at $1 each and two repeats: the cap is $4. The first repeat's subject spends $0.50 and its
    # judge $2, so $1.50 is left, less than the $2 the second repeat's phases may spend: it does not start.
    def judge_all(*args, **kwargs):
        usage = {"input_tokens": 1000, "output_tokens": 100}
        return [run.J.Judgement(provider="anthropic", model="claude-opus-5-5", effort="medium", usage=usage, cost_usd=2.0)]

    monkeypatch.setattr(run.J, "judge_all", judge_all)
    code, run_dir = run_phases(phased(phase("scaffold", TREE), phase("mvp")), "--repeat", "2")
    assert code == 0
    data = results(run_dir)
    (repeat,) = data["repeats"]
    assert [p["name"] for p in repeat["phases"]] == ["scaffold", "mvp"] and "cut_short" not in repeat
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["max_spend_usd"] == 4.0
    assert any(
        "repeat 1 and after did not run: $1.5000 of the run's $4 spend cap is left, and the phases of a repeat may spend $2" in n
        for n in data["notes"]
    )
    assert len(seen(run_dir)) == 2  # no session of the second repeat started


def test_a_run_whose_cap_covers_no_repeat_starts_none_and_says_why(run_phases):
    code, run_dir = run_phases(phased(phase("scaffold", TREE), phase("mvp")), "--repeat", "1", "--max-spend-usd", "1.5")
    assert code == 0
    data = results(run_dir)
    assert data["repeats"] == [] and seen(run_dir) == []
    assert any("repeat 0 and after did not run: $1.5000 of the run's $1.5 spend cap is left" in n for n in data["notes"])


# The subject's background tasks ------------------------------------------------


def test_the_subject_runs_with_its_background_tasks_off_on_the_host_and_on_another_machine(tmp_path, run_phases):
    code, run_dir = run_phases(phased(phase("scaffold", TREE), phase("review", cwd="output")))
    assert code == 0
    assert [s["background_tasks"] if s else None for s in seen(run_dir)] == ["1", "1"]
    one = {
        "name": "one",
        "kind": "skill",
        "subject": {"skill": "arch-explain", "prompt": do({}), "max_usd": 0.75, "max_turns": 4},
        "rubric": "r",
        "runtimes": ["host"],
        "judges": {"providers": "anthropic"},
    }
    code, run_dir = run_phases(one)
    assert code == 0 and [s["background_tasks"] if s else None for s in seen(run_dir)] == ["1"]
    # The vm prefix here starts each command in an empty environment, as a remote shell does: the variable
    # reaches the subject through its command, not through this machine's environment.
    config_path = tmp_path / "vm.json"
    config_path.write_text(json.dumps(vm_config(tmp_path)), encoding="utf-8")
    scenario = phased(phase("scaffold", TREE), phase("review", cwd="output"))
    code, run_dir = run_phases(scenario, "--repeat", "1", "--runtime", "vm", "--runtime-config", str(config_path))
    assert code == 0 and [s["background_tasks"] if s else None for s in seen(run_dir)] == ["1", "1"]


def test_a_container_runs_the_subject_under_env_with_its_background_tasks_off(tmp_path):
    scn = S.from_data(phased(phase("scaffold"), phase("review", cwd="output")))
    rt = run.RT.build("container", tmp_path, None, {"image": "img:1"})
    rt.prepare()
    for one in scn.subject.phases:
        command = rt.command(run.phase_argv(scn, one, "swe-guidelines", "/plugin", None), rt.workspace)
        inside = command[command.index("img:1") + 1 :]  # what the container runs
        at = inside.index("env")
        assert inside[at : at + 3] == ["env", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1", "claude"]
