"""The ops skills share one preamble, and keep their invariants inline.

The operational detail a reader needs once, and not in every skill, lives
in `.agents/skills/_shared/ops-preamble.md`: the profiles, the account
check, the env file's fields, and the way back when a token expires. A
skill that reaches a cloud environment names that file, by its path from
the skill's own folder, as every agent that reads a skill resolves it.

A rule that must never be missed does not travel by reference, so the
lines that stop a secret leaking stay written in each skill that could
break them, and this test holds them there.
"""

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

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".agents" / "skills"
PREAMBLE = SKILLS / "_shared" / "ops-preamble.md"
README = ROOT / "ops" / "README.md"
REFERENCE = "../_shared/ops-preamble.md"

# Every skill that can reach an environment, and so holds a credential.
READERS = [
    "audit-deploy-time",
    "audit-retention",
    "ops-cloud-deployment-create",
    "ops-cloud-deployment-nuke",
    "ops-infra-as-code",
    "ops-investigate",
    "ops-root-cause",
    "ops-simulate-traffic",
    "ops-watch",
    "stress-test-run",
]
# The skills that reach the env file's tokens.
TOKEN_HOLDERS = [
    "ops-investigate",
    "ops-root-cause",
    "ops-simulate-traffic",
    "ops-watch",
    "stress-test-run",
]
# The skills that read an environment under the investigate profile.
INVESTIGATORS = [*TOKEN_HOLDERS, "ops-infra-as-code", "audit-deploy-time", "audit-retention"]
# The skills that run under an account's administrator.
ADMINISTRATORS = ["ops-cloud-deployment-create", "ops-cloud-deployment-nuke"]
# The audits: read-only analyses that write a report and propose tickets.
AUDITS = [
    "audit-credential-lifetimes",
    "audit-database-calls",
    "audit-deploy-time",
    "audit-provider-calls",
    "audit-query-indexes",
    "audit-retention",
]
# The audits that build a database of their own on the local stack.
DATABASE_AUDITS = [
    "audit-credential-lifetimes",
    "audit-database-calls",
    "audit-provider-calls",
    "audit-query-indexes",
    "audit-retention",
]
# A path from the skill's own folder: one that climbs out of it (`../`),
# or one under its `references/`. Any other path is the tree's, from its root.
SKILL_PATH = re.compile(r"(?<![\w./-])((?:\.\./)+[\w.-][^\s`'\")]*|references/[^\s`'\")]+)")
# A path only one agent resolves: Claude Code's substitution, or its own folder.
ONE_AGENTS_PATH = re.compile(r"\$\{CLAUDE_SKILL_DIR\}|\.claude/skills/")


def _own(pattern: str = "*") -> list[str]:
    """The tree's own skills, by folder name: a skill folder that is a link
    belongs to what it links to, such as a clone of the guideline's
    skills, and is held there."""
    found = SKILLS.glob(f"{pattern}/SKILL.md")
    return sorted(p.parent.name for p in found if not p.parent.is_symlink())


def _skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def _prose(name: str) -> str:
    """The skill's text as one line: a rule is a sentence, wrapped where the
    margin falls, and the margin is not what this test is about."""
    return " ".join(_skill(name).split())


def _allowed_tools(name: str) -> str:
    return str(_frontmatter(name).get("allowed-tools", ""))


def _frontmatter(name: str) -> dict[str, object]:
    _, frontmatter, _ = _skill(name).split("---", 2)
    return yaml.safe_load(frontmatter)


def test_the_shared_preamble_exists_and_holds_what_moved_into_it() -> None:
    text = PREAMBLE.read_text()
    assert "deployment/cloud/environments.json" in text
    assert "aws sts get-caller-identity" in text
    assert "~/.config/acme/ops/<env>.env" in text
    assert "ACME_PROVISIONER_TOKEN" in text
    assert "acme-<env>-investigate" in text


