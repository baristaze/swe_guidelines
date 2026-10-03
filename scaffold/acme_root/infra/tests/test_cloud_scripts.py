"""The administrator's two scripts, dry-run and refused.

No cloud is needed: a dry run prints every command instead of running it,
and a refusal happens before any command. HOME is a temporary directory so a
dry run that wrote a profile or an env file by mistake would be caught.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CREATE = ROOT / "scripts" / "cloud_create.sh"
NUKE = ROOT / "scripts" / "cloud_nuke.sh"

INPUTS = {
    "OWNER_EMAIL": "owner@acme.example",
    "ALARM_EMAIL": "alarms@acme.example",
}
ENVIRONMENTS = json.loads((ROOT / "deployment" / "cloud" / "environments.json").read_text())
STAGING = ENVIRONMENTS["environments"]["staging"]
PRODUCTION = ENVIRONMENTS["environments"]["production"]


def _run(
    script: Path, *args: str, home: Path, **overrides: str | None
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "HOME": str(home), **INPUTS}
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        ["bash", str(script), *args], capture_output=True, text=True, env=env, cwd=ROOT
    )


def test_create_staging_dry_run_prints_every_step_and_writes_nothing(tmp_path: Path) -> None:
    result = _run(CREATE, "staging", "--dry-run", home=tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    account = STAGING["account_id"]
    expect = (
        f"+ aws sts get-caller-identity --profile acme-staging-admin  (expect account {account})"
    )
    # Once before anything, once more right before the apply.
    assert out.count(expect) == 2
    assert "+ gh auth status" in out
    # Budgets is asked before the apply, so the root is never left half made.
    budgets = f"+ aws budgets describe-budgets --account-id {account}"
    apply = "+ terraform -chdir=deployment/terraform/bootstrap/staging apply -input=false"
    assert budgets in out
    assert apply in out
    assert out.index(budgets) < out.index(apply)
    assert "-var owner_email=owner@acme.example" in out
    assert "-var replicate_to_production=" in out
    assert "init -input=false -migrate-state -force-copy" in out
    assert f"-backend-config=bucket=acme-state-{account}" in out
    assert "-backend-config=key=bootstrap/terraform.tfstate" in out
    assert f"-backend-config=region={ENVIRONMENTS['region']}" in out
    assert f"cloudflare GET /zones?name={ENVIRONMENTS['domain']}" in out
    assert f"NS {STAGING['api_domain_name']} -> " in out
    assert f"NS {STAGING['app_domain_name']} -> " in out
    assert f"NS {STAGING['site_domain_name']} -> " not in out
    site_name = STAGING["site_domain_name"]
    assert f"CNAME {site_name} -> <the distribution's domain> (DNS only)" in out
    assert "aws acm wait certificate-validated" in out
    assert f"+ gh variable set SITE_DOMAIN_NAME --env staging --body {site_name}" in out
    assert "[profile acme-staging-investigate]" in out
    assert "role_arn = <investigate_role_arn>" in out
    assert "source_profile = acme-staging" in out
    assert "create-access-key" not in out
    assert "+ gh variable set AWS_ROLE_ARN --env staging --body <deploy_role_arn>" in out
    assert f"+ gh variable set TF_STATE_BUCKET --env staging --body acme-state-{account}" in out
    assert "+ gh variable set ALARM_EMAIL --env staging --body alarms@acme.example" in out
    assert "+ gh api -X PUT repos/{owner}/{repo}/environments/staging" in out
    assert "+ gh api -X POST repos/{owner}/{repo}/rulesets --input <the main ruleset>" in out
    assert "deployment_branch_policy[custom_branch_policies]=true" in out
    assert "environments/staging/deployment-branch-policies -f name=main -f type=branch" in out
    assert "+ gh variable set AWS_ROLE_ARN --env staging-build --body <build_role_arn>" in out
    assert "environments/staging-build/deployment-branch-policies -f name=main" in out
    assert f"--env staging --body acme-artifacts-{account}" in out
    assert "production-plan" not in out
    assert f"ACME_API_URL=https://{STAGING['api_domain_name']}" in out
    # The operator's token, empty until the grant; never a password, and
    # never the provisioner's write token, which has a file of its own.
    assert "ACME_OPERATOR_TOKEN=" in out and "ACME_PROVISIONER_TOKEN" not in out
    assert "PASSWORD" not in out
    assert "/.config/acme/ops/staging.provisioner.env, never in " in out
    assert "+ gh workflow run deploy-staging.yml --ref main" in out
    grant = "gh workflow run grant-operator.yml --ref main -f environment=staging"
    assert f"{grant} -f email=<operator> -f permission=read" in out
    assert "gh variable set SMOKE_EMAIL --env staging" in out
    assert "uv run acme-ops signals check --env staging" in out
    assert not (tmp_path / ".aws").exists()
    assert not (tmp_path / ".config").exists()
    assert not (ROOT / "deployment/terraform/bootstrap/staging/backend_override.tf").exists()


def test_create_drops_a_site_cname_left_by_a_destroyed_environment(tmp_path: Path) -> None:
    # CloudFront refuses a name whose CNAME points at another distribution,
    # even one the nuke deleted, so the run removes that leftover before the
    # deploy, and only while no distribution of the account serves the name.
    result = _run(CREATE, "staging", "--dry-run", home=tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    step = out[out.index("== 3c.") : out.index("== 4.")]
    stale = step.index(f"type=CNAME&name={STAGING['site_domain_name']}  (a CNAME to CloudFront")
    assert stale < step.index("CNAME staging.acme.example -> <the distribution's domain>")
    assert out.index("== 3c.") < out.index("+ gh workflow run deploy-staging.yml --ref main")


def test_create_production_dry_run_sets_two_environments_and_waits_for_replication(
    tmp_path: Path,
) -> None:
    result = _run(CREATE, "production", "--dry-run", home=tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert f"(expect account {PRODUCTION['account_id']})" in out
    assert "+ terraform -chdir=deployment/terraform/bootstrap/prod apply -input=false" in out
    assert "replicate_to_production" not in out
    assert "+ gh variable set AWS_ROLE_ARN --env production-plan --body <plan_role_arn>" in out
    assert "+ gh variable set AWS_ROLE_ARN --env production --body <deploy_role_arn>" in out
    site_name = PRODUCTION["site_domain_name"]
    assert f"CNAME {site_name} -> <the distribution's domain> (DNS only)" in out
    assert f"+ gh variable set SITE_DOMAIN_NAME --env production-plan --body {site_name}" in out
    for github_environment in ["production-plan", "production"]:
        policy = f"environments/{github_environment}/deployment-branch-policies -f name=release"
        assert policy in out
    assert "acme-artifacts-" in out
    assert "-f reviewers[][type]=User -F reviewers[][id]=<owner id>" in out
    assert "source_profile = acme-prod" in out
    # The first release waits for a commit staging built after replication.
    assert "+ gh workflow run" not in out
    assert "scripts/cloud_create.sh staging, again" in out
    assert f"ACME_API_URL=https://{PRODUCTION['api_domain_name']}" in out
    assert "gh workflow run grant-operator.yml --ref release -f environment=production" in out
    assert "uv run acme-ops signals check --env production" in out


def test_create_refuses_to_run_for_real_without_the_cloudflare_token(tmp_path: Path) -> None:
    result = _run(CREATE, "staging", home=tmp_path, CLOUDFLARE_API_TOKEN=None)
    assert result.returncode == 2
    assert "refused: missing: CLOUDFLARE_API_TOKEN" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_create_refuses_to_run_for_real_on_placeholders(environment: str, tmp_path: Path) -> None:
    """environments.json and each root's WorkOS client id ship as
    placeholders, and a real run on them would act on accounts, names, and a
    repository nobody owns. It refuses before any command, and names each;
    the dry runs above print with them."""
    result = _run(CREATE, environment, home=tmp_path, CLOUDFLARE_API_TOKEN="token")
    assert result.returncode == 2
    assert result.stdout == ""
    refusal = result.stderr
    assert "refused: placeholders left: " in refusal
    for name in (
        "environments.staging.account_id",
        "environments.production.account_id",
        f"environments.{environment}.api_domain_name",
        "domain",
        "github_repository_id",
        "github_repository_owner_id",
    ):
        assert name in refusal
    root = ENVIRONMENTS["environments"][environment]["environment_root"]
    assert f"workos_client_id in deployment/terraform/{root}/variables.tf" in refusal


@pytest.mark.parametrize("script", [CREATE, NUKE], ids=["create", "nuke"])
def test_refuses_any_profile_but_the_environments_administrator(
    script: Path, tmp_path: Path
) -> None:
    # Production's administrator is the wrong account for staging.
    result = _run(
        script, "staging", "--dry-run", home=tmp_path, AWS_PROFILE=PRODUCTION["admin_profile"]
    )
    assert result.returncode == 2
    assert "refused" in result.stderr
    assert STAGING["admin_profile"] in result.stderr
    assert result.stdout == ""


def test_create_refuses_a_missing_input_by_name(tmp_path: Path) -> None:
    result = _run(CREATE, "staging", "--dry-run", home=tmp_path, OWNER_EMAIL=None)
    assert result.returncode == 2
    assert "refused: missing: OWNER_EMAIL" in result.stderr


def test_create_takes_the_inputs_as_flags_too(tmp_path: Path) -> None:
    result = _run(
        CREATE,
        "staging",
        "--dry-run",
        "--owner-email",
        "flag@acme.example",
        home=tmp_path,
        OWNER_EMAIL=None,
    )
    assert result.returncode == 0, result.stderr
    assert "-var owner_email=flag@acme.example" in result.stdout


STAGING_ROOT = "<a worktree of the deployed commit>/deployment/terraform/environments/staging"


def test_nuke_staging_dry_run_lifts_the_protections_then_destroys(tmp_path: Path) -> None:
    result = _run(NUKE, "staging", "--dry-run", home=tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert f"+ terraform -chdir={STAGING_ROOT} init" in out
    apply = out.index(f"+ terraform -chdir={STAGING_ROOT} apply")
    destroy = out.index(f"+ terraform -chdir={STAGING_ROOT} destroy")
    assert apply < destroy
    assert out.count("-var destroyable=true") == 2
    assert f"-var api_domain_name={STAGING['api_domain_name']}" in out
    assert "dns_zone_name" not in out
    # The apply is the code staging runs: the commit of its last deploy whose
    # apply succeeded, never the tip of main and never the working tree.
    assert "+ gh run list --workflow deploy-staging.yml --branch main" in out
    worktree = "<a worktree of the deployed commit> <the last commit staging deployed>"
    assert f"+ git worktree add --detach {worktree}" in out
    assert "origin/main" not in out
    assert out.count(f"(expect account {STAGING['account_id']})") == 4
    assert "== 6. What remains" in out
    assert "the bootstrap root, whole" in out
    assert "the state prefix environments/staging/" in out


def test_nuke_removes_what_terraform_does_not_own_after_the_destroy(tmp_path: Path) -> None:
    # The application's secrets and the leftovers AWS made are not in the
    # state. Each is found by this environment's names alone, after the
    # destroy, and the tenants' secrets follow the database's final snapshot.
    result = _run(NUKE, "staging", "--dry-run", home=tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    leftovers = out.index("== 5. Remove what Terraform does not own")
    assert out.index(f"+ terraform -chdir={STAGING_ROOT} destroy") < leftovers
    step = out[leftovers : out.index("== 6. What remains")]
    assert "describe-db-snapshots --db-snapshot-identifier acme-staging-final" in step
    assert "Values=acme/staging/app/org/" in step
    assert "starts_with(Name, 'acme/staging/app/org/')" in step
    assert "(only when acme-staging-final does not exist)" in step
    assert "--log-group-name-prefix /aws/ecs/containerinsights/acme-staging/" in step
    assert "--family-prefix acme-staging-" in step
    assert "--force-delete-without-recovery" in step
    assert "production" not in step
    # A dry run reads nothing, so it cannot say whether production holds
    # copies of staging's builds; it names the condition instead.
    assert "get-bucket-replication --bucket acme-artifacts-" in out
    assert "when the artifacts bucket replicates" in out


def test_nuke_refuses_production_without_its_typed_name(tmp_path: Path) -> None:
    result = _run(NUKE, "production", "--dry-run", home=tmp_path)
    assert result.returncode == 2
    assert "--confirm production" in result.stderr
    assert result.stdout == ""


def test_nuke_refuses_production_while_release_still_protects_the_database(
    tmp_path: Path,
) -> None:
    # Production applies release, and there the root reads
    # database_deletion_protection = true, so the typed name alone is not
    # enough. A checkout without origin/release refuses too, on the reading.
    result = _run(NUKE, "production", "--confirm", "production", "--dry-run", home=tmp_path)
    assert result.returncode == 2
    assert "refused" in result.stderr
    assert "origin/release" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "workflow",
    ["deploy-staging.yml", "deploy-production.yml", "grant-operator.yml", "stress.yml"],
)
def test_the_deploy_workflows_run_in_the_region_the_environments_name(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text()
    assert f"  AWS_REGION: {ENVIRONMENTS['region']}\n" in text


def test_the_stress_workflow_knows_one_environment_and_runs_on_a_dispatch() -> None:
    """A stress run drives real traffic at a deployed environment, so it
    happens because a person asked for it, and staging is the only
    environment it can be asked for: there is no environment to choose. The
    pass mark is a dispatch's to state, so one scenario answers both the
    wiring check and a harder question."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "stress.yml").read_text())
    triggers = workflow[True]  # `on:` is YAML's true
    assert list(triggers) == ["workflow_dispatch"]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert sorted(inputs) == ["duration_seconds", "error_ratio", "p95_ms", "scenario"]
    assert [name for name, spec in inputs.items() if spec.get("required")] == ["scenario"]
    job = workflow["jobs"]["stress"]
    assert job["environment"] == "staging"
    # The provisioner's write entry never stands between runs.
    disable = job["steps"][-1]
    assert disable["if"] == "always()" and "--disable" in disable["run"]


