"""A judgement against references: what agentic judges read, what they answer, and the score the harness weighs.

A scenario whose judges are `agentic` names `references` beside the
subject's output. Each reference becomes a root the judge reads through
the tools of `agentic.py`, under the reference's name; the output is the
root `output`.

- A reference of this checkout names `paths`, such as `architecture.md`,
  `lenses`, and `skills`. They are copied from the working tree into a
  folder of the run's sandbox, so the judge reads what the subject's
  plugin was staged from. Its version is the paths and one SHA-256 over
  the copy.
- A reference that is a public repository names its `https` URL and a
  `tag`. The tag is fetched alone, at depth 1, into a folder of the
  sandbox, with no configuration of this machine's git: no credential
  helper, no URL rewrite, and no prompt, so a repository that needs a
  credential is not fetched. A name that is not a tag there is refused.
  Its version is the URL, the tag, the commit the tag names, and the
  guideline release the repository pins. A repository that follows the
  guideline pins its release in `specs/architecture.md`, as
  `docs/adopting.md` says: "(pinned at `v0.37.0`)". The repository's
  `.git` is removed once the commit is read, so the judge reads the tree
  and nothing else.

Fetching a public repository spends nothing, so a dry run resolves every
reference too.

The output root is the tree the subject built, as the archive of its
last commit holds it, when the subject builds an output folder.
Otherwise it is the repeat's answer, `answer.md`, and the files the
scenario collects, under `workspace/`, as the run keeps them.

Each judge answers in one shape, whatever the scenario: for every
reference, a `score` from 0 to 100, the `gaps` behind it, and the
`strengths`; and one `rationale`. A gap names how severe it is, what is
missing or different, where in the output and where in the reference,
and, where they apply, the lens and what would close it. The judge is
never told the weights. The harness weighs the scores: the weighted
score is the sum of each reference's score times its weight, to one
decimal place, rounded half up.

The judges of a repeat run in parallel, one thread each. Each keeps its
own budget, transcript, and error handling, so a provider that fails
ends its own judgement and no other.

Like every harness module, this one imports the standard library only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from . import agentic as A
from . import archive as AR
from . import judge as J
from . import providers as P
from . import versions as V
from .scenario import OUTPUT_ROOT, Reference

SEVERITIES = ("high", "medium", "low")
# What a gap names, in the order a record keeps them.
GAP_KEYS = ("severity", "what", "in_output", "in_reference", "lens", "fix")
# Where a repository that follows the guideline pins the release it follows, and the two ways it says so.
SPEC = "specs/architecture.md"
PINNED = re.compile(r"pinned at `(v\d+\.\d+\.\d+)`")
PINNED_URL = re.compile(r"/blob/(v\d+\.\d+\.\d+)/architecture\.md")
GIT_TIMEOUT_S = 300
# What git is handed of this machine's environment: its path, and how it reaches the network.
GIT_PASSTHROUGH = ("PATH", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy", "SSL_CERT_FILE")
# The harness's own files in a repeat's artifacts, which no judge reads as the subject's.
HARNESS_FILES = ("judge-prompt.md", AR.ZIP, AR.MANIFEST)


class StageError(Exception):
    """A reference that cannot be staged: a path the checkout does not hold, or a tag that cannot be fetched."""


# The references -----------------------------------------------------------


def stage_paths(ref: Reference, checkout: Path, dest: Path) -> dict[str, Any]:
    """Copy a reference's paths of the checkout into `dest`; its version: the paths and one hash over the copy."""
    dest.mkdir(parents=True, exist_ok=True)
    base = checkout.resolve()
    for rel in ref.paths:
        source = base / rel
        if not source.resolve().is_relative_to(base) or not (source.is_dir() or source.is_file()):
            raise StageError(f"reference {ref.name}: the checkout holds no file or folder {rel!r}")
        if source.is_dir():
            shutil.copytree(source, dest / rel, symlinks=True, ignore=shutil.ignore_patterns(*V.CACHES), dirs_exist_ok=True)
        else:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / rel)
    return {"source": "checkout", "paths": list(ref.paths), "sha256": V.sha256_tree(dest)}


