"""benchmark/harness/references.py: the references staged as roots, the answer, and the weighted score.

A repository reference is fetched from a git repository this test makes,
named `acme-system`, over a `file://` URL. Every judge is a fake client
from `test_benchmark_agentic.py`. Nothing here reaches a network or reads a key.
"""

import _thread
import importlib.util
import json
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from harness import agentic as A
from harness import judge as J
from harness import providers as P
from harness import references as RF
from harness import scenario as S
from harness import versions as V
from test_benchmark_agentic import FAKES, KEYS, NAMES, PROVIDERS, step

needs_jsonschema = pytest.mark.skipif(importlib.util.find_spec("jsonschema") is None, reason="jsonschema is not installed")

WEIGHTS = {"guideline": 0.4, "reference": 0.6}
ANSWER: dict[str, Any] = {
    "references": {
        "guideline": {
            "score": 72,
            "gaps": [
                {
                    "severity": "high",
                    "what": "No outbox: a worker is queued in the same call that writes the row.",
                    "in_output": "src/app.py",
                    "in_reference": "Async, The Outbox",
                    "lens": "ASY-03",
                    "fix": "Write the work row and let the relay queue it.",
                }
            ],
            "strengths": ["One table per entity, in src/app.py."],
        },
        "reference": {
            "score": 66,
            "gaps": [{"severity": "medium", "what": "No integration test.", "in_output": "", "in_reference": "tests/"}],
            "strengths": [],
        },
    },
    "rationale": "The layering holds; the async edge does not.",
}