def test_a_pull_request_merges_on_checks_run_against_the_current_main() -> None:
    """Two changes can each be green alone and conflict together. The main
    ruleset's checks are strict, so a branch merges only up to date with
    main, and ci.yml runs on merge_group too, for a merge queue."""
    create = CREATE.read_text()
    assert "strict_required_status_checks_policy: true," in create
    assert "strict_required_status_checks_policy: false" not in create
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    triggers = workflow[True]  # `on:` is YAML's true
    assert {"pull_request", "merge_group"} <= set(triggers)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_environment_roots_default_to_the_region_the_environments_name(
    environment: str,
) -> None:
    root = ENVIRONMENTS["environments"][environment]["environment_root"]
    text = (ROOT / "deployment" / "terraform" / root / "variables.tf").read_text()
    assert f'default     = "{ENVIRONMENTS["region"]}"' in text


def test_the_grant_runner_refuses_to_run_without_arguments(tmp_path: Path) -> None:
    result = _run(
        ROOT / "scripts" / "cloud_grant.sh",
        "deployment/terraform/environments/staging",
        home=tmp_path,
    )
    assert result.returncode == 2
    assert "usage: cloud_grant.sh" in result.stderr


def _site_steps(job: dict) -> list[dict]:
    return [
        step
        for step in job["steps"]
        if "site" in str(step.get("name", ""))
        or "site" in str(step.get("with", {}).get("name", ""))
    ]