def git_env() -> dict[str, str]:
    """The environment git runs in: none of this machine's git configuration, and no prompt for a credential."""
    env = {k: os.environ[k] for k in GIT_PASSTHROUGH if os.environ.get(k)}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="")
    return env


def git(*args: str, cwd: Path | None = None) -> str:
    """What a git command prints, run in `cwd`; StageError with its last line of error when it fails."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=GIT_TIMEOUT_S, env=git_env()
        )
    except subprocess.TimeoutExpired:
        raise StageError(f"git {args[0]} ran past {GIT_TIMEOUT_S} s") from None
    except OSError as exc:
        raise StageError(f"git did not run: {exc.strerror or type(exc).__name__}") from None
    if out.returncode != 0:
        lines = [line for line in out.stderr.splitlines() if line.strip()]
        raise StageError(lines[-1] if lines else f"git {args[0]} exited {out.returncode}")
    return out.stdout


def fetch(url: str, tag: str, dest: Path) -> str:
    """Check out `tag` of the repository at `url` into `dest`, with no `.git` left; the commit it names.

    Only `refs/tags/<tag>` is fetched, so a branch of that name is not
    taken for the tag.
    """
    dest.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--template=", cwd=dest)
    git("fetch", "-q", "--depth", "1", "--no-tags", "--", url, f"refs/tags/{tag}:refs/tags/{tag}", cwd=dest)
    commit = git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}", cwd=dest).strip()
    git("checkout", "-q", commit, cwd=dest)
    shutil.rmtree(dest / ".git")
    return commit


def pinned_release(root: Path) -> str | None:
    """The guideline release a repository pins in `specs/architecture.md`, or None when it names none."""
    try:
        text = (root / SPEC).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = PINNED.search(text) or PINNED_URL.search(text)
    return found.group(1) if found else None


def stage_repository(ref: Reference, dest: Path) -> dict[str, Any]:
    """Fetch a reference's repository at its tag into `dest`; its version: the URL, the tag, the commit, and the pin."""
    assert ref.repository and ref.tag
    try:
        commit = fetch(ref.repository, ref.tag, dest)
    except StageError as exc:
        raise StageError(f"reference {ref.name}: {ref.repository} at tag {ref.tag} could not be fetched: {exc}") from None
    return {"source": "repository", "url": ref.repository, "tag": ref.tag, "commit": commit, "pins": pinned_release(dest)}


@dataclass
class Staged:
    """The references as roots: each one's folder, its version, and what the run notes about them."""

    roots: dict[str, Path] = field(default_factory=dict)
    versions: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def stage(refs: list[Reference], checkout: Path, folder: Path, release: str | None) -> Staged:
    """Stage every reference under `folder`, one folder each. Raises StageError on one that cannot be staged.

    `release` is the guideline release of this checkout, as its plugin
    manifest gives it, with or without the tag's `v`. A repository that pins
    another release, or names none, is noted, and still judged against.
    """
    staged = Staged()
    for ref in refs:
        dest = folder / ref.name
        if ref.repository:
            version = stage_repository(ref, dest)
            pins = version["pins"]
            if pins is None:
                staged.notes.append(f"reference `{ref.name}` names no guideline release in {SPEC}")
            elif release and pins.removeprefix("v") != release.removeprefix("v"):
                ours = f"v{release.removeprefix('v')}"
                staged.notes.append(f"reference `{ref.name}` pins the guideline at {pins}, and this checkout is at {ours}")
        else:
            version = stage_paths(ref, checkout, dest)
        staged.roots[ref.name] = dest
        staged.versions[ref.name] = version
    return staged


def stage_output(art_dir: Path, dest: Path, archived: bool) -> tuple[str, str | None]:
    """Build the output root from a repeat's artifacts; what it holds, as the judge is told, and why it is empty.

    `archived` says the subject builds an output folder: the root is then
    the archive's tree, and empty when the repeat kept no archive that
    opens. Otherwise it is the answer and the collected files.
    """
    dest.mkdir(parents=True, exist_ok=True)
    kept = art_dir / AR.ZIP
    if archived:
        if not zipfile.is_zipfile(kept):
            why = "the repeat kept no archive of it" if not kept.exists() else "its archive does not open as a zip"
            return f"nothing, since {why}", f"the judges read an empty output folder: {why}"
        with zipfile.ZipFile(kept) as zf:
            zf.extractall(dest)  # a member's absolute path or `..` stays inside dest
        return "the tree the subject built, its output folder as its last commit holds it", None
    for item in sorted(art_dir.iterdir()):
        if item.name in HARNESS_FILES:
            continue
        if item.is_dir():
            shutil.copytree(item, dest / item.name, symlinks=True)
        else:
            shutil.copy2(item, dest / item.name)
    return "the subject's answer, `answer.md`, and the files it wrote that the scenario collects, under `workspace/`", None


