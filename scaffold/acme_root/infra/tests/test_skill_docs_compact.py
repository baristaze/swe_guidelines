"""`docs-compact` rewrites the tree's documents to what holds, and on a
flag folds each migration chain. What it never touches, the choices it
leaves to no run, and the commands it runs are held here, the commands
run as the skill writes them."""

import itertools
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from skills_support import ROOT, SKILLS, allowed_tools, prose_of, text_of

NAME = "docs-compact"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "One pass: each document is rewritten once in a run.",
    "the first run plus at most 3 reruns, then stop and say which gate fails and why",
    "Its Next is the person's to run, never the session's",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"


# What a compaction never loses, written in the skill that could lose it.
COMPACTION_KEEPS = [
    "Never drops a deviation's end condition",
    "Never trims an expand and contract in flight",
    "Never renumbers an ADR, and never gives a removed ADR's number to another",
    "Never drops a reason that stops a plausible wrong change",
    "Never changes a statement of code.",
    "Never opens `om/migrations/` without `--migrations`",
    "Never pushes, never opens a pull request, never publishes or edits a release.",
]


@pytest.mark.parametrize("sentence", COMPACTION_KEEPS)
def test_the_compaction_says_what_it_never_touches(sentence: str) -> None:
    assert sentence in prose_of(NAME), f"docs-compact no longer says: {sentence}"


# What a run of the compaction does where two runs could do two things: each
# is a sentence of the skill, so a rewrite that leaves the choice open fails.
COMPACTION_DECIDES = [
    "When either exists, both take the next free suffix (`_2`), so a run never overwrites another's.",
    "A migration's revision id or file name that it cites stays as it is: only a fold moves one",
    "a field's `description`, which is the wire's document",
    "A citation in a place the run never edits stays as it is",
    "The ADR it cites is never removed, and keeps the part the citation is for",
    "A name in code is code, a test's name included",
]


@pytest.mark.parametrize("sentence", COMPACTION_DECIDES)
def test_the_compaction_leaves_no_choice_to_the_run(sentence: str) -> None:
    assert sentence in prose_of(NAME), f"docs-compact no longer says: {sentence}"


# What `make openapi` writes: each namespace's API document and the two
# schemas made from it, one file per document in each folder.
GENERATED_FOLDERS = [
    "clients/openapi",
    "clients/typescript/src/schema",
    "clients/python/src/acme/client/schema",
]
GENERATED = sorted(
    str(path.relative_to(ROOT))
    for folder in GENERATED_FOLDERS
    for path in (ROOT / folder).iterdir()
    if path.suffix in {".json", ".ts", ".py"}
)
# What a compaction never edits: an applied migration, a generated file, a
# lock file, and the skill's own folder, which holds the phrases it sweeps for.
NEVER_SWEPT = [
    "om/migrations/sql/core/202601010000_the_core_role.up.sql",
    "om/migrations/versions/core/202601010000_the_core_role.py",
    *GENERATED,
    "uv.lock",
    "pnpm-lock.yaml",
    "deployment/terraform/environments/staging/.terraform.lock.hcl",
    ".agents/skills/docs-compact/SKILL.md",
]
SWEPT = ["README.md", "docs/adr/0001-a-decision.md", "om/src/acme/om/tasks.py", "pyproject.toml"]


def _sweep() -> list[str]:
    """The sweep, as the skill writes it: one `git grep` with its pathspec."""
    lines = [line.strip() for line in text_of(NAME).splitlines()]
    (command,) = [line for line in lines if line.startswith("git grep -nIiwE -f ")]
    return shlex.split(command)


def test_the_sweep_lists_no_file_the_compaction_never_edits(tmp_path: Path) -> None:
    """Step 6 rewrites what the sweep lists, so the sweep's pathspec is what
    keeps an applied migration and a generated document as they are. It runs
    here as the skill writes it, over a tree where every file tells a past."""
    for folder in GENERATED_FOLDERS:
        assert any(name.startswith(f"{folder}/") for name in GENERATED), (
            f"{folder} holds no file `make openapi` writes"
        )
    for name in [*NEVER_SWEPT, *SWEPT]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("The key was renamed from another, and is no longer read.\n")
    references = SKILLS / NAME / "references"
    shutil.copytree(references, tmp_path / ".agents" / "skills" / "docs-compact" / "references")
    for command in (["git", "init", "--quiet"], ["git", "add", "--all"]):
        subprocess.run(command, cwd=tmp_path, check=True)
    swept = subprocess.run(_sweep(), cwd=tmp_path, capture_output=True, text=True, check=True)
    assert sorted({line.split(":", 1)[0] for line in swept.stdout.splitlines()}) == SWEPT


