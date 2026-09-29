"""benchmark/run.py: the subject's command, with the plugin and the target it can reach."""

import importlib.util
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from harness import scenario as S
from test_benchmark_agentic import FAKES, sent_results
from test_benchmark_agentic import step as turn
from test_benchmark_judge import FakeSdks
from test_benchmark_references import acme_repository, git
from test_benchmark_runtime import DOCKER_OWN, docker_holds, docker_removals, docker_stand_in

needs_jsonschema = pytest.mark.skipif(importlib.util.find_spec("jsonschema") is None, reason="jsonschema is not installed")

RUN = Path(__file__).resolve().parent.parent / "benchmark" / "run.py"
spec = importlib.util.spec_from_file_location("benchmark_run", RUN)
assert spec is not None and spec.loader is not None
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run  # its dataclasses look their module up by name
spec.loader.exec_module(run)

# Every runtime, the host first, so a run that names none runs on the host.
EVERYWHERE = ["host", "container", "vm"]
SKILL = {
    "name": "one",
    "kind": "skill",
    "subject": {"skill": "arch-review-om", "prompt": "Review it.", "max_usd": 1},
    "rubric": "r",
    "runtimes": EVERYWHERE,
}


def test_a_skill_gets_the_plugin_and_the_target_it_is_given():
    scn = S.from_data(SKILL)
    argv = run.subject_argv(scn, "swe-guidelines", "/plugin", "/target", "claude")
    assert argv[argv.index("--plugin-dir") + 1] == "/plugin"
    assert argv[argv.index("--add-dir") + 1] == "/target"
    prompt = argv[argv.index("-p") + 1]
    assert prompt.startswith("/swe-guidelines:arch-review-om Review it.")
    assert "The checkout to work on is at /target." in prompt


def test_a_skill_with_no_target_is_told_of_none():
    argv = run.subject_argv(S.from_data(SKILL), "swe-guidelines", "/plugin", None)
    assert "--add-dir" not in argv
    assert "checkout" not in argv[argv.index("-p") + 1]


def test_the_target_placeholder_is_filled_and_refused_when_there_is_no_target():
    scn = S.from_data(dict(SKILL, subject={"skill": "arch-review-om", "prompt": "Review {target}/om.", "max_usd": 1}))
    assert run.subject_prompt(scn, "/target") == "Review /target/om."
    with pytest.raises(S.ScenarioError, match="has no target"):
        run.subject_prompt(scn, None)


def test_a_command_gets_the_paths_in_its_argv():
    scn = S.from_data(
        {
            "name": "c",
            "kind": "command",
            "subject": {"argv": ["ls", "{target}", "{plugin}"]},
            "rubric": "r",
            "runtimes": EVERYWHERE,
        }
    )
    assert run.subject_argv(scn, "p", "/plugin", "/target") == ["ls", "/target", "/plugin"]


def test_the_shipped_review_runs_on_its_planted_checkout_with_the_answers_outside_it():
    pytest.importorskip("yaml")
    scn = S.load(RUN.parent / "scenarios" / "review-om.yaml")
    target = scn.resolve(scn.subject.target)
    expected = scn.resolve(scn.evidence.expected)
    assert target is not None and expected is not None
    assert (target / "om").is_dir() and expected.is_file()
    assert target not in expected.parents


QA = {
    "name": "q",
    "kind": "qa",
    "subject": {"prompt": "Why?", "context": ["notes/why.md"]},
    "rubric": "r",
    "runtimes": EVERYWHERE,
}


def test_a_qa_context_path_is_read_from_the_scenario_folder(tmp_path, monkeypatch):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "why.md").write_text("Because.\n", encoding="utf-8")
    scn = S.from_data(QA, tmp_path / "q.json")
    monkeypatch.chdir(tmp_path.parent)  # the run starts elsewhere; the path means the same thing
    assert run.context_text(scn) == "\n\n## Context: why.md\n\nBecause.\n"


def test_a_missing_qa_context_file_is_a_scenario_error(tmp_path):
    scn = S.from_data(QA, tmp_path / "q.json")
    with pytest.raises(S.ScenarioError, match=r"subject\.context 'notes/why\.md' is not a file"):
        run.context_text(scn)


def test_a_qa_subject_asks_within_its_scenarios_timeout(tmp_path, monkeypatch):
    sdks = FakeSdks(monkeypatch)
    monkeypatch.setattr(run.J.time, "monotonic", lambda: 100.0)
    scn = S.from_data(dict(QA, subject={"prompt": "Why?", "timeout_s": 42}))
    with run.CliStream(tmp_path / "cli.jsonl") as streams:
        status, text, _ = run.run_subject_qa(scn, streams, {"ANTHROPIC_API_KEY": "k"}, run.J.DEFAULT_MATRIX, "medium", "m")
    assert status.code == 0 and text == "an answer"
    assert [(method, request["timeout"]) for method, request in sdks.requests] == [("messages.stream", 42)]


def test_a_scenario_that_does_not_load_exits_2_with_its_message(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")  # the built-in matrix, no pyyaml needed
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(dict(SKILL, judges={"effort": "maximum"})), encoding="utf-8")
    assert run.main(["--scenario", str(bad), "--out", str(tmp_path / "runs")]) == 2
    assert "judges.effort is one of" in capsys.readouterr().err
    assert run.main(["--scenario", "no-such-scenario", "--out", str(tmp_path / "runs")]) == 2
    assert "no scenario named 'no-such-scenario'" in capsys.readouterr().err
    qa = tmp_path / "qa.json"
    qa.write_text(json.dumps(QA), encoding="utf-8")
    assert run.main(["--scenario", str(qa), "--out", str(tmp_path / "runs")]) == 2
    assert "is not a file" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()  # nothing was started
    good = tmp_path / "good.json"
    good.write_text(json.dumps(SKILL), encoding="utf-8")
    assert run.main(["--scenario", str(good), "--out", str(tmp_path / "runs"), "--dry-run"]) == 0


