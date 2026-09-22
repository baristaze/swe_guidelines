"""benchmark/run.py: the subject's command, with the plugin and the target it can reach."""

import importlib.util
import json
import os
import subprocess
import sys
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
        # `uv run --locked benchmark/run.py --scenario <name>`: three words, then the script.
        '#!/bin/sh\necho "$@" > "$ARGV_LOG"\nshift 3\necho "$2" >> "$UV_LOG"\n[ "$2" != broken ]\n',
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
            "ARGV_LOG": str(tmp_path / "argv.log"),
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
    [
        {"PROVIDERS": "3; touch pwned"},
        {"PROVIDERS": "$(id)"},
        {"EFFORT": "maximum"},
        {"EFFORT": "high --dry-run"},
        {"REPEAT": "0"},
        {"REPEAT": "1 --dry-run"},
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
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "files.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert run.main(["--scenario", str(path), "--out", "runs", "--repeat", "2"]) == 0
    (run_dir,) = (tmp_path / "runs").iterdir()
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
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "reach.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run.main(["--scenario", str(path), "--out", "runs"])
    (run_dir,) = (tmp_path / "runs").iterdir()
    answer = (run_dir / "artifacts" / "0" / "answer.md").read_text(encoding="utf-8")
    assert "skills" in answer and "no skills" not in answer
    assert "target" in answer and "no target" not in answer
    assert "seen:\n" in answer, answer


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
    scenario = {"name": "keys", "kind": "command", "subject": {"argv": [sys.executable, "-c", script]}, "rubric": "r"}
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    argv = ["--scenario", str(path), "--out", str(tmp_path / "runs"), "--providers", "15", "--repeat", "1"]
    if runtime == "vm":
        config = tmp_path / "vm.json"
        # `env` stands in for the prefix: it runs its words on this machine, as a remote shell would there.
        vm = {"exec_prefix": ["env"], "remote_workspace": str(tmp_path / "remote"), "remote_plugin": str(tmp_path)}
        config.write_text(json.dumps(vm), encoding="utf-8")

        argv += ["--runtime", "vm", "--runtime-config", str(config)]
    assert run.main(argv) == 0
    (run_dir,) = (tmp_path / "runs").iterdir()
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
    (run_dir,) = (tmp_path / "runs").iterdir()
    resolved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert resolved["runtime"]["config"]["keys"] == ["ANTHROPIC_API_KEY"]
    monkeypatch.delenv("SUBJECT_ANTHROPIC_API_KEY")
    assert run.main([*argv, "--out", str(tmp_path / "later")]) == 0
    (later,) = (tmp_path / "later").iterdir()
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
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--repeat", "2"]) == 6
    assert judged == []
    (run_dir,) = (tmp_path / "runs").iterdir()
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert [r["exit_status"]["code"] for r in results["repeats"]] == [127, 127]
    assert all(r["judgements"] == [] for r in results["repeats"])
    assert results["summary"]["overall_mean"] is None
    assert any("not judged" in note for note in results["notes"])


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
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "twice.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    out = str(tmp_path / "runs")
    assert run.main(["--scenario", str(path), "--out", out]) == 0
    assert run.main(["--scenario", str(path), "--out", out]) == 0
    first, second = sorted((tmp_path / "runs").iterdir())
    answers = [(d / "artifacts" / "0" / "answer.md").read_text(encoding="utf-8") for d in (first, second)]
    assert answers[0] != answers[1] and answers[0].count("\n") == answers[1].count("\n") == 1
    for folder in (first, second):
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        assert results["run_id"] == folder.name


def test_an_answer_with_a_unicode_line_separator_is_kept_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "MODELS", tmp_path / "models.yaml")
    monkeypatch.setattr(run.J, "judge_all", lambda *args, **kwargs: [])  # no provider is called
    script = "import sys; sys.stdout.buffer.write('one\\u2028two\\u2029three\\n'.encode())"
    scenario = {"name": "sep", "kind": "command", "subject": {"argv": [sys.executable, "-c", script]}, "rubric": "r"}
    path = tmp_path / "sep.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs"), "--providers", "1"]) == 0
    (run_dir,) = (tmp_path / "runs").iterdir()
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
        "judges": {"providers": "anthropic"},
    }
    path = tmp_path / "seen.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert run.main(["--scenario", str(path), "--out", str(tmp_path / "runs")]) == 0
    (run_dir,) = (tmp_path / "runs").iterdir()
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
