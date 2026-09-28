"""benchmark/harness/scenario.py: the scenario file and its defaults."""

import json
import re
from pathlib import Path

import pytest

from harness import agentic as A
from harness import scenario as S

MINIMAL = {
    "name": "one",
    "kind": "skill",
    "subject": {"skill": "arch-explain", "prompt": "why?", "max_usd": 1},
    "rubric": "Score it 0 to 100.",
    "runtimes": ["container"],
}


def write(folder: Path, name: str, data: dict) -> Path:
    path = folder / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_minimal_scenario_takes_the_defaults(tmp_path):
    scn = S.load(write(tmp_path, "one.json", MINIMAL))
    assert scn.kind == "skill"
    assert scn.subject.max_turns == 6
    assert scn.artifact.stdout is True
    assert scn.judges.providers == "3"
    assert scn.judges.effort == "medium"


def test_the_scenario_round_trips_as_plain_data(tmp_path):
    scn = S.load(write(tmp_path, "one.json", MINIMAL))
    data = scn.as_dict()
    assert data["subject"]["skill"] == "arch-explain"
    assert data["runtimes"] == ["container"] and data["requires"] == []
    assert json.loads(json.dumps(data))["rubric"].startswith("Score it")


def test_a_file_that_does_not_parse_is_a_scenario_error(tmp_path):
    (tmp_path / "broken.json").write_text('{"name": "broken",', encoding="utf-8")
    with pytest.raises(S.ScenarioError, match=r"broken\.json: does not parse as JSON: "):
        S.load(tmp_path / "broken.json")
    pytest.importorskip("yaml")
    (tmp_path / "broken.yaml").write_text("name: [broken\n", encoding="utf-8")
    with pytest.raises(S.ScenarioError, match=r"broken\.yaml: does not parse as YAML: "):
        S.load(tmp_path / "broken.yaml")


def test_a_value_or_a_file_the_scenario_cannot_read_is_a_scenario_error(tmp_path):
    with pytest.raises(S.ScenarioError, match=r"subject\.timeout_s: expected a whole number, got '900s'"):
        S.from_data(dict(MINIMAL, subject={"skill": "arch-explain", "timeout_s": "900s"}))
    with pytest.raises(S.ScenarioError, match=r"subject\.max_turns: expected a whole number, got \[6\]"):
        S.from_data(dict(MINIMAL, subject={"skill": "arch-explain", "max_turns": [6]}))
    with pytest.raises(S.ScenarioError, match=r"subject\.timeout_s: expected a whole number, got inf"):
        S.from_data(dict(MINIMAL, subject={"skill": "arch-explain", "timeout_s": float("inf")}))  # YAML's .inf
    huge = json.dumps(dict(MINIMAL, subject={"skill": "arch-explain", "max_turns": 7})).replace(": 7", ": 1e400")
    (tmp_path / "huge.json").write_text(huge, encoding="utf-8")  # JSON reads 1e400 as inf
    with pytest.raises(S.ScenarioError, match=r"subject\.max_turns: expected a whole number, got inf"):
        S.load(tmp_path / "huge.json")
    (tmp_path / "latin.json").write_bytes(b'{"name": "caf\xe9"}')
    with pytest.raises(S.ScenarioError, match=r"latin\.json: is not UTF-8"):
        S.load(tmp_path / "latin.json")


def test_an_unknown_key_is_refused(tmp_path):
    bad = dict(MINIMAL, rubrick="oops")
    with pytest.raises(S.ScenarioError, match="unknown key"):
        S.load(write(tmp_path, "bad.json", bad))


def test_each_kind_needs_its_own_field(tmp_path):
    with pytest.raises(S.ScenarioError, match=r"needs subject\.skill"):
        S.from_data(dict(MINIMAL, subject={"prompt": "x"}))
    with pytest.raises(S.ScenarioError, match=r"needs subject\.argv"):
        S.from_data(dict(MINIMAL, kind="command", subject={}))
    with pytest.raises(S.ScenarioError, match=r"needs subject\.prompt"):
        S.from_data(dict(MINIMAL, kind="qa", subject={}))


def test_an_unknown_kind_and_a_missing_rubric_are_refused():
    with pytest.raises(S.ScenarioError, match="is not one of"):
        S.from_data(dict(MINIMAL, kind="quiz"))
    with pytest.raises(S.ScenarioError, match="rubric is required"):
        S.from_data({k: v for k, v in MINIMAL.items() if k != "rubric"})