def test_a_runtime_the_scenario_does_not_list_is_refused_before_anything_starts(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "boxed.json"
    path.write_text(json.dumps(dict(SKILL, runtimes=["container"])), encoding="utf-8")
    for runtime in ("host", "vm"):
        assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--runtime", runtime]) == run.NOT_LISTED
        assert f"scenario one runs on container, not on {runtime}" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()  # no run folder, no runtime, nothing spent


def test_a_run_folder_goes_in_its_scenario_s_folder_under_the_runs_root(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "one.json"
    path.write_text(json.dumps(SKILL), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--dry-run"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    name = SKILL["name"]
    assert run_dir.parent == tmp_path / "runs" / name and run_dir.name.split("-", 2)[2].startswith(f"{name}-")
    # With no --out, the runs root is benchmark/runs.
    monkeypatch.setattr(run, "DEFAULT_OUT", tmp_path / "benchmark" / "runs")
    assert run.main(["--scenario", str(path), "--dry-run"]) == 0
    (run_dir,) = (tmp_path / "benchmark" / "runs").glob("*/*")
    assert run_dir.parent == tmp_path / "benchmark" / "runs" / name


def test_a_run_that_names_no_runtime_takes_the_scenario_s_first(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "two.json"
    path.write_text(json.dumps(dict(SKILL, runtimes=["container", "host"])), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--dry-run"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["runtime"]["name"] == "container"


def test_a_scenario_that_requires_docker_is_refused_in_a_container(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "system.json"
    path.write_text(json.dumps(dict(SKILL, runtimes=["vm"], requires=["docker"])), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--runtime", "container"]) == run.NOT_LISTED
    assert "scenario one runs on vm, not on container" in capsys.readouterr().err
    both = tmp_path / "both.json"
    both.write_text(json.dumps(dict(SKILL, runtimes=["vm", "container"], requires=["docker"])), encoding="utf-8")
    assert run.main(["--scenario", str(both), "--out", str(tmp_path / "runs"), "--runtime", "vm"]) == 2
    assert "the container runtime cannot provide docker, which the scenario requires" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_a_qa_subject_in_a_container_builds_no_image(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(run.J, "ask", lambda *args, **kwargs: ("the answer", {}))  # no provider is called
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    asked: list[str] = []
    monkeypatch.setattr(run.RT.ContainerRuntime, "image_version", lambda self: asked.append("image"))
    monkeypatch.setattr(run.RT.ContainerRuntime, "build", lambda self, streams=None: asked.append("build"))
    path = tmp_path / "q.json"
    path.write_text(json.dumps(dict(QA, subject={"prompt": "Why?"}, runtimes=["container"])), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1", "--providers", "1", "--build"]
    assert run.main(argv) == 0
    assert asked == []  # no engine is asked to build or name an image no subject runs in
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["versions"]["image"] is None
    assert not any("image" in note for note in results["notes"])


def test_the_shipped_review_never_runs_on_the_host(tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    assert run.main(["--scenario", "review-om", "--out", str(tmp_path / "runs"), "--runtime", "host"]) == run.NOT_LISTED
    assert "scenario review-om runs on container, not on host" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_the_listing_names_where_each_scenario_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "SCENARIOS", tmp_path)
    (tmp_path / "one.json").write_text(json.dumps(dict(SKILL, runtimes=["container", "host"])), encoding="utf-8")
    (tmp_path / "two.json").write_text(
        json.dumps(dict(SKILL, name="two", runtimes=["vm"], requires=["docker"])), encoding="utf-8"
    )
    (tmp_path / "three.json").write_text(
        json.dumps(dict(SKILL, name="three", runtimes=["vm"], requires=["docker"], repeat=1, max_spend_usd=190)),
        encoding="utf-8",
    )
    (tmp_path / "four.json").write_text(json.dumps(dict(SKILL, name="four", max_spend_usd=0.5)), encoding="utf-8")
    one = {"prompt": "p", "max_turns": 1, "max_usd": 1, "timeout_s": 60}
    phases = [{"name": "build", **one}, {"name": "review", "group": "extras", **one}, {"name": "tidy", "group": "polish", **one}]
    subject = {"skill": "arch-scaffold-new", "output": "site", "phases": phases, "groups": {"extras": None, "polish": None}}
    (tmp_path / "five.json").write_text(json.dumps(dict(SKILL, name="five", runtimes=["vm"], subject=subject)), encoding="utf-8")
    assert run.main(["list", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "runtimes=container,host\n" in out  # a scenario that requires nothing says nothing of it
    assert "runtimes=vm requires=docker\n" in out
    # A scenario's repeats and its run's spend cap, each only when it names one.
    assert "runtimes=vm requires=docker repeat=1 max_spend_usd=190\n" in out
    assert "runtimes=host,container,vm max_spend_usd=0.5\n" in out
    # Its optional groups, which a run takes with --with.
    assert "runtimes=vm groups=extras,polish\n" in out


WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "benchmark.yml"


def workflow_step(title: str) -> str:
    """The `run:` script of one step of the benchmark workflow, dedented."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"      - name: {title}")
    body = lines[lines.index("        run: |", start) + 1 :]
    script = []
    for line in body:
        if line.strip() and not line.startswith("          "):
            break
        script.append(line[10:])
    return "\n".join(script) + "\n"


@pytest.fixture
def scenario_step(tmp_path):
    """The run-every-scenario step, with `uv` and `claude` stubbed on PATH."""
    import shutil

    if shutil.which("bash") is None:
        pytest.skip("needs bash")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    # The stub records each scenario, fails the one named `broken`, and
    # refuses the one named `elsewhere` as run.py refuses a scenario that
    # does not list the runtime.
    (bin_dir / "uv").write_text(
        # `uv run --locked benchmark/run.py --scenario <name>`: three words, then the script.
        '#!/bin/sh\necho "$@" > "$ARGV_LOG"\nshift 3\necho "$2" >> "$UV_LOG"\n'
        f'[ "$2" = elsewhere ] && exit {run.NOT_LISTED}\n[ "$2" != broken ]\n',
        encoding="utf-8",
    )
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    scenarios = tmp_path / "benchmark" / "scenarios"
    scenarios.mkdir(parents=True)
    for name in ("a.yaml", "b.yml", "broken.json", "c.yaml", "elsewhere.yaml", "notes.txt"):
        (scenarios / name).write_text("{}", encoding="utf-8")
    script = tmp_path / "step.sh"
    script.write_text(workflow_step("run every scenario"), encoding="utf-8")
    # The step names no shell, so Actions runs it under `bash -e`, and so does the fixture.
    step = WORKFLOW.read_text(encoding="utf-8").split("      - name: run every scenario\n")[1].split("      - name: ")[0]
    assert "shell:" not in step

    def run_step(**inputs):
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "UV_LOG": str(tmp_path / "uv.log"),
            "ARGV_LOG": str(tmp_path / "argv.log"),
            **{"SCENARIOS": "all", "PROVIDERS": "3", "EFFORT": "medium", "REPEAT": "1", "MAX_SPEND_USD": "10", **inputs},
        }
        (tmp_path / "uv.log").unlink(missing_ok=True)
        # As Actions runs a step that names no shell: `bash -e {0}`.
        done = subprocess.run(["bash", "-e", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)
        log = tmp_path / "uv.log"
        ran = log.read_text(encoding="utf-8").split() if log.exists() else []
        return done.returncode, ran, done.stdout + done.stderr

    return run_step


def test_the_workflow_runs_every_cataloged_scenario_and_fails_at_the_end(scenario_step):
    code, ran, out = scenario_step()
    assert ran == ["a", "b", "broken", "c", "elsewhere"]  # every suffix the catalog reads, past the failure
    assert code == 1 and "failed scenarios: broken\n" in out
    code, ran, _ = scenario_step(SCENARIOS="a c", PROVIDERS="anthropic,openai", REPEAT="2")
    assert code == 0 and ran == ["a", "c"]


def test_the_workflow_skips_and_names_a_scenario_that_does_not_list_the_container(scenario_step):
    code, ran, out = scenario_step(SCENARIOS="elsewhere a elsewhere c")
    assert code == 0 and ran == ["elsewhere", "a", "elsewhere", "c"]  # a skip ends nothing after it
    assert "::notice::skipped, since they do not list the container runtime: elsewhere elsewhere\n" in out
    assert "failed scenarios" not in out


def test_a_failed_scenario_ends_nothing_after_it(scenario_step):
    code, ran, out = scenario_step(SCENARIOS="broken a elsewhere c")
    assert ran == ["broken", "a", "elsewhere", "c"]
    assert code == 1 and "failed scenarios: broken\n" in out
    assert "skipped, since they do not list the container runtime: elsewhere\n" in out


@pytest.mark.parametrize(
    "inputs",
    [
        {"PROVIDERS": "3; touch pwned"},
        {"PROVIDERS": "$(id)"},
        {"EFFORT": "maximum"},
        {"EFFORT": "high --dry-run"},
        {"REPEAT": "0"},
        {"REPEAT": "6"},
        {"REPEAT": "10"},
        {"REPEAT": "1 --dry-run"},
        {"MAX_SPEND_USD": "0"},
        {"MAX_SPEND_USD": "51"},
        {"MAX_SPEND_USD": "100"},
        {"MAX_SPEND_USD": "2.5"},
        {"MAX_SPEND_USD": ""},
    ],
)
def test_the_workflow_refuses_inputs_outside_their_pattern(scenario_step, tmp_path, inputs):
    code, ran, _ = scenario_step(**inputs)
    assert code == 2 and ran == []
    assert not (tmp_path / "pwned").exists()


def test_a_scenario_name_is_never_spliced_into_the_script(scenario_step, tmp_path):
    _code, ran, _ = scenario_step(SCENARIOS='a"; touch pwned; echo "')
    assert not (tmp_path / "pwned").exists()
    assert "pwned;" in ran  # the words reach run.py as arguments, not as shell


def test_every_repeat_starts_empty_and_keeps_its_files_at_their_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])  # no provider is called
    # the subject counts what it finds, then writes two files of one name and one named like the answer
    script = (
        "import os, pathlib; n = len(os.listdir('.')); print(n); "
        "[pathlib.Path(d).mkdir(exist_ok=True) for d in ('a', 'b')]; "
        "[pathlib.Path(p).write_text(p) for p in ('a/notes.md', 'b/notes.md', 'answer.md')]"
    )
    scenario = {
        "name": "files",
        "kind": "command",
        "subject": {"argv": [sys.executable, "-c", script]},
        "artifact": {"stdout": True, "files": ["**/*.md", "*.md"]},
        "rubric": "r",
        "runtimes": EVERYWHERE,
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "files.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert run.main(["--scenario", str(path), "--out", "runs", "--repeat", "2"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    for index in (0, 1):
        art = run_dir / "artifacts" / str(index)
        assert (art / "answer.md").read_text(encoding="utf-8") == "0\n"  # an empty workspace, both times
        assert (art / "workspace" / "a" / "notes.md").read_text(encoding="utf-8") == "a/notes.md"
        assert (art / "workspace" / "b" / "notes.md").read_text(encoding="utf-8") == "b/notes.md"
        assert (art / "workspace" / "answer.md").read_text(encoding="utf-8") == "answer.md"
        assert results["repeats"][index]["artifact_paths"] == [
            f"artifacts/{index}/answer.md",
            f"artifacts/{index}/workspace/a/notes.md",
            f"artifacts/{index}/workspace/answer.md",
            f"artifacts/{index}/workspace/b/notes.md",
        ]
    # The subject lived in a sandbox outside the run folder, removed after the run.
    assert not (run_dir / "home").exists() and not (run_dir / "workspace").exists()


def test_a_collected_file_is_kept_as_its_bytes_and_a_binary_one_is_not_shown_to_the_judges(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    prompts: list[str] = []

    def judge_all(flags, prompt, *args, **kwargs):
        prompts.append(prompt)
        return []

    monkeypatch.setattr(run.J, "judge_all", judge_all)
    image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256))
    script = (
        "import pathlib; "
        f"pathlib.Path('shot.png').write_bytes({image!r}); "
        "pathlib.Path('notes.md').write_bytes(b'caf\\xc3\\xa9 \\xff')"
    )
    scenario = {
        "name": "bytes",
        "kind": "command",
        "subject": {"argv": [sys.executable, "-c", script]},
        "artifact": {"stdout": False, "files": ["*.png", "*.md"]},
        "rubric": "r",
        "runtimes": EVERYWHERE,
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "bytes.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    kept = run_dir / "artifacts" / "0" / "workspace"
    assert (kept / "shot.png").read_bytes() == image
    assert (kept / "notes.md").read_bytes() == b"caf\xc3\xa9 \xff"  # as written, not decoded and written again
    (prompt,) = prompts
    assert f"### File: shot.png\n\n(binary, {len(image)} bytes; not shown)" in prompt
    assert "### File: notes.md\n\ncaf\u00e9 \ufffd" in prompt


def test_the_subject_reaches_no_answer_key_and_no_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")  # the built-in matrix, no pyyaml needed

    # What a subject can read: the staged plugin payload and the staged
    # target. The fixtures' answer keys, the benchmark folder, and every
    # CLAUDE.md up the tree are out of reach.
    probe = (
        "import pathlib, sys\n"
        "plugin, target = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])\n"
        "cwd = pathlib.Path.cwd()\n"
        "seen = [str(p) for p in plugin.rglob('*.expected.yaml')]\n"
        "seen += [str(p) for p in target.parent.glob('*.expected.yaml')]\n"
        "seen += [str(d / 'CLAUDE.md') for d in [cwd, *cwd.parents] if (d / 'CLAUDE.md').exists()]\n"
        "seen += ['benchmark'] if (plugin / 'benchmark').exists() else []\n"
        "print('skills' if (plugin / 'skills').is_dir() else 'no skills')\n"
        "print('target' if any(target.iterdir()) else 'no target')\n"
        "print('seen:' + ','.join(seen))\n"
    )
    scenario = {
        "name": "reach",
        "kind": "command",
        "subject": {
            "argv": [sys.executable, "-c", probe, "{plugin}", "{target}"],
            "target": str(run.ROOT / "benchmark" / "fixtures" / "review-om"),
        },
        "artifact": {"stdout": True},
        "rubric": "r",
        "runtimes": EVERYWHERE,
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "reach.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run.main(["--scenario", str(path), "--out", "runs"])
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    answer = (run_dir / "artifacts" / "0" / "answer.md").read_text(encoding="utf-8")
    assert "skills" in answer and "no skills" not in answer
    assert "target" in answer and "no target" not in answer
    assert "seen:\n" in answer, answer


def test_the_workflow_gives_every_scenario_the_run_s_spend_cap(scenario_step, tmp_path):
    code, ran, _ = scenario_step(SCENARIOS="a c", MAX_SPEND_USD="25")
    logged = (tmp_path / "argv.log").read_text(encoding="utf-8").split()
    assert code == 0 and ran == ["a", "c"] and logged[logged.index("--max-spend-usd") + 1] == "25"
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'default: "10"' in workflow[workflow.index("      max_spend_usd:") :].split("\n\n")[0]


def test_the_workflow_passes_judges_and_effort_only_when_given(scenario_step, tmp_path):
    code, ran, _ = scenario_step(SCENARIOS="a", PROVIDERS="", EFFORT="")
    assert code == 0 and ran == ["a"]
    assert "--providers" not in (tmp_path / "argv.log").read_text(encoding="utf-8")
    assert "--effort" not in (tmp_path / "argv.log").read_text(encoding="utf-8")
    code, _, _ = scenario_step(SCENARIOS="a", PROVIDERS="7", EFFORT="high")
    logged = (tmp_path / "argv.log").read_text(encoding="utf-8").split()
    assert code == 0 and logged[logged.index("--providers") + 1] == "7" and logged[logged.index("--effort") + 1] == "high"


JUDGE_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "XAI_API_KEY", "GROK_API_KEY")


@pytest.mark.parametrize("runtime", ["host", "vm"])
def test_the_subject_holds_its_own_key_and_no_judge_key(tmp_path, monkeypatch, runtime):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])  # no provider is called
    for name in JUDGE_KEYS:
        monkeypatch.setenv(name, f"judge-{name}")
    monkeypatch.setenv("SUBJECT_ANTHROPIC_API_KEY", "subject-key")
    script = "import os; print(sorted((k, v) for k, v in os.environ.items() if k.endswith('_KEY') or 'judge-' in v))"
    scenario = {
        "name": "keys",
        "kind": "command",
        "subject": {"argv": [sys.executable, "-c", script]},
        "rubric": "r",
        "runtimes": EVERYWHERE,
    }
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--providers", "15", "--repeat", "1"]
    if runtime == "vm":
        config = tmp_path / "vm.json"
        config.write_text(json.dumps(vm_config(tmp_path)), encoding="utf-8")

        argv += ["--runtime", "vm", "--runtime-config", str(config)]
    assert run.main(argv) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    answer = (run_dir / "artifacts" / "0" / "answer.md").read_text(encoding="utf-8")
    assert answer == "[('ANTHROPIC_API_KEY', 'subject-key')]\n"


def test_a_subject_key_that_is_a_judge_key_is_never_handed_on(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "shared")
    rt = run.RT.build("host", tmp_path)
    rt.prepare()
    script = "import os; print(sorted(k for k, v in os.environ.items() if v == 'shared'))"
    with run.CliStream(tmp_path / "cli.jsonl") as stream:
        status = rt.run(
            [sys.executable, "-c", script], rt.workspace, {"PATH": os.environ["PATH"], "ANTHROPIC_API_KEY": "shared"}, stream
        )
    assert status.ok
    lines = [r["line"] for r in run.CliStream.read(tmp_path / "cli.jsonl")]
    assert "[]" in lines
    assert any("ANTHROPIC_API_KEY holds a judge's key" in line for line in lines)


def test_the_container_names_only_the_subject_key(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"):
        monkeypatch.setenv(name, "k")
    monkeypatch.setenv("SUBJECT_ANTHROPIC_API_KEY", "s")
    path = tmp_path / "one.json"
    path.write_text(json.dumps(SKILL), encoding="utf-8")
    argv = ["--scenario", str(path), "--providers", "15", "--runtime", "container", "--dry-run"]
    assert run.main([*argv, "--out", str(tmp_path / "runs")]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["runtime"]["config"]["keys"] == ["ANTHROPIC_API_KEY"]
    monkeypatch.delenv("SUBJECT_ANTHROPIC_API_KEY")
    assert run.main([*argv, "--out", str(tmp_path / "later")]) == 0
    (later,) = (tmp_path / "later").glob("*/*")
    assert json.loads((later / "run.json").read_text(encoding="utf-8"))["runtime"]["config"]["keys"] == []


def test_a_failed_subject_is_never_judged_and_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    judged: list[str] = []

    def judge_all(*args, **kwargs):
        judged.append("called")
        return []

    monkeypatch.setattr(run.J, "judge_all", judge_all)
    scenario = {
        "name": "missing",
        "kind": "command",
        # A binary that is not there: exit 127, as the shell records it.
        "subject": {"argv": [str(tmp_path / "no-such-claude"), "-p", "hi"]},
        "rubric": "r",
        "runtimes": EVERYWHERE,
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "2"]) == 6
    assert judged == []
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert [r["exit_status"]["code"] for r in results["repeats"]] == [127, 127]
    assert all(r["judgements"] == [] for r in results["repeats"])
    assert results["summary"]["overall_mean"] == 0.0  # a failed repeat is a failure, never a gap in the mean
    assert results["summary"]["failed_repeats"] == [0, 1]
    assert any("not judged" in note for note in results["notes"])


def test_a_run_repeats_three_times_unless_told_otherwise(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    assert run.build_parser().parse_args([]).repeat is None  # the scenario's, else run.REPEAT
    assert run.REPEAT == 3

    def dry_run(scenario: dict, *flags: str) -> dict:
        path = tmp_path / "one.json"
        path.write_text(json.dumps(scenario), encoding="utf-8")
        out = tmp_path / "runs" / str(len(list((tmp_path / "runs").glob("*"))) if (tmp_path / "runs").exists() else 0)
        assert run.main(["--scenario", str(path), "--out", str(out), "--dry-run", *flags]) == 0
        (run_dir,) = out.glob("*/*")
        resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        return {"repeat": resolved["repeat"], "max_spend_usd": resolved["max_spend_usd"]}

    # A scenario that names neither: 3 repeats and no run cap.
    assert dry_run(SKILL) == {"repeat": 3, "max_spend_usd": None}
    # A scenario that names them: a run without the flags takes them.
    named = dict(SKILL, repeat=1, max_spend_usd=190)
    assert dry_run(named) == {"repeat": 1, "max_spend_usd": 190.0}
    # Each flag overrides its key, one without the other.
    assert dry_run(named, "--repeat", "2") == {"repeat": 2, "max_spend_usd": 190.0}
    assert dry_run(named, "--max-spend-usd", "5") == {"repeat": 1, "max_spend_usd": 5.0}
    assert dry_run(SKILL, "--repeat", "4", "--max-spend-usd", "7.5") == {"repeat": 4, "max_spend_usd": 7.5}
    workflow = WORKFLOW.read_text(encoding="utf-8")
    repeat = workflow[workflow.index("      repeat:") :]
    assert 'default: "3"' in repeat.split("\n\n")[0]


@pytest.mark.parametrize("repeat", ["0", "-1"])
def test_a_repeat_flag_below_1_is_refused_before_anything_starts(tmp_path, monkeypatch, capsys, repeat):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "one.json"
    path.write_text(json.dumps(dict(SKILL, repeat=2)), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", repeat]) == 2
    assert f"--repeat is a whole number of at least 1, got {repeat}" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_the_workflow_runs_every_scenario_strict():
    workflow = (RUN.parent.parent / ".github" / "workflows" / "benchmark.yml").read_text(encoding="utf-8")
    assert "--strict" in workflow


def test_two_runs_in_the_same_second_get_two_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])  # no provider is called
    monkeypatch.setattr(run.time, "strftime", lambda *args: "20260101-000000")  # one second for both
    scenario = {
        "name": "twice",
        "kind": "command",
        "subject": {"argv": [sys.executable, "-c", "import uuid; print(uuid.uuid4().hex)"]},
        "rubric": "r",
        "runtimes": EVERYWHERE,
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "twice.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    out = str(tmp_path / "runs")
    assert run.main(["--scenario", str(path), "--out", out]) == 0
    assert run.main(["--scenario", str(path), "--out", out]) == 0
    first, second = sorted((tmp_path / "runs").glob("*/*"))
    answers = [(d / "artifacts" / "0" / "answer.md").read_text(encoding="utf-8") for d in (first, second)]
    assert answers[0] != answers[1] and answers[0].count("\n") == answers[1].count("\n") == 1
    for folder in (first, second):
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        assert results["run_id"] == folder.name


def test_an_answer_with_a_unicode_line_separator_is_kept_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])  # no provider is called
    script = "import sys; sys.stdout.buffer.write('one\\u2028two\\u2029three\\n'.encode())"
    scenario = {
        "name": "sep",
        "kind": "command",
        "subject": {"argv": [sys.executable, "-c", script]},
        "rubric": "r",
        "runtimes": EVERYWHERE,
    }
    path = tmp_path / "sep.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--providers", "1"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    assert (run_dir / "artifacts" / "0" / "answer.md").read_text(encoding="utf-8") == "one\u2028two\u2029three\n"


def test_the_judges_read_the_target_the_subject_saw(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    prompts: list[str] = []

    def judge_all(flags, prompt, *args, **kwargs):
        prompts.append(prompt)
        return []

    monkeypatch.setattr(run.J, "judge_all", judge_all)
    read_from: list[Path] = []
    source = run.E.source

    def spy(target, globs, *args, **kwargs):
        read_from.append(Path(target))
        return source(target, globs, *args, **kwargs)

    monkeypatch.setattr(run.E, "source", spy)
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (target / "tests" / "test_a.py").write_text("def test_a(): ...\n", encoding="utf-8")
    probe = (
        "import pathlib, sys; root = pathlib.Path(sys.argv[1]); "
        "print(sorted(p.relative_to(root).as_posix() for p in root.rglob('*.py')))"
    )
    scenario = {
        "name": "seen",
        "kind": "command",
        "subject": {"argv": [sys.executable, "-c", probe, "{target}"], "target": str(target)},
        "evidence": {"files": ["**/*.py"]},
        "rubric": "r",
        "runtimes": EVERYWHERE,
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "seen.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs")]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    answer = (run_dir / "artifacts" / "0" / "answer.md").read_text(encoding="utf-8")
    assert answer == "['src/a.py', 'tests/test_a.py']\n"  # the subject saw the tests
    assert "### Source: tests/test_a.py" in prompts[0] and "### Source: src/a.py" in prompts[0]
    (staged,) = read_from
    assert staged != target and staged.name == "target"  # the staged copy, not the original


def test_the_workflow_runs_the_subject_in_the_container_built_before_the_keys():
    # On the host the subject could read the harness's environment and the
    # answer files; in the container it reaches its mounts and its one key.
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "--runtime container" in workflow_step("run every scenario")
    build = workflow.index("      - name: build the subject's image")
    run_step = workflow.index("      - name: run every scenario")
    assert build < run_step and "docker build -t swe-guidelines-benchmark:latest" in workflow[build:run_step]
    assert "_API_KEY" not in workflow[:run_step]  # no key is in reach while anything is installed


def test_strict_refuses_a_skill_whose_subject_has_no_key_of_its_own(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "judge")
    monkeypatch.delenv("SUBJECT_ANTHROPIC_API_KEY", raising=False)
    path = tmp_path / "one.json"
    path.write_text(json.dumps(SKILL), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--providers", "1", "--strict"]
    assert run.main(argv) == 3
    assert "SUBJECT_ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_a_skill_subject_runs_on_the_model_it_is_pinned_to():
    argv = run.subject_argv(S.from_data(SKILL), "swe-guidelines", "/plugin", None, "claude", model="claude-opus-5")
    assert argv[argv.index("--model") + 1] == "claude-opus-5"


def test_the_subject_model_defaults_to_the_scenario_then_the_matrix():
    matrix = run.J.DEFAULT_MATRIX
    assert run.subject_model(S.from_data(SKILL), None, matrix) == matrix["anthropic"]["model"]
    pinned = S.from_data(
        dict(SKILL, subject={"skill": "arch-review-om", "prompt": "Review it.", "model": "claude-sonnet-5", "max_usd": 1})
    )
    assert run.subject_model(pinned, None, matrix) == "claude-sonnet-5"
    assert run.subject_model(pinned, "claude-haiku-5", matrix) == "claude-haiku-5"
    command = S.from_data({"name": "c", "kind": "command", "subject": {"argv": ["true"]}, "rubric": "r", "runtimes": EVERYWHERE})
    assert run.subject_model(command, None, matrix) is None


def test_the_envelope_gives_the_answer_the_models_and_the_error():
    envelope = json.dumps({"type": "result", "is_error": False, "result": "the answer", "modelUsage": {"claude-opus-5": {}}})
    assert run.read_envelope(envelope) == ("the answer", ["claude-opus-5"], False)
    failed = json.dumps({"type": "result", "is_error": True, "result": "API Error: 401", "modelUsage": {}})
    assert run.read_envelope(failed) == ("API Error: 401", [], True)


def test_the_envelope_gives_the_subject_tokens_and_claude_code_s_own_cost():
    envelope = json.dumps(
        {
            "type": "result",
            "result": "the answer",
            "total_cost_usd": 0.4213,
            "usage": {
                "input_tokens": 20,
                "cache_creation_input_tokens": 3000,
                "cache_read_input_tokens": 40000,
                "output_tokens": 1500,
                "server_tool_use": {"web_search_requests": 0},
            },
        }
    )
    usage, cost = run.read_envelope_spend(envelope)
    assert usage == {
        "input_tokens": 43020,
        "output_tokens": 1500,
        "cache_read_input_tokens": 40000,
        "cache_creation_input_tokens": 3000,
    }
    assert cost == 0.4213
    assert run.read_envelope_spend("not an envelope") == ({}, None)
    assert run.read_envelope("plain text\n") == ("plain text\n", [], False)


def test_the_envelope_s_thinking_tokens_are_the_subject_s_reasoning():
    usage = {"input_tokens": 14, "output_tokens": 6281, "output_tokens_details": {"thinking_tokens": 2854}}
    envelope = {"type": "result", "result": "a", "total_cost_usd": 0.27, "usage": usage}
    recorded, cost = run.read_envelope_spend(json.dumps(envelope))
    assert recorded["reasoning_tokens"] == 2854
    assert recorded["output_tokens"] == 6281
    assert cost == 0.27


def test_the_thinking_tokens_per_model_count_when_the_usage_names_none():
    envelope = {
        "type": "result",
        "result": "a",
        "usage": {"input_tokens": 1, "output_tokens": 50},
        "modelUsage": {"claude-a": {"thinkingTokens": 10}, "claude-b": {"thinkingTokens": 5}, "claude-c": {}},
    }
    assert run.read_envelope_spend(json.dumps(envelope))[0]["reasoning_tokens"] == 15


def test_an_envelope_that_reports_no_thinking_records_no_reasoning():
    envelope = {"type": "result", "result": "a", "usage": {"input_tokens": 1, "output_tokens": 50}, "modelUsage": {"m": {}}}
    assert "reasoning_tokens" not in run.read_envelope_spend(json.dumps(envelope))[0]


def envelope_scenario(tmp_path, envelope):
    script = f"print({json.dumps(json.dumps(envelope))})"
    scenario = {
        "name": "envelope",
        "kind": "command",
        "subject": {"argv": [sys.executable, "-c", script]},
        "rubric": "r",
        "runtimes": EVERYWHERE,
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "envelope.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    return path


def test_an_envelope_that_reports_an_error_fails_the_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    judged: list[str] = []

    def judge_all(*args, **kwargs):
        judged.append("called")
        return []

    monkeypatch.setattr(run.J, "judge_all", judge_all)
    path = envelope_scenario(tmp_path, {"type": "result", "is_error": True, "result": "API Error: 401"})
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1"]) == 6
    assert judged == []
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["repeats"][0]["exit_status"]["code"] == 0
    assert results["repeats"][0]["exit_status"]["is_error"] is True


def test_the_model_the_envelope_reports_is_recorded_beside_the_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    envelope = {"type": "result", "is_error": False, "result": "ok", "modelUsage": {"claude-sonnet-5": {}}}
    path = envelope_scenario(tmp_path, envelope)
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1", "--subject-model", "claude-opus-5"]
    assert run.main(argv) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["subject_model"] == "claude-opus-5"
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["subject"]["model"] == "claude-opus-5"
    assert results["repeats"][0]["subject_models"] == ["claude-sonnet-5"]
    assert any("claude-opus-5" in note and "claude-sonnet-5" in note for note in results["notes"])


def test_the_workflow_redacts_the_run_folders_before_it_shows_or_uploads_them():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    run_step = workflow.index("      - name: run every scenario")
    redact = workflow.index("      - name: redact every run folder")
    summary = workflow.index("      - name: write the summary")
    upload = workflow.index("      - name: keep every run as evidence")
    assert run_step < redact < summary < upload
    assert "benchmark/run.py redact --out benchmark/runs" in workflow[redact:summary]
    for step in (workflow[summary:upload], workflow[upload:]):
        assert "steps.redact.outcome == 'success'" in step


def test_the_workflow_reaches_the_run_folders_in_each_scenario_s_folder():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    summary = workflow.index("      - name: write the summary")
    upload = workflow.index("      - name: keep every run as evidence")
    assert "for report in benchmark/runs/*/*/report.md; do" in workflow[summary:upload]
    for left_out in ("home", "tmp", "workspace"):
        assert f"!benchmark/runs/*/*/{left_out}\n" in workflow[upload:]
    assert "benchmark/runs/*/report.md" not in workflow and "!benchmark/runs/*/home" not in workflow


def test_the_workflow_runs_behind_the_benchmark_environment():
    assert "    environment: benchmark\n" in WORKFLOW.read_text(encoding="utf-8")


def test_the_redact_command_scrubs_every_run_folder_under_a_runs_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "judge-openai-value")
    answer = tmp_path / "one" / "20260101-000000-one-aa" / "artifacts" / "0" / "answer.md"
    answer.parent.mkdir(parents=True)
    answer.write_text("judge-openai-value\n", encoding="utf-8")
    assert run.main(["redact", "--out", str(tmp_path)]) == 0
    assert answer.read_text(encoding="utf-8") == "[redacted]\n"
    assert "redacted 1 key(s) in one/20260101-000000-one-aa/artifacts/0/answer.md" in capsys.readouterr().out


def test_the_listing_says_whether_the_subject_has_a_key_of_its_own(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "SCENARIOS", tmp_path)
    monkeypatch.delenv("SUBJECT_ANTHROPIC_API_KEY", raising=False)
    assert run.main(["list", "--out", str(tmp_path)]) == 0
    assert "subject    key=absent  (SUBJECT_ANTHROPIC_API_KEY)" in capsys.readouterr().out


def test_a_run_records_the_checkout_the_target_and_the_answers_by_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])  # no provider is called
    fixtures = run.ROOT / "benchmark" / "fixtures"
    scenario = {
        "name": "versions",
        "kind": "command",
        "subject": {"argv": [sys.executable, "-c", "print('ok')"], "target": str(fixtures / "review-om")},
        "evidence": {"expected": str(fixtures / "review-om.expected.yaml")},
        "rubric": "r",
        "runtimes": EVERYWHERE,
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    versions = resolved["versions"]
    assert results["versions"] == versions
    assert versions["checkout"]["commit"] == run.git_sha(run.ROOT)
    manifest = json.loads((run.ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert versions["checkout"]["plugin_version"] == manifest["version"]
    assert isinstance(versions["checkout"]["dirty"], bool)
    assert versions["target"] == {"path": "benchmark/fixtures/review-om", "sha256": run.V.sha256_tree(fixtures / "review-om")}
    assert versions["expected"] == {
        "path": "benchmark/fixtures/review-om.expected.yaml",
        "sha256": run.V.sha256_file(fixtures / "review-om.expected.yaml"),
    }
    assert versions["claude_code"] is None and versions["image"] is None  # a command runs no Claude Code, the host no image
    # No path of this machine stands in for the target or the answers.
    assert resolved["runtime"]["target"] == "benchmark/fixtures/review-om"
    assert resolved["evidence"]["expected"] == "benchmark/fixtures/review-om.expected.yaml"
    assert results["subject"]["target"] == "benchmark/fixtures/review-om"
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## Versions" in report and "- Target: `benchmark/fixtures/review-om`, sha256" in report


def fake_claude(tmp_path, version_line, exit_code=0):
    """A Claude Code stand-in: `--version` prints the line; anything else prints an envelope."""
    envelope = {"type": "result", "is_error": False, "result": "ok", "modelUsage": {"claude-opus-5": {}}}
    path = tmp_path / "claude"
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print({version_line!r})\n"
        f"    sys.exit({exit_code})\n"
        f"print(json.dumps({envelope!r}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return str(path)


def test_a_skill_run_records_the_claude_code_its_runtime_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    path = tmp_path / "one.json"
    path.write_text(json.dumps(SKILL), encoding="utf-8")
    claude = fake_claude(tmp_path, "9.9.9 (Claude Code)")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1", "--claude", claude]
    assert run.main([*argv, "--subject-model", "claude-opus-5"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["versions"]["claude_code"] == "9.9.9 (Claude Code)"
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["versions"]["claude_code"] == "9.9.9 (Claude Code)"
    assert "- Claude Code: `9.9.9 (Claude Code)`." in (run_dir / "report.md").read_text(encoding="utf-8")


def test_a_claude_code_that_does_not_answer_is_named_in_the_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    path = tmp_path / "one.json"
    path.write_text(json.dumps(SKILL), encoding="utf-8")
    claude = fake_claude(tmp_path, "broken", exit_code=1)
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1", "--claude", claude]
    assert run.main([*argv, "--subject-model", "claude-opus-5"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["versions"]["claude_code"] is None
    assert any("--version` answered nothing in the host runtime" in note for note in results["notes"])


def test_a_dry_run_records_the_checkout_and_asks_the_runtime_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    asked: list[list[str]] = []
    monkeypatch.setattr(run.RT.BaseRuntime, "probe", lambda self, argv, timeout_s=120: asked.append(argv))
    path = tmp_path / "one.json"
    path.write_text(json.dumps(SKILL), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--dry-run"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    versions = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["versions"]
    assert versions["checkout"]["commit"] == run.git_sha(run.ROOT)
    assert versions["claude_code"] is None and asked == []


def vm_config(tmp_path, **override):
    """A vm runtime config that runs here, as another machine would.

    `env -i` stands in for the prefix: the command starts in an empty
    environment, as a remote shell does, with nothing of this one. `cp -R`
    stands in for the copy.
    """
    return {
        "exec_prefix": ["env", "-i", "PATH=/usr/bin:/bin"],
        "copy": ["cp", "-R", "{local}", "{remote}"],
        "remote_workspace": str(tmp_path / "remote"),
        **override,
    }


def run_vm(tmp_path, scenario, config, *extra):
    """One repeat of a scenario on the vm runtime, and its run folder."""
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    config_path = tmp_path / "vm.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1", *extra]
    assert run.main([*argv, "--runtime", "vm", "--runtime-config", str(config_path)]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    return run_dir


def test_a_vm_run_reads_the_copies_this_checkout_staged(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    claude = fake_claude(tmp_path, "9.9.9 (Claude Code)")
    run_dir = run_vm(tmp_path, SKILL, vm_config(tmp_path), "--subject-model", "claude-opus-5", "--claude", claude)
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    plugin = str(tmp_path / "remote" / run_dir.name / "plugin")
    assert results["subject"]["plugin"] == plugin
    assert results["subject"]["argv"][results["subject"]["argv"].index("--plugin-dir") + 1] == plugin
    assert results["versions"]["checkout"]["commit"] == run.git_sha(run.ROOT)
    assert not any("not that one" in note for note in results["notes"])  # the checkout is what the subject read


def test_a_vm_run_on_a_placed_plugin_says_its_checkout_is_not_that_one(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    claude = fake_claude(tmp_path, "9.9.9 (Claude Code)")
    config = vm_config(tmp_path, remote_plugin="/srv/plugin")
    run_dir = run_vm(tmp_path, SKILL, config, "--subject-model", "claude-opus-5", "--claude", claude)
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["versions"]["claude_code"] == "9.9.9 (Claude Code)"
    assert results["subject"]["plugin"] == "/srv/plugin"
    assert any("/srv/plugin" in note and "not that one" in note for note in results["notes"])


def test_a_vm_run_keeps_the_subject_key_out_of_every_file_it_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    secret = "subject-key-for-the-run"
    monkeypatch.setenv("SUBJECT_ANTHROPIC_API_KEY", secret)
    script = "import os; print(len(os.environ.get('ANTHROPIC_API_KEY', '')))"
    scenario = {
        "name": "keys",
        "kind": "command",
        "subject": {"argv": [sys.executable, "-c", script]},
        "rubric": "r",
        "runtimes": EVERYWHERE,
    }
    run_dir = run_vm(tmp_path, scenario, vm_config(tmp_path))
    assert (run_dir / "artifacts" / "0" / "answer.md").read_text(encoding="utf-8") == f"{len(secret)}\n"
    for file in run_dir.rglob("*"):
        if file.is_file():
            assert secret not in file.read_text(encoding="utf-8", errors="replace"), file
    assert not (tmp_path / "remote" / run_dir.name).exists()  # the key file went with the run's folder


def test_a_vm_run_removes_what_each_repeats_docker_made_and_records_it(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    before = {*DOCKER_OWN, ("container", "c-kept", "kept-1")}
    state = docker_stand_in(tmp_path, *before)
    docker = str(state / "docker")
    monkeypatch.setattr(run.RT, "DOCKER", docker)
    # The subject says what Docker holds, then starts a database with its volume.
    stack = f"{docker} ps -a; {docker} create container c-db acme-db-1; {docker} create volume acme_pgdata acme_pgdata"
    scenario = {"name": "stack", "kind": "command", "subject": {"argv": ["sh", "-c", stack]}, "rubric": "r", "runtimes": ["vm"]}
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    config = tmp_path / "vm.json"
    config.write_text(json.dumps(vm_config(tmp_path)), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "2", "--runtime-config", str(config)]
    assert run.main(argv) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    for index in (0, 1):  # each repeat's subject starts beside none of an earlier one's stack
        assert (run_dir / "artifacts" / str(index) / "answer.md").read_text(encoding="utf-8") == "container c-kept kept-1\n"
    notes = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))["notes"]
    removed = "removed what the subject's Docker made on the other machine: 1 container (acme-db-1), 1 volume (acme_pgdata)"
    assert [n for n in notes if "Docker" in n] == [f"repeat 0: {removed}", f"repeat 1: {removed}"]
    assert docker_holds(state) == before  # nothing that was there before the run went
    assert docker_removals(state) == ["rm -f -v c-db", "volume rm -f acme_pgdata"] * 2


def test_a_vm_run_on_a_machine_that_does_not_answer_still_writes_its_results(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    scenario = {"name": "down", "kind": "command", "subject": {"argv": ["true"]}, "rubric": "r", "runtimes": EVERYWHERE}
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    config = tmp_path / "vm.json"
    config.write_text(json.dumps(vm_config(tmp_path, exec_prefix=[str(tmp_path / "no-such-prefix")])), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "2"]
    assert run.main([*argv, "--runtime", "vm", "--runtime-config", str(config)]) == 6  # every repeat failed
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert [r["exit_status"]["code"] for r in results["repeats"]] == [127, 127]
    assert not any("was not removed there" in note for note in results["notes"])  # it made nothing there
    assert (run_dir / "report.md").is_file()
    assert "the other machine did not answer" in (run_dir / "streams" / "cli.jsonl").read_text(encoding="utf-8")


def test_a_vm_config_that_reaches_no_machine_is_refused_before_the_run_starts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    scenario = {"name": "nowhere", "kind": "command", "subject": {"argv": ["true"]}, "rubric": "r", "runtimes": EVERYWHERE}
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    config = tmp_path / "vm.json"
    config.write_text(json.dumps({"remote_workspace": str(tmp_path / "remote")}), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--runtime", "vm", "--runtime-config", str(config)]
    assert run.main(argv) == 2
    assert "needs exec_prefix" in capsys.readouterr().err
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    assert list(run_dir.iterdir()) == []  # refused before run.json, as a missing copy is


def test_a_vm_dry_run_names_the_copies_there_and_touches_nothing(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    called: list[list[str]] = []

    def helper(self, argv, stdin=None, timeout_s=None, out=None):
        called.append(argv)
        return 0

    monkeypatch.setattr(run.RT.VmRuntime, "helper", helper)
    # A scenario that needs Docker, on the planted checkout; with no
    # `--runtime`, it runs on the vm, its first runtime.
    target = str(run.BENCHMARK / "fixtures" / "review-om")
    scenario = dict(
        SKILL,
        runtimes=["vm"],
        requires=["docker"],
        subject={"skill": "arch-review-om", "prompt": "Review it.", "target": target, "max_usd": 1},
    )
    path = tmp_path / "system.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    config = run.BENCHMARK / "runtime" / "lima" / "runtime-config.yaml"
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--runtime-config", str(config)]
    assert run.main([*argv, "--dry-run"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["runtime"]["name"] == "vm"
    subject = resolved["subject_argv"]
    there = f"/var/tmp/swe-benchmark/{run_dir.name}"
    assert subject[subject.index("--plugin-dir") + 1] == f"{there}/plugin"
    assert subject[subject.index("--add-dir") + 1] == f"{there}/target"
    assert str(run.ROOT) not in json.dumps(subject) and called == []


@pytest.mark.parametrize(
    ("scenario", "runtime"),
    [("review-om", "container"), ("explain-tenancy", "container"), ("support-turn", "host"), ("support-turn", "container")],
)
def test_a_shipped_scenario_s_run_names_no_path_of_the_checkout(tmp_path, monkeypatch, scenario, runtime):
    pytest.importorskip("yaml")
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    argv = ["--scenario", scenario, "--out", str(tmp_path / "runs"), "--runtime", runtime, "--dry-run"]
    assert run.main(argv) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    text = (run_dir / "run.json").read_text(encoding="utf-8")
    resolved = json.loads(text)
    assert resolved["scenario"]["path"] == f"benchmark/scenarios/{scenario}.yaml"
    assert str(run.ROOT) not in text


def test_a_scenario_outside_the_checkout_keeps_its_path(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    path = tmp_path / "one.json"
    path.write_text(json.dumps(SKILL), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--dry-run"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["scenario"]["path"] == str(path.resolve())


# Agentic judges against references ----------------------------------------

JUDGED = {
    "references": {
        "guideline": {
            "score": 64,
            "gaps": [
                {
                    "severity": "high",
                    "what": "The plan names no lens.",
                    "in_output": "workspace/plan.md",
                    "in_reference": "README.md",
                }
            ],
            "strengths": ["It is short, in workspace/plan.md."],
        },
        "reference": {"score": 70, "gaps": [], "strengths": []},
    },
    "rationale": "A plan with no lens.",
}


def agentic_scenario(argv: list[str], **judges) -> dict:
    """A command subject that writes a plan, judged by one agentic judge against this checkout and a repository."""
    return {
        "name": "agentic",
        "kind": "command",
        "subject": {"argv": argv},
        "artifact": {"stdout": True, "files": ["*.md"]},
        "rubric": "Judge the plan against the lenses.",
        "runtimes": EVERYWHERE,
        "judges": {
            "providers": "anthropic",
            "mode": "agentic",
            "references": [
                {"name": "guideline", "weight": 0.25, "paths": ["lenses/README.md"]},
                {"name": "reference", "weight": 0.75, "repository": "https://github.com/acme/acme-system", "tag": "v1.0.0"},
            ],
            **judges,
        },
    }


@pytest.fixture
def acme(tmp_path, monkeypatch):
    """The repository reference, fetched from a repository made here in place of its URL."""
    repo = acme_repository(tmp_path, spec="(pinned at `v0.36.0`)")
    fetched: list[tuple[str, str]] = []
    fetch = run.RF.fetch

    def local(url: str, tag: str, dest: Path) -> str:
        fetched.append((url, tag))
        return fetch(repo.as_uri(), tag, dest)

    monkeypatch.setattr(run.RF, "fetch", local)
    return repo, fetched


def test_a_dry_run_resolves_the_references_and_calls_no_judge(tmp_path, monkeypatch, acme):
    repo, fetched = acme
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    called: list[str] = []
    monkeypatch.setattr(run.RF.A, "judge_agentic", lambda *args, **kwargs: called.append("judged"))
    sandboxes: list[Path] = []
    new_sandbox = run.RT.new_sandbox

    def kept_sandbox() -> Path:
        sandboxes.append(new_sandbox())
        return sandboxes[-1]

    monkeypatch.setattr(run.RT, "new_sandbox", kept_sandbox)
    path = tmp_path / "agentic.json"
    path.write_text(json.dumps(agentic_scenario(["true"])), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--dry-run"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert fetched == [("https://github.com/acme/acme-system", "v1.0.0")] and called == []
    judges = resolved["scenario"]["judges"]
    assert judges["mode"] == "agentic" and judges["budget"]["max_usd"] == 3.0 and "max_output_tokens" not in judges["budget"]
    references = resolved["versions"]["references"]
    assert references["reference"] == {
        "source": "repository",
        "url": "https://github.com/acme/acme-system",
        "tag": "v1.0.0",
        "commit": git("rev-parse", "v1.0.0^{commit}", cwd=repo).strip(),
        "pins": "v0.36.0",
    }
    guideline = references["guideline"]
    assert guideline["source"] == "checkout" and guideline["paths"] == ["lenses/README.md"] and len(guideline["sha256"]) == 64
    release = run.V.plugin_version(run.ROOT)
    assert resolved["notes"] == [f"reference `reference` pins the guideline at v0.36.0, and this checkout is at v{release}"]
    assert str(repo) not in json.dumps(resolved)  # the run names the reference by its URL
    assert sandboxes and not sandboxes[0].exists()  # the fetched tree went with the sandbox


def test_a_reference_that_cannot_be_staged_stops_the_run_before_anything_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    ran: list[str] = []
    monkeypatch.setattr(run.RT.BaseRuntime, "run", lambda *args, **kwargs: ran.append("subject"))
    scenario = agentic_scenario(["true"])
    scenario["judges"]["references"][0]["paths"] = ["no-such-folder"]
    path = tmp_path / "agentic.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs")]) == 2
    assert "reference guideline: the checkout holds no file or folder 'no-such-folder'" in capsys.readouterr().err
    assert ran == []


@needs_jsonschema
def test_an_agentic_run_judges_the_output_against_each_reference_and_weighs_the_scores(tmp_path, monkeypatch, acme):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    reads = [
        ("read_file", {"root": "output", "path": "answer.md"}),
        ("read_file", {"root": "output", "path": "workspace/plan.md"}),
        ("read_file", {"root": "guideline", "path": "lenses/README.md", "end": 3}),
        ("read_file", {"root": "reference", "path": "src/app.py"}),
    ]
    fake = FAKES["anthropic"]([turn(*reads), turn(("submit", JUDGED))])
    monkeypatch.setitem(run.J.CLIENTS, "anthropic", lambda key: fake)
    script = "import pathlib; pathlib.Path('plan.md').write_text('A plan.\\n'); print('planned')"
    path = tmp_path / "agentic.json"
    path.write_text(json.dumps(agentic_scenario([sys.executable, "-c", script])), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    # The judge read the subject's answer and file, this checkout's lens catalog, and the repository at its tag.
    results_back = [text for _, text in sent_results("anthropic", fake.requests[1])]
    assert "planned" in results_back[0] and "A plan." in results_back[1]
    assert "lines 1-3 of" in results_back[2] and "TABLES = ['journalists']" in results_back[3]
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    (judged,) = results["repeats"][0]["judgements"]
    assert judged["status"] == "ok" and judged["verdict"] is None
    assert judged["judged"]["score"] == 68.5  # 0.25 * 64 + 0.75 * 70
    assert judged["judged"]["references"]["guideline"]["weight"] == 0.25
    assert judged["judged"]["transcript"] == "judgements/0-anthropic.jsonl"
    assert results["summary"]["per_provider"]["anthropic"]["mean"] == 68.5
    assert results["summary"]["references"]["guideline"]["gaps"] == {"high": 1, "medium": 0, "low": 0}
    assert results["versions"]["references"]["reference"]["pins"] == "v0.36.0"
    assert any("pins the guideline at v0.36.0" in note for note in results["notes"])
    kept = json.loads((run_dir / "judgements" / "0-anthropic.json").read_text(encoding="utf-8"))
    assert kept["answer"] == JUDGED and "raw" not in kept
    transcript = (run_dir / "judgements" / "0-anthropic.jsonl").read_text(encoding="utf-8").splitlines()
    assert (
        json.loads(transcript[0])["roots"] == ["output", "guideline", "reference"] and json.loads(transcript[-1])["kind"] == "end"
    )
    prompt = (run_dir / "artifacts" / "0" / "judge-prompt.md").read_text(encoding="utf-8")
    assert "- `output`: the subject's answer" in prompt and "at tag v1.0.0" in prompt and "0.25" not in prompt
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "| 0 | anthropic |" in report and "| 64 | 70 | 68.5 |" in report and "## Gaps" in report
    assert "The harness weighs each judgement's scores: 0.25 * `guideline` + 0.75 * `reference`." in report
    assert results["spend"]["judges"]["anthropic"]["cost_usd"] > 0


@needs_jsonschema
def test_a_judge_that_submits_on_its_last_turn_at_its_input_tokens_is_scored(tmp_path, monkeypatch, acme):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    # Each scripted turn carries 1000 input tokens, so after the second the next call would pass 2500.
    fake = FAKES["anthropic"](
        [
            turn(("read_file", {"root": "output", "path": "answer.md"})),
            turn(("read_file", {"root": "reference", "path": "src/app.py"})),
            turn(("submit", JUDGED)),
        ]
    )
    monkeypatch.setitem(run.J.CLIENTS, "anthropic", lambda key: fake)
    path = tmp_path / "agentic.json"
    path.write_text(json.dumps(agentic_scenario(["echo", "planned"], budget={"input_tokens": 2500})), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1"]) == 0
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    [(_, told)] = sent_results("anthropic", fake.requests[2])
    assert told.startswith("read_file was not run. No budget left for reads (input tokens: 2000 of 2500 spent")
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    (judged,) = results["repeats"][0]["judgements"]
    assert judged["status"] == "ok" and judged["judged"]["score"] == 68.5  # 0.25 * 64 + 0.75 * 70
    assert judged["judged"]["tool_calls"] == 1 and judged["judged"]["turns"] == 3
    assert results["summary"]["per_provider"]["anthropic"]["mean"] == 68.5


# The preflight ------------------------------------------------------------

CLEAN = {"commit": "c" * 40, "plugin_version": "0.37.0", "dirty": False, "dirty_paths": [], "dirty_sha256": None}


@pytest.fixture
def preflight_host(tmp_path, monkeypatch):
    """A host that has what a skill run needs, as fakes: the model lists, a clean checkout, Claude Code, and curl.

    It returns the argv a preflight starts with, and what ran: no subject and no judge may.
    """
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.PF, "http_status", lambda url, headers, timeout_s=20: 200)
    monkeypatch.setattr(run.PF, "system", lambda: "linux")
    monkeypatch.setattr(run.V, "checkout", lambda root, scope: dict(CLEAN))
    monkeypatch.setenv("SUBJECT_ANTHROPIC_API_KEY", "subject-key-of-its-own")
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY"):
        monkeypatch.setenv(name, f"judge-{name.lower()}")
    folder = tmp_path / "bin"
    folder.mkdir()
    curl = folder / "curl"
    curl.write_text("#!/bin/sh\nprintf 200\n", encoding="utf-8")
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    ran: list[str] = []
    monkeypatch.setattr(run.RT.BaseRuntime, "run", lambda *args, **kwargs: ran.append("subject"))

    def judged(*args, **kwargs) -> list:
        ran.append("judged")
        return []

    monkeypatch.setattr(run.J, "judge_all", judged)
    path = tmp_path / "one.json"
    path.write_text(json.dumps(dict(SKILL, max_spend_usd=5)), encoding="utf-8")
    claude = fake_claude(tmp_path, "2.1.283 (Claude Code)")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--claude", claude, "--preflight"]
    return argv, ran


def preflight_of(tmp_path) -> dict:
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["preflight"]


def test_a_preflight_that_passes_records_every_check_and_runs_nothing(tmp_path, preflight_host, capsys):
    argv, ran = preflight_host
    assert run.main(argv) == 0
    record = preflight_of(tmp_path)
    assert record["passed"] is True and record["failed"] is None and record["not_run"] == []
    assert [c["name"] for c in record["checks"]] == [name for name, _ in run.PF.CHECKS]
    assert ran == []  # no subject, no judge
    out = capsys.readouterr().out
    assert "preflight passed: 8 checks passed and 4 did not apply; nothing was run and no paid endpoint was called" in out


def test_a_preflight_stops_at_its_first_failure_and_exits_8(tmp_path, preflight_host, monkeypatch, capsys):
    argv, ran = preflight_host
    monkeypatch.delenv("SUBJECT_ANTHROPIC_API_KEY")
    assert run.main(argv) == run.PREFLIGHT_FAILED == 8
    record = preflight_of(tmp_path)
    assert record["passed"] is False and record["failed"] == "subject_key"
    assert record["checks"][-1]["fix"].startswith("set SUBJECT_ANTHROPIC_API_KEY")
    assert record["not_run"] == ["judge_keys", "runtime", "workspace", "tools", "resources", "network", "requires"]
    assert "preflight failed at subject_key; not run: judge_keys, runtime" in capsys.readouterr().err
    assert ran == []


def test_a_preflight_reads_the_dirty_checkout_the_run_records(tmp_path, preflight_host, monkeypatch):
    argv, _ = preflight_host
    dirty = dict(CLEAN, dirty=True, dirty_paths=["skills/arch-review-om/SKILL.md"])
    monkeypatch.setattr(run.V, "checkout", lambda root, scope: dict(dirty))
    assert run.main(argv) == run.PREFLIGHT_FAILED
    (run_dir,) = (tmp_path / "runs").glob("*/*")
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["preflight"]["failed"] == "checkout"
    assert resolved["preflight"]["checks"][-1]["facts"]["dirty_paths"] == resolved["versions"]["checkout"]["dirty_paths"]


def test_a_preflight_records_a_reference_it_cannot_stage_as_its_failed_check(tmp_path, preflight_host):
    argv, _ = preflight_host
    scenario = dict(agentic_scenario(["true"]), max_spend_usd=5)
    scenario["judges"]["references"][0]["paths"] = ["no-such-folder"]
    path = tmp_path / "agentic.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    argv[argv.index("--scenario") + 1] = str(path)
    assert run.main(argv) == run.PREFLIGHT_FAILED  # a run stops at exit 2 with the same words
    record = preflight_of(tmp_path)
    assert record["failed"] == "references"
    assert "the checkout holds no file or folder 'no-such-folder'" in record["checks"][-1]["detail"]


def test_a_preflight_is_not_a_dry_run(tmp_path, preflight_host):
    argv, _ = preflight_host
    with pytest.raises(SystemExit) as exited:
        run.main([*argv, "--dry-run"])
    assert exited.value.code == 2


def test_a_run_that_spends_holds_this_machine_awake_and_a_dry_run_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])
    log = tmp_path / "caffeinate.log"
    caffeinate = tmp_path / "caffeinate"
    caffeinate.write_text(
        f"#!{sys.executable}\nimport sys, time\nopen({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    caffeinate.chmod(0o755)
    monkeypatch.setattr(run.PF, "system", lambda: "darwin")
    monkeypatch.setattr(run.PF.shutil, "which", lambda name: str(caffeinate) if name == "caffeinate" else None)
    path = tmp_path / "one.json"
    path.write_text(json.dumps(SKILL), encoding="utf-8")
    base = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "1"]
    assert run.main([*base, "--dry-run"]) == 0
    assert not log.exists()
    claude = fake_claude(tmp_path, "2.1.283 (Claude Code)")

    def slow_claude(*args, **kwargs):
        # The subject runs while caffeinate holds the machine: wait for it to say so.
        for _ in range(100):
            if log.exists():
                break
            time.sleep(0.05)
        return run.RT.ExitStatus(code=0)

    monkeypatch.setattr(run.RT.BaseRuntime, "run", slow_claude)
    run.main([*base, "--claude", claude, "--subject-model", "claude-opus-5"])
    assert log.read_text(encoding="utf-8") == f"-i -s -w {os.getpid()}\n"


# Judging a run's output again -----------------------------------------------

# A subject in phases that builds an output folder, with a group whose sentence the rubric takes, and one agentic judge.
BUILT = {
    "name": "built",
    "kind": "skill",
    "runtimes": ["vm"],
    "max_spend_usd": 999,
    "subject": {
        "skill": "arch-scaffold-new",
        "output": "acme",
        "groups": {"extras": {"rubric": "After the build, a review read the tree."}},
        "phases": [
            {"name": "scaffold", "prompt": "Build acme.", "max_usd": 100, "timeout_s": 60},
            {"name": "review", "group": "extras", "prompt": "Review acme.", "max_usd": 50, "timeout_s": 60},
        ],
    },
    "artifact": {"stdout": True, "files": ["review/report.md"]},
    "rubric": "Judge the tree against the lenses.",
    "judges": {
        "providers": "anthropic",
        "mode": "agentic",
        "budget": {"max_usd": 5},
        "references": [{"name": "guideline", "weight": 1, "paths": ["lenses/README.md"]}],
    },
}
TREE = {"README.md": "# acme\n", "om/entity.py": "class Journalist: ...\n"}
JUDGED_TREE = {
    "references": {"guideline": {"score": 81, "gaps": [], "strengths": ["It names its entity, in om/entity.py."]}},
    "rationale": "A small tree in the guideline's shape.",
}


def no_subject(*args, **kwargs):
    raise AssertionError("judge made a runtime or ran a subject")


@pytest.fixture
def built(tmp_path, monkeypatch):
    """This checkout's scenario, found by the name a source run records; the built-in matrix; a judge key; no runtime."""
    folder = tmp_path / "scenarios"
    folder.mkdir()
    (folder / "built.json").write_text(json.dumps(BUILT), encoding="utf-8")
    monkeypatch.setattr(run, "SCENARIOS", folder)
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(run.RT, "build", no_subject)
    monkeypatch.setattr(run.RT.BaseRuntime, "run", no_subject)


def ran_phase(name: str, status: str = "ok") -> dict:
    """A phase's record in a run's results.json, as a run writes it, spend included."""
    return {"name": name, "session": "fresh", "status": status, "capped": None, "cost_usd": 12.5, "wall_s": 60.0}


def source_run(runs: Path, outputs: dict[int, dict[str, str] | None], results: bool = True, rehearsal: bool = False) -> Path:
    """A run folder as a run of BUILT with extras leaves it, under `runs`.

    `outputs` maps each repeat to the files of its archive, or to None for a
    repeat that kept no archive. Every repeat keeps its answer, its collected
    report, and its own judge prompt. With `results`, results.json records
    each archive, its SHA-256 and its commit, and the phases each repeat
    ran, scaffold and review, as a run records them. With `rehearsal`,
    run.json is marked a rehearsal's.
    """
    src = runs / "20260927-233327-built-11435123"
    repeats = []
    for index, files in outputs.items():
        art = src / "artifacts" / str(index)
        (art / "workspace" / "review").mkdir(parents=True)
        (art / "answer.md").write_text("## scaffold\n\nBuilt.\n", encoding="utf-8")
        (art / "workspace" / "review" / "report.md").write_text("# Review\n", encoding="utf-8")
        (art / "judge-prompt.md").write_text("the source run's own prompt\n", encoding="utf-8")
        record: dict = {"index": index, "exit_status": {"code": 0}, "artifact_paths": [], "judgements": []}
        record["phases"] = [ran_phase("scaffold"), ran_phase("review")]
        if files is not None:
            with zipfile.ZipFile(art / "output.zip", "w") as zf:
                for name, text in files.items():
                    zf.writestr(name, text)
            run.A.write_manifest(art / "output.zip")
            record["archive"] = {**run.A.record(art / "output.zip", src), "commit": "c" * 40}
        repeats.append(record)
    resolved: dict = {
        "run_id": src.name,
        "scenario": {"name": "built"},
        "runtime": {"name": "vm"},
        "groups": ["extras"],
        "subject_model": "claude-opus-5",
    }
    if rehearsal:
        resolved["rehearsal"] = True
    (src / "run.json").write_text(json.dumps(resolved), encoding="utf-8")
    if results:
        (src / "results.json").write_text(json.dumps({"run_id": src.name, "repeats": repeats}), encoding="utf-8")
    return src


def judges(monkeypatch, *reads) -> list:
    """Each judgement gets a fake Anthropic judge of its own, which reads `reads` and submits; returns those started."""
    started: list = []

    def client(key: str):
        script = [turn(*reads), turn(("submit", JUDGED_TREE))] if reads else [turn(("submit", JUDGED_TREE))]
        started.append(FAKES["anthropic"](script))
        return started[-1]

    monkeypatch.setitem(run.J.CLIENTS, "anthropic", client)
    return started


@needs_jsonschema
def test_judge_judges_a_run_s_archived_output_again_and_records_no_subject_session(tmp_path, monkeypatch, built):
    src = source_run(tmp_path / "runs", {0: TREE})
    started = judges(monkeypatch, ("read_file", {"root": "output", "path": "om/entity.py"}))
    assert run.main(["judge", "--source", str(src)]) == 0
    (run_dir,) = [d for d in src.parent.iterdir() if d != src]  # a new run folder beside the source
    [(_, told)] = sent_results("anthropic", started[0].requests[1])
    assert "class Journalist" in told  # the judge read the tree the source run archived
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["source"] == {
        "run_id": src.name,
        "path": str(src.resolve()),
        "repeats": [0],
        "refused": [],
        "capped": [],
        "rubric_groups": [{"repeat": 0, "groups": ["extras"]}],
    }
    (repeat,) = results["repeats"]
    assert repeat["archive"]["sha256"] == run.A.digest(src / "artifacts" / "0" / "output.zip")
    assert repeat["archive"]["commit"] == "c" * 40
    (judged,) = repeat["judgements"]
    assert judged["status"] == "ok" and judged["judged"]["score"] == 81
    assert results["summary"]["per_provider"]["anthropic"]["mean"] == 81
    # No subject session and no subject spend. What the run spent is the judge's. The phases are the source's, as they
    # ran there, with none of their spend.
    kept = [{"name": name, "session": "fresh", "status": "ok", "capped": None} for name in ("scaffold", "review")]
    assert repeat["phases"] == kept and repeat["subject_usage"] == {} and repeat["subject_cost_usd"] is None
    spend = results["spend"]
    assert spend["subject"]["cost_usd"] == 0 and spend["total_usd"] == spend["judges"]["anthropic"]["cost_usd"] > 0
    # The source's artifacts but its judge prompt, and a prompt of this run's own, whose rubric takes the source's group.
    assert repeat["artifact_paths"] == [
        "artifacts/0/MANIFEST.txt",
        "artifacts/0/answer.md",
        "artifacts/0/output.zip",
        "artifacts/0/workspace/review/report.md",
    ]
    prompt = (run_dir / "artifacts" / "0" / "judge-prompt.md").read_text(encoding="utf-8")
    assert "After the build, a review read the tree." in prompt and "the source run's own prompt" not in prompt
    assert "- `output`: the tree the subject built" in prompt
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["source"] == results["source"] and resolved["groups"] == ["extras"]
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert f"This run judged again the archived output of the run `{src.name}`" in report


@needs_jsonschema
def test_judge_keeps_the_mark_of_a_repeat_whose_subject_named_the_run_folders(tmp_path, monkeypatch, built, capsys):
    src = source_run(tmp_path / "runs", {0: TREE})
    results = json.loads((src / "results.json").read_text(encoding="utf-8"))
    read = {"tool": "Read", "id": "toolu_1", "key": "file_path", "value": "/tmp/benchmark/runs/x/report.md"}
    results["repeats"][0]["phases"][1]["read_runs"] = [read]
    (src / "results.json").write_text(json.dumps(results), encoding="utf-8")
    judges(monkeypatch)
    # The output a marked subject made stays marked, whoever judges it, and the run exits non-zero.
    assert run.main(["judge", "--source", str(src)]) == run.READ_RUNS
    (run_dir,) = [d for d in src.parent.iterdir() if d != src]
    (repeat,) = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))["repeats"]
    assert repeat["phases"][1]["read_runs"] == [read] and repeat["read_runs"] == [{"phase": "review", **read}]
    assert "## Marked" in (run_dir / "report.md").read_text(encoding="utf-8")
    assert "repeat 0 is marked: phase review named the benchmark's run folders" in capsys.readouterr().err


@needs_jsonschema
def test_judge_judges_a_repeat_never_judged_and_refuses_one_with_no_output(tmp_path, monkeypatch, built, capsys):
    src = source_run(tmp_path / "runs", {0: TREE, 1: TREE, 2: TREE, 3: None, 4: {}})
    results = json.loads((src / "results.json").read_text(encoding="utf-8"))
    # None of the three was judged: a phase failed, the run ended early after one, and the one judge missed.
    results["repeats"][0]["exit_status"] = {"code": 1}
    results["repeats"][1]["ended_early"] = {"phase": "review", "reason": "incomplete", "not_run": []}
    results["repeats"][2]["judgements"] = [
        {"provider": "anthropic", "model": "m", "effort": "high", "status": "missed", "latency_s": 1, "verdict": None}
    ]
    (src / "results.json").write_text(json.dumps(results), encoding="utf-8")
    started = judges(monkeypatch)
    assert run.main(["judge", "--source", str(src)]) == 0
    (run_dir,) = [d for d in src.parent.iterdir() if d != src]
    judged = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert [r["index"] for r in judged["repeats"]] == [0, 1, 2]
    assert all(r["judgements"][0]["judged"]["score"] == 81 for r in judged["repeats"])
    assert judged["source"]["refused"] == [
        {"repeat": 3, "reason": "the source run kept no archive of its output"},
        {"repeat": 4, "reason": "its archive holds no file"},
    ]
    # No judge started on a repeat with no output: one judge for each of the three, and no transcript, no artifacts else.
    assert len(started) == 3
    assert sorted(p.name for p in (run_dir / "judgements").glob("*.jsonl")) == [f"{i}-anthropic.jsonl" for i in range(3)]
    assert sorted(p.name for p in (run_dir / "artifacts").iterdir()) == ["0", "1", "2"]
    err = capsys.readouterr().err
    assert "repeat 3 is not judged: the source run kept no archive of its output" in err
    assert "repeat 4 is not judged: its archive holds no file" in err
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Repeat 4 of it was not judged: its archive holds no file." in report
    # A source with nothing to judge makes no run folder and starts no judge.
    empty = source_run(tmp_path / "other", {0: None, 1: {}})
    assert run.main(["judge", "--source", str(empty)]) == 2
    assert list(empty.parent.iterdir()) == [empty] and len(started) == 3
    assert "holds no output to judge: no run folder was made and no judge started" in capsys.readouterr().err


@needs_jsonschema
def test_judge_tells_a_repeat_s_judges_only_the_groups_whose_phases_all_ran_in_it(tmp_path, monkeypatch, built):
    # The group holds a review and a close, and its sentence stands for both.
    close = {"name": "close", "group": "extras", "prompt": "Close the findings.", "max_usd": 50, "timeout_s": 60}
    grouped = json.loads(json.dumps(BUILT))
    grouped["subject"]["phases"].append(close)
    (tmp_path / "scenarios" / "built.json").write_text(json.dumps(grouped), encoding="utf-8")
    src = source_run(tmp_path / "runs", {0: TREE, 1: TREE, 2: TREE, 3: TREE})
    results = json.loads((src / "results.json").read_text(encoding="utf-8"))
    # Repeat 0 ran every phase. Repeat 1 ended early after the scaffold, and the run's spend cap cut repeat 2 short
    # after the review, before the close. Repeat 3's record lists no phase.
    results["repeats"][0]["phases"].append(ran_phase("close"))
    results["repeats"][1] |= {
        "phases": [ran_phase("scaffold", "incomplete")],
        "ended_early": {"phase": "scaffold", "reason": "incomplete", "not_run": ["review", "close"]},
    }
    results["repeats"][2] |= {"cut_short": ["close"]}
    del results["repeats"][3]["phases"]
    (src / "results.json").write_text(json.dumps(results), encoding="utf-8")
    started = judges(monkeypatch)
    out = tmp_path / "judged"
    assert run.main(["judge", "--source", str(src), "--out", str(out / "dry"), "--dry-run"]) == 0
    assert run.main(["judge", "--source", str(src), "--out", str(out / "run")]) == 0
    resolved, judged, report = only_run(out / "run")
    (made,) = (out / "run").glob("*/*")
    prompts = [(made / "artifacts" / str(i) / "judge-prompt.md").read_text(encoding="utf-8") for i in range(4)]
    sentence, review, closed = "After the build, a review read the tree.", "Review acme.", "Close the findings."
    # Every phase of the group ran: the sentence and both phases. None of them ran: no word of the group.
    assert sentence in prompts[0] and review in prompts[0] and closed in prompts[0]
    assert sentence not in prompts[1] and review not in prompts[1] and closed not in prompts[1] and "Build acme." in prompts[1]
    # The review ran and the close did not: no sentence, which names the close too; the review is described, the close not.
    assert sentence not in prompts[2] and review in prompts[2] and closed not in prompts[2]
    # A repeat whose record lists no phase is judged as the source run took it.
    assert sentence in prompts[3] and review in prompts[3] and closed in prompts[3]
    # The record says which groups each repeat's rubric took, and a dry run shows it before any judge starts.
    took = [
        {"repeat": 0, "groups": ["extras"]},
        {"repeat": 1, "groups": []},
        {"repeat": 2, "groups": []},
        {"repeat": 3, "groups": ["extras"]},
    ]
    assert judged["source"]["rubric_groups"] == took and resolved["source"]["rubric_groups"] == took
    (dry,) = (out / "dry").glob("*/*")
    assert json.loads((dry / "run.json").read_text(encoding="utf-8"))["source"]["rubric_groups"] == took
    assert "Groups the rubric of repeat 2 took, those whose every phase ran in it: none." in report
    assert "Groups the rubric of repeat 0 took, those whose every phase ran in it: `extras`." in report
    # What the judges may spend is theirs alone, whatever the rubric took: one judge's $5 over the four repeats.
    assert len(started) == 4 and resolved["max_spend_usd"] == 20


@needs_jsonschema
def test_a_judge_of_a_judge_s_folder_tells_its_judges_what_the_first_source_ran(tmp_path, monkeypatch, built):
    src = source_run(tmp_path / "runs", {0: TREE, 1: TREE})
    results = json.loads((src / "results.json").read_text(encoding="utf-8"))
    # Repeat 0 ended early after the scaffold; repeat 1 ran both phases.
    results["repeats"][0] |= {
        "phases": [ran_phase("scaffold", "incomplete")],
        "ended_early": {"phase": "scaffold", "reason": "incomplete", "not_run": ["review"]},
    }
    (src / "results.json").write_text(json.dumps(results), encoding="utf-8")
    judges(monkeypatch)
    out = tmp_path / "judged"
    assert run.main(["judge", "--source", str(src), "--out", str(out / "first")]) == 0
    (first,) = (out / "first").glob("*/*")
    assert run.main(["judge", "--source", str(first), "--out", str(out / "second")]) == 0
    resolved, judged, _ = only_run(out / "second")
    (second,) = (out / "second").glob("*/*")
    prompts = [(second / "artifacts" / str(i) / "judge-prompt.md").read_text(encoding="utf-8") for i in range(2)]
    sentence, review = "After the build, a review read the tree.", "Review acme."
    # The first judge's folder keeps each repeat's phases as they ran in its source, so the second tells the same.
    assert sentence not in prompts[0] and review not in prompts[0] and "Build acme." in prompts[0]
    assert sentence in prompts[1] and review in prompts[1]
    assert judged["source"]["run_id"] == first.name and resolved["groups"] == ["extras"]
    assert judged["source"]["rubric_groups"] == [{"repeat": 0, "groups": []}, {"repeat": 1, "groups": ["extras"]}]
    kept = {"session": "fresh", "capped": None}
    assert [r["phases"] for r in judged["repeats"]] == [
        [{"name": "scaffold", "status": "incomplete", **kept}],
        [{"name": "scaffold", "status": "ok", **kept}, {"name": "review", "status": "ok", **kept}],
    ]


def test_judge_refuses_an_archive_that_is_not_the_one_the_source_run_recorded(tmp_path, monkeypatch, built, capsys):
    src = source_run(tmp_path / "runs", {0: TREE})
    with zipfile.ZipFile(src / "artifacts" / "0" / "output.zip", "a") as zf:
        zf.writestr("planted.py", "print('not built by the subject')\n")
    started = judges(monkeypatch)
    assert run.main(["judge", "--source", str(src)]) == 2
    assert started == [] and list(src.parent.iterdir()) == [src]
    assert "repeat 0 is not judged: its archive's SHA-256 is " in capsys.readouterr().err


def test_judge_caps_its_spend_at_the_judges_budgets_over_the_repeats_it_judges(tmp_path, monkeypatch, built):
    src = source_run(tmp_path / "runs", {0: TREE, 1: TREE, 2: None})
    out = tmp_path / "judged"
    base = ["judge", "--source", str(src), "--providers", "anthropic,openai", "--out", str(out)]

    def cap_of(*flags: str) -> float:
        before = set(out.glob("*/*")) if out.exists() else set()
        assert run.main([*base, *flags, "--dry-run"]) == 0
        (made,) = set(out.glob("*/*")) - before
        return json.loads((made / "run.json").read_text(encoding="utf-8"))["max_spend_usd"]

    # Two judges at $5 each over the two repeats it judges: no phase's cap ($150 a repeat), not the scenario's $999.
    assert cap_of() == 20
    assert cap_of("--max-spend-usd", "7") == 7


def only_run(folder: Path) -> tuple[dict, dict, str]:
    """The run.json, the results.json, and the report of the one run folder under `folder`."""
    (made,) = folder.glob("*/*")
    read = [json.loads((made / name).read_text(encoding="utf-8")) for name in ("run.json", "results.json")]
    return read[0], read[1], (made / "report.md").read_text(encoding="utf-8")


def test_judge_starts_no_judge_on_a_repeat_its_cap_does_not_cover(tmp_path, monkeypatch, built):
    src = source_run(tmp_path / "runs", {0: TREE, 1: TREE})
    out = tmp_path / "judged"
    started = judges(monkeypatch)
    # $4 does not cover one repeat's judge budget, $5: no judge starts, and the notes say why.
    assert run.main(["judge", "--source", str(src), "--out", str(out / "below"), "--max-spend-usd", "4"]) == 0
    _, results, report = only_run(out / "below")
    assert started == [] and results["repeats"] == []
    assert results["source"]["repeats"] == [] and results["source"]["capped"] == [0, 1]
    why = "repeat 0 and after were not judged: $4.0000 of the run's $4 spend cap is left, and the judges of a repeat may spend $5"
    assert why in results["notes"]
    assert (
        "Repeat(s) 0, 1 of it were not judged: what was left of the run's spend cap did not cover their judges' budgets."
        in report
    )
    # $5.001 covers the first repeat's judge; what that judge spent leaves too little for the second's.
    assert run.main(["judge", "--source", str(src), "--out", str(out / "one"), "--max-spend-usd", "5.001"]) == 0
    _, results, _ = only_run(out / "one")
    assert len(started) == 1 and [r["index"] for r in results["repeats"]] == [0]
    assert results["source"]["repeats"] == [0] and results["source"]["capped"] == [1]
    assert any(n.startswith("repeat 1 and after were not judged") for n in results["notes"])


def test_judge_judges_a_rehearsal_s_output_within_a_rehearsal_s_bounds(tmp_path, monkeypatch, built, capsys):
    src = source_run(tmp_path / "runs", {i: TREE for i in range(6)}, rehearsal=True)
    out = tmp_path / "judged"
    budgets: list = []
    judge_agentic = run.RF.A.judge_agentic

    def spy(*args, **kwargs):
        budgets.append(kwargs["budget"])
        return judge_agentic(*args, **kwargs)

    monkeypatch.setattr(run.RF.A, "judge_agentic", spy)
    started = judges(monkeypatch)
    # Two judges at the stub's $0.50 over six repeats is $6, and a rehearsal's cap is $5.
    both = ["--providers", "anthropic,openai"]
    assert run.main(["judge", "--source", str(src), *both, "--out", str(out / "dry"), "--dry-run"]) == 0
    (dry,) = (out / "dry").glob("*/*")  # a dry run writes run.json alone
    resolved = json.loads((dry / "run.json").read_text(encoding="utf-8"))
    assert resolved["max_spend_usd"] == 5 and resolved["rehearsal"] is True
    assert {k: resolved["scenario"]["judges"]["budget"][k] for k in ("max_usd", "wall_s", "submits")} == {
        "max_usd": 0.5,
        "wall_s": 900.0,
        "submits": 2,
    }
    # A flag can lower that cap, never raise it.
    assert run.main(["judge", "--source", str(src), "--out", str(out / "high"), "--max-spend-usd", "7"]) == 2
    assert not (out / "high").exists() and "would raise it" in capsys.readouterr().err
    # Judged: every judge gets the stub budget, the cap is the stub's over the repeats, and the folder is a rehearsal's.
    assert run.main(["judge", "--source", str(src), "--out", str(out / "run")]) == 0
    resolved, results, _ = only_run(out / "run")
    assert len(started) == 6 and [r["index"] for r in results["repeats"]] == list(range(6))
    assert all((b.max_usd, b.wall_s, b.submits) == (0.5, 900.0, 2) for b in budgets) and len(budgets) == 6
    assert resolved["max_spend_usd"] == 3 and resolved["rehearsal"] is True


def test_judge_refuses_a_flag_of_the_subject_and_a_run_refuses_a_source(tmp_path, monkeypatch, built, capsys):
    src = source_run(tmp_path / "runs", {0: TREE})
    started = judges(monkeypatch)
    assert run.main(["judge", "--source", str(src), "--with", "extras", "--repeat", "2"]) == 2
    assert "--with, --repeat is not for it" in capsys.readouterr().err
    assert run.main(["--scenario", "built", "--source", str(src)]) == 2  # a run of the scenario would run its subject
    assert "--source is for judge" in capsys.readouterr().err
    assert started == [] and list(src.parent.iterdir()) == [src]
