"""benchmark/harness/preflight.py: each check's pass and fail, on fakes, and the machine held awake."""

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

import pytest

from harness import preflight as PF
from harness import providers as P
from harness import runtime as RT
from harness import scenario as S
from test_benchmark_runtime import DOCKER_OWN, docker_stand_in

BENCHMARK = Path(__file__).resolve().parent.parent / "benchmark"
EVERYWHERE = ["host", "container", "vm"]
SKILL = {
    "name": "one",
    "kind": "skill",
    "subject": {"skill": "arch-review-om", "prompt": "Review it.", "max_usd": 1},
    "rubric": "r",
    "runtimes": EVERYWHERE,
}
PHASED = {
    "name": "built",
    "kind": "skill",
    "runtimes": ["vm"],
    "requires": ["docker"],
    "subject": {
        "skill": "arch-scaffold-new",
        "output": "acme",
        "gates": ["make check"],
        "phases": [
            {"name": "scaffold", "prompt": "Build it.", "max_turns": 40, "max_usd": 6, "timeout_s": 600},
            {"name": "close", "prompt": "Close it.", "max_turns": 20, "max_usd": 4, "timeout_s": 300, "max_gate_reruns": 0},
        ],
    },
    "rubric": "r",
}
SUBJECT_KEY = "subject-key-for-the-preflight"
JUDGE_KEYS = {
    "ANTHROPIC_API_KEY": "judge-anthropic-key",
    "OPENAI_API_KEY": "judge-openai-key",
    "GEMINI_API_KEY": "judge-gemini-key",
    "XAI_API_KEY": "judge-xai-key",
}


def scenario(base: dict = SKILL, **over) -> S.Scenario:
    return S.from_data({**base, **over})


def context(scn: S.Scenario, rt: RT.BaseRuntime, **over) -> PF.Context:
    """The run as `run.py` resolves it: a clean checkout, a spend cap, and the keys set."""
    sessions = list(scn.subject.phases) or [
        S.Phase(name="subject", prompt="p", max_turns=scn.subject.max_turns, max_usd=scn.subject.max_usd or 0.0, timeout_s=900)
    ]
    fields = {
        "scenario": scn,
        "runtime": rt,
        "sessions": sessions if scn.kind == "skill" else [],
        "max_spend_usd": 10.0,
        "providers": P.ALL,
        "release": "0.37.0",
        "checkout": {"commit": "a" * 40, "dirty": False, "dirty_paths": []},
        "env": {"SUBJECT_ANTHROPIC_API_KEY": SUBJECT_KEY, **JUDGE_KEYS},
        **over,
    }
    return PF.Context(**fields)


def host(tmp_path: Path) -> RT.BaseRuntime:
    return RT.build("host", tmp_path / "run", sandbox=tmp_path / "sandbox")


def tools_folder(tmp_path: Path, **scripts: str) -> Path:
    """A folder of stand-ins, each a shell script with the body given, and `sh` beside them."""
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    if not (folder / "sh").exists():
        (folder / "sh").symlink_to("/bin/sh")
    for name, body in scripts.items():
        path = folder / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)
    return folder


def machine(tmp_path: Path, folder: Path | None = None, **config) -> RT.VmRuntime:
    """Another machine that runs here: `env -i` stands in for the prefix, with the stand-ins first on its path."""
    path = f"{folder}:/usr/bin:/bin" if folder else "/usr/bin:/bin"
    settings = {"exec_prefix": ["env", "-i", f"PATH={path}"], "remote_workspace": str(tmp_path / "remote"), **config}
    rt = RT.build("vm", tmp_path / "run", config=settings, sandbox=tmp_path / "sandbox")
    assert isinstance(rt, RT.VmRuntime)
    return rt


@pytest.fixture
def lists(monkeypatch):
    """Every model list the preflight asks, answering 200 unless a key is listed as refused; the calls it got."""
    calls: list[dict] = []
    refused: set[str] = set()

    def answer(url: str, headers: dict[str, str], timeout_s: float = PF.HTTP_TIMEOUT_S) -> int | str:
        calls.append({"url": url, "headers": dict(headers)})
        return 401 if any(key in value for value in headers.values() for key in refused) else 200

    monkeypatch.setattr(PF, "http_status", answer)
    return calls, refused


# budgets ------------------------------------------------------------------


def test_a_phased_run_names_every_bound_it_has():
    scn = scenario(PHASED)
    check = PF.budgets(context(scn, RT.build("host", Path("run")), max_spend_usd=10.0))
    assert check.status == PF.PASS
    assert check.facts["max_spend_usd"] == 10.0
    assert check.facts["sessions"] == [
        {"name": "scaffold", "max_turns": 40, "max_usd": 6.0, "timeout_s": 600, "max_gate_reruns": 3},
        {"name": "close", "max_turns": 20, "max_usd": 4.0, "timeout_s": 300, "max_gate_reruns": 0},
    ]
    assert "gate-rerun cap" in check.detail and "$10" in check.detail