def test_a_list_field_written_as_a_string_is_refused():
    with pytest.raises(S.ScenarioError, match="expected a list"):
        S.from_data(dict(MINIMAL, subject={"skill": "arch-explain", "allowed_tools": "Read", "max_usd": 1}))


def test_the_catalog_lists_one_file_per_name(tmp_path):
    write(tmp_path, "b.json", MINIMAL)
    write(tmp_path, "a.json", MINIMAL)
    (tmp_path / "notes.md").write_text("not a scenario", encoding="utf-8")
    assert [p.stem for p in S.catalog(tmp_path)] == ["a", "b"]


def test_find_takes_a_name_or_a_path(tmp_path):
    path = write(tmp_path, "one.json", MINIMAL)
    assert S.find("one", tmp_path) == path
    assert S.find(str(path), tmp_path) == path
    with pytest.raises(S.ScenarioError, match="known: one"):
        S.find("two", tmp_path)


def test_find_takes_the_name_a_scenario_file_gives_itself(tmp_path):
    named = write(tmp_path, "first.json", dict(MINIMAL, name="alpha"))
    (tmp_path / "broken.json").write_text('{"name": "beta",', encoding="utf-8")
    assert S.find("alpha", tmp_path) == named  # the name run.py list prints
    assert S.find("first", tmp_path) == named  # and the file's stem
    with pytest.raises(S.ScenarioError, match="known: alpha, broken, first"):
        S.find("beta", tmp_path)  # a file that does not load has no name to match


def test_find_takes_a_name_before_a_stem(tmp_path):
    one = write(tmp_path, "one.json", dict(MINIMAL, name="two"))
    two = write(tmp_path, "two.json", dict(MINIMAL, name="three"))
    assert S.find("two", tmp_path) == one  # list prints "two" for one.json
    assert S.find("three", tmp_path) == two
    assert S.find("one", tmp_path) == one  # a stem no file bears as a name


def test_the_shipped_scenarios_are_a_catalog_of_four():
    folder = Path(__file__).resolve().parent.parent / "benchmark" / "scenarios"
    assert [p.stem for p in S.catalog(folder)] == ["create-full-system", "explain-tenancy", "review-om", "support-turn"]


def test_a_scenario_without_runtimes_does_not_load():
    with pytest.raises(S.ScenarioError, match="runtimes is required"):
        S.from_data({k: v for k, v in MINIMAL.items() if k != "runtimes"})
    with pytest.raises(S.ScenarioError, match="runtimes is required"):
        S.from_data(dict(MINIMAL, runtimes=[]))
    with pytest.raises(S.ScenarioError, match="expected a list, got a string"):
        S.from_data(dict(MINIMAL, runtimes="container"))
    with pytest.raises(S.ScenarioError, match="runtimes: laptop is not one of host, container, vm"):
        S.from_data(dict(MINIMAL, runtimes=["container", "laptop"]))
    assert S.from_data(dict(MINIMAL, runtimes=["host", "container"])).runtimes == ["host", "container"]


def test_a_scenario_requires_only_what_each_of_its_runtimes_can_provide():
    scn = S.from_data(dict(MINIMAL, runtimes=["vm"], requires=["docker"]))
    assert scn.runtimes == ["vm"] and scn.requires == ["docker"]
    with pytest.raises(S.ScenarioError, match="the container runtime cannot provide docker, which the scenario requires"):
        S.from_data(dict(MINIMAL, runtimes=["vm", "container"], requires=["docker"]))
    # The host hands its subject no engine's settings, so it provides no engine either.
    with pytest.raises(S.ScenarioError, match="the host runtime cannot provide docker, which the scenario requires"):
        S.from_data(dict(MINIMAL, runtimes=["vm", "host"], requires=["docker"]))
    with pytest.raises(S.ScenarioError, match="requires: gpu is not one of docker"):
        S.from_data(dict(MINIMAL, runtimes=["vm"], requires=["gpu"]))
    assert S.from_data(MINIMAL).requires == []


def test_the_shipped_scenarios_say_where_they_run():
    pytest.importorskip("yaml")
    folder = Path(__file__).resolve().parent.parent / "benchmark" / "scenarios"
    declared = {p.stem: (S.load(p).runtimes, S.load(p).requires) for p in S.catalog(folder)}
    assert declared == {
        "create-full-system": (["vm"], ["docker"]),
        "explain-tenancy": (["container"], []),
        "review-om": (["container"], []),
        "support-turn": (["host", "container"], []),
    }