def test_the_citation_search_lists_every_form_and_no_bare_number(tmp_path: Path) -> None:
    """Who cites an ADR decides whether it goes and what is re-pointed. The
    checker reads `ADR NNNN`, `ADR-NNNN`, and `ADRNNNN`; a citation wraps
    after `ADR` where the margin falls; and the four digits alone match a
    port, a build, and a stamp. The two searches run here as the skill writes
    them, for ADR 0007, its own file left out."""
    cited = {
        "om/src/acme/om/bound.py": "# The bound is a lock's (ADR 0007).\n",
        "om/src/acme/om/lock.py": "held = 1  # arch-check: ignore[STO-26] ADR-0007 a lock bound\n",
        "docs/runbooks/deploy.md": "The bound is ADR0007's.\n",
        "pyproject.toml": 'adr = "docs/adr/0007-a-lock-bound.md"\n',
        "docs/adr/0009-another.md": "See [the bound](0007-a-lock-bound.md).\n",
    }
    wrapped = {
        "om/src/acme/om/wait.py": "# A statement waits under the bound (ADR\n# 0007). No more.\n"
    }
    uncited = {
        "om/src/acme/om/port.py": "PORT = 10007\n",
        "om/src/acme/om/rows.py": "# The table holds\n# 0007 rows at most.\n",
        "README.md": "Build 0007 of 2026 holds 20260007 rows.\n",
        "docs/adr/0007-a-lock-bound.md": "# ADR 0007: A lock bound\n",
    }
    for name, text in {**cited, **wrapped, **uncited}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for command in (["git", "init", "--quiet"], ["git", "add", "--all"]):
        subprocess.run(command, cwd=tmp_path, check=True)
    lines = [line.strip() for line in text_of(NAME).splitlines()]
    one_line, line_start = [line for line in lines if line.startswith("git grep -nE ")]
    assert "-B1" in shlex.split(line_start), "the second search prints the line above a hit"

    def search(command: str) -> list[str]:
        ran = shlex.split(command.replace("NNNN", "0007"))
        return subprocess.run(
            ran, cwd=tmp_path, capture_output=True, text=True, check=True
        ).stdout.splitlines()

    assert sorted({line.split(":", 1)[0] for line in search(one_line)}) == sorted(cited)
    # A number at a line's start is listed with the line above it, and is a
    # citation only when that line ends in `ADR`.
    listed = search(line_start)
    hits = {
        line.split(":", 1)[0]: above
        for above, line in itertools.pairwise(listed)
        if re.match(r"[^:]+:\d+:", line)
    }
    assert sorted(hits) == sorted([*wrapped, "om/src/acme/om/rows.py"])
    assert [name for name, above in hits.items() if above.endswith("ADR")] == list(wrapped)


def test_the_compaction_tells_a_contract_in_flight_by_the_tree() -> None:
    """A tree that releases often holds a contract still in flight under
    many tags, so a count of tags says nothing of it. The head does: the
    piece kept for the release before is still there, in the schema or in
    the code. What the verdict costs is small on purpose: code the test
    calls long gone keeps its comment, and only the report names it."""
    text = prose_of(NAME)
    assert "The test reads the tree alone, and counts no release and no deploy." in text
    assert "While the head holds it, the step that ends the contract has not landed" in text
    assert "`git grep -nwF '<its name>' -- 'om/migrations/sql/*.up.sql'`" in text
    assert "In doubt, it is in flight." in text
    # A later file may drop a piece and make it again, so the last one decides.
    assert "the last up file that names it decides" in text
    assert "unless that file drops it and does not make it again" in text
    # An ADR is rewritten once, in step 3, so the test reaches it there.
    assert "takes step 6's test here, before its rewrite" in text
    assert "That is its one rewrite, in this step's commit." in text
    assert "Its ADR changed in step 3, by this test, and is not edited here." in text
    assert "It is listed in the report and left as it is, its comment with it" in text
    assert "git tag" not in text_of(NAME)