@pytest.mark.parametrize("name", READERS)
def test_every_skill_that_holds_a_credential_reads_the_preamble(name: str) -> None:
    """The reference is the skill's first instruction, before its steps."""
    text = _skill(name)
    assert REFERENCE in text, f"{name} drops the reference to the shared preamble"
    assert text.index(REFERENCE) < text.index("## Input")


def test_the_skills_that_hold_a_credential_are_the_ones_that_can_call_aws() -> None:
    """A new ops skill that reaches the cloud joins the list above, or this
    fails: the preamble is not optional for a skill that holds a
    credential."""
    reaches_the_cloud = sorted(name for name in _own() if "Bash(aws:*)" in _allowed_tools(name))
    assert reaches_the_cloud == sorted(READERS)


def test_the_scenario_writer_holds_no_credential_and_needs_no_preamble() -> None:
    text = _prose("stress-test-create-or-update")
    assert "No cloud, no credential, no secret." in text
    assert REFERENCE not in text


@pytest.mark.parametrize("name", TOKEN_HOLDERS)
def test_the_secret_rules_stay_inline(name: str) -> None:
    text = _prose(name)
    assert "Never read the env file" in text
    assert "Never print a token." in text


@pytest.mark.parametrize("name", INVESTIGATORS)
def test_the_refusal_of_a_wider_profile_stays_inline(name: str) -> None:
    assert "Refuse any profile wider than the investigate role." in _prose(name)


@pytest.mark.parametrize("name", ADMINISTRATORS)
def test_the_refusal_of_anything_but_the_administrator_stays_inline(name: str) -> None:
    assert "Refuse any profile but the environment's administrator" in _prose(name)


@pytest.mark.parametrize("name", TOKEN_HOLDERS)
def test_a_skill_that_holds_a_token_pre_approves_the_ops_command_and_no_other(name: str) -> None:
    """`uv run` takes any program, `python -c` among them: pre-approved, it
    is code on the operator's machine beside the env file's tokens, with
    nobody asked. The skill runs `acme-ops` and names that."""
    tools = [tool.strip() for tool in _allowed_tools(name).split(",")]
    assert "Bash(uv run acme-ops:*)" in tools
    assert not [
        tool for tool in tools if tool.startswith("Bash(uv") and tool != "Bash(uv run acme-ops:*)"
    ]
    runs = set(re.findall(r"\buv run ([\w-]+)", _prose(name)))
    assert runs == {"acme-ops"}, f"{name} runs uv with {sorted(runs)}"


# What a tenant writes and the operator plane answers with: an org's name and
# slug, a member's display name and address. A tenant chooses those words, and
# the session that reads them holds an operator's token.
TENANT_TEXT = {"name", "slug", "display_name", "email"}
TENANT_READ = re.compile(
    r'^ *curl [^\n]*"\$ACME_API_URL/v1/admin/orgs/<org_id>(?:/members(?:\?cursor=<next_cursor>)?)?"'
    r"(?P<piped> \\\n +\| jq '(?P<kept>[^'\n]*)'$)?",
    re.MULTILINE,
)


def test_the_root_cause_reads_of_a_tenant_keep_no_text_the_tenant_wrote() -> None:
    """The org, its members, and a next page of them are read through `jq`,
    which keeps the ids, the kind, and the timestamps: no read is printed
    whole."""
    reads = list(TENANT_READ.finditer(_skill("ops-root-cause")))
    assert len(reads) == 3, (
        "ops-root-cause no longer reads the org and its members as this test sees them"
    )
    for read in reads:
        assert read["piped"], f"a read of the tenant is printed whole: {read[0]}"
        assert not set(re.findall(r"[a-z_]+", read["kept"])) & TENANT_TEXT, read["kept"]
    assert "Bash(jq:*)" in _allowed_tools("ops-root-cause")
    assert "never run either read without its `jq`" in _prose("ops-root-cause")
    assert "**Tenant.** <kind> org" in _skill("ops-root-cause")


