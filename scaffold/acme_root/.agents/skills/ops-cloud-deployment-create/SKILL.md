---
name: ops-cloud-deployment-create
description: "Create one cloud environment of the platform from nothing, in its own AWS account, as that account's administrator: the bootstrap root (state bucket, registry, OIDC trust, the deploy roles, the investigate role, the budget, the zones), the delegation of its public names at Cloudflare, the investigate profile, the GitHub environments and their variables, then the first deploy through the pipeline and the grants that follow it. Runs scripts/cloud_create.sh after checking the administrator profile, the account, and the GitHub login. Supports --dry-run. The one skill besides nuke that needs a credential that writes, so a person invokes it by name and approves each run of the script."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(gh:*), Bash(jq:*)
---

# ops-cloud-deployment-create

The bootstrap. Everything the pipeline needs before its first run is
made here, once per account, by a person holding the account's
administrator profile with an agent narrating. After this skill the
environment moves only by pull request, and the administrator's
permission set goes back to the organization.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles, the account check, and the env
file are there.

## Input

`--env staging|production [--dry-run]`

`--env` is required; ask for it when missing. `--dry-run` runs the
script in its dry mode, which prints every command it would run and
runs none, so the whole path is readable before the first resource
exists. With `--dry-run` the skill runs the dry mode only and stops;
the real run is a second invocation without the flag. The script creates a cloud environment only; the compose stack
is `make up`, not this skill.

The accounts themselves, Identity Center, the permission sets, the
local profiles, and the Cloudflare token are made by hand, once, as
`deployment/cloud/first_time_manual.md` says. When a profile the skill
needs is missing, or Cost Explorer is off, point the person there
rather than working around it.

Everything about the environment's account comes from
`deployment/cloud/environments.json`: the account id, the region, the
administrator profile, the Identity Center profile an operator signs in
with, and the three public names. Read it first and say what it names.

The script also needs `OWNER_EMAIL` and `ALARM_EMAIL` (as environment
variables or as `--owner-email`, `--alarm-email`), and
`CLOUDFLARE_API_TOKEN` as an environment variable only: a token that
can edit DNS in the domain's zone at Cloudflare, so it never lands in
shell history. That token is the run's one credential outside the
account: scoped to the domain's zone, short-lived where Cloudflare
allows it, and held only for the run. The script refuses without them;
ask for any that is missing, and never for the token's value in the
conversation. The dry run needs no token.

## Order

Each environment is its own account, and production reads nothing from
staging's. What staging builds replicates into production's account,
and the replication needs production's state bucket to exist first. So
the environments are created in this order:

1. `--env staging`: staging's account, and its first deploy. The
   script finds no production bucket and leaves the replication off.
2. `--env production`: production's account. No release yet.
3. `--env staging` again: the script finds production's bucket and
   turns the replication on. Nothing else changes.

The first release is a commit merged to `main` after step 3, because
replication copies from the moment it is on and nothing before.

## Role and credential

Refuse any profile but the environment's administrator
(`acme-staging-admin` or `acme-prod-admin`), checked with `sts
get-caller-identity` before any other command as the preamble states.
Another environment's administrator is the wrong account, and a
sign-in profile cannot write IAM. The script checks the account again
before every apply, and the Terraform providers pin it as well.