def test_the_vm_machine_s_probe_asks_each_command_a_vm_gate_starts_for_its_version():
    yaml = pytest.importorskip("yaml")
    benchmark = Path(__file__).resolve().parent.parent / "benchmark"
    machine = yaml.safe_load((benchmark / "runtime" / "lima" / "benchmark.yaml").read_text(encoding="utf-8"))
    probes = "\n".join(probe["script"] for probe in machine["probes"])
    scenarios = [S.load(p) for p in S.catalog(benchmark / "scenarios")]
    started = {gate.split()[0] for scn in scenarios if "vm" in scn.runtimes for gate in scn.subject.gates}
    assert "make" in started
    for command in sorted(started):
        assert re.search(rf"^{re.escape(command)} (--)?version$", probes, re.M), command


def test_evidence_is_read_and_its_unknown_keys_refused(tmp_path):
    data = dict(
        MINIMAL,
        subject={"skill": "arch-explain", "target": "t", "max_usd": 1},
        evidence={"files": ["**/*.py"], "expected": "e.yaml"},
    )
    scn = S.load(write(tmp_path, "one.json", data))
    assert scn.evidence.files == ["**/*.py"] and scn.evidence.expected == "e.yaml"
    assert scn.as_dict()["evidence"] == {"files": ["**/*.py"], "expected": "e.yaml"}
    with pytest.raises(S.ScenarioError, match="unknown key"):
        S.from_data(dict(MINIMAL, evidence={"file": []}))


def test_expected_findings_need_a_target_of_their_own():
    with pytest.raises(S.ScenarioError, match="describes a target"):
        S.from_data(dict(MINIMAL, evidence={"expected": "e.yaml"}))


def test_a_relative_path_is_read_from_the_scenario_folder(tmp_path):
    (tmp_path / "scenarios").mkdir()
    scn = S.load(write(tmp_path / "scenarios", "one.json", MINIMAL))
    assert scn.resolve("../fixtures/x") == (tmp_path / "fixtures" / "x").resolve()
    assert scn.resolve("/abs/x") == Path("/abs/x").resolve()
    assert scn.resolve(None) is None


def test_a_qa_subject_answers_with_exactly_one_provider():
    qa = dict(MINIMAL, kind="qa", subject={"prompt": "why?", "provider": "gemini"})
    assert S.from_data(qa).subject.provider == "gemini"
    for many in ("all", "anthropic,openai", "3"):
        with pytest.raises(S.ScenarioError, match="names one provider"):
            S.from_data(dict(qa, subject={"prompt": "why?", "provider": many}))
    with pytest.raises(S.ScenarioError, match="unknown provider"):
        S.from_data(dict(qa, subject={"prompt": "why?", "provider": "acme"}))


def test_the_judges_are_checked_when_the_scenario_loads():
    scn = S.from_data(dict(MINIMAL, judges={"providers": "anthropic,xai", "effort": "high"}))
    assert scn.judges.providers == "anthropic,xai" and scn.judges.effort == "high"
    with pytest.raises(S.ScenarioError, match=r"judges\.effort is one of low, medium, high, got 'maximum'"):
        S.from_data(dict(MINIMAL, judges={"effort": "maximum"}))
    with pytest.raises(S.ScenarioError, match=r"judges\.providers: unknown provider 'claude'"):
        S.from_data(dict(MINIMAL, judges={"providers": "claude"}))
    with pytest.raises(S.ScenarioError, match=r"judges\.providers: provider selection 99 sets a bit"):
        S.from_data(dict(MINIMAL, judges={"providers": 99}))


def phase(name: str, **extra) -> dict:
    return {"name": name, "prompt": "Do it.", "max_turns": 10, "max_usd": 5, "timeout_s": 600, **extra}


def phased(*phases: dict, runtimes=("vm",), **subject) -> dict:
    return dict(
        MINIMAL,
        runtimes=list(runtimes),
        subject={"skill": "arch-scaffold-new", "output": "site", "phases": list(phases), **subject},
    )