def test_the_audits_are_the_skills_named_for_one() -> None:
    assert _own("audit-*") == AUDITS


@pytest.mark.parametrize("name", [*AUDITS, "tickets-triage"])
def test_an_audit_reports_and_never_changes_the_code(name: str) -> None:
    text = _prose(name)
    assert "Never modifies a tracked file, never commits, never opens a pull request" in text
    assert f"~/Downloads/acme_{name.removeprefix('audit-').replace('-', '_')}" in text or (
        name == "tickets-triage" and "~/Downloads/acme_ticket_triage_" in text
    )
    for section in ("## Input", "## Role and credential", "## Procedure", "## What it never does"):
        assert section in _skill(name), f"{name} has no {section}"
    assert "## Output" in _skill(name)


def _readme_needs(name: str) -> str:
    """What `ops/README.md` says a skill needs: its "Needs" cell, a note in parentheses left out."""
    row = re.search(rf"^\| `{re.escape(name)}` \| ([^|]+) \|", README.read_text(), re.MULTILINE)
    assert row, f"ops/README.md has no row for {name}"
    return re.sub(r"\([^)]*\)", "", row.group(1)).strip().lower()


@pytest.mark.parametrize("name", AUDITS)
def test_an_audit_holds_the_one_role_ops_readme_gives_it(name: str) -> None:
    """An audit opens its Role and credential section with its role, and
    ops/README.md names the same one, so a reader who trusts either grants
    the same credential."""
    section = _skill(name).split("## Role and credential", 1)[1].strip()
    said = re.split(r"[,.]", section, maxsplit=1)[0].strip().lower()
    needs = _readme_needs(name)
    assert "," not in needs, f"ops/README.md gives {name} more than one role: {needs}"
    assert said == needs


@pytest.mark.parametrize("name", AUDITS)
def test_an_audit_writes_to_no_shared_database_and_no_environment(name: str) -> None:
    text = _prose(name)
    assert "Never writes to" in text and "an environment" in text
    assert "proposed ticket" in text.lower()


@pytest.mark.parametrize("name", DATABASE_AUDITS)
def test_a_database_audit_builds_its_own_database_and_drops_it(name: str) -> None:
    text = _prose(name)
    run = f"audit_{name.removeprefix('audit-').replace('-', '_')}_<yyyymmdd>"
    assert f"uv run python ops/audit/auditdb.py create {run}" in text
    assert f"uv run python ops/audit/auditdb.py drop {run}" in text
    assert "it logs in only to the database it made on the local stack, and drops it" in text