def test_a_run_with_no_spend_cap_fails_and_says_where_to_set_one(tmp_path):
    check = PF.budgets(context(scenario(PHASED), host(tmp_path), max_spend_usd=None))
    assert check.status == PF.FAIL
    assert check.detail == "the run has no spend cap"
    assert check.fix is not None and "max_spend_usd" in check.fix and "--max-spend-usd" in check.fix


def test_a_session_with_no_spend_bound_fails(tmp_path):
    unbounded = S.Phase(name="loose", prompt="p", max_turns=10, max_usd=0.0, timeout_s=60)
    check = PF.budgets(context(scenario(), host(tmp_path), sessions=[unbounded]))
    assert check.status == PF.FAIL
    assert check.detail == "session loose has no max_usd"


def test_a_session_needs_money_and_time_and_no_turn_cap(tmp_path):
    # A turn count is no bound: a session that names none passes, and one without a timeout does not.
    free = S.Phase(name="free", prompt="p", max_usd=2.0, timeout_s=600)
    check = PF.budgets(context(scenario(), host(tmp_path), sessions=[free]))
    assert check.status == PF.PASS and "turn" not in check.detail
    assert check.facts["sessions"] == [{"name": "free", "max_usd": 2.0, "timeout_s": 600}]
    endless = S.Phase(name="endless", prompt="p", max_usd=2.0, timeout_s=0)
    check = PF.budgets(context(scenario(), host(tmp_path), sessions=[endless]))
    assert (check.status, check.detail) == (PF.FAIL, "session endless has no timeout_s")
    assert check.fix is not None and "max_turns" not in check.fix


# checkout -----------------------------------------------------------------


def test_a_clean_checkout_passes(tmp_path):
    check = PF.checkout(context(scenario(), host(tmp_path)))
    assert check.status == PF.PASS and check.facts["dirty"] is False


def test_a_dirty_checkout_fails_and_names_what_changed(tmp_path):
    dirty = {"commit": "b" * 40, "dirty": True, "dirty_paths": ["skills/arch-review-om/SKILL.md", "benchmark/run.py"]}
    check = PF.checkout(context(scenario(), host(tmp_path), checkout=dirty))
    assert check.status == PF.FAIL
    assert "skills/arch-review-om/SKILL.md, benchmark/run.py" in check.detail
    assert check.fix == "commit the changes, or set them aside, and run again"
    assert check.facts["dirty_paths"] == dirty["dirty_paths"]


def test_a_checkout_git_cannot_read_fails(tmp_path):
    check = PF.checkout(context(scenario(), host(tmp_path), checkout={"commit": "", "dirty": None, "dirty_paths": []}))
    assert check.status == PF.FAIL and "git could not say" in check.detail


# references ---------------------------------------------------------------

AGENTIC = {
    "providers": "anthropic",
    "mode": "agentic",
    "references": [
        {"name": "guideline", "weight": 0.4, "paths": ["lenses"]},
        {"name": "reference", "weight": 0.6, "repository": "https://github.com/acme/acme-system", "tag": "v1.0.0"},
    ],
}


def test_one_shot_judges_read_no_reference(tmp_path):
    assert PF.references(context(scenario(), host(tmp_path))).status == PF.SKIP


def test_a_reference_pinned_at_this_checkout_s_release_passes(tmp_path):
    staged = {"guideline": {"source": "checkout"}, "reference": {"source": "repository", "pins": "v0.37.0"}}
    check = PF.references(context(scenario(judges=AGENTIC), host(tmp_path), references=staged))
    assert check.status == PF.PASS and "pins v0.37.0" in check.detail


@pytest.mark.parametrize(("pins", "said"), [("v0.36.0", "pins the guideline at v0.36.0"), (None, "names no guideline release")])
def test_a_reference_pinned_elsewhere_or_nowhere_fails(tmp_path, pins, said):
    staged = {"guideline": {"source": "checkout"}, "reference": {"source": "repository", "pins": pins}}
    check = PF.references(context(scenario(judges=AGENTIC), host(tmp_path), references=staged))
    assert check.status == PF.FAIL and said in check.detail
    assert check.fix is not None and "pins v0.37.0" in check.fix


def test_a_reference_that_could_not_be_staged_fails_with_why(tmp_path):
    why = "reference reference: https://github.com/acme/acme-system at tag v1.0.0 could not be fetched: not found"
    check = PF.references(context(scenario(judges=AGENTIC), host(tmp_path), stage_error=why))
    assert check.status == PF.FAIL and check.detail == why and "git ls-remote" in (check.fix or "")


# awake --------------------------------------------------------------------


def test_off_macos_nothing_is_held_awake(tmp_path, monkeypatch):
    monkeypatch.setattr(PF, "system", lambda: "linux")
    assert PF.awake(context(scenario(), host(tmp_path))).status == PF.SKIP