def test_a_skill_subject_names_its_spend_cap():
    with pytest.raises(S.ScenarioError, match=r"subject\.max_usd is required"):
        S.from_data(dict(MINIMAL, subject={"skill": "arch-explain", "prompt": "why?"}))
    for bad in (0, -1, "5", True):
        with pytest.raises(S.ScenarioError, match="an amount in US dollars above 0"):
            S.from_data(dict(MINIMAL, subject={"skill": "arch-explain", "max_usd": bad}))
    assert S.from_data(MINIMAL).subject.max_usd == 1.0
    with pytest.raises(S.ScenarioError, match="max_usd belong to a skill subject"):
        S.from_data(dict(MINIMAL, kind="qa", subject={"prompt": "why?", "max_usd": 1}))


def test_a_subject_in_phases_takes_the_defaults_of_each_phase():
    scn = S.from_data(phased(phase("scaffold"), phase("mvp", session="resume", hint=True)))
    first, second = scn.subject.phases
    assert (first.session, first.cwd, first.hint, first.max_gate_reruns, first.on_cap) == (
        "fresh",
        "workspace",
        False,
        3,
        "continue",
    )
    assert (second.session, second.hint, second.max_usd) == ("resume", True, 5.0)
    assert scn.as_dict()["subject"]["phases"][1]["session"] == "resume"
    assert scn.subject.output == "site"


def test_every_phase_names_its_prompt_and_its_bounds():
    for key in ("name", "prompt", "max_turns", "max_usd", "timeout_s"):
        broken = {k: v for k, v in phase("scaffold").items() if k != key}
        with pytest.raises(S.ScenarioError, match=f"every phase names its {key}"):
            S.from_data(phased(broken))
    with pytest.raises(S.ScenarioError, match=r"max_turns: a whole number of at least 1, got 0"):
        S.from_data(phased(phase("scaffold", max_turns=0)))
    with pytest.raises(S.ScenarioError, match="unknown key"):
        S.from_data(phased(phase("scaffold", turns=3)))
    with pytest.raises(S.ScenarioError, match=r"session is one of fresh, resume, got 'continue'"):
        S.from_data(phased(phase("scaffold", session="continue")))
    with pytest.raises(S.ScenarioError, match="a lowercase word of its own"):
        S.from_data(phased(phase("scaffold"), phase("scaffold")))
    with pytest.raises(S.ScenarioError, match="each phase names its own prompt, max_turns"):
        S.from_data(phased(phase("scaffold"), prompt="x", max_turns=3))


def test_a_resumed_phase_continues_the_one_before_it_where_it_ran():
    with pytest.raises(S.ScenarioError, match="the first phase has no session before it to resume"):
        S.from_data(phased(phase("scaffold", session="resume")))
    with pytest.raises(S.ScenarioError, match="starts where the phase before it did, in its workspace"):
        S.from_data(phased(phase("scaffold"), phase("mvp", session="resume", cwd="output")))
    with pytest.raises(S.ScenarioError, match="starts every phase in a new container, so no phase resumes there"):
        S.from_data(phased(phase("scaffold"), phase("mvp", session="resume"), runtimes=("vm", "container")))
    # A fresh phase runs anywhere, the container included.
    assert S.from_data(phased(phase("scaffold"), phase("mvp"), runtimes=("container",))).runtimes == ["container"]


def test_a_subject_in_phases_builds_an_output_folder_of_the_workspace():
    data = phased(phase("scaffold"))
    del data["subject"]["output"]
    with pytest.raises(S.ScenarioError, match=r"subject\.output is required"):
        S.from_data(data)
    for bad in ("/abs", "../up", ".", "a//b", "HANDOFF.md", ".archive/x"):
        with pytest.raises(S.ScenarioError, match=r"subject\.output"):
            S.from_data(phased(phase("scaffold"), output=bad))
    assert S.from_data(phased(phase("scaffold"), output="apps/site")).subject.output == "apps/site"
    with pytest.raises(S.ScenarioError, match="gates run in the output folder"):
        S.from_data(dict(MINIMAL, subject={"skill": "arch-explain", "max_usd": 1, "gates": ["make check"]}))
    with pytest.raises(S.ScenarioError, match="only a skill subject runs in phases"):
        S.from_data(dict(MINIMAL, kind="command", subject={"argv": ["true"], "phases": [phase("a")]}))