# Every loop an agent runs in a skill, stated with its count and with what the
# skill does when the count is reached. A strong model keeps going until
# something stops it, and a clock alone stops nothing before the session runs
# out, so a rewrite that drops a count fails here.
COUNT_BOUNDS = {
    "ops-watch": [
        "at least 30 seconds",
        "A shorter interval is raised to 30 seconds",
        "A watch runs at most 30 batches.",
        "the interval is widened to `--for` divided by 30, up to five minutes",
        "an hour asked at 10 seconds runs 30 batches of two minutes",
        "its report names the part of the window it did not watch",
        "A batch makes at most 20 tool calls.",
        "A read that would be the 21st call is not made",
        "with `k` from 0 to at most 29",
        "The period of each `get-metric-data` query, its `Period` and the last argument "
        "of a `SEARCH` expression, is a whole minute",
        "The sub-agent names its Next and never runs it",
        "A session follows at most 2 hops of Next.",
        "never more than 30 batches, never a batch shorter than 30 seconds, "
        "never more than 20 tool calls in a batch",
    ],
    "ops-root-cause": [
        "A run follows at most 5 request ids, one pass each",
        "It lists every id past the fifth as not followed",
        "Poll `get-query-results` at most 10 times for one query, each poll after `sleep 5`",
        'writes its log leg as "not read: the query did not finish in 10 polls"',
        "Probe with `limit=1`",
        'the pass ends with "not found" for its id',
        "never more than 5 request ids, never a second pass over one, "
        "never more than 10 polls of a query",
        "When `next_cursor` is not null, read the next page with it as `cursor`",
        "Read at most 20 pages of members, 1,000 of them.",
        "never more than 20 pages of members",
        "An answer that is empty, or that `jq` cannot parse, is no answer.",
        "Either way the run ends there, as on a refusal",
        "The read is not made a second time.",
    ],
    "ops-investigate": [
        "Poll `get-query-results` at most 10 times for one query, each poll after `sleep 5`",
        'as "not read: the query did not finish in 10 polls"',
        "Step 8's query is polled the same way.",
        "A session follows at most 2 hops of Next.",
        "never more than 10 polls of a query",
    ],
    "stress-test-run": ["A session follows at most 2 hops of Next."],
    "ops-cloud-deployment-create": ["Its Next is the person's to run, never the session's"],
    "stress-test-create-or-update": ["Its Next is the person's to run, never the session's"],
    "audit-database-calls": ["the first run plus at most 1 rerun"],
    "docs-compact": [
        "One pass: each document is rewritten once in a run.",
        "the first run plus at most 3 reruns, then stop and say which gate fails and why",
        "Its Next is the person's to run, never the session's",
    ],
}
# The skills whose report's Next a person runs: no session follows it.
PERSONS_NEXT = ["ops-cloud-deployment-create", "stress-test-create-or-update", "docs-compact"]
# The skills that wait between two reads with `sleep`.
SLEEPERS = ["ops-investigate", "ops-root-cause", "ops-watch"]


@pytest.mark.parametrize(
    ("name", "bound"), [(name, bound) for name, bounds in COUNT_BOUNDS.items() for bound in bounds]
)
def test_every_loop_a_skill_runs_states_its_count(name: str, bound: str) -> None:
    assert bound in _prose(name), f"{name} no longer says: {bound}"


@pytest.mark.parametrize("name", PERSONS_NEXT)
def test_a_next_that_is_the_persons_is_never_counted_as_a_hop(name: str) -> None:
    assert "hops of Next" not in _prose(name)


@pytest.mark.parametrize("name", SLEEPERS)
def test_a_skill_that_waits_between_polls_may_sleep(name: str) -> None:
    assert "Bash(sleep:*)" in _allowed_tools(name)


# A watch's batches start at its own second, and a metric's datapoint is a
# whole minute: a batch that passed its own bounds would read the minute it
# shares with the next batch twice, and every count would be about double.
ONE_READ_A_MINUTE = [
    "the two bounds a batch passes are its start and its end, each rounded down to a whole minute",
    "no minute is read twice",
    "makes no metric read; the next batch reads that minute",
    "The period of each `get-metric-data` query, its `Period` and the last argument of a "
    "`SEARCH` expression, is a whole minute, 60 seconds, whatever the batch interval",
    "a count is `increase(<metric>[1m])`",
    "each a range query from the rounded start plus 60 seconds to the rounded end, "
    "at a 60-second `step`",
]


@pytest.mark.parametrize("sentence", ONE_READ_A_MINUTE)
def test_a_watch_reads_each_minute_of_a_metric_once(sentence: str) -> None:
    assert sentence in _prose("ops-watch"), f"ops-watch no longer says: {sentence}"


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
    assert sentence in _prose("docs-compact"), f"docs-compact no longer says: {sentence}"


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
    assert sentence in _prose("docs-compact"), f"docs-compact no longer says: {sentence}"


# What `make openapi` writes: the API document and the two schemas made from it.
GENERATED = [
    "clients/typescript/openapi.json",
    "clients/typescript/src/schema.d.ts",
    "clients/python/src/acme/client/schema.py",
]
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
    lines = [line.strip() for line in _skill("docs-compact").splitlines()]
    (command,) = [line for line in lines if line.startswith("git grep -nIiwE -f ")]
    return shlex.split(command)


