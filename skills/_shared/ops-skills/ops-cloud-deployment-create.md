---
name: ops-cloud-deployment-create
description: "Create one cloud environment of the platform from nothing, as the administrator: the state backend, the shared account resources (roles, boundaries, the budget, the operators user, the zone), the operator profiles, the GitHub variables and environments, then the first deploy through the pipeline and the smoke test. Runs scripts/cloud_create.sh after checking the administrator profile, the zone, and the GitHub login. Supports --dry-run. The one skill besides nuke that needs a credential that writes."
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(gh:*), Bash(scripts/cloud_create.sh:*)
---

# ops-cloud-deployment-create

The bootstrap. Everything the pipeline needs before its first run is
made here, once, by a person holding the administrator profile with an
agent narrating. After this skill the environment moves only by pull
request; the administrator profile goes back in the drawer.

## Input

`--env local|staging|production [--dry-run]`

`--env` is required; ask for it when missing. `--dry-run` runs the
script in its dry mode, which prints every command it would run and
runs none, so the whole path is readable before the first resource
exists. `local` is the compose stack: the skill runs `make up` through
the script's local branch and the smoke test against it, and needs no
cloud, so the skill is testable with no account.

## Role and credential

This skill needs the administrator profile `acme-admin` and refuses
anything else. Before any other command, run

```bash
aws sts get-caller-identity --profile acme-admin
```

and check that `Arn` is the administrator's own identity in the
account the environment belongs to (the account id
`deployment/terraform/environments/<env>/` names): a user or a role
with administrator access, never `assumed-role/acme-investigate-*`.
An investigate profile cannot create a state bucket or a role, and
the skill stops rather than try. Every `aws` command below carries
`--profile acme-admin`; the script inherits it.

The GitHub login is `gh auth status`; it names a user who can write
the repository's variables and environments.

No env file is read. The script writes one: `~/.config/acme/ops/<env>.env`,
owner-only, from the first operator it bootstraps, and it writes the
operator profiles into `~/.aws/config`. The skill prints the names of
what was written and never a value.

## Procedure

1. Verify the administrator profile as Role and credential states.
   Check the GitHub login. Cloud only: check the zone. `shared`
   creates the DNS zone when `create_dns_zone` is true and outputs its
   name servers; when the zone exists already, read it with
   `aws route53 list-hosted-zones-by-name --profile acme-admin` and
   confirm the base domain of the environment (production the
   product's domain, staging its `staging.` subdomain) resolves to it
   or will after the delegation the script prints.
2. Run the script, in dry mode first when `--dry-run` was given, or
   when it is the first time this environment is created:

   ```bash
   scripts/cloud_create.sh <env> --dry-run
   scripts/cloud_create.sh <env>
   ```

3. Narrate each step as the script reaches it, in one line each, so
   the person can stop it between two:
   - The state backend: the state bucket with versioning and the lock
     table, the prefix `environments/<env>/`.
   - `shared`: the investigate role `acme-investigate-<env>` with its
     fences, the permission boundary, the budget and the anomaly
     monitor, the operators user `acme-operators` with its one
     `sts:AssumeRole` policy, the zone.
   - The operators user's access key, minted once and written into
     `~/.aws/config` as the profile `acme-operators`, with
     `acme-<env>-investigate` chained from it by `role_arn` and
     `source_profile`. The key is never printed and never lands in
     the repository.
   - The GitHub variables (account id, region, the base domain, the
     zone) and the environments `staging` and `production`, the
     latter with its required reviewers, and the OIDC trust for the
     deployer role.
   - The first deploy, through the pipeline: the script pushes
     nothing and applies nothing itself; it dispatches the deploy
     workflow, which applies the environment as every later merge
     does.
   - The smoke test: the telemetry round trip against the deployed
     base, one traffic session read back by request id through
     CloudWatch, X-Ray, and the error tracker.
4. Check the result with the investigate profile the script wrote,
   because that is the profile every later skill holds:

   ```bash
   aws sts get-caller-identity --profile acme-<env>-investigate
   ```

5. Write the report.

## What it never does

- No apply by hand: the script applies `shared` and the backend,
  which have no pipeline, and dispatches the pipeline for the
  environment; it never runs `terraform apply` in an environment
  root.
- No secret value printed: the access key, the operator password, the
  tracker token go into owner-only files and appear in the report as
  the file paths that hold them.
- No console clicks: what the script cannot do with the CLI is
  reported as a manual step with its exact command.
- No tenant data; there is none yet.
- No run under any profile but `acme-admin`, and no run twice without
  `--dry-run` in between: the script is written to be rerun, and the
  dry run shows what a rerun would touch.

## Output

```markdown
# Environment created: <env>

**Credential.** acme-admin, <Arn>, account <id>
**GitHub.** <login>, variables set: <names>, environments: <names>

## Made

- State backend: <bucket>, <table>, prefix environments/<env>/
- shared: <role>, <user>, budget <usd>/month, zone <name> (<created | existing>)
- Profiles written: acme-operators, acme-<env>-investigate (~/.aws/config)
- Env file written: ~/.config/acme/ops/<env>.env
- First deploy: workflow run <url>, <status>
- Smoke test: <passed | failed>, request id <id>

## Left for a person

- <delegation of the zone at the registrar, or nothing>

## Next

- Put acme-admin away; every later skill runs under acme-<env>-investigate.
```