def test_the_shipped_skill_scenarios_name_their_spend_caps():
    pytest.importorskip("yaml")
    folder = Path(__file__).resolve().parent.parent / "benchmark" / "scenarios"
    skills = [S.load(p) for p in S.catalog(folder) if S.load(p).kind == "skill"]
    # A subject in phases names a cap per phase, and none of its own.
    caps = {s.name: s.subject.max_usd or {p.name: p.max_usd for p in s.subject.phases} for s in skills}
    assert caps == {
        "create-full-system": {"scaffold": 60.0, "mvp": 75.0, "review": 25.0, "close": 30.0},
        "explain-tenancy": 2.0,
        "review-om": 3.0,
    }


def test_a_scenario_names_its_repeats_and_its_run_s_spend_cap():
    scn = S.from_data(MINIMAL)
    assert (scn.repeat, scn.max_spend_usd) == (None, None)  # a run takes 3 repeats and no run cap
    assert (scn.as_dict()["repeat"], scn.as_dict()["max_spend_usd"]) == (None, None)
    scn = S.from_data(dict(MINIMAL, repeat=1, max_spend_usd=190))
    assert (scn.repeat, scn.max_spend_usd) == (1, 190.0)
    assert (scn.as_dict()["repeat"], scn.as_dict()["max_spend_usd"]) == (1, 190.0)
    assert S.from_data(dict(MINIMAL, repeat=None, max_spend_usd=None)).repeat is None  # YAML's empty value names none


@pytest.mark.parametrize("bad", [0, -1, 1.5, "2", True])
def test_a_repeat_that_is_not_a_whole_number_of_at_least_1_is_refused_when_the_scenario_loads(tmp_path, bad):
    message = rf"scenario one: repeat: a whole number of at least 1, got {re.escape(repr(bad))}"
    with pytest.raises(S.ScenarioError, match=message):
        S.load(write(tmp_path, "one.json", dict(MINIMAL, repeat=bad)))


@pytest.mark.parametrize("bad", [0, -1, "190", True, float("inf")])
def test_a_run_s_spend_cap_that_is_not_an_amount_above_0_is_refused_when_the_scenario_loads(bad):
    with pytest.raises(S.ScenarioError, match=r"scenario one: max_spend_usd: an amount in US dollars above 0"):
        S.from_data(dict(MINIMAL, max_spend_usd=bad))


def test_the_full_system_scenario_runs_once_under_the_sum_of_its_phases_caps():
    pytest.importorskip("yaml")
    folder = Path(__file__).resolve().parent.parent / "benchmark" / "scenarios"
    scenarios = {s.name: s for s in (S.load(p) for p in S.catalog(folder))}
    system = scenarios.pop("create-full-system")
    assert system.repeat == 1
    assert system.max_spend_usd == sum(p.max_usd for p in system.subject.phases) == 190.0
    # The others name neither, so a run of one takes 3 repeats and no run cap unless a flag says otherwise.
    assert {(s.repeat, s.max_spend_usd) for s in scenarios.values()} == {(None, None)}


def test_the_full_system_scenario_tells_each_phase_only_what_it_needs():
    pytest.importorskip("yaml")
    scn = S.load(Path(__file__).resolve().parent.parent / "benchmark" / "scenarios" / "create-full-system.yaml")
    phases = {p.name: p for p in scn.subject.phases}
    assert list(phases) == ["scaffold", "mvp", "review", "close"]
    assert {p.session for p in phases.values()} == {"fresh"}
    # The builders keep the handoff note and read the product spec; the review and the close start in the tree.
    assert [p.name for p in phases.values() if p.hint] == ["scaffold", "mvp"]
    assert [p.name for p in phases.values() if "{target}" in p.prompt] == ["scaffold", "mvp"]
    assert phases["review"].cwd == phases["close"].cwd == "output"
    # The review and the close get no word of the scaffold or of the benchmark.
    for name in ("review", "close"):
        assert not re.search(r"scaffold|benchmark", phases[name].prompt, re.IGNORECASE)
    # No prompt names or points at the reference the judges read.
    repository = next(r.repository for r in scn.judges.references if r.repository)
    assert repository is not None
    project = repository.rstrip("/").rsplit("/", 1)[-1].lower()
    for phase in phases.values():
        assert repository not in phase.prompt and project not in phase.prompt.lower()
    assert scn.judges.weights == {"guideline": 0.4, "reference": 0.6}
    assert scn.subject.gates == ["make check", "make test-integration"]


# Agentic judges and their references -------------------------------------