def test_the_sweep_lists_no_file_the_compaction_never_edits(tmp_path: Path) -> None:
    """Step 6 rewrites what the sweep lists, so the sweep's pathspec is what
    keeps an applied migration and a generated document as they are. It runs
    here as the skill writes it, over a tree where every file tells a past."""
    for name in GENERATED:
        assert (ROOT / name).is_file(), f"{name} is not a file `make openapi` writes"
    for name in [*NEVER_SWEPT, *SWEPT]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("The key was renamed from another, and is no longer read.\n")
    references = SKILLS / "docs-compact" / "references"
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
    lines = [line.strip() for line in _skill("docs-compact").splitlines()]
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
    text = _prose("docs-compact")
    assert "The test reads the tree alone, and counts no release and no deploy." in text
    assert "While the head holds it, the step that ends the contract has not landed" in text
    assert "`git grep -nwF '<its name>' -- 'om/migrations/sql/*.up.sql'`" in text
    assert "In doubt, it is in flight." in text
    assert "It is listed in the report and left as it is, its comment with it" in text
    assert "git tag" not in _skill("docs-compact")


def test_the_compaction_regenerates_the_api_document_before_its_gate() -> None:
    """A docstring of an API type is in the generated document, and CI fails
    a tree whose document is not the one its code writes. So the gates write
    it again after the sweep and before `make check`, and commit what changed."""
    gates = _prose("docs-compact").split("**Run the gates.**", 1)[1]
    assert gates.index("`make setup`") < gates.index("`make openapi`") < gates.index("`make check`")
    assert "commit what it changes, apart. Then `make check`." in gates
    assert "Bash(make openapi)" in [
        tool.strip() for tool in _allowed_tools("docs-compact").split(",")
    ]
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "make openapi && git diff --exit-code" in ci


def _database_gates() -> list[str]:
    """The commands of the gates' database, as the skill writes them, in its order."""
    lines = [line.strip() for line in _skill("docs-compact").splitlines()]
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
    tools = {tool.strip() for tool in _allowed_tools("docs-compact").split(",")}
    assert not tools & {"Bash(make migrate-check)", "Bash(make test-integration)"}
    text = _prose("docs-compact")
    assert "on a database the run makes and drops, never the stack's own" in text
    assert "never export them in the shell" in text
    assert "The drop runs whatever the targets answered." in text
    assert "never runs a test on the local stack's own database" in text


def test_the_fold_states_its_precondition_its_proof_and_its_bound() -> None:
    """A fold replaces a chain every database applied, so the reference the
    fold step reads says when it may run, what shows it equal, and where a
    proof that fails stops."""
    assert "`references/fold.md`" in _prose("docs-compact")
    fold = " ".join((SKILLS / "docs-compact" / "references" / "fold.md").read_text().split())
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


def test_the_fold_reads_the_commit_staging_deployed_as_the_release_does() -> None:
    """A deploy run's head is the branch's tip when it started, and its
    title ends with the commit it deployed. The release workflow reads the
    title of the newest run whose apply job succeeded, and the fold reads
    it the same way, by the job's name as the deploy workflow has it."""
    workflows = ROOT / ".github" / "workflows"
    deploy = yaml.safe_load((workflows / "deploy-staging.yml").read_text())
    apply_job = deploy["jobs"]["staging"]["name"]
    fold = (SKILLS / "docs-compact" / "references" / "fold.md").read_text()
    for text in (fold, (workflows / "release.yml").read_text()):
        assert f'select(.name == "{apply_job}")' in text
        assert "--json displayTitle" in text
    assert "--json headSha" not in fold
    # Production's deploys are read by its own apply job, never by a branch.
    production = yaml.safe_load((workflows / "deploy-production.yml").read_text())
    assert f'select(.name == "{production["jobs"]["apply"]["name"]}")' in fold