def test_the_compaction_regenerates_the_api_documents_before_its_gate() -> None:
    """A docstring of an API type is in a generated document, and CI fails a
    tree whose documents are not the ones its code writes. So the gates write
    them again after the sweep and before `make check`, and commit what
    changed. CI's drift step lists, then refuses, a change under one pathspec
    that starts with `clients`; a tree with a service of its own adds the
    service's documents after it."""
    gates = prose_of(NAME).split("**Run the gates.**", 1)[1]
    assert gates.index("`make setup`") < gates.index("`make openapi`") < gates.index("`make check`")
    assert "commit what it changes, apart. Then `make check`." in gates
    assert "Bash(make openapi)" in [tool.strip() for tool in allowed_tools(NAME).split(",")]
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    drift = re.search(
        r"make openapi\n +git status --porcelain -- (?P<listed>[^\n]+)\n"
        r' +test -z "\$\(git status --porcelain -- (?P<refused>[^)\n]+)\)"',
        ci,
    )
    assert drift is not None, "CI has no drift step after `make openapi`"
    assert drift["listed"] == drift["refused"]
    assert drift["listed"].split()[0] == "clients"


def _database_gates() -> list[str]:
    """The commands of the gates' database, as the skill writes them, in its order."""
    lines = [line.strip() for line in text_of(NAME).splitlines()]
    return [line for line in lines if line.startswith("uv run python ") and "audit_docs_" in line]