def git(*args: str, cwd: Path) -> str:
    """A git command in a repository this test owns, with no signing and an identity of its own."""
    config = [
        "-c",
        "user.name=acme",
        "-c",
        "user.email=acme@example.com",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "tag.gpgsign=false",
    ]
    return subprocess.run(["git", *config, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def acme_repository(tmp_path: Path, spec: str | None = "(pinned at `v0.37.0`)") -> Path:
    """A repository with `v1.0.0` as an annotated tag, a later commit, and a branch named like a tag."""
    repo = tmp_path / "acme-system"
    (repo / "src").mkdir(parents=True)
    git("init", "-q", cwd=repo)
    (repo / "src" / "app.py").write_text("TABLES = ['journalists']\n", encoding="utf-8")
    if spec is not None:
        (repo / "specs").mkdir()
        (repo / "specs" / "architecture.md").write_text(f"# Architecture\n\nThis project follows it {spec}.\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "one", cwd=repo)
    git("tag", "-a", "v1.0.0", "-m", "release", cwd=repo)
    (repo / "src" / "later.py").write_text("LATER = True\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "two", cwd=repo)
    git("branch", "v2.0.0", cwd=repo)
    return repo


def reference(repo: Path, tag: str = "v1.0.0", name: str = "reference", weight: float = 0.6) -> S.Reference:
    return S.Reference(name=name, weight=weight, repository=repo.as_uri(), tag=tag)


# The references -----------------------------------------------------------


def test_a_repository_reference_is_its_tag_s_tree_with_no_git_left(tmp_path):
    repo = acme_repository(tmp_path)
    dest = tmp_path / "staged" / "reference"
    version = RF.stage_repository(reference(repo), dest)
    commit = git("rev-parse", "v1.0.0^{commit}", cwd=repo).strip()
    assert version == {"source": "repository", "url": repo.as_uri(), "tag": "v1.0.0", "commit": commit, "pins": "v0.37.0"}
    assert (dest / "src" / "app.py").read_text(encoding="utf-8") == "TABLES = ['journalists']\n"
    assert not (dest / "src" / "later.py").exists()  # the tag's tree, not the branch's head
    assert not (dest / ".git").exists()


def test_a_branch_named_like_a_tag_is_not_the_tag_and_is_refused(tmp_path):
    repo = acme_repository(tmp_path)
    with pytest.raises(
        RF.StageError, match=r"reference reference: .* at tag v2\.0\.0 could not be fetched: .*refs/tags/v2\.0\.0"
    ):
        RF.stage_repository(reference(repo, tag="v2.0.0"), tmp_path / "staged")
    with pytest.raises(RF.StageError, match="could not be fetched"):
        RF.stage_repository(reference(tmp_path / "no-such-repository"), tmp_path / "missing")


def test_git_runs_with_none_of_this_machine_s_configuration_and_asks_for_no_credential(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-for-git")
    env = RF.git_env()
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_ASKPASS"] == ""
    assert "GIT_DIR" not in env and "ANTHROPIC_API_KEY" not in env and "PATH" in env


@pytest.mark.parametrize(
    ("spec", "pins"),
    [
        ("(pinned at `v0.36.2`)", "v0.36.2"),
        ("at <https://github.com/acme/guide/blob/v0.35.0/architecture.md>", "v0.35.0"),
        ("with no release named", None),
        (None, None),
    ],
)
def test_the_release_a_repository_pins_is_read_from_its_specs(tmp_path, spec, pins):
    repo = acme_repository(tmp_path, spec)
    assert RF.stage_repository(reference(repo), tmp_path / "staged")["pins"] == pins


def test_a_checkout_reference_is_a_copy_of_its_paths_with_one_hash(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "lenses").mkdir(parents=True)
    (checkout / "lenses" / "om.md").write_text("# OM\n", encoding="utf-8")
    (checkout / "lenses" / "__pycache__").mkdir()
    (checkout / "lenses" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (checkout / "architecture.md").write_text("# Guideline\n", encoding="utf-8")
    (checkout / "benchmark").mkdir()
    (checkout / "benchmark" / "answers.yaml").write_text("secret: planted\n", encoding="utf-8")
    ref = S.Reference(name="guideline", weight=1, paths=["architecture.md", "lenses"])
    dest = tmp_path / "staged" / "guideline"
    version = RF.stage_paths(ref, checkout, dest)
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) == ["architecture.md", "lenses/om.md"]
    assert version == {"source": "checkout", "paths": ["architecture.md", "lenses"], "sha256": V.sha256_tree(dest)}


def test_a_checkout_path_that_is_not_there_or_leads_out_is_refused(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (tmp_path / "outside").mkdir()
    (checkout / "out").symlink_to(tmp_path / "outside")
    for path in ("missing.md", "out"):
        ref = S.Reference(name="guideline", weight=1, paths=[path])
        with pytest.raises(RF.StageError, match=f"reference guideline: the checkout holds no file or folder '{path}'"):
            RF.stage_paths(ref, checkout, tmp_path / "staged")


def test_staging_notes_a_repository_that_pins_another_release_or_none(tmp_path):
    pinned = acme_repository(tmp_path / "a")
    unpinned = acme_repository(tmp_path / "b", spec=None)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "architecture.md").write_text("# Guideline\n", encoding="utf-8")
    refs = [
        S.Reference(name="guideline", weight=0.2, paths=["architecture.md"]),
        reference(pinned, weight=0.4),
        reference(unpinned, name="other", weight=0.4),
    ]
    staged = RF.stage(refs, checkout, tmp_path / "references", release="v0.38.0")
    assert list(staged.roots) == ["guideline", "reference", "other"]
    assert staged.roots["other"] == tmp_path / "references" / "other"
    assert [v["source"] for v in staged.versions.values()] == ["checkout", "repository", "repository"]
    assert staged.notes == [
        "reference `reference` pins the guideline at v0.37.0, and this checkout is at v0.38.0",
        "reference `other` names no guideline release in specs/architecture.md",
    ]
    assert RF.stage(refs[1:2], checkout, tmp_path / "again", release="v0.37.0").notes == []
    # The plugin manifest names a release without the tag's `v`.
    assert RF.stage(refs[1:2], checkout, tmp_path / "bare", release="0.37.0").notes == []
    assert RF.stage(refs[1:2], checkout, tmp_path / "older", release="0.36.0").notes == [
        "reference `reference` pins the guideline at v0.37.0, and this checkout is at v0.36.0"
    ]


# The output root ----------------------------------------------------------


def repeat_folder(tmp_path: Path, zip_members: dict[str, str] | None = None) -> Path:
    """A repeat's artifacts as a run keeps them: the answer, a collected file, the judge prompt, and maybe the archive."""
    art = tmp_path / "artifacts" / "0"
    (art / "workspace" / "notes").mkdir(parents=True)
    (art / "answer.md").write_text("The answer.\n", encoding="utf-8")
    (art / "workspace" / "notes" / "plan.md").write_text("A plan.\n", encoding="utf-8")
    (art / "judge-prompt.md").write_text("an earlier prompt\n", encoding="utf-8")
    if zip_members is not None:
        with zipfile.ZipFile(art / "output.zip", "w") as zf:
            for name, text in zip_members.items():
                zf.writestr(name, text)
        (art / "MANIFEST.txt").write_text("", encoding="utf-8")
    return art


def files_under(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_the_output_root_is_the_archived_tree_when_the_subject_builds_one(tmp_path):
    art = repeat_folder(tmp_path, {"acme/Makefile": "check:\n", "../escape.txt": "out", "/abs.txt": "abs"})
    dest = tmp_path / "judged" / "output"
    holds, why = RF.stage_output(art, dest, archived=True)
    assert holds.startswith("the tree the subject built") and why is None
    assert files_under(dest) == ["abs.txt", "acme/Makefile", "escape.txt"]  # a member never lands outside the root
    assert not (tmp_path / "judged" / "escape.txt").exists()


def test_the_output_root_is_empty_and_noted_when_the_archive_is_missing_or_unreadable(tmp_path):
    art = repeat_folder(tmp_path)
    holds, why = RF.stage_output(art, tmp_path / "a", archived=True)
    assert (
        holds == "nothing, since the repeat kept no archive of it"
        and why == "the judges read an empty output folder: the repeat kept no archive of it"
    )
    (art / "output.zip").write_bytes(b"[redacted: a zip that could not be scanned for keys]\n")
    holds, why = RF.stage_output(art, tmp_path / "b", archived=True)
    assert why is not None and "does not open as a zip" in why
    assert files_under(tmp_path / "a") == files_under(tmp_path / "b") == []


def test_without_an_output_folder_the_root_is_the_answer_and_the_collected_files(tmp_path):
    art = repeat_folder(tmp_path)
    dest = tmp_path / "judged" / "output"
    holds, why = RF.stage_output(art, dest, archived=False)
    assert "`answer.md`" in holds and why is None
    assert files_under(dest) == ["answer.md", "workspace/notes/plan.md"]  # the harness's own files stay out


# The answer and the weighted score ---------------------------------------


@needs_jsonschema
def test_the_answer_schema_asks_every_reference_for_a_score_its_gaps_and_its_strengths():
    check = A.answer_checker(RF.answer_schema(list(WEIGHTS)))
    assert check(ANSWER) == []
    missing = json.loads(json.dumps(ANSWER))
    del missing["references"]["reference"]
    assert check(missing) == ["references: 'reference' is a required property"]
    extra = json.loads(json.dumps(ANSWER))
    extra["references"]["another"] = extra["references"]["guideline"]
    assert "Additional properties are not allowed ('another' was unexpected)" in check(extra)[0]
    bad = json.loads(json.dumps(ANSWER))
    bad["references"]["guideline"]["score"] = 101
    bad["references"]["reference"]["gaps"][0]["severity"] = "critical"
    assert sorted(check(bad)) == [
        "references/guideline/score: 101 is greater than the maximum of 100",
        "references/reference/gaps/0/severity: 'critical' is not one of ['high', 'medium', 'low']",
    ]
    weighed = json.loads(json.dumps(ANSWER))
    weighed["weighted"] = 68.4  # the harness weighs; a judge's own weighing has no field
    assert "Additional properties are not allowed ('weighted' was unexpected)" in check(weighed)[0]


def test_no_two_parts_of_the_answer_schema_share_an_object():
    schema = RF.answer_schema(["a", "b"])
    a, b = schema["properties"]["references"]["properties"].values()
    assert a == b and a is not b and a["properties"]["gaps"] is not b["properties"]["gaps"]


@pytest.mark.parametrize(
    ("scores", "weights", "expected"),
    [
        ({"guideline": 72, "reference": 66}, WEIGHTS, 68.4),
        ({"guideline": 71, "reference": 72}, {"guideline": 0.25, "reference": 0.75}, 71.8),  # 71.75, half up
        ({"a": 70, "b": 71}, {"a": 0.5, "b": 0.5}, 70.5),
        ({"a": 1, "b": 2, "c": 2}, {"a": 0.333, "b": 0.333, "c": 0.334}, 1.7),  # 1.667
        ({"only": 83}, {"only": 1.0}, 83.0),
    ],
)
def test_the_harness_weighs_the_scores_to_one_place_half_up(scores, weights, expected):
    assert RF.weighted(scores, weights) == expected


def agentic_judgement(status="ok", answer=ANSWER, error=None):
    return A.AgenticJudgement(
        provider="openai",
        model="gpt-6-sol",
        effort="medium",
        status=status,
        latency_s=12.5,
        usage={"input_tokens": 9000, "output_tokens": 400},
        cost_usd=0.022,
        error=error,
        answer=answer if status == "ok" else None,
        tool_calls=7,
        turns=8,
    )


def test_a_judgement_is_read_per_reference_with_its_weight_and_the_weighted_score():
    judged = RF.Judged.of(agentic_judgement(), WEIGHTS, "judgements/0-openai.jsonl")
    assert judged.score == 68.4 and judged.rationale == ANSWER["rationale"]
    assert list(judged.references) == ["guideline", "reference"]
    assert judged.references["guideline"] == {"weight": 0.4, **ANSWER["references"]["guideline"]}
    assert judged.references["reference"]["gaps"] == [
        {"severity": "medium", "what": "No integration test.", "in_output": "", "in_reference": "tests/"}
    ]
    record = judged.as_dict()
    assert record["verdict"] is None and record["status"] == "ok" and record["cost_usd"] == 0.022
    assert record["judged"]["score"] == 68.4 and record["judged"]["transcript"] == "judgements/0-openai.jsonl"
    assert (record["judged"]["tool_calls"], record["judged"]["turns"]) == (7, 8)


def test_a_judgement_with_no_answer_has_no_score_and_no_reference_entry():
    judged = RF.Judged.of(agentic_judgement("missed", error="tool calls: all 40 spent"), WEIGHTS, "judgements/0-openai.jsonl")
    assert judged.score is None and judged.references == {} and judged.status == "missed"
    assert judged.as_dict()["judged"]["references"] == {} and judged.error == "tool calls: all 40 spent"


def test_the_prompt_names_every_root_and_never_the_weights(tmp_path):
    refs = [
        S.Reference(name="guideline", weight=0.4, paths=["architecture.md", "lenses"]),
        S.Reference(name="reference", weight=0.6, repository="https://github.com/acme/acme-system", tag="v0.7.0"),
    ]
    versions: dict[str, dict[str, Any]] = {
        "guideline": {"source": "checkout", "paths": ["architecture.md", "lenses"], "sha256": "0" * 64},
        "reference": {"source": "repository", "url": refs[1].repository, "tag": "v0.7.0", "commit": "a" * 40, "pins": "v0.37.0"},
    }
    prompt = RF.build_prompt("Judge the shape.", "A subject built it.", "the tree the subject built", refs, versions)
    assert "against 2 references" in prompt and "## Rubric\n\nJudge the shape." in prompt
    assert "- `output`: the tree the subject built." in prompt
    assert "- `guideline`: `architecture.md`, `lenses`, from the checkout" in prompt
    assert f"- `reference`: the repository https://github.com/acme/acme-system at tag v0.7.0, commit {'a' * 12}" in prompt
    assert "which pins the guideline at v0.37.0" in prompt
    assert "0.4" not in prompt and "0.6" not in prompt and "the harness weighs" in prompt


# The judges ---------------------------------------------------------------


@pytest.fixture
def roots(tmp_path: Path) -> dict[str, Path]:
    output, guideline, ref = (tmp_path / n for n in ("output", "guideline", "reference"))
    for folder in (output, guideline, ref):
        folder.mkdir()
    (output / "app.py").write_text("TABLES = []\n", encoding="utf-8")
    (guideline / "architecture.md").write_text("# Guideline\n", encoding="utf-8")
    (ref / "README.md").write_text("# Acme\n", encoding="utf-8")
    return {"output": output, "guideline": guideline, "reference": ref}


@needs_jsonschema
@pytest.mark.parametrize("provider", PROVIDERS, ids=NAMES)
def test_every_provider_judges_every_reference_and_its_transcript_sits_under_judgements(provider, roots, tmp_path):
    name = P.name(provider)
    script = [
        step(("read_file", {"root": "output", "path": "app.py"}), ("list_dir", {"root": "reference"})),
        step(("submit", ANSWER)),
    ]
    fake = FAKES[name](script)
    folder = tmp_path / "run" / "judgements"
    (judged,) = RF.judge_all(
        provider, "the task", roots, folder, 2, "medium", WEIGHTS, A.Budget(tool_calls=5), env=KEYS, clients={name: fake}
    )
    assert judged.status == "ok" and judged.score == 68.4 and judged.tool_calls == 2
    assert judged.transcript == f"judgements/2-{name}.jsonl"
    records = [json.loads(line) for line in (folder / f"2-{name}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records[0]["roots"] == ["output", "guideline", "reference"] and records[-1]["kind"] == "end"
    assert records[0]["schema"] == RF.answer_schema(["guideline", "reference"])
    assert judged.cost_usd == J.cost_usd(J.DEFAULT_MATRIX, name, judged.model, judged.usage)


@needs_jsonschema
def test_the_judges_of_a_repeat_run_at_once_and_one_that_fails_stops_none_of_the_others(roots, tmp_path):
    # Every judge's first call waits until all four have made theirs: one after another, the first would wait alone.
    together = threading.Barrier(len(PROVIDERS), timeout=5)
    started: set[str] = set()

    def on_call(name: str):
        def wait(fake: Any) -> None:
            if name not in started:
                started.add(name)
                together.wait()
                if name == "anthropic":
                    time.sleep(0.2)  # the first in flag order finishes last

        return wait

    clients: dict[str, Any] = {}
    for name in NAMES:
        script = [RuntimeError(f"{name} is down")] * 5 if name == "gemini" else [step(("submit", ANSWER))]
        clients[name] = FAKES[name](script, on_call=on_call(name))
    flags = P.Provider.ANTHROPIC | P.Provider.OPENAI | P.Provider.GEMINI | P.Provider.XAI
    judged = RF.judge_all(flags, "t", roots, tmp_path / "judgements", 0, "low", WEIGHTS, env=KEYS, clients=clients)
    assert [(j.provider, j.status) for j in judged] == [("anthropic", "ok"), ("openai", "ok"), ("gemini", "error"), ("xai", "ok")]
    assert "gemini is down" in (judged[2].error or "")
    assert not together.broken
    # Each keeps its own transcript, whole.
    for name in NAMES:
        records = [
            json.loads(line) for line in (tmp_path / "judgements" / f"0-{name}.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert records[0]["kind"] == "start" and records[-1]["kind"] == "end" and {r["provider"] for r in records} == {name}


@needs_jsonschema
def test_an_interrupt_stops_every_judge_before_its_next_call_and_waits_on_none(roots, tmp_path):
    # Every judge makes its first call; one of them interrupts the run, as Ctrl-C would; each call returns only
    # once the judges were told to stop, and one call is still in flight when the interrupt is raised.
    stop = threading.Event()
    together = threading.Barrier(len(PROVIDERS), timeout=5)
    interrupted: list[float] = []

    def on_call(name: str):
        def wait(fake: Any) -> None:
            if fake.requests[1:]:
                return
            together.wait()
            if name == "anthropic":
                interrupted.append(time.monotonic())
                _thread.interrupt_main()
            stop.wait(5)
            if name == "xai":
                time.sleep(1.5)  # a call in flight, which the caller does not wait on

        return wait

    read = step(("list_dir", {"root": "output"}))
    clients = {name: FAKES[name]([read, step(("submit", ANSWER))], on_call=on_call(name)) for name in NAMES}
    flags = P.Provider.ANTHROPIC | P.Provider.OPENAI | P.Provider.GEMINI | P.Provider.XAI
    with pytest.raises(KeyboardInterrupt):
        RF.judge_all(flags, "t", roots, tmp_path / "judgements", 0, "low", WEIGHTS, env=KEYS, clients=clients, stop=stop)
    returned = time.monotonic()
    assert stop.is_set() and returned - interrupted[0] < 1.0  # well before the call in flight returns
    deadline = time.monotonic() + 10
    while any(t.name.startswith("judge-") for t in threading.enumerate()) and time.monotonic() < deadline:
        time.sleep(0.05)
    # No judge made a second call, and each transcript ends with why.
    assert {name: len(fake.requests) for name, fake in clients.items()} == dict.fromkeys(NAMES, 1)
    for name in NAMES:
        lines = (tmp_path / "judgements" / f"0-{name}.jsonl").read_text(encoding="utf-8").splitlines()
        end = json.loads(lines[-1])
        assert (end["kind"], end["status"], end["error"]) == ("end", "error", A.STOPPED)


@needs_jsonschema
def test_a_provider_with_no_key_is_skipped_and_scores_nothing(roots, tmp_path):
    flags = P.Provider.ANTHROPIC | P.Provider.XAI
    fake = FAKES["anthropic"]([step(("submit", ANSWER))])
    judged = RF.judge_all(
        flags, "t", roots, tmp_path / "judgements", 0, "low", WEIGHTS, env={"ANTHROPIC_API_KEY": "k"}, clients={"anthropic": fake}
    )
    assert [(j.provider, j.status, j.score) for j in judged] == [("anthropic", "ok", 68.4), ("xai", "skipped", None)]
    assert judged[1].error == "no key: set XAI_API_KEY or GROK_API_KEY"
