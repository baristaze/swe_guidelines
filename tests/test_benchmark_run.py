"""benchmark/run.py: the subject's command, with the plugin and the target it can reach."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import scenario as S

RUN = Path(__file__).resolve().parent.parent / "benchmark" / "run.py"
spec = importlib.util.spec_from_file_location("benchmark_run", RUN)
assert spec is not None and spec.loader is not None
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

SKILL = {"name": "one", "kind": "skill", "subject": {"skill": "arch-review-om", "prompt": "Review it."}, "rubric": "r"}


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
    scn = S.from_data(dict(SKILL, subject={"skill": "arch-review-om", "prompt": "Review {target}/om."}))
    assert run.subject_prompt(scn, "/target") == "Review /target/om."
    with pytest.raises(S.ScenarioError, match="has no target"):
        run.subject_prompt(scn, None)


def test_a_command_gets_the_paths_in_its_argv():
    scn = S.from_data({"name": "c", "kind": "command", "subject": {"argv": ["ls", "{target}", "{plugin}"]}, "rubric": "r"})
    assert run.subject_argv(scn, "p", "/plugin", "/target") == ["ls", "/target", "/plugin"]


def test_the_shipped_review_runs_on_its_planted_checkout_with_the_answers_outside_it():
    pytest.importorskip("yaml")
    scn = S.load(RUN.parent / "scenarios" / "review-om.yaml")
    target = scn.resolve(scn.subject.target)
    expected = scn.resolve(scn.evidence.expected)
    assert target is not None and expected is not None
    assert (target / "om").is_dir() and expected.is_file()
    assert target not in expected.parents


QA = {"name": "q", "kind": "qa", "subject": {"prompt": "Why?", "context": ["notes/why.md"]}, "rubric": "r"}


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
    # the stub records each scenario and fails the one named `broken`
    (bin_dir / "uv").write_text(
        '#!/bin/sh\nshift 2\necho "$2" >> "$UV_LOG"\n[ "$2" != broken ]\n',
        encoding="utf-8",
    )
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    scenarios = tmp_path / "benchmark" / "scenarios"
    scenarios.mkdir(parents=True)
    for name in ("a.yaml", "b.yml", "broken.json", "c.yaml", "notes.txt"):
        (scenarios / name).write_text("{}", encoding="utf-8")
    script = tmp_path / "step.sh"
    script.write_text(workflow_step("run every scenario"), encoding="utf-8")

    def run_step(**inputs):
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "UV_LOG": str(tmp_path / "uv.log"),
            **{"SCENARIOS": "all", "PROVIDERS": "3", "EFFORT": "medium", "REPEAT": "1", **inputs},
        }
        (tmp_path / "uv.log").unlink(missing_ok=True)
        done = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)
        log = tmp_path / "uv.log"
        ran = log.read_text(encoding="utf-8").split() if log.exists() else []
        return done.returncode, ran, done.stdout + done.stderr

    return run_step


def test_the_workflow_runs_every_cataloged_scenario_and_fails_at_the_end(scenario_step):
    code, ran, out = scenario_step()
    assert ran == ["a", "b", "broken", "c"]  # every suffix the catalog reads, past the failure
    assert code == 1 and "failed scenarios: broken" in out
    code, ran, _ = scenario_step(SCENARIOS="a c", PROVIDERS="anthropic,openai", REPEAT="2")
    assert code == 0 and ran == ["a", "c"]


@pytest.mark.parametrize(
    "inputs",
    [{"PROVIDERS": "3; touch pwned"}, {"PROVIDERS": "$(id)"}, {"REPEAT": "0"}, {"REPEAT": "1 --dry-run"}],
)
def test_the_workflow_refuses_inputs_outside_their_pattern(scenario_step, tmp_path, inputs):
    code, ran, _ = scenario_step(**inputs)
    assert code == 2 and ran == []
    assert not (tmp_path / "pwned").exists()


def test_a_scenario_name_is_never_spliced_into_the_script(scenario_step, tmp_path):
    _code, ran, _ = scenario_step(SCENARIOS='a"; touch pwned; echo "')
    assert not (tmp_path / "pwned").exists()
    assert "pwned;" in ran  # the words reach run.py as arguments, not as shell
