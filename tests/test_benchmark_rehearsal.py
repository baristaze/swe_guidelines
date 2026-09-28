"""benchmark/harness/rehearsal.py and `run.py --rehearsal`: the scenario as it will really run, every bound cut small.

The subject is `benchmark_fake_claude.py` on the host runtime, as in the
phase tests. The preflight a rehearsal runs first reads fakes: the model
lists answer 200, the checkout is clean unless a test says otherwise, and
`curl` answers 200.
"""

import json
from pathlib import Path

import pytest

from harness import agentic as A
from harness import judge as J
from harness import rehearsal as RH
from harness import scenario as S
from test_benchmark_phases import do, phase, phased, results, seen
from test_benchmark_phases import fake_claude as phase_claude
from test_benchmark_run import CLEAN, run

SHIPPED = Path(__file__).resolve().parent.parent / "benchmark" / "scenarios"


# The rehearsal's scenario ---------------------------------------------------


def test_a_rehearsal_of_the_shipped_system_keeps_its_prompts_and_cuts_every_bound():
    scn = S.load(SHIPPED / "create-full-system.yaml")
    small = RH.scenario(scn, J.load_matrix(None))
    assert [p.prompt for p in small.subject.phases] == [p.prompt for p in scn.subject.phases]
    for p in small.subject.phases:
        assert (p.max_turns, p.max_usd, p.timeout_s, p.on_cap) == (RH.MAX_TURNS, RH.SESSION_USD, RH.TIMEOUT_S, "continue")
    assert small.subject.model == "claude-sonnet-5"  # the cheapest Anthropic model the matrix prices
    assert small.subject.gate_timeout_s == RH.GATE_TIMEOUT_S and small.subject.gates == scn.subject.gates
    assert (small.repeat, small.max_spend_usd) == (1, RH.MAX_SPEND_USD)
    assert small.judges.budget == RH.JUDGE_BUDGET
    assert (small.judges.providers, small.judges.effort, small.judges.references) == (
        scn.judges.providers,
        scn.judges.effort,
        scn.judges.references,
    )
    assert (small.runtimes, small.requires, small.preflight, small.rubric) == (
        scn.runtimes,
        scn.requires,
        scn.preflight,
        scn.rubric,
    )


def test_a_rehearsal_never_raises_a_bound_the_scenario_sets_lower():
    scn = S.from_data(
        dict(
            phased(phase("scaffold", max_turns=2, max_usd=0.1, timeout_s=60, on_cap="stop"), gate_timeout_s=30),
            max_spend_usd=2,
            judges={
                "providers": "anthropic",
                "mode": "agentic",
                "budget": {"tool_calls": 1, "max_usd": 0.2},
                "references": [{"name": "guideline", "weight": 1, "paths": ["lenses"]}],
            },
        )
    )
    small = RH.scenario(scn, J.load_matrix(None))
    (only,) = small.subject.phases
    assert (only.max_turns, only.max_usd, only.timeout_s, only.on_cap) == (2, 0.1, 60, "continue")
    assert small.subject.gate_timeout_s == 30 and small.max_spend_usd == 2
    assert small.judges.budget is not None and (small.judges.budget.tool_calls, small.judges.budget.max_usd) == (1, 0.2)
    assert small.judges.budget.input_tokens == RH.JUDGE_BUDGET.input_tokens


def test_a_one_session_skill_and_a_qa_subject_take_the_cheapest_model_of_their_provider():
    matrix = J.load_matrix(None)
    skill = S.from_data(
        {**phased(phase("x")), "subject": {"skill": "arch-explain", "prompt": "Why?", "max_usd": 2, "max_turns": 14}}
    )
    small = RH.scenario(skill, matrix)
    assert (small.subject.max_turns, small.subject.max_usd, small.subject.model) == (
        RH.MAX_TURNS,
        RH.SESSION_USD,
        "claude-sonnet-5",
    )
    qa = S.from_data(
        {"name": "q", "kind": "qa", "subject": {"prompt": "Why?", "provider": "gemini"}, "rubric": "r", "runtimes": ["host"]}
    )
    assert RH.scenario(qa, matrix).subject.model == "gemini-3.8-flash"


def test_the_cheapest_model_is_the_lowest_input_price_then_output():
    matrix = {
        "anthropic": {
            "model": "a",
            "fallbacks": ["b", "c", "unpriced"],
            "prices": {"a": {"input": 3, "output": 10}, "b": {"input": 1, "output": 20}, "c": {"input": 1, "output": 5}},
        }
    }
    assert RH.cheapest(matrix, "anthropic") == "c"
    assert RH.cheapest({"anthropic": {"model": "a"}}, "anthropic") is None