def test_on_macos_without_caffeinate_the_check_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(PF, "system", lambda: "darwin")
    monkeypatch.setattr(PF.shutil, "which", lambda name: None)
    check = PF.awake(context(scenario(), host(tmp_path)))
    assert check.status == PF.FAIL and "caffeinate is not on the path" in check.detail


@pytest.mark.parametrize(("source", "status"), [("Battery Power", PF.FAIL), ("AC Power", PF.PASS), (None, PF.PASS)])
def test_on_macos_the_check_asks_for_ac_power(tmp_path, monkeypatch, source, status):
    monkeypatch.setattr(PF, "system", lambda: "darwin")
    monkeypatch.setattr(PF.shutil, "which", lambda name: "/usr/bin/caffeinate")
    monkeypatch.setattr(PF, "power_source", lambda: source)
    check = PF.awake(context(scenario(), host(tmp_path)))
    assert check.status == status
    if status == PF.FAIL:
        assert check.fix == "plug it in, and keep it plugged in until the run ends"


def test_the_power_source_is_read_from_pmset(tmp_path, monkeypatch):
    folder = tools_folder(tmp_path, pmset="echo \"Now drawing from 'Battery Power'\"; echo ' -InternalBattery-0'")
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    assert PF.power_source() == "Battery Power"