def test_the_database_gates_run_on_a_database_the_run_makes_and_drops(tmp_path: Path) -> None:
    """The integration tests empty every table of the database they run on,
    so the gates make a database, run on it, and drop it. The command between
    runs here as the skill writes it, with a probe in place of `make` and of
    the audit module: the three targets get the URL of the run's database in
    one process, and the command ends with their status."""
    run = "audit_docs_compact_<yyyymmdd>"
    create, gates, drop = _database_gates()
    assert create == f"uv run python ops/audit/auditdb.py create {run}"
    assert drop == f"uv run python ops/audit/auditdb.py drop {run}"
    *runner, program = shlex.split(gates)
    assert runner == ["uv", "run", "python", "-c"]
    audit = tmp_path / "ops" / "audit"
    audit.mkdir(parents=True)
    (audit / "auditdb.py").write_text(
        "def urls(name):\n    return {'ACME_DATABASE_URL': f'postgresql://127.0.0.1/{name}'}\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "seen"
    fake = bin_dir / "make"
    fake.write_text(f'#!/bin/sh\necho "$* $ACME_DATABASE_URL" > "{seen}"\nexit 3\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    shell = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    done = subprocess.run([sys.executable, "-c", program], cwd=tmp_path, env=shell, check=False)
    assert done.returncode == 3, "the command does not end with the targets' status"
    targets = ["migrate", "migrate-check", "test-integration"]
    assert seen.read_text().split() == [*targets, f"postgresql://127.0.0.1/{run}"]
    # The targets run inside that one command, so none is pre-approved alone,
    # and no step exports the URLs where `make check` would read them.
    tools = {tool.strip() for tool in allowed_tools(NAME).split(",")}
    assert not tools & {"Bash(make migrate-check)", "Bash(make test-integration)"}
    text = prose_of(NAME)
    assert "on a database the run makes and drops, never the stack's own" in text
    assert "never export them in the shell" in text
    # A create that fails can leave the database, which is the run's own to drop.
    assert "The name is chosen once, before the run's first create" in text
    assert "From its first create on, the name is this run's own." in text
    assert "the targets do not run then, and the drop still does" in text
    assert "The drop runs whatever the create and the targets answered" in text
    assert "never runs a test on the local stack's own database" in text


def test_the_fold_states_its_precondition_its_proof_and_its_bound() -> None:
    """A fold replaces a chain every database applied, so the reference the
    fold step reads says when it may run, what shows it equal, and where a
    proof that fails stops."""
    assert "`references/fold.md`" in prose_of(NAME)
    fold = " ".join((SKILLS / NAME / "references" / "fold.md").read_text().split())
    assert "Every database that exists is at its chain's head." in fold
    assert "`diff` prints nothing, for every role." in fold
    assert "the first run plus at most 3 reruns" in fold
    assert "never drop a database this run did not make" in fold
    assert "A test goes only when it pins a revision id the fold removes" in fold
    assert "Every other test stays" in fold
    assert "Those assertions stay, in a test that migrates to the head" in fold
    assert "upgrades to the head again before it returns" in fold
    assert "the schema dump of the chain equals the fold's" in fold
    # A chain may set its grants in a later step, so the fold takes them from
    # the step that does, and the dump says whether it has them all.
    assert "taken from the step of the chain that sets them, the first or a later one" in fold
    assert "`git grep -nE 'GRANT|REVOKE|DEFAULT PRIVILEGES' --" in fold
    assert "one the fold lacks is a line of step 4's `diff`" in fold


def test_a_fold_on_a_base_takes_only_the_revisions_the_tree_owns() -> None:
    """A copy or a layer merges the render of its base on every move, so a
    fold that rewrote a rendered file would meet each later change the base
    makes to it as a conflict. The fold takes only the tree's own revisions,
    on its base's head, and reads the chain by its parents."""
    fold = " ".join((SKILLS / NAME / "references" / "fold.md").read_text().split())
    assert "never by its stamp" in fold
    assert "`git rev-parse --verify --quiet origin/scaffold` prints nothing." in fold
    assert (
        "`git ls-tree -r --name-only origin/scaffold -- om/migrations/sql om/migrations/versions`"
        in fold
    )
    assert "Bash(git ls-tree:*)" in [tool.strip() for tool in allowed_tools(NAME).split(",")]
    assert "Each rendered file stays as the render gave it" in fold
    assert "The fold's parent is the base's head as the chain holds it" in fold
    assert "no rendered revision follows an owned one on any line" in fold
    assert "no path `git diff --name-only --relative HEAD -- om/migrations` lists" in fold
    assert "Every other owned file of the role's chain goes: `git rm`." in fold
    assert "A rendered file stays as it is." in fold
    # Each path the fold reads is the tree's, so the skill says where the
    # tree's root is in a copy, in a layer, and in the guideline itself.
    where = prose_of(NAME)
    assert "In a copy, that folder is the root of the checkout." in where
    assert "In a layer, it is the folder of the layer's repository that holds its tree." in where
    assert "it is the folder that holds this scaffold, never the repository's root" in where
    # A fold with a parent leaves the rendered revisions below it, so a step
    # down from its head lands on that parent, not on an empty role.
    assert "With a parent, it returns the role to the parent, the last rendered revision" in fold
    # A layer's repository runs none of its tree's deploy workflows, so a
    # read of its deploys would fail and stop every fold of a layer.
    assert (
        "A tree that is a folder of its repository, as a layer's is, passes this part whole" in fold
    )


def test_the_fold_reads_the_commit_staging_deployed_as_the_release_does() -> None:
    """A deploy run's head is the branch's tip when it started, and its
    title ends with the commit it deployed. The release workflow reads the
    title of the newest run whose apply job succeeded, and the fold reads
    it the same way, by the job's name as the deploy workflow has it."""
    workflows = ROOT / ".github" / "workflows"
    deploy = yaml.safe_load((workflows / "deploy-staging.yml").read_text())
    apply_job = deploy["jobs"]["staging"]["name"]
    fold = (SKILLS / NAME / "references" / "fold.md").read_text()
    for text in (fold, (workflows / "release.yml").read_text()):
        assert f'select(.name == "{apply_job}")' in text
        assert "--json displayTitle" in text
    assert "--json headSha" not in fold
    # Production's deploys are read by its own apply job, never by a branch.
    production = yaml.safe_load((workflows / "deploy-production.yml").read_text())
    assert f'select(.name == "{production["jobs"]["apply"]["name"]}")' in fold
