"""The ops skills share one preamble, and keep their invariants inline.

The operational detail a reader needs once, and not in every skill, lives
in `.agents/skills/_shared/ops-preamble.md`: the profiles, the account
check, the env file's fields, and the way back when a token expires. A
skill that reaches a cloud environment names that file, by its path from
the skill's own folder, as every agent that reads a skill resolves it.

A rule that must never be missed does not travel by reference, so the
lines that stop a secret leaking stay written in each skill that could
break them, and this test holds them there. What one skill alone says is
held in the test file named for it, `test_skill_<name>.py`.
"""

import os
import re

import pytest
import yaml
from skills_support import (
    ENV_FILE,
    OPERATOR_READ,
    PREAMBLE,
    PROVISIONER_FILE,
    REFERENCE,
    ROOT,
    SHARED,
    SKILLS,
    allowed_tools,
    dashboard_schemas,
    frontmatter,
    own,
    prose_of,
    search_schemas,
    text_of,
)

README = ROOT / "ops" / "README.md"

# Every skill that can reach an environment, and so holds a credential.
READERS = [
    "audit-deploy-time",
    "audit-retention",
    "ops-cloud-deployment-create",
    "ops-cloud-deployment-nuke",
    "ops-infra-as-code",
    "ops-integration-silent",
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
# The two that drive traffic, whose `acme-ops` command reads the provisioner's
# file; every other token holder reads, and holds the read token alone.
PROVISIONERS = ["ops-simulate-traffic", "stress-test-run"]
READS = [name for name in TOKEN_HOLDERS if name not in PROVISIONERS]
# A file a shell command sources: `. <path>` or `source <path>`, first on its
# line or after a `;`, `&&`, `|`, or `(`, where the path starts with `~`, `/`,
# `$`, or `./`; a `jq` filter's `(. - 1)` is no path.
SOURCED = re.compile(
    r"(?:^[ \t]*|[;&|(][ \t]*)(?:\.|source)[ \t]+((?:[~/$]|\.{1,2}/)[^\s;&|)]*)", re.MULTILINE
)
# The skills that read an environment under the investigate profile.
INVESTIGATORS = [
    *TOKEN_HOLDERS,
    "ops-infra-as-code",
    "ops-integration-silent",
    "audit-deploy-time",
    "audit-retention",
]
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


def test_the_shared_preamble_exists_and_holds_what_moved_into_it() -> None:
    text = PREAMBLE.read_text()
    assert "deployment/cloud/environments.json" in text
    assert "aws sts get-caller-identity" in text
    assert ENV_FILE in text
    assert "ACME_PROVISIONER_TOKEN" in text
    assert PROVISIONER_FILE in text
    assert "acme-<env>-investigate" in text


def test_no_skill_sources_the_provisioners_file() -> None:
    """A command that sources a file puts every value in it into the shell.
    The env file holds the read token, and the provisioner's `write` token
    has a file of its own that `acme-ops` reads for traffic and stress: every
    file a skill, the preamble, or a signals file sources is the env file,
    and the skills that read an operator's rows source it."""
    texts = {name: text_of(name) for name in own()}
    texts |= {str(path.relative_to(SKILLS)): path.read_text() for path in SHARED.rglob("*.md")}
    sourced = {name: SOURCED.findall(text) for name, text in texts.items()}
    assert {path for paths in sourced.values() for path in paths} == {ENV_FILE}
    assert sourced["ops-investigate"] and sourced["ops-root-cause"]


@pytest.mark.parametrize("name", READS)
def test_a_skill_that_reads_names_no_write_token(name: str) -> None:
    """The skills that read never name the provisioner's file or its key, so
    none of their commands can reach the one `write` token."""
    text = text_of(name)
    assert ".provisioner.env" not in text
    assert "ACME_PROVISIONER_TOKEN" not in text


# The `acme-ops` commands a skill that reads may run: neither loads the
# provisioner's token, which `traffic` and `stress` do.
READ_COMMANDS = {"size", "signals"}


@pytest.mark.parametrize("name", READS)
def test_a_skill_that_reads_pre_approves_only_the_read_commands_it_runs(name: str) -> None:
    """`uv run acme-ops:*` covers `traffic` and `stress`, which load the
    provisioner's `write` token: a skill that reads pre-approves each read
    command it runs by name, and nothing wider."""
    tools = [tool.strip() for tool in allowed_tools(name).split(",")]
    approved = {
        match[1]
        for tool in tools
        if (match := re.fullmatch(r"Bash\(uv run acme-ops ([a-z]+):\*\)", tool))
    }
    assert "Bash(uv run acme-ops:*)" not in tools
    assert approved <= READ_COMMANDS, approved
    assert approved == set(re.findall(r"\buv run acme-ops (\w+)", prose_of(name))) & READ_COMMANDS


@pytest.mark.parametrize("name", READS)
def test_a_skill_that_reads_stops_at_the_refusal_before_it_sources_the_env_file(
    name: str,
) -> None:
    """`acme-ops` refuses an env file that holds the provisioner's token, and
    prints the line that moves it. A skill that reads stops there, before any
    command of it sources that file."""
    text = prose_of(name)
    stop = text.index("give the person the line it printed")
    sourced = text.find(f". {ENV_FILE}")
    assert sourced == -1 or stop < sourced


@pytest.mark.parametrize("name", PROVISIONERS)
def test_a_skill_that_drives_traffic_leaves_the_provisioners_file_to_acme_ops(name: str) -> None:
    text = prose_of(name)
    assert PROVISIONER_FILE in text
    assert "Never read the env file or the provisioner's file, and never source" in text
    assert "`acme-ops` reads both itself from `--env`." in text


@pytest.mark.parametrize("name", READERS)
def test_every_skill_that_holds_a_credential_reads_the_preamble(name: str) -> None:
    """The reference is the skill's first instruction, before its steps."""
    text = text_of(name)
    assert REFERENCE in text, f"{name} drops the reference to the shared preamble"
    assert text.index(REFERENCE) < text.index("## Input")


def test_the_skills_that_hold_a_credential_are_the_ones_that_can_call_aws() -> None:
    """A new ops skill that reaches the cloud joins the list above, or this
    fails: the preamble is not optional for a skill that holds a
    credential."""
    reaches_the_cloud = sorted(name for name in own() if "Bash(aws:*)" in allowed_tools(name))
    assert reaches_the_cloud == sorted(READERS)


@pytest.mark.parametrize("name", TOKEN_HOLDERS)
def test_the_secret_rules_stay_inline(name: str) -> None:
    text = prose_of(name)
    assert "Never read the env file" in text
    assert "Never print a token." in text


@pytest.mark.parametrize("name", INVESTIGATORS)
def test_the_refusal_of_a_wider_profile_stays_inline(name: str) -> None:
    assert "Refuse any profile wider than the investigate role." in prose_of(name)


@pytest.mark.parametrize("name", ADMINISTRATORS)
def test_the_refusal_of_anything_but_the_administrator_stays_inline(name: str) -> None:
    assert "Refuse any profile but the environment's administrator" in prose_of(name)


@pytest.mark.parametrize("name", TOKEN_HOLDERS)
def test_a_skill_that_holds_a_token_pre_approves_the_ops_command_and_no_other(name: str) -> None:
    """`uv run` takes any program, `python -c` among them: pre-approved, it
    is code on the operator's machine beside the env file's tokens, with
    nobody asked. The skill runs `acme-ops` and names that."""
    tools = [tool.strip() for tool in allowed_tools(name).split(",")]
    uv = [tool for tool in tools if tool.startswith("Bash(uv")]
    assert uv, f"{name} pre-approves no acme-ops command"
    assert all(re.fullmatch(r"Bash\(uv run acme-ops(?: [a-z]+)?:\*\)", tool) for tool in uv), uv
    runs = set(re.findall(r"\buv run ([\w-]+)", prose_of(name)))
    assert runs == {"acme-ops"}, f"{name} runs uv with {sorted(runs)}"


@pytest.mark.parametrize("name", own())
def test_every_read_of_the_operator_plane_goes_through_jq(name: str) -> None:
    for read in OPERATOR_READ.finditer(text_of(name)):
        assert read["piped"], f"{name} prints a read of the operator plane whole: {read[0]}"


def test_the_audits_are_the_skills_named_for_one() -> None:
    assert own("audit-*") == AUDITS


@pytest.mark.parametrize("name", [*AUDITS, "tickets-triage"])
def test_an_audit_reports_and_never_changes_the_code(name: str) -> None:
    text = prose_of(name)
    assert "Never modifies a tracked file, never commits, never opens a pull request" in text
    assert f"~/Downloads/acme_{name.removeprefix('audit-').replace('-', '_')}" in text or (
        name == "tickets-triage" and "~/Downloads/acme_ticket_triage_" in text
    )
    for section in ("## Input", "## Role and credential", "## Procedure", "## What it never does"):
        assert section in text_of(name), f"{name} has no {section}"
    assert "## Output" in text_of(name)


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
    section = text_of(name).split("## Role and credential", 1)[1].strip()
    said = re.split(r"[,.]", section, maxsplit=1)[0].strip().lower()
    needs = _readme_needs(name)
    assert "," not in needs, f"ops/README.md gives {name} more than one role: {needs}"
    assert said == needs


@pytest.mark.parametrize("name", AUDITS)
def test_an_audit_writes_to_no_shared_database_and_no_environment(name: str) -> None:
    text = prose_of(name)
    assert "Never writes to" in text and "an environment" in text
    assert "proposed ticket" in text.lower()


@pytest.mark.parametrize("name", DATABASE_AUDITS)
def test_a_database_audit_builds_its_own_database_and_drops_it(name: str) -> None:
    text = prose_of(name)
    run = f"audit_{name.removeprefix('audit-').replace('-', '_')}_<yyyymmdd>"
    assert f"uv run python ops/audit/auditdb.py create {run}" in text
    assert f"uv run python ops/audit/auditdb.py drop {run}" in text
    assert "it logs in only to the database it made on the local stack, and drops it" in text


# The skills whose report's Next a person runs: no session follows it.
PERSONS_NEXT = ["ops-cloud-deployment-create", "stress-test-create-or-update", "docs-compact"]
# The skills that wait between two reads with `sleep`.
SLEEPERS = ["ops-integration-silent", "ops-investigate", "ops-root-cause", "ops-watch"]


@pytest.mark.parametrize("name", PERSONS_NEXT)
def test_a_next_that_is_the_persons_is_never_counted_as_a_hop(name: str) -> None:
    assert "hops of Next" not in prose_of(name)


@pytest.mark.parametrize("name", SLEEPERS)
def test_a_skill_that_waits_between_polls_may_sleep(name: str) -> None:
    assert "Bash(sleep:*)" in allowed_tools(name)


METRIC_READERS = ["ops-integration-silent", "ops-investigate", "ops-watch"]


@pytest.mark.parametrize("name", METRIC_READERS)
def test_every_search_schema_a_skill_writes_is_one_the_dashboard_writes(name: str) -> None:
    texts = sorted((SKILLS / name).rglob("*.md"))
    written = set().union(*(search_schemas(path.read_text()) for path in texts))
    assert written, f"{name} writes no SEARCH, or this test no longer sees the ones it writes"
    unknown = written - dashboard_schemas()
    assert not unknown, f"{name} searches {sorted(unknown)}, a schema the dashboard does not write"


def test_a_schema_without_the_exporters_dimension_is_not_the_dashboards() -> None:
    """The schema is read out of a command as a skill writes one, in the
    shell's quotes, and one that leaves `OTelLib` out is refused."""
    command = (
        r"""SUM(SEARCH('"'"'{Acme,environment,method,route,service,status} """
        r"""MetricName=\"acme_http_requests_total\"'"'"', '"'"'Sum'"'"', 60))"""
    )
    assert search_schemas(command) == {"{Acme,environment,method,route,service,status}"}
    assert not search_schemas(command) & dashboard_schemas()
    assert '{"Acme",OTelLib,environment,method,route,service,status}' in dashboard_schemas()


@pytest.mark.parametrize("name", own())
def test_every_reference_resolves_and_every_reference_file_is_named_by_a_step(name: str) -> None:
    """A skill keeps its spine and names its detail: a file beside SKILL.md
    is read by the step that names it, so one no step names is an orphan. A
    path is read from the skill's folder and stays in the tree."""
    folder = SKILLS / name
    body = text_of(name)
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


@pytest.mark.parametrize("name", [*own(), "_shared"])
def test_no_skill_names_a_path_only_one_agent_resolves(name: str) -> None:
    """Another agent reads `${CLAUDE_SKILL_DIR}` as it is written, and
    `.claude/skills/` is Claude Code's folder, not the standard's."""
    for path in sorted((SKILLS / name).rglob("*.md")):
        found = ONE_AGENTS_PATH.findall(path.read_text())
        assert not found, f"{path.relative_to(SKILLS)} names {found}"


def test_the_skills_a_person_starts_by_name_are_the_administrators() -> None:
    started_by_name = [
        name for name in own() if frontmatter(name).get("disable-model-invocation") is True
    ]
    assert started_by_name == ADMINISTRATORS


@pytest.mark.parametrize("name", own())
def test_codex_starts_a_skill_on_its_own_exactly_when_claude_code_does(name: str) -> None:
    """Codex does not read `disable-model-invocation`; a skill that carries
    it also carries Codex's switch, `agents/openai.yaml`, and a skill the
    model may start carries neither."""
    by_name = frontmatter(name).get("disable-model-invocation") is True
    codex = SKILLS / name / "agents" / "openai.yaml"
    policy = yaml.safe_load(codex.read_text()).get("policy", {}) if codex.exists() else {}
    assert (policy.get("allow_implicit_invocation") is False) == by_name, f"{name}: {policy}"


def test_claude_code_finds_the_same_skills_through_a_link() -> None:
    """Every agent that reads the Agent Skills standard finds the skills in
    `.agents/skills/`; Claude Code reads `.claude/skills/`, a link to it."""
    link = ROOT / ".claude" / "skills"
    assert link.is_symlink(), ".claude/skills is not a link"
    assert os.readlink(link) == "../.agents/skills"
    assert link.resolve() == SKILLS.resolve()