def test_the_stub_budget_is_small_enough_that_four_judges_leave_the_subject_its_share():
    # Four judges at their stub dollar bound, and one call past it each, still leave the phases room under the cap.
    assert 4 * RH.JUDGE_BUDGET.max_usd + 4 * RH.SESSION_USD <= RH.MAX_SPEND_USD
    assert RH.JUDGE_BUDGET.tool_calls < A.Budget().tool_calls


# A rehearsal run -------------------------------------------------------------


@pytest.fixture
def rehearse(tmp_path, monkeypatch):
    """Run a scenario with `--rehearsal` and the fake Claude Code; its exit code, its run folder, and what the judges got.

    The judge answers with a spend of its own, so the rehearsal has a judge
    to say its money went to.
    """
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.PF, "http_status", lambda url, headers, timeout_s=20: 200)
    monkeypatch.setattr(run.PF, "system", lambda: "linux")
    monkeypatch.setattr(run.V, "checkout", lambda root, scope: dict(CLEAN))
    monkeypatch.setenv("SUBJECT_ANTHROPIC_API_KEY", "subject-key-of-its-own")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "judge-key")
    folder = tmp_path / "bin"
    folder.mkdir()
    curl = folder / "curl"
    curl.write_text("#!/bin/sh\nprintf 200\n", encoding="utf-8")
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    judged: list[str] = []

    def judge_all(*args, **kwargs):
        judged.append("judged")
        usage = {"input_tokens": 1000, "output_tokens": 100}
        return [J.Judgement(provider="anthropic", model="claude-opus-5-5", effort="medium", usage=usage, cost_usd=0.006)]

    monkeypatch.setattr(run.J, "judge_all", judge_all)

    def go(scenario: dict, *extra: str) -> tuple[int, Path, list[str]]:
        path = tmp_path / "scenario.json"
        path.write_text(json.dumps(scenario), encoding="utf-8")
        out = tmp_path / "runs" / str(len(list((tmp_path / "runs").glob("*"))) if (tmp_path / "runs").exists() else 0)
        argv = ["--scenario", str(path), "--out", str(out), "--claude", phase_claude(tmp_path), "--rehearsal", *extra]
        code = run.main(argv)
        folders = list(out.iterdir()) if out.exists() else []
        assert len(folders) <= 1
        return code, folders[0] if folders else out, judged

    return go


def test_a_rehearsal_runs_every_phase_small_after_its_preflight_and_says_where_the_money_went(rehearse, capsys):
    scenario = dict(
        phased(
            phase("scaffold", {"write": {"site/README.md": "r"}}, max_turns=400, max_usd=60, timeout_s=14400),
            phase("review", cwd="output", max_turns=150, max_usd=25, timeout_s=7200),
            gates=["test -f README.md"],
        ),
        repeat=3,
        max_spend_usd=190,
    )
    code, run_dir, judged = rehearse(scenario)
    assert code == 0 and judged == ["judged"]
    for session in seen(run_dir):
        assert session is not None
        args = session["args"]
        assert args[args.index("--max-turns") + 1] == "5" and args[args.index("--max-budget-usd") + 1] == "0.5"
        assert args[args.index("--model") + 1] == "claude-sonnet-5"
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["rehearsal"] is True and (resolved["repeat"], resolved["max_spend_usd"]) == (1, 5.0)
    assert resolved["preflight"]["passed"] is True
    assert next(c for c in resolved["preflight"]["checks"] if c["name"] == "checkout")["status"] == "skip"
    data = results(run_dir)
    (repeat,) = data["repeats"]
    assert [p["name"] for p in repeat["phases"]] == ["scaffold", "review"] and all(p["checkpoint"] for p in repeat["phases"])
    assert repeat["archive"]["files"] == 1 and repeat["gates"][0]["passed"] is True
    assert data["rehearsal"] == {
        "status": "completed",
        "missing": [],
        "max_spend_usd": 5.0,
        "spent_usd": 0.506,
        "spent": [
            {"what": "phase scaffold", "usd": 0.25},
            {"what": "phase review", "usd": 0.25},
            {"what": "judge anthropic", "usd": 0.006},
        ],
    }
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## Rehearsal" in report and "It ended `completed`, having spent $0.5060 of its $5.0000 cap:" in report
    out = capsys.readouterr().out
    assert "preflight passed" in out
    assert "the rehearsal ended completed, having spent $0.5060 of its $5 cap: phase scaffold $0.2500" in out