GUIDELINE = {"name": "guideline", "weight": 0.4, "paths": ["architecture.md", "lenses", "skills"]}
REPOSITORY = {"name": "reference", "weight": 0.6, "repository": "https://github.com/acme/acme-system", "tag": "v0.7.0"}


def agentic(*refs: dict, **judges) -> dict:
    """MINIMAL with agentic judges against the references given, the two above by default."""
    return dict(MINIMAL, judges={"mode": "agentic", "references": list(refs or (GUIDELINE, REPOSITORY)), **judges})


def test_agentic_judges_read_references_of_the_checkout_and_of_a_repository():
    scn = S.from_data(agentic(budget={"tool_calls": 60, "wall_s": 1800, "max_usd": 5}))
    judges = scn.judges
    assert judges.agentic and judges.mode == "agentic"
    assert judges.weights == {"guideline": 0.4, "reference": 0.6}
    guideline, reference = judges.references
    assert guideline.paths == ["architecture.md", "lenses", "skills"] and guideline.repository is None
    assert (reference.repository, reference.tag, reference.paths) == ("https://github.com/acme/acme-system", "v0.7.0", [])
    # What the scenario sets replaces a default; the rest stay.
    assert judges.budget == A.Budget(tool_calls=60, wall_s=1800.0, max_usd=5.0)
    assert judges.budget.max_output_tokens == 16_000 and judges.budget.submits == 3
    assert scn.as_dict()["judges"] == {
        "providers": "3",
        "effort": "medium",
        "mode": "agentic",
        "budget": A.Budget(tool_calls=60, wall_s=1800.0, max_usd=5.0).as_dict(),
        "references": [GUIDELINE, REPOSITORY],
    }
    assert S.from_data(agentic()).judges.budget == A.Budget()


def test_one_shot_judges_are_the_default_and_have_no_budget_or_references():
    judges = S.from_data(MINIMAL).judges
    assert judges.mode == "one-shot" and not judges.agentic and judges.references == [] and judges.budget is None
    assert S.from_data(MINIMAL).as_dict()["judges"] == {"providers": "3", "effort": "medium", "mode": "one-shot"}
    with pytest.raises(S.ScenarioError, match=r"judges\.mode is one of one-shot, agentic, got 'tools'"):
        S.from_data(dict(MINIMAL, judges={"mode": "tools"}))
    with pytest.raises(S.ScenarioError, match="budget, references belong to agentic judges"):
        S.from_data(dict(MINIMAL, judges={"budget": {}, "references": [GUIDELINE]}))


@pytest.mark.parametrize(
    ("refs", "message"),
    [
        ((), r"references: a list of at least one reference"),
        ((dict(GUIDELINE, name="output"),), r"references\[0\]\.name 'output' is taken"),
        ((GUIDELINE, dict(REPOSITORY, name="guideline")), r"references\[1\]\.name 'guideline' is taken"),
        ((dict(GUIDELINE, name="The Guide"),), r"references\[0\]\.name: lowercase letters"),
        ((dict(GUIDELINE, weight=0), dict(REPOSITORY, weight=1)), r"references\[0\]\.weight: a share above 0"),
        ((dict(GUIDELINE, weight=True),), r"references\[0\]\.weight: a share above 0"),
        ((dict(GUIDELINE, weight=1.5),), r"references\[0\]\.weight: a share above 0 and at most 1"),
        ((GUIDELINE, dict(REPOSITORY, weight=0.5)), r"references: the weights sum to 1, got 0\.9"),
        ((dict(GUIDELINE, repository=REPOSITORY["repository"]),), r"one of the two"),
        (({"name": "guideline", "weight": 1},), r"one of the two"),
        ((dict(GUIDELINE, tag="v1"),), r"tag pins a repository"),
        ((dict(GUIDELINE, paths=[]),), r"paths: at least one path"),
        ((dict(GUIDELINE, paths="lenses"),), r"paths: expected a list"),
        ((dict(GUIDELINE, paths=["/etc"]),), r"paths: a file or a folder inside this checkout"),
        ((dict(GUIDELINE, paths=["../outside"]),), r"paths: a file or a folder inside this checkout"),
        ((dict(GUIDELINE, paths=["."]),), r"paths: a file or a folder inside this checkout"),
        ((dict(REPOSITORY, repository="git@github.com:acme/acme-system.git"),), r"repository: a public repository's https"),
        ((dict(REPOSITORY, repository="http://github.com/acme/acme-system"),), r"repository: a public repository's https"),
        ((dict(REPOSITORY, repository="https://token@github.com/acme/acme-system"),), r"repository: a public"),
        ((dict(REPOSITORY, repository="https://github.com/acme/acme-system?ref=x"),), r"repository: a public"),
        ((dict(REPOSITORY, tag="-v1"),), r"tag: the tag the repository is pinned at"),
        ((dict(REPOSITORY, tag="v1..2"),), r"tag: the tag the repository is pinned at"),
        (({k: v for k, v in REPOSITORY.items() if k != "tag"},), r"tag: the tag the repository is pinned at"),
        ((dict(REPOSITORY, branch="main"),), r"unknown key\(s\) branch"),
    ],
)
def test_a_reference_is_checked_when_the_scenario_loads(refs, message):
    data = agentic()
    data["judges"]["references"] = list(refs)
    with pytest.raises(S.ScenarioError, match=message):
        S.from_data(data)