# A `SEARCH` schema names every dimension a series has, or it matches nothing
# and answers with no datapoint and no error: a read of nothing that looks
# like a quiet hour. The dashboard module writes the schemas the app's series
# carry, the exporter's own `OTelLib` among them, and its Terraform test holds
# them there. So a skill's schema is one of the dashboard's.
DASHBOARD = ROOT / "deployment" / "terraform" / "modules" / "dashboard"
METRIC_READERS = ["ops-investigate", "ops-watch"]
SEARCH_SCHEMA = re.compile(r"SEARCH\(\W*?(\{[^}]*\})")


def _search_schemas(text: str) -> set[str]:
    """Every `SEARCH` schema a text writes, as CloudWatch reads it: the quotes
    of the shell that carries the expression are skipped, and the backslashes
    of its JSON or its Terraform string are left out."""
    return {schema.replace("\\", "") for schema in SEARCH_SCHEMA.findall(text)}


def _dashboard_schemas() -> set[str]:
    files = sorted(path for path in DASHBOARD.iterdir() if path.is_file())
    return set().union(*(_search_schemas(path.read_text()) for path in files))


@pytest.mark.parametrize("name", METRIC_READERS)
def test_every_search_schema_a_skill_writes_is_one_the_dashboard_writes(name: str) -> None:
    texts = sorted((SKILLS / name).rglob("*.md"))
    written = set().union(*(_search_schemas(path.read_text()) for path in texts))
    assert written, f"{name} writes no SEARCH, or this test no longer sees the ones it writes"
    unknown = written - _dashboard_schemas()
    assert not unknown, f"{name} searches {sorted(unknown)}, a schema the dashboard does not write"


def test_a_schema_without_the_exporters_dimension_is_not_the_dashboards() -> None:
    """The schema is read out of a command as a skill writes one, in the
    shell's quotes, and one that leaves `OTelLib` out is refused."""
    command = (
        r"""SUM(SEARCH('"'"'{Acme,environment,method,route,service,status} """
        r"""MetricName=\"acme_http_requests_total\"'"'"', '"'"'Sum'"'"', 60))"""
    )
    assert _search_schemas(command) == {"{Acme,environment,method,route,service,status}"}
    assert not _search_schemas(command) & _dashboard_schemas()
    assert '{"Acme",OTelLib,environment,method,route,service,status}' in _dashboard_schemas()


def test_the_tracker_read_asks_for_its_window_the_trackers_way() -> None:
    """The tracker's `statsPeriod` takes `24h` and `14d` and answers 400 to
    any other window, so the read asks Sentry for the issues last seen in it.
    GlitchTip reads that term as a tag and answers with no issue, deployed or
    local, so only Sentry's own host is sent it."""
    text = _skill("ops-investigate")
    assert 'in *sentry.io*) window=" lastSeen:-<since>" ;; *) window="" ;; esac' in text
    assert '"query=environment:<env>$window"' in text
    assert "statsPeriod=" not in text
    assert "keep the issues whose `lastSeen` field is inside the window" in _prose(
        "ops-investigate"
    )


# A cloud read that returned nothing sums to zero, and a p95 no request fed is
# `null`. Neither is a number of the batch: a zero reads as traffic stopping.
NOTHING_READ_IS_NOT_A_ZERO = [
    "in the cloud a batch whose `requests` is 0 read nothing",
    'Such a batch writes "metrics not read" in place of its numbers, never a zero',
    "no later batch reads those minutes",
    "the batch line's p95 is that number times 1,000, in milliseconds, "
    "and a `null` writes `p95 none`",
    "p95 <ms, or none>, <failures> worker failures "
    "| metrics read in the next batch | metrics not read>",
]


@pytest.mark.parametrize("sentence", NOTHING_READ_IS_NOT_A_ZERO)
def test_a_watch_never_writes_a_read_of_nothing_as_a_number(sentence: str) -> None:
    assert sentence in _prose("ops-watch"), f"ops-watch no longer says: {sentence}"