def test_a_rehearsal_whose_preflight_fails_spends_nothing(rehearse, monkeypatch, capsys):
    monkeypatch.delenv("SUBJECT_ANTHROPIC_API_KEY")
    code, run_dir, judged = rehearse(phased(phase("scaffold")))
    assert code == run.PREFLIGHT_FAILED and judged == []
    assert not (run_dir / "streams" / "cli.jsonl").exists() and not (run_dir / "results.json").exists()
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["rehearsal"] is True and resolved["preflight"]["failed"] == "subject_key"


def test_a_rehearsal_skips_the_checkout_check_and_a_preflight_of_the_real_run_does_not(rehearse, monkeypatch):
    dirty = dict(CLEAN, dirty=True, dirty_paths=["benchmark/run.py"])
    monkeypatch.setattr(run.V, "checkout", lambda root, scope: dict(dirty))
    code, run_dir, _ = rehearse(phased(phase("scaffold")), "--preflight")
    assert code == 0
    checks = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["preflight"]["checks"]
    checkout = next(c for c in checks if c["name"] == "checkout")
    assert checkout["status"] == "skip" and checkout["detail"].startswith("a rehearsal is never checked in")
    assert not (run_dir / "streams" / "cli.jsonl").exists()  # a preflight of a rehearsal runs nothing either
    path = run_dir.parent / "real.json"
    path.write_text(json.dumps(dict(phased(phase("scaffold")), max_spend_usd=5)), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(run_dir.parent), "--preflight"]) == run.PREFLIGHT_FAILED


def test_a_rehearsal_the_run_s_spend_cap_cuts_short_ends_capped_and_exits_9(rehearse, capsys):
    code, run_dir, judged = rehearse(phased(phase("scaffold"), phase("mvp"), phase("review")), "--max-spend-usd", "0.3")
    assert code == run.REHEARSAL_UNPROVEN == 9 and judged == []
    data = results(run_dir)
    assert data["repeats"][0]["cut_short"] == ["review"]
    assert data["rehearsal"]["status"] == "capped" and data["rehearsal"]["max_spend_usd"] == 0.3
    assert [s["what"] for s in data["rehearsal"]["spent"]] == ["phase scaffold", "phase mvp"]
    captured = capsys.readouterr()
    assert "the rehearsal ended capped, having spent $0.5000 of its $0.3 cap" in captured.out
    assert "the rehearsal did not prove the pipeline to its end: the run's spend cap cut it short" in captured.err


def fail_checkpoints(monkeypatch, *numbers: int) -> None:
    """The checkpoint after each phase of these numbers fails, as a git that fails there would."""
    real = run.checkpoint

    def checkpoint(rt, harness, plan, folder, number, label):
        if number in numbers:
            return None, "the checkpoint commit failed (exit 4); see streams/harness.jsonl"
        return real(rt, harness, plan, folder, number, label)

    monkeypatch.setattr(run, "checkpoint", checkpoint)


def no_zip_back(monkeypatch) -> None:
    """The runtime brings the workspace back without the output's zip, as a fetch that lost it would."""
    real = run.RT.BaseRuntime.collect
    monkeypatch.setattr(
        run.RT.BaseRuntime, "collect", lambda self, globs: [f for f in real(self, globs) if f.name != "output.zip"]
    )


def no_judge_answers(monkeypatch) -> None:
    """Every judge fails, and none answers."""
    failed = J.Judgement(provider="anthropic", model="claude-opus-5-5", effort="medium", status="error", error="refused")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [failed])