@pytest.mark.parametrize(
    ("budget", "message"),
    [
        ({"tool_calls": 0}, r"budget\.tool_calls: a whole number of at least 1, got 0"),
        ({"input_tokens": 1.5}, r"budget\.input_tokens: a whole number"),
        ({"max_output_tokens": True}, r"budget\.max_output_tokens: a whole number"),
        ({"max_usd": 0}, r"budget\.max_usd: an amount above 0, got 0"),
        ({"wall_s": float("inf")}, r"budget\.wall_s: an amount above 0"),
        ({"max_usd": "3"}, r"budget\.max_usd: an amount above 0"),
        ({"dollars": 3}, r"unknown key\(s\) dollars"),
        ([], r"budget holds a mapping"),
    ],
)
def test_an_agentic_budget_is_checked_when_the_scenario_loads(budget, message):
    with pytest.raises(S.ScenarioError, match=message):
        S.from_data(agentic(budget=budget))


def test_agentic_judges_take_no_evidence_since_they_read_their_references():
    data = agentic()
    data["subject"] = dict(data["subject"], target="t")
    with pytest.raises(S.ScenarioError, match="an agentic judge reads its references instead"):
        S.from_data(dict(data, evidence={"files": ["**/*.py"]}))
    assert S.from_data(data).judges.agentic


def test_a_scenario_names_what_its_preflight_checks():
    scn = S.from_data(MINIMAL)
    assert scn.preflight == S.PreflightSpec() and scn.as_dict()["preflight"] == {
        "registries": [],
        "disk_gib": None,
        "memory_gib": None,
    }
    named = {"registries": ["https://pypi.org/simple/", "https://github.com"], "disk_gib": 40, "memory_gib": 0.5}
    scn = S.from_data(dict(MINIMAL, preflight=named))
    assert scn.preflight == S.PreflightSpec(
        registries=["https://pypi.org/simple/", "https://github.com"], disk_gib=40.0, memory_gib=0.5
    )
    assert scn.as_dict()["preflight"]["disk_gib"] == 40.0


@pytest.mark.parametrize(
    ("preflight", "said"),
    [
        ({"registries": ["http://pypi.org/simple/"]}, r"preflight\.registries: an https URL"),
        ({"registries": ["https://user:secret@pypi.org/"]}, r"preflight\.registries: an https URL"),
        ({"registries": "https://pypi.org/"}, r"preflight\.registries: expected a list"),
        ({"disk_gib": 0}, r"preflight\.disk_gib: an amount of GiB above 0"),
        ({"memory_gib": "16"}, r"preflight\.memory_gib: an amount of GiB above 0"),
        ({"cpus": 8}, r"preflight: unknown key\(s\) cpus"),
        (["https://pypi.org/"], r"preflight holds a mapping"),
    ],
)
def test_a_preflight_the_harness_cannot_check_is_refused_when_the_scenario_loads(preflight, said):
    with pytest.raises(S.ScenarioError, match=said):
        S.from_data(dict(MINIMAL, preflight=preflight))


def test_the_shipped_full_system_names_the_registries_and_the_room_it_needs():
    pytest.importorskip("yaml")
    scn = S.load(Path(__file__).resolve().parent.parent / "benchmark" / "scenarios" / "create-full-system.yaml")
    assert "https://pypi.org/simple/" in scn.preflight.registries and "https://registry.npmjs.org/" in scn.preflight.registries
    assert scn.preflight.disk_gib and scn.preflight.memory_gib