def test_a_deploy_without_a_site_name_deploys_everything_else() -> None:
    """The company site is optional. A missing SITE_DOMAIN_NAME never makes
    the cloud 'not configured': the rest plans, applies, and publishes, and
    every step that keeps, plans, or publishes the site is behind its own
    condition. Terraform's side, the plan with no site, is the staging
    root's `terraform test`."""
    staging = yaml.safe_load((ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text())
    check = next(s for s in staging["jobs"]["cloud"]["steps"] if s.get("id") == "check")
    assert "for name in AWS_ROLE_ARN" in check["run"]
    required = check["run"].split("for name in ", 1)[1].split(";", 1)[0]
    assert "SITE_DOMAIN_NAME" not in required
    keep = [
        s
        for s in staging["jobs"]["site"]["steps"]
        if s.get("name") == "keep the build by the commit"
    ]
    assert keep and "site_domain_name != ''" in keep[0]["if"]
    deploy = staging["jobs"]["staging"]
    plan = next(s for s in deploy["steps"] if s.get("name") == "plan staging")
    assert plan["env"]["SITE_DOMAIN_NAME"] == "${{ steps.site.outputs.site_domain_name }}"
    publish = [s for s in _site_steps(deploy) if s.get("id") != "site"]
    assert publish and all("steps.site.outputs.site_domain_name != ''" in s["if"] for s in publish)
    assert 'if [ "$SITE" = "true" ]; then record site' in str(staging["jobs"]["record"])

    production = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "deploy-production.yml").read_text()
    )
    guard = str(production["jobs"]["guard"])
    required = guard.split("for name in AWS_ROLE_ARN", 1)[1].split(";", 1)[0]
    assert "SITE_DOMAIN_NAME" not in required
    plan = next(s for s in production["jobs"]["plan"]["steps"] if s.get("id") == "plan")
    assert plan["env"]["SITE_DOMAIN_NAME"] == "${{ needs.resolve.outputs.site_domain_name }}"
    publish = _site_steps(production["jobs"]["apply"])
    assert publish and all(
        "needs.resolve.outputs.site_domain_name != ''" in s["if"] for s in publish
    )