# The prompt and the answer ----------------------------------------------

PROMPT = """\
You are judging what a subject produced against {count}. You are a
senior architect reviewing a colleague's work, not a cheerleader and not
a pedant.

## Rubric

{rubric}

## What produced the output

{subject}

## The roots

{roots}

## How to answer

Read the roots through the tools, then call `submit` once. Under
`references`, give each reference an entry of its own: `score`, an
integer from 0 to 100 for how the output measures against that
reference; `gaps`, the gaps behind that score; and `strengths`, each one
sentence that names where in the output. A gap gives its `severity`
(`high`, `medium`, or `low`), `what` is missing or different,
`in_output`, the path in the output (empty when the output has nothing
there), and `in_reference`, the path or the section in the reference
that shows it; and, where they apply, the `lens` id and the `fix` that
would close it. Give `rationale` as at most six sentences saying what
decided the scores. Score each reference on its own; the harness weighs
the scores, so do not weigh them yourself.
"""


def describe(ref: Reference, version: dict[str, Any]) -> str:
    """One line on what a reference root holds, as the judge reads it."""
    if version.get("source") == "repository":
        pins = f", which pins the guideline at {version['pins']}" if version.get("pins") else ""
        return f"- `{ref.name}`: the repository {ref.repository} at tag {ref.tag}, commit {str(version['commit'])[:12]}{pins}."
    paths = ", ".join(f"`{p}`" for p in ref.paths)
    return f"- `{ref.name}`: {paths}, from the checkout of the guideline this run measures."


def build_prompt(rubric: str, subject: str, output: str, refs: list[Reference], versions: dict[str, dict[str, Any]]) -> str:
    """The task every agentic judge gets: the rubric, what produced the output, and what each root holds."""
    count = "one reference" if len(refs) == 1 else f"{len(refs)} references"
    roots = "\n".join([f"- `{OUTPUT_ROOT}`: {output}.", *(describe(r, versions.get(r.name, {})) for r in refs)])
    return PROMPT.format(count=count, rubric=rubric.strip(), subject=subject.strip(), roots=roots)


def answer_schema(names: list[str]) -> dict[str, Any]:
    """The JSON schema of an answer: an entry per reference, each with its score, gaps, and strengths; and a rationale.

    Every entry is built afresh, so no two parts of the schema share an object.
    """

    def gap() -> dict[str, Any]:
        described = {
            "what": "What is missing or different.",
            "in_output": "The path in the output; empty when the output has nothing there.",
            "in_reference": "The path or the section in the reference that shows it.",
            "lens": "The lens id, where one applies.",
            "fix": "What would close the gap.",
        }
        return {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": list(SEVERITIES)},
                **{key: {"type": "string", "description": says} for key, says in described.items()},
            },
            "required": ["severity", "what", "in_output", "in_reference"],
            "additionalProperties": False,
        }

    def entry() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "gaps": {"type": "array", "items": gap()},
                "strengths": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "gaps", "strengths"],
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": {
            "references": {
                "type": "object",
                "properties": {name: entry() for name in names},
                "required": list(names),
                "additionalProperties": False,
            },
            "rationale": {"type": "string"},
        },
        "required": ["references", "rationale"],
        "additionalProperties": False,
    }


