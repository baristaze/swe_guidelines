"""A phase's milestone, and a run that resumes an earlier one from it.

A run in phases keeps what each phase left in its run folder: the
phase's checkpoint as a zip, and the files the scenario collects as they
stood after it. `run.py resume` starts a new run from one: it restores
the milestone into a fresh workspace, runs only the phases after it, and
carries the source's records of the phases before.

The subject is `benchmark_fake_claude.py` on the host runtime, and on the
vm runtime through a prefix that runs here. No provider is called: every
judge is a fake, and every key is out of the environment.
"""

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from harness import archive as A
from test_benchmark_phases import do, fake_claude, results, seen
from test_benchmark_run import needs_jsonschema, run, vm_config
from test_benchmark_runtime import DOCKER_OWN, docker_holds, docker_stand_in


def phase(name: str, action: dict | None = None, **extra) -> dict:
    return {"name": name, "prompt": do(action or {}), "max_usd": 1, "timeout_s": 60, **extra}


def scenario(*phases: dict, **top) -> dict:
    """A subject in phases that builds `site`, collects the review's report, and runs one gate."""
    return {
        "name": "system",
        "kind": "skill",
        "subject": {"skill": "arch-scaffold-new", "output": "site", "gates": ["test -f a.txt"], "phases": list(phases)},
        "artifact": {"stdout": True, "files": ["review/report.md"]},
        "rubric": "r",
        "runtimes": ["host", "vm"],
        "judges": {"providers": "anthropic"},
        **top,
    }


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A folder of scenarios the run finds by name, the built-in matrix, fake judges that count their calls, and no key."""
    folder = tmp_path / "scenarios"
    folder.mkdir()
    monkeypatch.setattr(run, "SCENARIOS", folder)
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY", "GROK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    judged: list[str] = []

    def judge(kind: str):
        def judge_all(*args, **kwargs) -> list:
            judged.append(kind)
            return []

        return judge_all

    monkeypatch.setattr(run.J, "judge_all", judge("one-shot"))
    monkeypatch.setattr(run.RF, "judge_all", judge("agentic"))
    claude = fake_claude(tmp_path)

    class Bench:
        calls = judged

        @staticmethod
        def write(data: dict) -> None:
            (folder / "system.json").write_text(json.dumps(data), encoding="utf-8")

        @staticmethod
        def run(*argv: str) -> tuple[int, Path | None]:
            """`run.py` with these words, the fake Claude Code, and a folder of its own; its exit and its run folder."""
            out = tmp_path / "runs" / str(len(list((tmp_path / "runs").glob("*"))) if (tmp_path / "runs").exists() else 0)
            code = run.main([*argv, "--out", str(out), "--claude", claude])
            made = sorted(out.iterdir()) if out.exists() else []
            return code, made[0] if made else None

        @staticmethod
        def source(*extra: str) -> Path:
            """A run of the scenario from its first phase: its run folder."""
            _, run_dir = Bench.run("--scenario", "system", "--repeat", "1", "--subject-model", "claude-opus-5-5", *extra)
            assert run_dir is not None
            return run_dir

    return Bench


def zipped(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name).decode() for name in zf.namelist() if not name.endswith("/")}


def resolved(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


# A milestone per phase ------------------------------------------------------------


@needs_jsonschema
def test_a_run_in_phases_keeps_each_phase_s_milestone_in_its_run_folder(bench):
    bench.write(
        scenario(
            phase("scaffold", {"write": {"site/a.txt": "one\n", "review/report.md": "first\n"}}),
            phase("mvp", {"tree": ".", "write": {"site/a.txt": "two\n", "site/b.txt": "b\n", "review/report.md": "second\n"}}),
        )
    )
    run_dir = bench.source()
    repeat = results(run_dir)["repeats"][0]
    scaffold, mvp = repeat["phases"]
    for record, tree, report in (
        (scaffold, {"a.txt": "one\n"}, "first\n"),
        (mvp, {"a.txt": "two\n", "b.txt": "b\n"}, "second\n"),
    ):
        kept = record["milestone"]
        folder = f"artifacts/0/milestones/{record['name']}"
        assert kept["path"] == f"{folder}/output.zip" and kept["manifest"] == f"{folder}/MANIFEST.txt"
        # The checkpoint of that phase, whole, with its SHA-256 recorded; and the collected files as they stood after it.
        assert kept["commit"] == record["checkpoint"] and kept["sha256"] == A.digest(run_dir / kept["path"])
        assert zipped(run_dir / kept["path"]) == tree and kept["files"] == len(tree)
        assert kept["collected"] == ["review/report.md"]
        assert (run_dir / folder / "workspace" / "review" / "report.md").read_text(encoding="utf-8") == report
    # The next phase found nothing of the milestone in its workspace; the workspace itself is gone with the sandbox.
    (_, second) = seen(run_dir)
    assert second is not None and not [p for p in second["tree"] if p.startswith(".archive")]
    assert "the milestone of scaffold: `artifacts/0/milestones/scaffold/output.zip`, 1 file(s)" in (
        run_dir / "report.md"
    ).read_text(encoding="utf-8")


def test_a_phased_run_on_another_machine_keeps_each_milestone_and_leaves_none_there(bench, tmp_path):
    bench.write(scenario(phase("scaffold", {"write": {"site/a.txt": "a"}}), phase("mvp", {"tree": "."})))
    config = tmp_path / "vm.json"
    config.write_text(json.dumps(vm_config(tmp_path, fetch=["cp", "-R", "{remote}/.", "{local}"])), encoding="utf-8")
    run_dir = bench.source("--runtime", "vm", "--runtime-config", str(config))
    scaffold, mvp = results(run_dir)["repeats"][0]["phases"]
    assert zipped(run_dir / scaffold["milestone"]["path"]) == {"a.txt": "a"}
    assert zipped(run_dir / mvp["milestone"]["path"]) == {"a.txt": "a"}
    (_, second) = seen(run_dir)
    assert second is not None and second["tree"] == ["site/a.txt"]  # no zip of the harness's in the workspace there


# Resume ----------------------------------------------------------------------------


# Four phases, each leaving its own file, so a tree tells which phases built it.
FOUR = scenario(
    phase("scaffold", {"write": {"site/a.txt": "a"}}),
    phase("mvp", {"write": {"site/b.txt": "b", "review/report.md": "r"}}),
    phase("review", {"tree": "site", "read": ["review/report.md"], "write": {"site/c.txt": "c"}}),
    phase("close", {"tree": "site", "write": {"site/d.txt": "d"}}),
)


@needs_jsonschema
def test_resume_restores_a_milestone_runs_only_the_phases_after_it_and_carries_the_ones_before(bench):
    bench.write(FOUR)
    src = bench.source()
    assert len(seen(src)) == 4 and bench.calls == ["one-shot"]
    code, run_dir = bench.run("resume", "--source", str(src), "--after", "mvp")
    assert code == 0 and run_dir is not None and run_dir.parent != src.parent
    # Only review and close ran, and review started on the tree mvp left, with the report mvp wrote, and not c.txt.
    review, close = seen(run_dir)
    assert review is not None and review["tree"] == ["a.txt", "b.txt"] and review["read"] == {"review/report.md": "r"}
    assert close is not None and close["tree"] == ["a.txt", "b.txt", "c.txt"]
    record = results(run_dir)
    repeat = record["repeats"][0]
    assert [(p["name"], p.get("carried", False)) for p in repeat["phases"]] == [
        ("scaffold", True),
        ("mvp", True),
        ("review", False),
        ("close", False),
    ]
    source_phases = results(src)["repeats"][0]["phases"]
    assert repeat["phases"][0] == {**{k: v for k, v in source_phases[0].items() if k != "milestone"}, "carried": True}
    # The milestone it restored is copied into this run, and the carried mvp names that copy.
    restored = repeat["phases"][1]["milestone"]
    assert restored["path"] == "artifacts/0/milestones/mvp/output.zip"
    assert restored["sha256"] == source_phases[1]["milestone"]["sha256"] == A.digest(run_dir / restored["path"])
    assert (run_dir / "artifacts/0/milestones/mvp/workspace/review/report.md").read_text(encoding="utf-8") == "r"
    # The gates, the archive, and the judges ran on the new tree.
    assert zipped(run_dir / repeat["archive"]["path"]) == {"a.txt": "a", "b.txt": "b", "c.txt": "c", "d.txt": "d"}
    assert repeat["gates"][0]["passed"] is True and bench.calls == ["one-shot", "one-shot"]
    # What it spent is what review and close spent, nothing of the carried phases.
    assert repeat["subject_cost_usd"] == 0.5
    source = record["source"]
    assert source["run_id"] == src.name and source["after"] == "mvp" and source["repeats"] == [0]
    assert source["refused"] == [] and source["capped"] == []
    assert source["milestones"] == [
        {"repeat": 0, "path": "artifacts/0/milestones/mvp/output.zip", "sha256": restored["sha256"], "commit": restored["commit"]}
    ]
    assert source["checkout"] == resolved(src)["versions"]["checkout"]
    assert record["versions"]["checkout"]["commit"] == source["checkout"]["commit"]  # one checkout here; both are named
    assert [p["name"] for p in resolved(run_dir)["phases"]] == ["review", "close"]
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert f"This run resumed the run `{src.name}`" in report and "after its phase `mvp`" in report
    assert "| 0 | scaffold | fresh | ok (carried) |" in report


def test_resume_by_default_starts_after_the_last_milestone(bench):
    # The source ended early after scaffold, as a session with a helper unanswered ends it.
    bench.write(
        scenario(
            phase("scaffold", {"write": {"site/a.txt": "a"}, "agents": [["toolu_1", "helper", False]]}),
            phase("mvp", {"tree": "site", "write": {"site/b.txt": "b"}}),
            phase("review", {"tree": "site"}),
        )
    )
    src = bench.source()
    assert results(src)["repeats"][0]["ended_early"]["phase"] == "scaffold" and len(seen(src)) == 1
    code, run_dir = bench.run("resume", "--source", str(src))
    assert code == 0 and run_dir is not None
    mvp, review = seen(run_dir)
    assert mvp is not None and mvp["tree"] == ["a.txt"] and review is not None and review["tree"] == ["a.txt", "b.txt"]
    record = results(run_dir)
    assert record["source"]["after"] == "scaffold"
    assert [p["status"] for p in record["repeats"][0]["phases"]] == ["incomplete", "ok", "ok"]


def old_source(bench) -> Path:
    """A run folder as the harness wrote it before phases kept milestones: its archive is its last checkpoint."""
    bench.write(
        scenario(
            phase("scaffold", {"write": {"site/a.txt": "a", "review/report.md": "r"}, "agents": [["toolu_1", "helper", False]]}),
            phase("mvp", {"tree": "site", "read": ["review/report.md"]}),
        )
    )
    src = bench.source()
    record = results(src)
    for p in record["repeats"][0]["phases"]:
        p.pop("milestone")
    (src / "results.json").write_text(json.dumps(record), encoding="utf-8")
    subprocess.run(["rm", "-rf", str(src / "artifacts" / "0" / "milestones")], check=True)
    return src


@needs_jsonschema
def test_a_source_recorded_before_milestones_resumes_from_its_archive(bench):
    src = old_source(bench)
    code, run_dir = bench.run("resume", "--source", str(src))
    assert code == 0 and run_dir is not None
    (mvp,) = seen(run_dir)
    assert mvp is not None and mvp["tree"] == ["a.txt"] and mvp["read"] == {"review/report.md": "r"}
    source = results(run_dir)["source"]
    assert source["after"] == "scaffold" and source["milestones"][0]["path"] == "artifacts/0/output.zip"
    assert source["milestones"][0]["sha256"] == results(src)["repeats"][0]["archive"]["sha256"]


def test_a_source_whose_archive_is_not_its_last_checkpoint_or_not_the_one_recorded_is_refused(bench, capsys):
    src = old_source(bench)
    record = results(src)
    kept = json.dumps(record)
    record["repeats"][0]["archive"]["commit"] = "f" * 40
    (src / "results.json").write_text(json.dumps(record), encoding="utf-8")
    runs = sorted(src.parent.parent.glob("*/*"))
    assert bench.run("resume", "--source", str(src)) == (2, None)
    assert "its archive holds the commit ffffffffff" in capsys.readouterr().err
    # The recorded commit is right, and the zip is not the one the source recorded.
    (src / "results.json").write_text(kept, encoding="utf-8")
    with zipfile.ZipFile(src / "artifacts" / "0" / "output.zip", "a") as zf:
        zf.writestr("planted.txt", "not built by the subject\n")
    assert bench.run("resume", "--source", str(src)) == (2, None)
    err = capsys.readouterr().err
    assert "repeat 0 is not resumed: its milestone's SHA-256 is " in err and "no run folder was made and no phase started" in err
    assert sorted(src.parent.parent.glob("*/*")) == runs  # no run folder, and no session


def test_a_milestone_that_is_not_the_one_recorded_is_refused(bench, capsys):
    bench.write(FOUR)
    src = bench.source()
    with zipfile.ZipFile(src / "artifacts/0/milestones/mvp/output.zip", "a") as zf:
        zf.writestr("planted.txt", "x")
    assert bench.run("resume", "--source", str(src), "--after", "mvp") == (2, None)
    assert "repeat 0 is not resumed: its milestone's SHA-256 is " in capsys.readouterr().err


# The handoff note ----------------------------------------------------------------------

NOTE = "what scaffold did, decided, and left"
# Two builders that keep the note, and between them a phase no one tells of it.
NOTED = scenario(
    phase("scaffold", {"note": NOTE, "write": {"site/a.txt": "a"}}, hint=True),
    phase("review", {"tree": "."}),
    phase("mvp", {"write": {"site/b.txt": "b"}}, hint=True),
)


@needs_jsonschema
def test_a_resumed_builder_gets_the_note_the_builder_before_it_left_and_no_other_phase_sees_it(bench):
    bench.write(NOTED)
    src = bench.source()
    _, review, mvp = seen(src)
    # The unbroken run: mvp reads the note, and the review never finds it.
    assert mvp is not None and mvp["note"] == NOTE
    assert review is not None and review["handoff_in_reach"] == [] and "HANDOFF.md" not in review["tree"]
    # Each milestone keeps the note as it stood after its phase, the review's included.
    for record in results(src)["repeats"][0]["phases"]:
        assert (src / record["milestone"]["handoff"]).read_text(encoding="utf-8") == NOTE
    for after in ("scaffold", "review"):
        code, run_dir = bench.run("resume", "--source", str(src), "--after", after)
        assert code == 0 and run_dir is not None
        *before, resumed = seen(run_dir)
        assert resumed is not None and resumed["note"] == NOTE  # as the unbroken run's mvp read it
        for other in before:  # the review, when it runs: no note in reach, and none in its tree
            assert other is not None and other["handoff_in_reach"] == [] and "HANDOFF.md" not in other["tree"]
        (carried,) = [p for p in results(run_dir)["repeats"][0]["phases"] if p["name"] == after]
        assert carried["carried"] and (run_dir / carried["milestone"]["handoff"]).read_text(encoding="utf-8") == NOTE


def test_on_another_machine_the_note_is_kept_and_given_back_hidden(bench, tmp_path):
    bench.write(NOTED)
    config = tmp_path / "vm.json"
    config.write_text(json.dumps(vm_config(tmp_path, fetch=["cp", "-R", "{remote}/.", "{local}"])), encoding="utf-8")
    src = bench.source("--runtime", "vm", "--runtime-config", str(config))
    scaffold = results(src)["repeats"][0]["phases"][0]
    assert (src / scaffold["milestone"]["handoff"]).read_text(encoding="utf-8") == NOTE
    code, run_dir = bench.run("resume", "--source", str(src), "--after", "scaffold")  # the config the source recorded
    assert code == 0 and run_dir is not None
    review, mvp = seen(run_dir)
    assert review is not None and review["handoff_in_reach"] == [] and "HANDOFF.md" not in review["tree"]
    assert mvp is not None and mvp["note"] == NOTE


# Where resume meets the rest of a run -------------------------------------------------

SENTENCE = "After the build, a review read the tree, and a last session closed its findings."


def grouped() -> dict:
    """Four phases, the last two in the group `extras`, judged by one agentic judge."""
    data = scenario(
        phase("scaffold", {"write": {"site/a.txt": "a"}}),
        phase("mvp", {"write": {"site/b.txt": "b"}}),
        phase("review", {"write": {"review/report.md": "r"}}, group="extras"),
        phase("close", group="extras"),
        judges={
            "providers": "anthropic",
            "mode": "agentic",
            "budget": {"max_usd": 5},
            "references": [{"name": "guideline", "weight": 1, "paths": ["lenses/README.md"]}],
        },
    )
    data["subject"]["groups"] = {"extras": {"rubric": SENTENCE}}
    return data


@needs_jsonschema
def test_a_resumed_run_s_carried_phases_count_as_run_for_the_rubric_s_groups(bench):
    bench.write(grouped())
    src = bench.source("--with", "extras")
    # Resumed after scaffold, the group's phases all run here; after review, one of them is carried.
    for after in ("scaffold", "review"):
        code, run_dir = bench.run("resume", "--source", str(src), "--after", after)
        assert code == 0 and run_dir is not None
        prompt = (run_dir / "artifacts" / "0" / "judge-prompt.md").read_text(encoding="utf-8")
        assert SENTENCE in prompt
        assert all(f"{n}. {name} (fresh session)" in prompt for n, name in enumerate(("scaffold", "mvp", "review", "close"), 1))
        assert resolved(run_dir)["groups"] == ["extras"] and results(run_dir)["subject"]["groups"] == ["extras"]
    # A judge of the resumed run reads its phases, the carried ones included, and takes the group too.
    code, judged = bench.run("judge", "--source", str(run_dir))
    assert code == 0 and judged is not None
    assert results(judged)["source"]["rubric_groups"] == [{"repeat": 0, "groups": ["extras"]}]
    assert SENTENCE in (judged / "artifacts" / "0" / "judge-prompt.md").read_text(encoding="utf-8")


def test_a_resumed_repeat_on_another_machine_removes_what_its_docker_made(bench, tmp_path, monkeypatch):
    bench.write(FOUR)
    src = bench.source()
    state = docker_stand_in(tmp_path, *DOCKER_OWN)
    docker = str(state / "docker")
    monkeypatch.setattr(run.RT, "DOCKER", docker)
    # The machine's check runs after the run lists what Docker holds, so it stands in for a subject's stack here.
    made = vm_config(
        tmp_path, fetch=["cp", "-R", "{remote}/.", "{local}"], check=[docker, "create", "container", "c-db", "acme-db-1"]
    )
    config = tmp_path / "vm.json"
    config.write_text(json.dumps(made), encoding="utf-8")
    record = resolved(src)
    record["runtime"]["name"] = "vm"
    (src / "run.json").write_text(json.dumps(record), encoding="utf-8")
    code, run_dir = bench.run("resume", "--source", str(src), "--after", "mvp", "--runtime-config", str(config))
    assert code == 0 and run_dir is not None and len(seen(run_dir)) == 2
    notes = results(run_dir)["notes"]
    removed = "removed what the subject's Docker made on the other machine: 1 container (acme-db-1)"
    assert f"repeat 0: {removed}" in notes
    assert docker_holds(state) == set(DOCKER_OWN)  # nothing that was there before the run went


# What resume may spend ---------------------------------------------------------------


AGENTIC = scenario(
    phase("scaffold", {"write": {"site/a.txt": "a"}}, max_usd=100),
    phase("mvp", {"write": {"site/b.txt": "b"}}, max_usd=7),
    phase("review", max_usd=3),
    judges={
        "providers": "anthropic,openai",
        "mode": "agentic",
        "budget": {"max_usd": 5},
        "references": [{"name": "guideline", "weight": 1, "paths": ["lenses/README.md"]}],
    },
)


def test_resume_caps_its_spend_at_the_phases_it_runs_and_the_judges_budgets(bench):
    bench.write(AGENTIC)
    src = bench.source("--max-spend-usd", "200")
    # mvp and review, $7 and $3, and two judges at $5: $20. Not scaffold's $100.
    code, dry = bench.run("resume", "--source", str(src), "--after", "scaffold", "--dry-run")
    assert code == 0 and dry is not None
    assert resolved(dry)["max_spend_usd"] == 20 and sorted(p.name for p in dry.iterdir()) == ["run.json"]
    assert resolved(dry)["source"]["after"] == "scaffold"
    code, dry = bench.run("resume", "--source", str(src), "--after", "scaffold", "--dry-run", "--max-spend-usd", "15")
    assert code == 0 and dry is not None and resolved(dry)["max_spend_usd"] == 15
    # $15 does not cover a repeat's phases and judges, $20: no repeat starts, no session runs, and no judge.
    judged = len(bench.calls)
    code, run_dir = bench.run("resume", "--source", str(src), "--after", "scaffold", "--max-spend-usd", "15")
    assert code == 0 and run_dir is not None and seen(run_dir) == [] and len(bench.calls) == judged
    record = results(run_dir)
    assert record["repeats"] == [] and record["source"]["capped"] == [0] and record["source"]["repeats"] == []
    why = "repeat 0 and after did not run: $15.0000 of the run's $15 spend cap is left, and the phases and the judges"
    assert any(n.startswith(why) and n.endswith("of a repeat may spend $20") for n in record["notes"])
    # Its own cap covers them, and the repeat runs.
    code, run_dir = bench.run("resume", "--source", str(src), "--after", "scaffold")
    assert code == 0 and run_dir is not None and len(seen(run_dir)) == 2 and len(bench.calls) == judged + 1


# Where the milestone goes -------------------------------------------------------------


def test_resume_on_another_machine_copies_the_milestone_there(bench, tmp_path):
    bench.write(FOUR)
    src = bench.source()
    config = tmp_path / "vm.json"
    config.write_text(json.dumps(vm_config(tmp_path, fetch=["cp", "-R", "{remote}/.", "{local}"])), encoding="utf-8")
    # The source ran on the host; this checkout's scenario lists vm too, and the flag names the config.
    record = resolved(src)
    record["runtime"]["name"] = "vm"
    (src / "run.json").write_text(json.dumps(record), encoding="utf-8")
    code, run_dir = bench.run("resume", "--source", str(src), "--after", "mvp", "--runtime-config", str(config))
    assert code == 0 and run_dir is not None
    review, _ = seen(run_dir)
    assert review is not None and review["tree"] == ["a.txt", "b.txt"] and review["read"] == {"review/report.md": "r"}
    assert review["cwd"].startswith(str(tmp_path / "remote"))


def test_a_milestone_that_does_not_arrive_where_the_subject_runs_runs_no_phase(bench, tmp_path):
    bench.write(FOUR)
    src = bench.source()
    record = resolved(src)
    record["runtime"] = {"name": "vm", "config": vm_config(tmp_path, copy=["true"])}  # a copy that copies nothing
    (src / "run.json").write_text(json.dumps(record), encoding="utf-8")
    code, run_dir = bench.run("resume", "--source", str(src), "--after", "mvp")
    assert code == 6 and run_dir is not None and seen(run_dir) == [] and bench.calls == ["one-shot"]
    repeat = results(run_dir)["repeats"][0]
    assert [p["name"] for p in repeat["phases"]] == ["scaffold", "mvp"] and "archive" not in repeat
    why = "the milestone did not arrive whole where the subject runs: it holds 2 file(s) of site"
    assert any(n.startswith(f"repeat 0: {why}") and n.endswith("; no phase ran") for n in results(run_dir)["notes"])


def test_a_checkpoint_s_zip_unpacks_with_its_executable_files_and_its_links(tmp_path):
    tree = tmp_path / "tree"
    (tree / "bin").mkdir(parents=True)
    (tree / "bin" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tree / "bin" / "run.sh").chmod(0o755)
    (tree / "README.md").write_text("r\n", encoding="utf-8")
    (tree / "link.md").symlink_to("README.md")
    git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git[:3], "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "t"], check=True)
    subprocess.run([*git, "archive", "--format=zip", "-o", str(tmp_path / "out.zip"), "HEAD"], check=True)
    run.unpack(tmp_path / "out.zip", tmp_path / "unpacked")
    unpacked = tmp_path / "unpacked"
    assert os.access(unpacked / "bin" / "run.sh", os.X_OK) and not os.access(unpacked / "README.md", os.X_OK)
    assert (unpacked / "link.md").is_symlink() and os.readlink(unpacked / "link.md") == "README.md"
    assert run.files_in(unpacked) == 3


# What resume refuses -----------------------------------------------------------------


def test_resume_refuses_what_its_source_decides_and_a_source_it_cannot_continue(bench, capsys):
    bench.write(
        scenario(
            phase("scaffold", {"write": {"site/a.txt": "a"}}),
            phase("mvp", {"write": {"site/b.txt": "b"}}),
            phase("fix", session="resume"),
        )
    )
    src = bench.source()
    before = sorted(src.parent.parent.glob("*/*"))
    assert bench.run("resume", "--source", str(src), "--with", "extras", "--subject-model", "m") == (2, None)
    assert "--with, --subject-model is not for it" in capsys.readouterr().err
    assert bench.run("--scenario", "system", "--after", "scaffold") == (2, None)
    assert "--after is for resume" in capsys.readouterr().err
    assert bench.run("resume", "--source", str(src), "--after", "deploy") == (2, None)
    assert "--after names deploy, and the phases of the source run are scaffold, mvp, fix" in capsys.readouterr().err
    assert bench.run("resume", "--source", str(src)) == (2, None)  # after fix, the last phase: only the judges are left
    assert "judges are one-shot: resume carries agentic judgements only" in capsys.readouterr().err
    assert bench.run("resume", "--source", str(src), "--providers", "openai", "--runtime-config", "vm.json") == (2, None)
    assert "--runtime-config, --providers is not for it; --judges names the judges to run again" in capsys.readouterr().err
    assert bench.run("resume", "--source", str(src), "--after", "scaffold", "--judges", "openai") == (2, None)
    assert "a resume after scaffold runs mvp, fix, and every judge judges what they build" in capsys.readouterr().err
    assert bench.run("--scenario", "system", "--judges", "openai") == (2, None)
    assert "--judges is for resume" in capsys.readouterr().err
    assert bench.run("resume", "--source", str(src), "--after", "mvp") == (2, None)
    assert "phase fix continues the session of mvp, which only the source run held" in capsys.readouterr().err
    marked = resolved(src)
    marked["rehearsal"] = True
    (src / "run.json").write_text(json.dumps(marked), encoding="utf-8")
    assert bench.run("resume", "--source", str(src), "--after", "scaffold") == (2, None)
    assert "is a rehearsal" in capsys.readouterr().err
    assert sorted(src.parent.parent.glob("*/*")) == before  # no run folder was made


# Resuming the judges ------------------------------------------------------------------

SCORES = {"anthropic": 80, "openai": 60, "gemini": 90, "xai": 70}
ALL_FOUR = ["anthropic", "openai", "gemini", "xai"]


def four_judges() -> dict:
    """The grouped scenario, judged by all four providers, each within $5."""
    data = grouped()
    data["judges"]["providers"] = "15"
    return data


@pytest.fixture
def panel(monkeypatch):
    """Fake agentic judges in place of every provider's.

    Each judgement scores its provider's SCORES and costs $1.50, unless
    `answers` names another status for the provider, such as `error`. Each
    call records the judges it ran, the task they were given, and the files
    of the output they read.
    """

    class Panel:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.answers: dict[str, str] = {}

    fake = Panel()

    def judge_all(flags, prompt, roots, folder, index, effort, weights, *args, **kwargs) -> list:
        names = [run.P.name(p) for p in run.P.members(flags)]
        tree = sorted(p.relative_to(roots["output"]).as_posix() for p in roots["output"].rglob("*") if p.is_file())
        fake.calls.append({"names": names, "prompt": prompt, "tree": tree, "effort": effort})
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        for name in names:
            path = folder / f"{index}-{name}.jsonl"
            path.write_text(json.dumps({"kind": "end", "judge": name}) + "\n", encoding="utf-8")
            status = fake.answers.get(name, "ok")
            answer = {
                "references": {"guideline": {"score": SCORES[name], "gaps": [], "strengths": [f"{name} read a.txt"]}},
                "rationale": f"what {name} weighed",
            }
            judgement = run.RF.A.AgenticJudgement(
                name,
                f"{name}-model",
                effort,
                status=status,
                latency_s=1.0,
                usage={"input_tokens": 1000, "output_tokens": 100},
                error=None if status == "ok" else f"{name}: RateLimitError",
                answer=answer if status == "ok" else None,
                cost_usd=1.5,
                tool_calls=3,
                turns=2,
            )
            out.append(run.RF.Judged.of(judgement, weights, f"{folder.name}/{path.name}"))
        return out

    monkeypatch.setattr(run.RF, "judge_all", judge_all)
    return fake


def no_runtime(*args, **kwargs):
    raise AssertionError("a resume of the judges made a runtime")


@needs_jsonschema
def test_a_resume_of_a_run_whose_phases_all_ran_runs_only_the_judges_named_and_carries_the_rest(bench, panel, monkeypatch):
    bench.write(four_judges())
    panel.answers["openai"] = "error"
    src = bench.source("--with", "extras")
    assert [c["names"] for c in panel.calls] == [ALL_FOUR]
    panel.answers.clear()
    monkeypatch.setattr(run.RT, "build", no_runtime)
    code, run_dir = bench.run("resume", "--source", str(src), "--judges", "openai")
    assert code == 0 and run_dir is not None and not (run_dir / "streams").exists()  # no phase, and no session
    # Only openai judged, once, the tree the source archived, with the very task the source's judges were given.
    first, again = panel.calls
    assert again["names"] == ["openai"] and again["tree"] == first["tree"] == ["a.txt", "b.txt"]
    assert again["prompt"] == first["prompt"] == (src / "artifacts/0/judge-prompt.md").read_text(encoding="utf-8")
    record = results(run_dir)
    (repeat,) = record["repeats"]
    by = {j["provider"]: j for j in repeat["judgements"]}
    assert list(by) == ALL_FOUR and [p for p, j in by.items() if j.get("carried")] == ["anthropic", "gemini", "xai"]
    was = {j["provider"]: j for j in results(src)["repeats"][0]["judgements"]}
    assert all(by[p] == {**was[p], "carried": True} for p in ("anthropic", "gemini", "xai"))
    assert was["openai"]["status"] == "error" and by["openai"]["status"] == "ok" and by["openai"]["judged"]["score"] == 60
    # The scores, the means, and the report hold all four, and the mean is over the four.
    summary = record["summary"]
    assert sorted(summary["per_provider"]) == sorted(ALL_FOUR) and summary["overall_mean"] == 75
    assert summary["skipped"] == [] and summary["references"]["guideline"]["n"] == 4
    # What it spent is openai's judgement alone: the source paid for the carried ones.
    assert list(record["spend"]["judges"]) == ["openai"] and record["spend"]["total_usd"] == 1.5
    assert record["source"]["judges"] == [{"repeat": 0, "run": ["openai"], "carried": ["anthropic", "gemini", "xai"]}]
    assert record["source"]["run_id"] == src.name and "after" not in record["source"]
    # Each carried judgement comes with its transcript and its answer, marked carried.
    assert all((run_dir / "judgements" / f"0-{p}.jsonl").is_file() for p in ALL_FOUR)
    kept = json.loads((run_dir / "judgements" / "0-gemini.json").read_text(encoding="utf-8"))
    assert kept["carried"] is True and kept["answer"]["rationale"] == "what gemini weighed"
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert f"This run resumed the judges of the run `{src.name}`" in report
    assert "Repeat 0: judged here by `openai`; carried: `anthropic`, `gemini`, `xai`." in report
    assert "| 90 | 90.0 | 3 | 1.0 | ok (carried) |" in report and "| 60 | 60.0 | 3 | 1.0 | ok |" in report
    assert "Overall mean, of the weighted scores: 75.0." in report and "### Not answered" not in report


@needs_jsonschema
def test_a_resume_of_the_judges_runs_by_default_those_that_did_not_answer_and_none_when_all_did(bench, panel, capsys):
    bench.write(four_judges())
    panel.answers.update(openai="error", xai="missed")
    src = bench.source("--with", "extras")
    panel.answers.clear()
    code, run_dir = bench.run("resume", "--source", str(src))
    assert code == 0 and run_dir is not None
    assert [c["names"] for c in panel.calls] == [ALL_FOUR, ["openai", "xai"]]
    record = results(run_dir)
    assert record["source"]["judges"] == [{"repeat": 0, "run": ["openai", "xai"], "carried": ["anthropic", "gemini"]}]
    assert [j["status"] for j in record["repeats"][0]["judgements"]] == ["ok"] * 4
    assert record["summary"]["overall_mean"] == 75 and record["spend"]["total_usd"] == 3
    # Every judge of the resumed run answered: nothing is left to resume, no judge starts, and no run folder is made.
    runs = sorted(run_dir.parent.parent.glob("*/*"))
    assert bench.run("resume", "--source", str(run_dir)) == (2, None)
    err = capsys.readouterr().err
    assert (
        "every judge answered in it, so no judge is left to run" in err and "No run folder was made and no judge started" in err
    )
    assert len(panel.calls) == 2 and sorted(run_dir.parent.parent.glob("*/*")) == runs


def test_a_judgement_is_carried_only_from_the_same_archive_judged_with_the_same_task(bench, panel, capsys):
    bench.write(four_judges())
    panel.answers["openai"] = "error"
    src = bench.source("--with", "extras")
    runs = sorted(src.parent.parent.glob("*/*"))
    kept = (src / "results.json").read_text(encoding="utf-8")
    prompt = src / "artifacts" / "0" / "judge-prompt.md"
    told = prompt.read_text(encoding="utf-8")
    another = "its judges were given another task than this run's judges get, so a judgement it holds is of another rubric"

    def refused(why: str) -> str:
        assert bench.run("resume", "--source", str(src), "--judges", "openai") == (2, None)
        err = capsys.readouterr().err
        assert f"repeat 0 is not resumed: {why}" in err and "No run folder was made and no judge started" in err
        assert len(panel.calls) == 1 and sorted(src.parent.parent.glob("*/*")) == runs
        return err

    # The source's judges were told of no group, and this run's judges take the group the source ran whole.
    prompt.write_text(told.replace(f"\n\n{SENTENCE}", ""), encoding="utf-8")
    assert f"and {SENTENCE!r} here" in refused(another)
    prompt.write_text(told, encoding="utf-8")
    # This checkout says the group's sentence in other words: the rubric is another.
    changed = four_judges()
    changed["subject"]["groups"]["extras"]["rubric"] = "A last session read the tree."
    bench.write(changed)
    assert f"reads {SENTENCE!r} there, and 'A last session read the tree.' here" in refused(another)
    bench.write(four_judges())
    # Nothing says what the source's judges were told.
    prompt.unlink()
    refused("the source kept no judge prompt of it, so nothing says what its judges were told")
    prompt.write_text(told, encoding="utf-8")
    # Nothing says which archive the source's judges read.
    record = json.loads(kept)
    del record["repeats"][0]["archive"]["sha256"]
    (src / "results.json").write_text(json.dumps(record), encoding="utf-8")
    refused("the source recorded no SHA-256 of the archive its judges read")
    (src / "results.json").write_text(kept, encoding="utf-8")
    # The archive is not the one the source's judges read.
    with zipfile.ZipFile(src / "artifacts" / "0" / "output.zip", "a") as zf:
        zf.writestr("planted.txt", "not what the judges read\n")
    refused("its archive's SHA-256 is ")


@needs_jsonschema
def test_a_resume_of_the_judges_caps_its_spend_at_the_budgets_of_the_judges_it_runs(bench, panel):
    bench.write(four_judges())
    panel.answers["openai"] = "error"
    src = bench.source("--with", "extras")

    def dry(*flags: str) -> dict:
        code, made = bench.run("resume", "--source", str(src), "--dry-run", *flags)
        assert code == 0 and made is not None and sorted(p.name for p in made.iterdir()) == ["run.json"]
        return resolved(made)

    # One judge at $5: not the four judges' $20, and no phase's cap. A dry run calls no judge.
    named = dry("--judges", "openai")
    assert named["max_spend_usd"] == 5 and named["providers"]["names"] == ["openai"]
    assert named["source"]["judges"] == [{"repeat": 0, "run": ["openai"], "carried": ["anthropic", "gemini", "xai"]}]
    assert dry()["max_spend_usd"] == 5 and dry("--judges", "openai,xai")["max_spend_usd"] == 10
    assert dry("--judges", "openai", "--max-spend-usd", "7")["max_spend_usd"] == 7 and len(panel.calls) == 1
    # $4 does not cover the one judge's $5: no judge starts, and the notes say why.
    code, run_dir = bench.run("resume", "--source", str(src), "--judges", "openai", "--max-spend-usd", "4")
    assert code == 0 and run_dir is not None and len(panel.calls) == 1
    record = results(run_dir)
    assert record["repeats"] == [] and record["source"]["capped"] == [0] and record["spend"]["total_usd"] == 0
    why = "repeat 0 and after were not judged: $4.0000 of the run's $4 spend cap is left, and the judges of a repeat may spend $5"
    assert why in record["notes"]


@needs_jsonschema
def test_a_resume_of_the_judges_runs_them_at_the_source_s_effort_unless_one_is_named(bench, panel):
    bench.write(four_judges())  # the scenario's effort is medium
    panel.answers["openai"] = "error"
    src = bench.source("--with", "extras", "--effort", "low")
    panel.answers.clear()
    assert resolved(src)["effort"] == "low" and panel.calls[0]["effort"] == "low"
    # The judge it runs judges at low, as the carried ones did, and the run records it.
    code, run_dir = bench.run("resume", "--source", str(src), "--judges", "openai")
    assert code == 0 and run_dir is not None and panel.calls[-1]["effort"] == "low"
    assert resolved(run_dir)["effort"] == "low"
    assert {j["effort"] for j in results(run_dir)["repeats"][0]["judgements"]} == {"low"}
    # --effort names another, and the judge it runs takes that one.
    code, run_dir = bench.run("resume", "--source", str(src), "--judges", "openai", "--effort", "high")
    assert code == 0 and run_dir is not None and panel.calls[-1]["effort"] == "high"
    assert resolved(run_dir)["effort"] == "high"