def fake_caffeinate(tmp_path: Path) -> tuple[Path, Path]:
    """A caffeinate stand-in that writes its words and its process id, then waits to be stopped."""
    log = tmp_path / "caffeinate.log"
    path = tmp_path / "caffeinate"
    path.write_text(
        f"#!{sys.executable}\nimport os, sys, time\n"
        f"open({str(log)!r}, 'w').write(' '.join(sys.argv[1:]) + '\\n' + str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path, log


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers: read its state where the platform says it.
    state = os.popen(f"ps -o stat= -p {pid}").read().strip()
    return bool(state) and not state.startswith("Z")


def test_on_macos_a_run_is_held_awake_until_it_ends(tmp_path, monkeypatch):
    path, log = fake_caffeinate(tmp_path)
    monkeypatch.setattr(PF, "system", lambda: "darwin")
    monkeypatch.setattr(PF.shutil, "which", lambda name: str(path) if name == "caffeinate" else None)
    with PF.held_awake() as note:
        assert note is None
        for _ in range(100):
            if log.exists() and "\n" in log.read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        words, pid = log.read_text(encoding="utf-8").split("\n")
        assert words == f"-i -s -w {os.getpid()}"
        assert alive(int(pid))
    assert not alive(int(pid))


def test_nothing_is_held_awake_when_the_run_spends_nothing_or_off_macos(tmp_path, monkeypatch):
    path, log = fake_caffeinate(tmp_path)
    monkeypatch.setattr(PF.shutil, "which", lambda name: str(path))
    monkeypatch.setattr(PF, "system", lambda: "darwin")
    with PF.held_awake(False) as note:
        assert note is None
    monkeypatch.setattr(PF, "system", lambda: "linux")
    with PF.held_awake() as note:
        assert note is None
    assert not log.exists()
    monkeypatch.setattr(PF, "system", lambda: "darwin")
    monkeypatch.setattr(PF.shutil, "which", lambda name: None)
    with PF.held_awake() as note:
        assert note == "nothing holds this machine awake: caffeinate is not on the path"


# the keys -----------------------------------------------------------------


def test_the_subject_s_own_key_passes_on_anthropic_s_model_list_in_a_header(tmp_path, lists):
    calls, _ = lists
    check = PF.subject_key(context(scenario(), host(tmp_path)))
    assert check.status == PF.PASS and check.facts == {"key": "SUBJECT_ANTHROPIC_API_KEY", "answered": 200}
    assert calls == [
        {"url": "https://api.anthropic.com/v1/models", "headers": {"x-api-key": SUBJECT_KEY, "anthropic-version": "2023-06-01"}}
    ]


def test_a_skill_with_no_key_of_its_own_fails_and_a_command_needs_none(tmp_path, lists):
    env = dict(JUDGE_KEYS)
    check = PF.subject_key(context(scenario(), host(tmp_path), env=env))
    assert check.status == PF.FAIL and "SUBJECT_ANTHROPIC_API_KEY is not set" in check.detail
    command = scenario(kind="command", subject={"argv": ["true"]})
    assert PF.subject_key(context(command, host(tmp_path), env=env)).status == PF.SKIP


def test_a_subject_key_that_is_a_judge_s_fails_before_any_call(tmp_path, lists):
    calls, _ = lists
    env = {**JUDGE_KEYS, "SUBJECT_ANTHROPIC_API_KEY": JUDGE_KEYS["OPENAI_API_KEY"]}
    check = PF.subject_key(context(scenario(), host(tmp_path), env=env))
    assert check.status == PF.FAIL and "holds a judge's key" in check.detail and calls == []


def test_a_subject_key_the_model_list_refuses_fails_by_its_name_never_its_value(tmp_path, lists):
    _, refused = lists
    refused.add(SUBJECT_KEY)
    check = PF.subject_key(context(scenario(), host(tmp_path)))
    assert check.status == PF.FAIL
    assert check.detail == "anthropic's model list answered 401 to SUBJECT_ANTHROPIC_API_KEY"
    assert SUBJECT_KEY not in json.dumps(check.as_dict())


def test_a_qa_subject_is_checked_by_its_provider_s_key(tmp_path, lists):
    calls, _ = lists
    qa = scenario(kind="qa", subject={"prompt": "Why?", "provider": "openai"})
    check = PF.subject_key(context(qa, host(tmp_path)))
    assert check.status == PF.PASS and check.facts["key"] == "OPENAI_API_KEY"
    assert calls[0]["url"] == "https://api.openai.com/v1/models"
    check = PF.subject_key(context(qa, host(tmp_path), env={}))
    assert check.status == PF.FAIL and "OPENAI_API_KEY" in check.detail


def test_every_judge_s_key_is_taken_by_its_model_list(tmp_path, lists):
    calls, _ = lists
    env = {"SUBJECT_ANTHROPIC_API_KEY": SUBJECT_KEY, **JUDGE_KEYS, "GOOGLE_API_KEY": "another-account"}
    del env["XAI_API_KEY"]
    env["GROK_API_KEY"] = "judge-grok-key"
    check = PF.judge_keys(context(scenario(), host(tmp_path), env=env))
    assert check.status == PF.PASS
    assert [(r["provider"], r["key"], r["answered"]) for r in check.facts["judges"]] == [
        ("anthropic", "ANTHROPIC_API_KEY", 200),
        ("openai", "OPENAI_API_KEY", 200),
        ("gemini", "GEMINI_API_KEY", 200),
        ("xai", "GROK_API_KEY", 200),
    ]
    gemini = next(c for c in calls if "googleapis" in c["url"])
    assert gemini["headers"] == {"x-goog-api-key": "judge-gemini-key"}  # never the other account's key, never in the URL
    assert all("key" not in c["url"] for c in calls)


def test_a_judge_with_no_key_or_a_refused_one_fails_and_names_the_variable(tmp_path, lists):
    _, refused = lists
    refused.add(JUDGE_KEYS["OPENAI_API_KEY"])
    env = {k: v for k, v in JUDGE_KEYS.items() if k != "GEMINI_API_KEY"}
    check = PF.judge_keys(context(scenario(), host(tmp_path), env=env))
    assert check.status == PF.FAIL
    assert check.detail == "openai's model list answered 401 to OPENAI_API_KEY; gemini has no key (GEMINI_API_KEY)"
    assert check.fix == "set OPENAI_API_KEY to a valid key; set GEMINI_API_KEY, or leave gemini out of --providers"
    assert not any(v in json.dumps(check.as_dict()) for v in JUDGE_KEYS.values())


def test_only_the_selected_judges_are_asked(tmp_path, lists):
    calls, _ = lists
    check = PF.judge_keys(context(scenario(), host(tmp_path), providers=P.parse("anthropic")))
    assert check.status == PF.PASS and [c["url"] for c in calls] == ["https://api.anthropic.com/v1/models"]


def test_a_model_list_that_does_not_answer_says_where_to_look(tmp_path, monkeypatch):
    monkeypatch.setattr(PF, "http_status", lambda url, headers, timeout_s=20: "no answer: timed out")
    check = PF.judge_keys(context(scenario(), host(tmp_path), providers=P.parse("xai")))
    assert check.status == PF.FAIL and check.fix == "check that this machine reaches api.x.ai"


# the runtime --------------------------------------------------------------


def test_the_host_is_up(tmp_path):
    assert PF.runtime(context(scenario(), host(tmp_path))).status == PF.PASS


def test_a_qa_subject_needs_no_runtime(tmp_path):
    qa = scenario(kind="qa", subject={"prompt": "Why?"})
    for check in (PF.runtime, PF.workspace, PF.tools, PF.resources, PF.network):
        assert check(context(qa, host(tmp_path))).status == PF.SKIP


def test_a_machine_that_does_not_answer_fails_and_says_to_start_it(tmp_path):
    rt = machine(tmp_path, exec_prefix=[str(tmp_path / "no-such-prefix")])
    check = PF.runtime(context(scenario(), rt))
    assert check.status == PF.FAIL and "did not answer" in check.detail
    assert check.fix is not None and "limactl start swe-benchmark" in check.fix


def test_a_machine_whose_check_fails_fails(tmp_path):
    check = PF.runtime(context(scenario(), machine(tmp_path, check=["sh", "-c", "exit 1"])))
    assert check.status == PF.FAIL and "the machine's check fails there" in check.detail
    passing = PF.runtime(context(scenario(), machine(tmp_path, check=["true"])))
    assert passing.status == PF.PASS and passing.detail == "the other machine answers through its prefix, and its check passes"


def docker(tmp_path: Path, engine: int = 0, image: int = 0) -> str:
    """A docker stand-in: the engine and the image answer, or not, as asked; a build logs itself."""
    log = tmp_path / "docker.log"
    body = (
        f'echo "$*" >> {log}\n'
        f'case "$1" in version) echo 29.8.1; exit {engine};; image) echo sha256:abc; exit {image};; build) exit 0;; esac\n'
        "exit 0"
    )
    return str(tools_folder(tmp_path, docker=body) / "docker")


def test_a_container_run_needs_the_engine_and_the_image(tmp_path):
    down = RT.build("container", tmp_path / "a", config={"docker": docker(tmp_path, engine=1)})
    check = PF.runtime(context(scenario(), down))
    assert check.status == PF.FAIL and check.fix == "start Docker on this machine"
    missing = RT.build("container", tmp_path / "b", config={"docker": docker(tmp_path, image=1)})
    check = PF.runtime(context(scenario(), missing))
    assert check.status == PF.FAIL and check.fix == "pass --build, which builds it before the run"
    ready = RT.build("container", tmp_path / "c", config={"docker": docker(tmp_path)})
    assert PF.runtime(context(scenario(), ready)).status == PF.PASS


def test_a_container_run_that_builds_its_image_builds_it_in_the_preflight(tmp_path):
    rt = RT.build("container", tmp_path / "run", config={"docker": docker(tmp_path)})
    check = PF.runtime(context(scenario(), rt, build=True, run_dir=tmp_path / "run"))
    assert check.status == PF.PASS
    assert any(line.startswith("build -t") for line in (tmp_path / "docker.log").read_text(encoding="utf-8").splitlines())
    assert (tmp_path / "run" / "streams" / "build.jsonl").is_file()


def test_the_folder_runs_work_in_there_holds_nothing(tmp_path):
    rt = machine(tmp_path)
    assert PF.workspace(context(scenario(), rt)).status == PF.PASS  # not there yet
    (tmp_path / "remote").mkdir()
    assert PF.workspace(context(scenario(), rt)).status == PF.PASS
    assert PF.workspace(context(scenario(), host(tmp_path))).status == PF.SKIP


def test_a_machine_another_run_holds_fails_and_names_that_run(tmp_path):
    (tmp_path / "remote" / ".lock").mkdir(parents=True)
    (tmp_path / "remote" / ".lock" / "run").write_text("20260927-000000-built-1234abcd\n", encoding="utf-8")
    check = PF.workspace(context(scenario(), machine(tmp_path)))
    assert check.status == PF.FAIL
    assert check.detail.endswith("/.lock is there and names 20260927-000000-built-1234abcd")
    assert check.fix is not None and "rm -rf --" in check.fix


def test_what_an_earlier_run_left_there_fails(tmp_path):
    (tmp_path / "remote" / "20260926-000000-built-1234abcd").mkdir(parents=True)
    check = PF.workspace(context(scenario(), machine(tmp_path)))
    assert check.status == PF.FAIL and "20260926-000000-built-1234abcd" in check.detail
    assert check.fix is not None and check.fix.endswith(f"sudo rm -rf -- {tmp_path / 'remote'}`")


def test_a_machine_whose_docker_holds_a_container_or_a_volume_fails_and_names_them_and_how_to_clear_them(tmp_path, monkeypatch):
    left = (("container", "c-db", "acme-db-1"), ("container", "c-web", "acme-web-1"), ("volume", "acme_pgdata", "acme_pgdata"))
    state = docker_stand_in(tmp_path, *DOCKER_OWN, ("network", "n-acme", "acme_default"), *left)
    monkeypatch.setattr(RT, "DOCKER", str(state / "docker"))
    rt = machine(tmp_path)
    check = PF.workspace(context(scenario(), rt))
    assert check.status == PF.FAIL
    assert check.detail == (
        "the machine's Docker holds what earlier runs left, and the next subject would start beside it: "
        "2 containers (acme-db-1, acme-web-1), 1 volume (acme_pgdata)"
    )
    assert check.facts["docker"] == {"containers": ["acme-db-1", "acme-web-1"], "volumes": ["acme_pgdata"]}
    clear = "env -i PATH=/usr/bin:/bin sh -c " + shlex.quote(PF.CLEAR_DOCKER)
    assert check.fix == f"no run holds the machine, so clear its Docker: `{clear}`"
    assert "docker ps -aq | xargs -r docker rm -f -v" in PF.CLEAR_DOCKER and "docker volume prune --all" in PF.CLEAR_DOCKER
    (tmp_path / "remote" / "20260926-000000-built-1234abcd").mkdir(parents=True)
    both = PF.workspace(context(scenario(), rt))  # one failure names both, so one fix clears the machine
    assert both.detail.startswith(f"{tmp_path / 'remote'} there holds what earlier runs left") and both.detail.endswith(
        check.detail
    )
    assert both.fix is not None and both.fix.endswith(check.fix)


def test_a_machine_whose_docker_holds_only_networks_holds_nothing_a_subject_starts_beside(tmp_path, monkeypatch):
    state = docker_stand_in(tmp_path, *DOCKER_OWN, ("network", "n-acme", "acme_default"))
    monkeypatch.setattr(RT, "DOCKER", str(state / "docker"))
    check = PF.workspace(context(scenario(), machine(tmp_path)))
    assert check.status == PF.PASS
    assert check.detail.endswith("holds nothing, and the machine's Docker holds no container and no volume")


def test_a_machine_whose_docker_does_not_answer_fails(tmp_path, monkeypatch):
    folder = tools_folder(tmp_path, docker="exit 1")
    monkeypatch.setattr(RT, "DOCKER", str(folder / "docker"))
    check = PF.workspace(context(scenario(), machine(tmp_path)))
    assert check.status == PF.FAIL and check.detail == "the machine's Docker did not answer (exit 3)"
    assert check.fix is not None and "docker ps -a` fails" in check.fix


# the tools ----------------------------------------------------------------


def pinned(**versions: str | None) -> dict:
    """A runtime config whose tools are asked `<name> --version`, each pinned at the version given, or at none."""
    return {"tools": [{"argv": [name, "--version"], **({"version": v} if v else {})} for name, v in versions.items()]}


def test_every_tool_answers_at_its_pin(tmp_path):
    folder = tools_folder(tmp_path, claude="echo '2.1.283 (Claude Code)'", node="echo v24.21.0", make="echo 'GNU Make 4.4.1'")
    rt = machine(tmp_path, folder, **pinned(claude="2.1.283", node="24", make=None))
    check = PF.tools(context(scenario(), rt))
    assert check.status == PF.PASS and check.detail == "3 tool(s) answer, 2 of them at their pinned versions"
    assert check.facts["tools"][1] == {"argv": ["node", "--version"], "pinned": "24", "answered": "v24.21.0"}


def test_a_tool_at_another_version_or_missing_fails(tmp_path):
    folder = tools_folder(tmp_path, claude="echo '2.1.200 (Claude Code)'")
    rt = machine(tmp_path, folder, **pinned(claude="2.1.283", **{"no-such-tool": None}))
    check = PF.tools(context(scenario(), rt))
    assert check.status == PF.FAIL
    assert "`claude --version` answered '2.1.200 (Claude Code)', and 2.1.283 is pinned" in check.detail
    assert "`no-such-tool --version` did not answer (exit 127" in check.detail
    assert check.fix is not None and "limactl create" in check.fix


def test_a_skill_s_claude_code_is_asked_even_where_nothing_is_pinned(tmp_path):
    folder = tools_folder(tmp_path, claude="echo '2.1.283 (Claude Code)'")
    check = PF.tools(context(scenario(), machine(tmp_path, folder)))
    assert check.status == PF.PASS
    assert check.facts["tools"] == [{"argv": ["claude", "--version"], "pinned": None, "answered": "2.1.283 (Claude Code)"}]
    command = scenario(kind="command", subject={"argv": ["true"]})
    assert PF.tools(context(command, machine(tmp_path, folder))).status == PF.SKIP


@pytest.mark.parametrize("tools", ["claude", [{"argv": "claude --version"}], [{"argv": ["claude"], "pin": "1"}]])
def test_a_tools_list_of_another_shape_fails(tmp_path, tools):
    check = PF.tools(context(scenario(), machine(tmp_path, tools=tools)))
    assert check.status == PF.FAIL and check.detail.startswith("the runtime config's tools")


def test_a_container_asks_the_tools_its_dockerfile_pins(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "ENV NODE_MAJOR=24\nCOPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /usr/local/bin/\n"
        "RUN npm install -g @anthropic-ai/claude-code@2.1.283\n",
        encoding="utf-8",
    )
    rt = RT.build("container", tmp_path / "run", config={"dockerfile": str(dockerfile)})
    assert PF.tools_of(context(scenario(), rt)) == [
        PF.Tool(("claude", "--version"), "2.1.283"),
        PF.Tool(("uv", "--version"), "0.12.17"),
        PF.Tool(("node", "--version"), "24"),
    ]


def test_the_shipped_dockerfile_pins_what_the_container_check_asks():
    found = PF.dockerfile_pins(BENCHMARK / "runtime" / "Dockerfile")
    assert [t.argv[0] for t in found] == ["claude", "uv", "node"] and all(t.version for t in found)


def test_the_lima_runtime_config_pins_what_the_template_installs():
    pytest.importorskip("yaml")
    config = S.parse_text((BENCHMARK / "runtime" / "lima" / "runtime-config.yaml").read_text(encoding="utf-8"), ".yaml")
    asked = {" ".join(t.argv[:2]) if t.argv[0] == "docker" else t.argv[0]: t.version for t in PF.parse_tools(config["tools"])}
    template = (BENCHMARK / "runtime" / "lima" / "benchmark.yaml").read_text(encoding="utf-8")
    pins = dict(re.findall(r"^\s*([A-Z_]+)=(\S+)$", template, re.MULTILINE))
    # An apt version names the release after its epoch and before its build: 5:29.8.1-1~ubuntu gives 29.8.1.
    release = {k: re.sub(r"^\d+:|-[^-]*$", "", v) for k, v in pins.items()}
    assert asked == {
        "claude": pins["CLAUDE_CODE_VERSION"],
        "uv": pins["UV_VERSION"],
        "node": pins["NODE_MAJOR"],
        "pnpm": pins["PNPM_VERSION"],
        "terraform": pins["TERRAFORM_VERSION"],
        "docker version": release["DOCKER_VERSION"],
        "docker compose": release["COMPOSE_VERSION"],
        "make": None,
        "git": None,
    }
    # What the readiness probe runs, the preflight asks.
    probe = template.split("probes:", 1)[1]
    for name in ("claude", "uv", "node", "pnpm", "terraform", "docker", "make", "git"):
        assert re.search(rf"^\s+{name} ", probe, re.MULTILINE), name


@pytest.mark.parametrize(
    ("pin", "line", "ok"),
    [
        ("24", "v24.21.0", True),
        ("1.16.4", "Terraform v1.16.4", True),
        ("0.12.17", "uv 0.12.17 (aarch64-unknown-linux-gnu)", True),
        ("5.5.1", "Docker Compose version v5.5.1", True),
        ("1.16.4", "Terraform v1.16.40", False),
        ("24", "v2.4.0", False),
    ],
)
def test_a_version_matches_its_pin_or_lies_within_it(pin, line, ok):
    assert PF.matches(pin, PF.version_in(line)) is ok


# resources ----------------------------------------------------------------


def test_the_disk_and_memory_are_read_where_the_subject_works(tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:  8388608 kB\nMemAvailable:  4194304 kB\n", encoding="utf-8")
    monkeypatch.setattr(PF, "MEMINFO", str(meminfo))
    rt = machine(tmp_path)
    check = PF.resources(context(scenario(preflight={"disk_gib": 0.001, "memory_gib": 2}), rt))
    assert check.status == PF.PASS
    assert check.facts["folder"] == str(tmp_path / "remote")  # not there yet: the folder above it is read
    assert check.facts["memory_available_gib"] == 4.0 and check.facts["disk_free_gib"] > 0
    assert "against the 0.001 GiB of disk and 2 GiB of memory the scenario needs" in check.detail


def test_too_little_disk_or_memory_fails(tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable:  1048576 kB\n", encoding="utf-8")
    monkeypatch.setattr(PF, "MEMINFO", str(meminfo))
    check = PF.resources(context(scenario(preflight={"disk_gib": 100_000, "memory_gib": 16}), machine(tmp_path)))
    assert check.status == PF.FAIL
    assert (
        "the scenario needs 100000;" in check.detail
        and "1.0 GiB of memory is available, and the scenario needs 16" in check.detail
    )
    assert check.fix is not None and "docker system prune" in check.fix and "docker ps" in check.fix


def test_memory_the_runtime_cannot_report_fails_only_when_the_scenario_needs_some(tmp_path, monkeypatch):
    monkeypatch.setattr(PF, "MEMINFO", str(tmp_path / "none"))
    check = PF.resources(context(scenario(), machine(tmp_path)))
    assert check.status == PF.PASS and check.facts["memory_available_gib"] is None
    check = PF.resources(context(scenario(preflight={"memory_gib": 1}), machine(tmp_path)))
    assert check.status == PF.FAIL and "reports no available memory" in check.detail


# network ------------------------------------------------------------------

CURL = 'for last; do :; done; case "$last" in *down*) printf 000; exit 6;; *docker*) printf 401;; *) printf 200;; esac'


def test_the_model_api_and_the_registries_answer_from_where_the_subject_runs(tmp_path):
    folder = tools_folder(tmp_path, curl=CURL)
    scn = scenario(preflight={"registries": ["https://pypi.org/simple/", "https://registry-1.docker.io/v2/"]})
    check = PF.network(context(scn, machine(tmp_path, folder)))
    assert check.status == PF.PASS
    assert check.facts["urls"] == [
        {"url": "https://api.anthropic.com", "answered": "200"},
        {"url": "https://pypi.org/simple/", "answered": "200"},
        {"url": "https://registry-1.docker.io/v2/", "answered": "401"},  # an answer: it is reached
    ]


def test_a_registry_that_answers_nothing_fails(tmp_path):
    folder = tools_folder(tmp_path, curl=CURL)
    scn = scenario(preflight={"registries": ["https://down.example.org/"]})
    check = PF.network(context(scn, machine(tmp_path, folder)))
    assert check.status == PF.FAIL and check.detail == "https://down.example.org/ answered nothing from where the subject runs"
    assert check.fix is not None and "curl -sS -o /dev/null https://down.example.org/" in check.fix


def test_no_curl_where_the_subject_runs_fails(tmp_path):
    rt = machine(tmp_path, exec_prefix=["env", "-i", f"PATH={tools_folder(tmp_path)}"])
    check = PF.network(context(scenario(), rt))
    assert check.status == PF.FAIL and check.detail.startswith("curl is not there")


def test_a_command_that_names_no_registry_reaches_nothing_to_check(tmp_path):
    command = scenario(kind="command", subject={"argv": ["true"]})
    assert PF.network(context(command, machine(tmp_path))).status == PF.SKIP


# requires -----------------------------------------------------------------


def compose(tmp_path: Path, up: int = 0, down: int = 0) -> Path:
    """A docker stand-in for Compose: it logs each call and keeps the file it is given; up and down exit as asked."""
    log = tmp_path / "compose.log"
    body = (
        f'echo "$*" >> {log}\n'
        f'case "$*" in *" up "*) cp "$5" {tmp_path}/compose.yaml; exit {up};; *" down "*) exit {down};; esac\n'
        "exit 0"
    )
    return tools_folder(tmp_path, docker=body)


def test_a_compose_stack_starts_turns_healthy_and_stops(tmp_path):
    rt = machine(tmp_path, compose(tmp_path))
    check = PF.requires(context(scenario(PHASED), rt))
    assert check.status == PF.PASS
    project = check.facts["docker"]["project"]
    calls = (tmp_path / "compose.log").read_text(encoding="utf-8").splitlines()
    assert calls[0].startswith(f"compose -p {project} -f ") and calls[0].endswith(" up -d --quiet-pull --wait --wait-timeout 120")
    assert calls[1].endswith("down -v --remove-orphans --timeout 10")
    stack = (tmp_path / "compose.yaml").read_text(encoding="utf-8")
    assert f"image: {PF.COMPOSE_IMAGE}" in stack and 'test: ["CMD", "true"]' in stack and "init: true" in stack
    pytest.importorskip("yaml")
    assert S.parse_text(stack, ".yaml")["services"]["probe"]["healthcheck"]["interval"] == "1s"


@pytest.mark.parametrize(("up", "down", "said"), [(1, 0, "did not start and turn healthy within 120 s"), (0, 1, "did not stop")])
def test_a_compose_stack_that_does_not_start_or_stop_fails_and_is_taken_down(tmp_path, up, down, said):
    rt = machine(tmp_path, compose(tmp_path, up=up, down=down))
    check = PF.requires(context(scenario(PHASED), rt))
    assert check.status == PF.FAIL and said in check.detail
    assert "down -v" in (tmp_path / "compose.log").read_text(encoding="utf-8")  # down runs whatever up did


def test_a_scenario_that_requires_nothing_has_nothing_to_start(tmp_path):
    assert PF.requires(context(scenario(), host(tmp_path))).status == PF.SKIP


# the run of the checks ----------------------------------------------------


def test_the_checks_stop_at_the_first_failure_and_name_the_rest(tmp_path, lists):
    said: list[str] = []
    ctx = context(scenario(), host(tmp_path), env=dict(JUDGE_KEYS))
    record = PF.run(ctx, said.append)
    assert record["passed"] is False and record["failed"] == "subject_key"
    assert [c["name"] for c in record["checks"]] == ["budgets", "checkout", "references", "awake", "subject_key"]
    assert record["not_run"] == ["judge_keys", "runtime", "workspace", "tools", "resources", "network", "requires"]
    assert said[-1].startswith("  FAIL  subject_key") and "fix: set SUBJECT_ANTHROPIC_API_KEY" in said[-1]


def test_a_check_that_breaks_is_a_check_that_failed(tmp_path, monkeypatch):
    def broken(ctx):
        raise RuntimeError("no such thing")

    monkeypatch.setattr(PF, "CHECKS", (("budgets", broken), ("checkout", PF.checkout)))
    record = PF.run(context(scenario(), host(tmp_path)), lambda line: None)
    assert record["failed"] == "budgets" and record["not_run"] == ["checkout"]
    assert record["checks"][0]["detail"] == "the check could not run: RuntimeError: no such thing"


def test_every_check_passes_on_a_host_that_has_what_the_run_needs(tmp_path, lists, monkeypatch):
    monkeypatch.setattr(PF, "system", lambda: "linux")
    folder = tools_folder(tmp_path, claude="echo '2.1.283 (Claude Code)'", curl=CURL)
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    record = PF.run(context(scenario(), host(tmp_path)), lambda line: None)
    assert record["passed"] is True and record["not_run"] == []
    assert {c["name"]: c["status"] for c in record["checks"]} == {
        "budgets": PF.PASS,
        "checkout": PF.PASS,
        "references": PF.SKIP,
        "awake": PF.SKIP,
        "subject_key": PF.PASS,
        "judge_keys": PF.PASS,
        "runtime": PF.PASS,
        "workspace": PF.SKIP,
        "tools": PF.PASS,
        "resources": PF.PASS,
        "network": PF.PASS,
        "requires": PF.SKIP,
    }