def weighted(scores: dict[str, int], weights: dict[str, float]) -> float:
    """The weighted score: each reference's score times its weight, summed, to one place, rounded half up."""
    total = sum((Decimal(str(weights[name])) * score for name, score in scores.items()), Decimal(0))
    share = sum((Decimal(str(weights[name])) for name in scores), Decimal(0))
    return float((total / share).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


# The judgements ---------------------------------------------------------


@dataclass
class Judged:
    """One provider's agentic judgement against the references, with the weighted score the harness computed.

    `references` holds, per reference in the scenario's order, its weight,
    and the score, the gaps, and the strengths the judge gave it; it is
    empty, and `score` None, when the judgement has no answer.
    """

    provider: str
    model: str
    effort: str
    status: str
    latency_s: float
    usage: dict[str, int]
    cost_usd: float | None
    error: str | None
    fallback: dict[str, str] | None
    tool_calls: int
    turns: int
    transcript: str
    answer: dict[str, Any] | None = None
    references: dict[str, dict[str, Any]] = field(default_factory=dict)
    rationale: str = ""
    score: float | None = None

    @staticmethod
    def of(judgement: A.AgenticJudgement, weights: dict[str, float], transcript: str) -> Judged:
        """The judgement with its answer read per reference and weighed; the answer passed the schema."""
        judged = Judged(
            provider=judgement.provider,
            model=judgement.model,
            effort=judgement.effort,
            status=judgement.status,
            latency_s=judgement.latency_s,
            usage=dict(judgement.usage),
            cost_usd=judgement.cost_usd,
            error=judgement.error,
            fallback=judgement.fallback,
            tool_calls=judgement.tool_calls,
            turns=judgement.turns,
            transcript=transcript,
            answer=judgement.answer,
        )
        answer = judgement.answer
        if judgement.status != "ok" or not answer:
            return judged
        given = answer["references"]
        for name, weight in weights.items():
            entry = given[name]
            gaps = [{k: str(g[k]) for k in GAP_KEYS if k in g} for g in entry["gaps"]]
            strengths = [str(s) for s in entry["strengths"]]
            judged.references[name] = {"weight": weight, "score": int(entry["score"]), "gaps": gaps, "strengths": strengths}
        judged.rationale = str(answer.get("rationale", ""))
        judged.score = weighted({n: r["score"] for n, r in judged.references.items()}, weights)
        return judged

    def as_dict(self) -> dict[str, Any]:
        """The judgement as `results.json` records it: the fields every judgement has, and what the agentic one adds."""
        return {
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
            "status": self.status,
            "latency_s": round(self.latency_s, 3),
            "usage": dict(self.usage),
            "cost_usd": self.cost_usd,
            "error": self.error,
            "fallback": dict(self.fallback) if self.fallback else None,
            "verdict": None,
            "judged": {
                "score": self.score,
                "references": {name: dict(entry) for name, entry in self.references.items()},
                "rationale": self.rationale,
                "tool_calls": self.tool_calls,
                "turns": self.turns,
                "transcript": self.transcript,
            },
        }


def judge_all(
    flags: P.Provider,
    prompt: str,
    roots: dict[str, Path],
    folder: Path,
    index: int,
    effort: str,
    weights: dict[str, float],
    budget: A.Budget | None = None,
    matrix: dict[str, dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
    clients: dict[str, Any] | None = None,
) -> list[Judged]:
    """Every selected provider's agentic judgement of one repeat, run in parallel and returned in flag order.

    Each transcript is `<index>-<provider>.jsonl` in `folder`, the run's
    `judgements/`. `clients` maps a provider to its SDK client; a provider
    it does not name gets the one `judge.py` builds from its key. A
    judgement never raises on what its provider does, so one provider's
    failure is its own judgement and the others run on.
    """
    matrix = matrix or J.DEFAULT_MATRIX
    schema = answer_schema(list(weights))
    providers = P.members(flags)

    def judge(provider: P.Provider) -> Judged:
        name = P.name(provider)
        path = folder / f"{index}-{name}.jsonl"
        judgement = A.judge_agentic(
            provider,
            prompt,
            schema,
            roots,
            path,
            effort=effort,
            matrix=matrix,
            budget=budget,
            env=env,
            client=(clients or {}).get(name),
        )
        return Judged.of(judgement, weights, f"{folder.name}/{path.name}")

    if not providers:
        return []
    with ThreadPoolExecutor(max_workers=len(providers), thread_name_prefix="judge") as pool:
        return list(pool.map(judge, providers))