The script clears any `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or
`AWS_SESSION_TOKEN` exported in the shell, because Terraform prefers
them to a profile, and prints a `note:` line naming the ones it
cleared, dry run included. Put that line in the report: an exported
key is a long-lived one, and this platform uses none.

The GitHub login is `gh auth status`; it names a user who can write
the repository's environments and their variables.

No env file is read. The script writes one, the file the preamble
describes: `~/.config/acme/ops/<env>.env`, owner-only, with
`ACME_API_URL` set and the lines `ACME_OPERATOR_TOKEN` and the
tracker's left empty, because no operator exists until
`grant-operator.yml` has run and the operator has enrolled a second
factor. It writes no provisioner's file: `acme-ops token --identity
provisioner` makes that one when the person copies the token. The script prints the two tracker lines,
`ACME_ERROR_TRACKER_URL` and `ACME_ERROR_TRACKER_TOKEN`, as the one
part of the file a person fills by hand, once the product's project
exists in the error tracker; it writes `ACME_ERROR_TRACKER_ORG` and
`ACME_ERROR_TRACKER_PROJECT` itself. It appends the
`acme-<env>-investigate` profile to `~/.aws/config`, chained from the
Identity Center profile. It writes no key anywhere. The skill prints
the names of what was written and never a value.

## Procedure

1. Read `deployment/cloud/environments.json` and name the account, the
   region, and the three public names. Verify the administrator profile
   as Role and credential states. Check the GitHub login.
2. Run the script, in dry mode first when `--dry-run` was given, or
   when it is the first time this environment is created. The script
   is not among this skill's tools, so every run asks the person
   before it starts, and the person's approval is the go:

   ```bash
   scripts/cloud_create.sh <env> --dry-run
   scripts/cloud_create.sh <env>
   ```

3. Narrate each step as the script reaches it, in one line each, so
   the person can stop it between two:
   - Who am I: the account check, and the GitHub login.
   - The bootstrap root, `deployment/terraform/bootstrap/<staging|prod>`:
     the state bucket `acme-state-<account>` with versioning, made on
     the first apply with local state and then adopted as the backend;
     the artifacts bucket `acme-artifacts-<account>`, which keeps the
     portal and site builds and never the state; the registry; the GitHub OIDC provider; the deploy role (staging)
     or the plan and deploy roles (production); the task boundary; the
     investigate role `acme-investigate-<env>`; the budget and the
     anomaly monitor; a hosted zone for the API's name and one for the
     app's; the company site's certificate in us-east-1. The monitor
     needs Cost Explorer, which only the organization's management
     account turns on; when the account's Cost Explorer does not
     answer, the script leaves the monitor out and says so, and the
     person decides whether to turn it on and run the script again.
     The budget needs Budgets, turned on the same way; the script asks
     it before the apply and refuses until it answers, pointing to the
     manual's "Turn on Cost Explorer and Budgets for the member accounts". A local `terraform.tfstate` in the bootstrap
     root is a first apply that stopped part way: the script applies
     against it, and it is never deleted by hand.
     For staging, whether the replication into production is on. For
     production, the two grants that let staging's replication write
     in.
   - The delegation at Cloudflare: each public name gets NS records
     naming its zone's four name servers, and any stale NS record at
     that name is deleted. This is the only write outside AWS and
     GitHub, and the only one the token is for. The company site's
     name is not delegated: it is a record in the Cloudflare zone
     (`acme.example`, `staging.acme.example`). The run writes its
     certificate's validation record there and waits for the
     certificate (3b), then, once a deploy has made the site's
     distribution, the name as a CNAME to it, DNS only (3c). On a first
     run there is no distribution yet, and 3c says so: the person runs
     the script again after the first green deploy. A name that holds
     an address record is refused, and the person decides.
   - The investigate profile, `acme-<env>-investigate`: the role's ARN
     with the Identity Center profile as its `source_profile`.
   - The GitHub environments and their variables: `staging-build` and
     `staging`, or `production-plan` (no reviewer) and `production`
     (the owner as required reviewer). Each deploys from one branch
     only, `main` for staging and `release` for both production ones.
     Each holds `AWS_ROLE_ARN`, `TF_STATE_BUCKET`, and `ARTIFACTS_BUCKET`
     for its own account; the plan environment and staging also hold
     `API_DOMAIN_NAME`, `APP_DOMAIN_NAME`, `SITE_DOMAIN_NAME`, and
     `ALARM_EMAIL`. No secret:
     the OIDC trust replaces keys. For staging, the ruleset on `main`:
     a pull request whose checks passed on a branch up to date with
     `main`.
   - The first deploy, through the pipeline: the script pushes
     nothing and applies no environment root itself. For staging it
     dispatches `deploy-staging.yml`. For production it prints the
     order above, because the first release waits for a replicated
     build.
   - The providers, printed for the person and never run by this
     skill (step 7b). The first deploy makes three secrets holding
     `off`: `acme/<env>/workos_api_key`, `workos_webhook_secret`, and
     `sentry_dsn`.
     After it, the person makes each value in the provider's dashboard
     and writes it under their own sign-in (`acme-staging`; in
     production `acme-prod-power`, when authorized).
     `workos_api_key` is the Acme App application's own API key,
     never the WorkOS environment's; the API refuses to start on
     another. WorkOS comes first, since the grants below sign people
     up through it and every
     sign-in answers `503` without its key. `acme-ops workos-bootstrap`
     proves the key and reconciles the application's redirects.
     `workos_webhook_secret` is the signing secret of the endpoint
     `https://<api name>/webhooks/identity` in the WorkOS dashboard;
     until it is set, the route refuses every delivery. `sentry_dsn`
     is the DSN of the product's one project in the error tracker.
     The values reach the API and the worker when their tasks next
     start. Point the person to `docs/runbooks/providers/workos.md`
     for the steps; never ask for a value in the conversation, and
     never run a `put-secret-value` yourself.
   - The grants, printed for the person and never run by this skill:
     the first operator, the provisioner, and the smoke identity, each
     through `grant-operator.yml` on the environment's branch, which
     production's reviewer approves like an apply.
   - The smoke test, printed as the step after the grants and never
     run by this skill. Every deploy runs its own after the rollout
     (`scripts/cloud_smoke.sh`): the smoke identity's operator token,
     minted by the grant task, reads the operator plane through the
     edge. It is skipped until `grant-operator.yml` has granted the
     smoke identity a `read` entry and the environment's `SMOKE_EMAIL`
     variable names it. By hand, one request through the edge, then
     `acme-ops signals check --request-id` reads its log lines, its
     metric, its trace, and its error event back by that id.