@pytest.mark.parametrize(
    ("break_it", "step"),
    [
        (lambda mp: fail_checkpoints(mp, 1), "phase scaffold left no checkpoint"),
        (lambda mp: mp.setattr(run.PH, "ARCHIVE_SCRIPT", "exit 3"), "the archive of the output failed"),
        (no_zip_back, "no archive of the output came back from the runtime"),
        (lambda mp: fail_checkpoints(mp, 1, 2), "the gates did not run"),
        (no_judge_answers, "no judge answered"),
    ],
    ids=["checkpoint", "archive", "fetch", "gates", "judges"],
)
def test_a_rehearsal_whose_step_did_not_happen_ends_incomplete_and_names_it(rehearse, monkeypatch, capsys, break_it, step):
    break_it(monkeypatch)
    scenario = phased(
        phase("scaffold", {"write": {"site/README.md": "r"}}),
        phase("review", {"write": {"notes.md": "n"}}, cwd="output"),
        gates=["test -f README.md"],
    )
    code, run_dir, _ = rehearse(scenario)
    assert code == run.REHEARSAL_UNPROVEN
    record = results(run_dir)["rehearsal"]
    assert record["status"] == "incomplete" and step in record["missing"]
    assert f"It did not prove the pipeline to its end:\n\n- {record['missing'][0]}" in (run_dir / "report.md").read_text(
        encoding="utf-8"
    )
    captured = capsys.readouterr()
    assert "the rehearsal ended incomplete, having spent" in captured.out and "; it did not prove: " in captured.out
    assert step in captured.out
    assert "the rehearsal did not prove the pipeline to its end: a step it exists to prove did not happen" in captured.err


def test_a_rehearsal_makes_the_output_folder_a_phase_left_out_so_the_phases_after_it_run(rehearse):
    scenario = phased(
        phase("scaffold"),  # writes nothing: a session this small can end before it makes the folder
        phase("review", {"write": {"report.md": "r"}}, cwd="output"),
        gates=["test -f report.md"],
    )
    code, run_dir, judged = rehearse(scenario)
    assert code == 0 and judged == ["judged"]
    data = results(run_dir)
    (repeat,) = data["repeats"]
    assert [p["status"] for p in repeat["phases"]] == ["ok", "ok"] and all(p["checkpoint"] for p in repeat["phases"])
    assert repeat["archive"]["files"] == 1 and repeat["gates"][0]["passed"] is True
    assert any("phase scaffold left no site, so the rehearsal made it, empty" in n for n in data["notes"])
    assert not any("phase review left no site" in n for n in data["notes"])


def test_a_run_that_is_not_a_rehearsal_makes_no_output_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(phased(phase("scaffold"), phase("review", cwd="output"))), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--claude", phase_claude(tmp_path), "--repeat", "1"]
    assert run.main(argv) == 6  # the second phase has no folder to start in
    (run_dir,) = (tmp_path / "runs").iterdir()
    assert "rehearsal" not in results(run_dir)


@pytest.mark.parametrize(
    ("flags", "says"),
    [
        (("--repeat", "2"), "a rehearsal runs one repeat, and --repeat asks for 2"),
        (("--max-spend-usd", "6"), "a rehearsal spends at most $5; --max-spend-usd can lower that, and 6 would raise it"),
    ],
)
def test_a_rehearsal_refuses_a_flag_that_would_raise_its_bounds(rehearse, capsys, flags, says):
    code, run_dir, judged = rehearse(phased(phase("scaffold")), *flags)
    assert code == 2 and judged == [] and not run_dir.exists()  # no run folder is made
    assert says in capsys.readouterr().err


def test_a_rehearsal_takes_the_subject_model_a_flag_names(rehearse):
    code, run_dir, _ = rehearse(phased(phase("scaffold", {"write": {"site/a": "a"}})), "--subject-model", "claude-opus-5-5")
    assert code == 0
    (session,) = seen(run_dir)
    assert session is not None and session["args"][session["args"].index("--model") + 1] == "claude-opus-5-5"


def test_a_dry_run_of_a_rehearsal_resolves_its_bounds_and_runs_nothing(rehearse):
    code, run_dir, judged = rehearse(phased(phase("scaffold", max_turns=400, max_usd=60)), "--dry-run")
    assert code == 0 and judged == []
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["rehearsal"] is True and "preflight" not in resolved
    (planned,) = resolved["phases"]
    assert (planned["max_turns"], planned["max_usd"]) == (5, 0.5)
    assert planned["argv"][planned["argv"].index("--model") + 1] == "claude-sonnet-5"
    assert resolved["subject_model"] == "claude-sonnet-5" and resolved["scenario"]["repeat"] == 1
    assert sorted(p.name for p in run_dir.iterdir()) == ["run.json"]


def test_the_phases_prompts_reach_the_subject_unchanged(rehearse):
    code, run_dir, _ = rehearse(phased(phase("scaffold", {"write": {"site/a": "a"}})))
    assert code == 0
    (session,) = seen(run_dir)
    assert session is not None and session["args"][session["args"].index("-p") + 1] == do({"write": {"site/a": "a"}})