def test_the_cloud_p95_of_an_investigation_is_one_number_in_milliseconds() -> None:
    text = _prose("ops-investigate")
    assert "The report's p95 is its highest minute times 1,000, in milliseconds" in text
    assert "The cloud has no p95 by route" in text
    assert "p95 <ms> by route (cloud: one p95 <ms, or none>, every route together)" in text


def test_triage_closes_nothing_without_the_persons_word() -> None:
    text = _prose("tickets-triage")
    assert "Never closes a ticket without `--apply` and the person's word in this session" in text


@pytest.mark.parametrize("name", _own())
def test_every_reference_resolves_and_every_reference_file_is_named_by_a_step(name: str) -> None:
    """A skill keeps its spine and names its detail: a file beside SKILL.md
    is read by the step that names it, so one no step names is an orphan. A
    path is read from the skill's folder and stays in the tree."""
    folder = SKILLS / name
    body = _skill(name)
    for ref in SKILL_PATH.findall(body):
        target = (folder / ref.rstrip(".,;:")).resolve()
        assert target.exists(), f"{name}: {ref} does not exist"
        assert target.is_relative_to(ROOT.resolve()), f"{name}: {ref} resolves outside the tree"
    procedure = body.split("## Procedure", 1)[-1].split("\n## ", 1)[0]
    for extra in folder.rglob("*.md"):
        if extra.name == "SKILL.md":
            continue
        relative = extra.relative_to(folder).as_posix()
        assert relative in SKILL_PATH.findall(procedure), f"{name}: no step names {relative}"


@pytest.mark.parametrize("name", [*_own(), "_shared"])
def test_no_skill_names_a_path_only_one_agent_resolves(name: str) -> None:
    """Another agent reads `${CLAUDE_SKILL_DIR}` as it is written, and
    `.claude/skills/` is Claude Code's folder, not the standard's."""
    for path in sorted((SKILLS / name).rglob("*.md")):
        found = ONE_AGENTS_PATH.findall(path.read_text())
        assert not found, f"{path.relative_to(SKILLS)} names {found}"


def test_the_skills_a_person_starts_by_name_are_the_administrators() -> None:
    started_by_name = [
        name for name in _own() if _frontmatter(name).get("disable-model-invocation") is True
    ]
    assert started_by_name == ADMINISTRATORS


@pytest.mark.parametrize("name", _own())
def test_codex_starts_a_skill_on_its_own_exactly_when_claude_code_does(name: str) -> None:
    """Codex does not read `disable-model-invocation`; a skill that carries
    it also carries Codex's switch, `agents/openai.yaml`, and a skill the
    model may start carries neither."""
    by_name = _frontmatter(name).get("disable-model-invocation") is True
    codex = SKILLS / name / "agents" / "openai.yaml"
    policy = yaml.safe_load(codex.read_text()).get("policy", {}) if codex.exists() else {}
    assert (policy.get("allow_implicit_invocation") is False) == by_name, f"{name}: {policy}"


def test_the_watch_hands_its_sub_agent_the_preamble_from_the_repository_root() -> None:
    """A sub-agent gets the skill's text without its folder, so a path from
    that folder does not resolve for it; the path from the root does."""
    root_path = PREAMBLE.relative_to(ROOT).as_posix()
    assert root_path == ".agents/skills/_shared/ops-preamble.md"
    assert f"`{root_path}`" in _prose("ops-watch")


def test_claude_code_finds_the_same_skills_through_a_link() -> None:
    """Every agent that reads the Agent Skills standard finds the skills in
    `.agents/skills/`; Claude Code reads `.claude/skills/`, a link to it."""
    link = ROOT / ".claude" / "skills"
    assert link.is_symlink(), ".claude/skills is not a link"
    assert os.readlink(link) == "../.agents/skills"
    assert link.resolve() == SKILLS.resolve()