4. Check the result with the investigate profile the script wrote,
   because that is the profile every later skill holds. The person
   signs in with the Identity Center profile first:

   ```bash
   aws sts get-caller-identity --profile acme-<env>-investigate
   ```

   `Arn` is `assumed-role/acme-investigate-<env>/...` in the
   environment's account. A dry run skips this step: the profile does
   not exist yet.

5. Write the report. Its Next is the person's to run, never the
   session's: the next run of Order acts on another account,
   `grant-operator.yml` grants an operator, and the providers' secrets
   are written under the person's own sign-in.

## What it never does

- No apply by hand: the script applies the bootstrap root, which has
  no pipeline, and dispatches the pipeline for the environment; it
  never runs `terraform apply` in an environment root.
- No act outside the environment's account: every provider pins it,
  and the script refuses a profile that resolves anywhere else.
- No IAM user and no access key, created or read, for a person or an
  agent.
- No secret value read or printed: the Cloudflare token, the operator
  tokens, and the tracker token stay in the environment, the secret
  store, or owner-only files, and appear in the report as the names
  that hold them.
- No console clicks: what the script cannot do with the CLI is
  reported as a manual step with its exact command.
- No tenant data; there is none yet.
- No run under any profile but the environment's administrator, and
  no run twice without `--dry-run` in between: the script is written
  to be rerun, and the dry run shows what a rerun would touch.

## Output

The budget is `monthly_budget_usd` in the bootstrap root's
`variables.tf`. In a dry run, write the report with "would be" for
what the script would make, and "decided on the real run" for the
replication and the anomaly monitor: their probes run only then. A
dry run's smoke test is "not yet".

```markdown
# Environment created: <env>

**Credential.** <admin_profile>, <Arn>, account <id>
**GitHub.** <login>, environments: <names>, variables set: <names>

## Made

- Bootstrap root: bootstrap/<staging | prod>, state in s3://acme-state-<id>/bootstrap/
- Roles: <deploy, plan, investigate role names>; budget <usd>/month
- Zones: <api name>, <app name>, delegated at Cloudflare (<created | already there>)
- Replication into production: <on | off | n/a>
- Profile written: acme-<env>-investigate (~/.aws/config), source_profile <sso_profile>
- Env file written: ~/.config/acme/ops/<env>.env
- First deploy: workflow run <url>, <status> | production: waits for Order
- Smoke test: not yet; it follows the smoke identity's grant and SMOKE_EMAIL

## Left for a person

- <the next step of the order, or nothing>

## Next

- <the next run of Order, or nothing>
- Dispatch `grant-operator.yml` for the first operator, who enrols the second factor at the console's first sign-in and runs `uv run acme-ops token --env <env> --identity operator` in their own terminal; grant the smoke identity and set `SMOKE_EMAIL`, so the next deploy runs the smoke test
- Manual steps left: <the tracker's lines in the env file, or none>
- The providers, after the first deploy: write workos_api_key, workos_webhook_secret, and sentry_dsn under <sso profile | acme-prod-power>, run `acme-ops workos-bootstrap`, then roll or wait for the next deploy (docs/runbooks/providers/workos.md)
- Hand the administrator permission set back; every later skill runs
  under acme-<env>-investigate.
```
