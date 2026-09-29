# Deploy: staging is `main`, production is `release`

Two branches, two workflows, one approval.

- `main` is staging. `.github/workflows/deploy-staging.yml` follows every
  green `ci` run on a push to `main` in this repository (or a
  `workflow_dispatch`): it builds the images once, tagged by the commit,
  keeps the portal build and the company site's build by the commit
  (the site's holds a page per environment, since its links are in its
  HTML), and plans and applies staging
  with no approval. A merge is the deployment. A pull request never
  deploys, a fork's branch least of all: `ci` runs on every pull
  request, and a `workflow_run` carries this repository's staging role
  whatever the code it followed. `main`'s ruleset requires the checks on
  a branch up to date with `main`, so a pull request's checks ran on its
  merge with the current `main`. After each merge, every other open pull
  request updates its branch and runs its checks again before it merges.
- `release` is production. It moves only by a fast-forward from `main`,
  which `.github/workflows/release.yml` makes when a person dispatches
  it; nobody commits to `release` and nothing merges into it. A push to
  `release` runs `.github/workflows/deploy-production.yml`: it refuses
  unless `release` is an ancestor of `main`, resolves the digests and
  the portal and site builds staging made for that commit, from the copies
  replicated into production's account (a commit with no copy there is
  refused), plans production, waits for a reviewer's approval
  on that plan, and applies exactly it.

The approval is the `production` GitHub environment's required reviewer,
the owner. The environment is created outside the repository, not by
Terraform, since it is what protects the deploy role's use; the
production workflow fails before it plans when the rule is missing.

## One account per environment, one credential per job

Each environment has an AWS account of its own, named in
[deployment/cloud/environments.json](../../deployment/cloud/environments.json)
with the region and the public names
([ADR 0021](../adr/0021-each-environment-has-an-aws-account-of-its-own.md)).
Three roles stand behind the workflows, each declared by its account's
bootstrap root (`deployment/terraform/bootstrap/staging`,
`bootstrap/prod`), and each trusts exactly one subject:

| Role | Account | Assumed by a job that declares | On ref | May |
|------|---------|--------------------------------|--------|-----|
| `acme-deploy-staging` | staging | `environment: staging` | `refs/heads/main` | apply staging |
| `acme-plan-production` | production | `environment: production-plan` | `refs/heads/release` | read production, plan it |
| `acme-deploy-production` | production | `environment: production` | `refs/heads/release` | apply production |

A GitHub job presents `repo:<owner>@<owner id>/<name>@<repo id>:environment:<name>` in its
token only when it declares that environment, and presents its branch
when it declares none. So the gate on the `production` environment is
the gate on the credential: a job that has not waited for the reviewer
never produces the subject the applying role trusts. A staging run,
which waits for nobody by design, holds a credential in another account,
and nothing in production's account trusts it.

The repository issues GitHub's immutable OIDC subject
(`use_immutable_subject` in the repository's OIDC settings), so the
subject carries the owner's and the repository's ids beside their names,
and a renamed or recreated repository never matches it. A role that
expects the name-only form refuses every job with `AccessDenied`.

Each role's permissions stop at what its environment owns: names
beginning `acme-<environment>`, secrets under `acme/<environment>/`,
log groups under `/acme/<environment>/`, its own keys in its account's
state bucket, and its environment's two delegated public names. Everything tagged
as the other environment is denied too, which holds if a root is ever
applied in the wrong account. So is any path by which a role could
widen itself: the deploy roles, the OIDC trust, the replication, a new
user or access key, and a task role created without the
`acme-task-boundary-<environment>` permissions boundary.

Each GitHub environment holds its own variables under the same names,
so a job reads `AWS_ROLE_ARN` and gets the role of the environment it
declared:

| GitHub environment | Variables |
|--------------------|-----------|
| `staging` | `AWS_ROLE_ARN` (the staging deploy role), `TF_STATE_BUCKET`, `API_DOMAIN_NAME`, `APP_DOMAIN_NAME`, `ALARM_EMAIL`, and optionally `SITE_DOMAIN_NAME` (the company site, left out of every deploy until it is set and its certificate is issued) and `PORTAL_SENTRY_DSN` (the product's one tracker project, the same DSN production's environment carries) |
| `production-plan` | the same names: `AWS_ROLE_ARN` is the plan role |
| `production` | `AWS_ROLE_ARN` (the production deploy role), `TF_STATE_BUCKET` |

No AWS secret is stored in GitHub: the roles are assumed through OIDC.

Production reads nothing from staging's account. Staging's registry
replicates every image into production's, digest for digest, and
staging's artifacts bucket replicates every static build under
`builds/` (the portal's under `builds/portal/`, the site's under
`builds/site/`) into production's. The state buckets hold state and
nothing else. Production's registry and artifacts bucket grant staging
those two writes and nothing else.

Each GitHub environment deploys from one branch: `staging` from
`main`, `production-plan` and `production` from `release`. The roles'
trust names the branch as well, so a job on any other branch is
refused twice.

`release.yml` fast-forwards `release` to the last commit staging
deployed: the newest `deploy-staging` run whose apply succeeded. That
is `main`'s tip only once its staging deploy has passed.

`acme-plan-production` runs the jobs before the approval. It changes
nothing: it reads production, takes the state lock while it plans, and
writes the plan file the reviewer approves. The state holds no secret
value: the database password is generated for the run and written
write-only, to the database and to its secret. A refresh still reads
production's secrets through the secret store, so the approval holds
the write, not the read.

The roles a person or an agent reads an environment under, and the
profiles that hold them, are in [operate.md](operate.md).

## Create the environment

Before the first run, the organization, the two accounts, Identity
Center, the permission sets, the local profiles, and the Cloudflare
token exist, as
[deployment/cloud/first_time_manual.md](../../deployment/cloud/first_time_manual.md)
says. That part is by hand, once; everything below is scripted.

`scripts/cloud_create.sh <staging|production>` is the administrator's
one run per account, and the `ops-cloud-deployment-create` skill
narrates it. It runs under the environment's administrator profile
(`acme-staging-admin` or `acme-prod-admin`, an Identity Center
permission set) and refuses any other. It clears any AWS key exported in
the shell, checks the account before every apply, prints every command
before it runs it, and with `--dry-run` prints them all and runs none.
Its inputs are `OWNER_EMAIL` and `ALARM_EMAIL`, as flags or environment
variables, and `CLOUDFLARE_API_TOKEN`, an environment variable with DNS
edit on the domain's zone. Everything else comes from
`deployment/cloud/environments.json`.

Run it three times: staging, production, then staging again. Staging's
replication into production needs production's bucket to exist, and
the second staging run finds it and turns the replication on. In order,
each run does:

1. `aws sts get-caller-identity`, compared with the environment's
   account, and `gh auth status`.
2. The account's bootstrap root, applied with local state because the
   root makes the state bucket `acme-state-<account>`, then `init
   -migrate-state` into it. The root declares the registry, the OIDC
   provider, the task boundary, the environment's deploy roles above,
   its investigate role, the budget, the anomaly monitor, and a hosted
   zone per public name; staging's adds the replication into
   production.
3. The delegation at Cloudflare, where the domain is registered: NS
   records for each public name, naming its zone's name servers. A
   stale NS record at that name is deleted.
4. The investigate profile below, appended to `~/.aws/config`. A
   profile that exists is left alone.
5. The GitHub environments and their variables, from the root's
   outputs: `staging` with no rule, or `production-plan` with no rule
   (a reviewer on it would hold the plan the reviewer is meant to read)
   and `production` with the owner as required reviewer. Setting the
   wrong ARN in one of them does not cross the boundary: the role
   refuses a subject it does not trust.
6. `~/.config/acme/ops/<environment>.env`, mode 600, with
   `ACME_API_URL` set and the two operator token lines
   (`ACME_OPERATOR_TOKEN`, `ACME_PROVISIONER_TOKEN`) and the error
   tracker's URL and token empty, and with
   `ACME_ERROR_TRACKER_ORG` and `ACME_ERROR_TRACKER_PROJECT` naming
   the product's one project, the same in every environment; the skills
   read it. No operator exists until
   the grants below, and the file never holds a password.
7. The first deploy, through the pipeline like every other:
   `deploy-staging.yml` for staging. Production's first release waits:
   replication copies only what staging pushes after it is on, so the
   first release is a commit merged to `main` after the second staging
   run, released with `release.yml`.
8. The grants, printed for the person to dispatch: the first operator,
   the provisioner, and the smoke identity, each through
   `grant-operator.yml` (see Grant an operator below).
9. When a deploy is green after that, the smoke test the deploy ran,
   and one request by hand through the edge with every signal read
   back by the request id it answered with.

   ```bash
   id=$(curl -s -o /dev/null -D - "https://<api host>/v1/me" \
     | awk 'tolower($1) == "x-request-id:" { print $2 }' | tr -d '\r')
   uv run acme-ops signals check --env <environment> --request-id "$id"
   ```

10. Sign-in: write the Acme App application's API key once (made on
   that application's own API keys tab in WorkOS, never the
   environment's API Keys page;
   `aws secretsmanager put-secret-value --secret-id acme/<environment>/workos_api_key
   --secret-string <key>`, then roll the API), and hold the WorkOS
   configuration to the repository's with `WORKOS_API_KEY=<key> uv run
   acme-ops workos-bootstrap --environment <workos environment>
   --apply` (ops/README.md). Until the key is set, the API starts and
   every sign-in answers `503`. With a key of another application or
   environment, the API refuses to start.
11. The first person: the environment carries no seed (`make seed` is
   local), so open the portal and sign in at `/login` through WorkOS.
   A first sign-in creates the identity and its personal org, with the
   person as its owner; a team org is made from the org chip after, and
   its settings invite the rest.

A bootstrap root is never applied by a deploy run; every deploy role
denies the calls that would change the registry, its replication, the
state bucket, or the trust.

### The profiles

People sign in through IAM Identity Center. There is no IAM user and no
access key.

| Profile | Holds | Used by |
|---------|-------|---------|
| `acme-staging-admin`, `acme-prod-admin` | the account's administrator permission set, granted for the bootstrap | create and nuke, nothing else |
| `acme-staging`, `acme-prod` | a person's sign-in: PowerUserAccess in staging, ReadOnlyAccess in production, which writes nothing | the `source_profile` of the investigate profile, never a skill directly |
| `acme-staging-ro` | a person's read-only sign-in in staging (ReadOnlyAccess) | inspection by a person, never a skill |
| `acme-prod-power` | PowerUserAccess in production | a change a person explicitly authorized, never a skill and never by default |
| `acme-staging-investigate` | `role_arn` = `acme-investigate-staging`, `source_profile` = `acme-staging` | every read of staging |
| `acme-production-investigate` | `role_arn` = `acme-investigate-production`, `source_profile` = `acme-prod` | every read of production |

The investigate role trusts the sign-in role `environments.json` names
for its account (`sso_role_name`), matched by pattern because its name
carries a generated suffix. In production that is a read-only
permission set: no person holds a credential that writes production,
and every change there is a release. A person signs
in once (`aws sso login --profile acme-staging`), and an agent works
under the investigate profile inside that session.

An investigate role reads everything in its environment and writes
nothing: every log group, metric, trace, alarm, and resource
description, and the state so `terraform plan -lock=false` runs. It is
denied a secret's value, an object in a data bucket, a database
connection, anything tagged as the other environment, and every IAM
write. Sessions last one hour.

### The budget and the anomaly monitor

Each bootstrap root declares its account's monthly cost budget
(`monthly_budget_usd`, default 200, sized in [the cloud pricing](../../deployment/cloud/README.md)) that mails `owner_email` at 50, 80, and 100 percent of the amount
and when the forecast crosses it, and an anomaly monitor on each
service's spend that reports a jump of 20 USD or more daily. Every
resource carries `acme:environment`, so a cost report splits by it.

### The alarm topic

Each environment root declares an SNS topic `acme-<environment>-alarms`
with `alarm_email` subscribed (the address confirms by mail once) and seven
alarms to it: the load balancer's 5xx ratio and p95 latency, unhealthy
targets, the database's CPU and free storage, and each service running
fewer tasks than it wants. Another address subscribes by hand under the
administrator profile; Terraform leaves it alone.

### The autoscaling flip

Both roots declare `autoscaling_enabled = false`. Every lever under it
is on, so a pull request that flips it to `true` scales the whole
environment; nothing else changes. [scale.md](scale.md) says what turns
on and how to read that it happened.

## Grant an operator

The operator allowlist of a deployed environment changes through one
workflow, `grant-operator.yml`, dispatched by a person on the
environment's branch. It runs `acme-api grant-operator` as a one-off
task on the grant task definition (the root's
`grant_task_definition_arn` output, which holds the runtime and the
system logins' URLs and nothing wider) under the deploy role, through
`scripts/cloud_grant.sh`. Production's run waits for the same required
reviewer as its apply. The email is masked in the run's log.

```bash
# The identity signed up first, like any person.
gh workflow run grant-operator.yml --ref main -f environment=staging \
  -f email=<email> -f permission=read          # or write
gh workflow run grant-operator.yml --ref main -f environment=staging \
  -f email=<email> -f permission=none -f disable=true
gh workflow run grant-operator.yml --ref main -f environment=staging \
  -f email=<provisioner> -f permission=none -f mint_token=provisioner
```

Production's runs use `--ref release -f environment=production`.

- **A person** is granted `read` or `write`, enrols the second factor
  through the API (`POST /v1/admin/me/totp`, then `/confirm`; the
  steps are in `deployment/cloud/first_time_manual.md`, section 20;
  until then the plane admits the enrolment alone), and writes a `read` token into the env file in
  their own terminal: `uv run acme-ops token --env <env> --identity
  operator`. It asks the person to confirm a sign-in in their browser
  and for the TOTP code there; no agent ever holds either.
- **The provisioner** is granted `write` and has its token minted with
  `mint_token=provisioner` right before a traffic run, since a token
  lasts an hour; `uv run acme-ops token --env <env> --identity
  provisioner` copies it from `acme-<env>-provisioner-token` into the
  env file under the person's own sign-in (`--profile acme-prod-power`
  in production, whose everyday sign-in reads no secret), never under
  an agent's investigate profile, which is denied every secret value.
  In production the provisioner is disabled again after the run. On
  staging, `stress.yml` grants it, mints its token, and disables it
  again itself; the `staging` environment's `PROVISIONER_EMAIL`
  variable names it for that run:
  `gh variable set PROVISIONER_EMAIL --env staging --body <email>`.
- **The smoke identity** is granted `read`, and the environment's
  `SMOKE_EMAIL` variable names it:
  `gh variable set SMOKE_EMAIL --env <staging|production> --body <email>`.

## The smoke test

Every deploy runs it after the rollout: staging as the `smoke` job,
production at the end of the apply job, since a job of its own would
wait for a second approval. `scripts/cloud_smoke.sh` mints the smoke
identity's token through the grant task into
`acme-<env>-smoke-token` (the root's
`operator_token_secret_names.smoke`), reads it into the step's shell alone
(masked), and calls `GET /v1/admin/me` with it through the edge. The
summary names the request id. While `SMOKE_EMAIL` is empty the step is
skipped with a notice, not failed. A staging run whose smoke failed is
not a run `release.yml` takes.

## The stress run on staging

`stress.yml` is dispatched by a person, never run on a push, and it
knows one environment: staging. It takes no environment input, so
production cannot be picked from a dropdown by mistake; a production
run is a person's own, under `stress-test-run`.

```bash
gh workflow run stress.yml --ref main -f scenario=staging
gh workflow run stress.yml --ref main -f scenario=staging -f duration_seconds=60
gh workflow run stress.yml --ref main -f scenario=staging -f p95_ms=1200
```

`scenario` names a file under `ops/stress/`, and `duration_seconds`
overrides that scenario's duration and nothing else. `p95_ms` and
`error_ratio` are the pass mark: each one given overrides the
scenario's half of the target, each one left empty keeps it, and the
run's text says which target it judged and where each half came from.
A generous target with a short duration is a wiring check; the same
scenario with a strict one is the run worth reading. The run grants
the provisioner
`write`, mints its operator token through the grant task the way the
smoke step does, drives the scenario, reads the signals back, and
keeps the report and the run's text as an artifact. A missed target
fails the run. Whatever the outcome, the last step disables the
provisioner's entry again: a write credential that stands between runs
is what the guideline refuses. The `staging` environment's
`PROVISIONER_EMAIL` names the identity, the way `SMOKE_EMAIL` names
the smoke one, and while it is empty the run fails and names the
dispatch to make.

This is a reference shape for a stress test on staging, not a load
test. One small GitHub runner drives it, so what it measures is what
one generator can ask for from one address, bounded by that runner's
CPU, its one network path, and the API's per-address rate limit on
sign-in. A load test needs many generators, from many addresses. What
this workflow is worth is the wiring: a credential that does not
stand, tenants made and removed, a scenario, the signals, and a
verdict.

### Nuke

`scripts/cloud_nuke.sh <environment>` is the administrator's other run,
narrated by `ops-cloud-deployment-nuke`. It applies from the exact
commit the environment runs, in a clean worktree of its own: `release`
for production, and for staging the commit of the newest
`deploy-staging` run whose apply succeeded, never `main`'s tip, and it
refuses while a staging deploy is still going. Staging goes on the word.
Production refuses unless `--confirm production` is typed and
`environments/prod/main.tf` on `origin/release` already reads
`database_deletion_protection = false` and production's state shows it
applied, so destroying production is a pull request a person read and
released. Production's database keeps its final snapshot and its
automated backups. The run applies the root once with
`destroyable=true` (buckets empty on destroy, the database skips its
final snapshot and drops its protection), destroys it, and prints what
remains: the account's bootstrap root whole (its zones and their
delegation, the registry and its images, the roles, the budget), the
state prefix, the static builds, the profile and the env file. It runs
under the environment's administrator profile and checks the account
before the apply and the destroy. `--dry-run` prints every command and
runs none.

## Cut a release

1. Pick the `main` commit: it has run on staging (the `deploy-staging`
   run for it is green, its smoke test included) and staging looks
   right. The release is the last commit staging deployed; to go back,
   see Roll back.
2. Actions, `release`, Run workflow on `main`. The job lists the commits
   `release` gains in its summary and pushes the fast-forward. If it
   says `release` is not an ancestor of `main`, someone committed to
   `release`: see When it fails.
3. `deploy-production` starts on the push (or is dispatched by the
   release job, when the push was made with the workflow token). Its
   `guard`, `resolve`, and `plan` jobs run on their own; the run stops
   at `apply the approved plan, migrate, and publish`. GitHub shows
   "Review deployments" on the run page.

## Approve a plan

Read the `plan production` job's summary (the whole text is in the
`production-plan` artifact) before pressing Approve:

- The `Plan:` line: the counts of add, change, destroy against what the
  release intended. A destroy of a database, a cache, or a bucket is
  never routine; reject and look.
- The images are the digests the `resolve` job names, tagged with the
  release commit by staging.
- `terraform show`: the resources named match the change; nothing is
  replaced (`-/+`) that holds data. The `terraform_data.pre_rollout`
  replacement under `module.environment.module.api` is the migration. It
  appears when the release changes a file `deployment/migration-inputs.json`
  names, or the database, the password version, or the migrate task's
  secrets changed; a release with none of those has no migration in its
  plan.

Approve: `apply` applies exactly the saved plan (Terraform refuses it if
the state moved meanwhile). Inside the apply the migration, when the
plan has one, runs as a one-off task on the new API image, then the API
and the worker roll;
the apply waits until the new tasks serve; then the portal files staging
kept for the commit are published, and the site's production page from the
build staging kept beside its own. Reject: the run is cancelled and
nothing was applied; `release` still points at the commit, so a fix is
a new `main` commit and another release.

## Roll back

Two paths, and neither moves `release` backwards.

- **The fast rollback, to the previous release only**, for a release
  that is healthy but wrong. The previous release is the newest commit
  of `release` before its tip whose latest `released/production` status
  is a success; the apply writes that status once the release answers
  ready. Dispatch the production workflow on `release` with it:

  ```bash
  gh workflow run deploy-production.yml --ref release -f rollback_to=<previous release commit>
  ```

  Any other commit is refused, naming the previous one. After the same
  approval the `rollback` job swaps each service's application image
  back to the digest production ran for that release (its `prod-<sha>`
  tag, compared with staging's `deployed/` statuses), publishes that
  release's portal and site builds (a release from before the site has
  none, and the site then stays as it is), and reads `/readyz`. It runs no migration
  and plans no Terraform, so nothing else the current release declared
  moves; the next apply writes the declared shape again. It marks the
  release it rolled back from with an error `released/production`
  status, so a later rollback never returns to it. The registry keeps
  the last 30 images per repository and, beyond those, the last 10
  production ran; the artifacts bucket keeps each static build a year.
- **A revert through `main`**, the rollback of record and the only one
  for anything older: revert the change, merge it, let staging deploy
  it, and release as always. A revert never removes a migration that
  has run: the schema rolls forward with a new one, because both
  environments' version tables name every revision applied, and a
  missing revision file fails the next migrate.

The schema is not rolled back by the fast rollback either: a migration
is compatible with the release before it (expand and contract), so the
previous release runs against the newer schema. A release older than
that has no such promise, which is why the fast rollback stops at one.

## A failed migration stops the rollout

The migration is a one-off task on the new API task definition that
runs before either service rolls; the service depends on it. A migration
waits 5 seconds at most for a lock
(`ACME_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS`). One that gives up
applies nothing of its role and exits 75, and the task runs again, three
runs in all (ADR 0071); the apply log says `asked to run again`. When the
task exits non-zero otherwise, or a third time, the apply fails at
`terraform_data.pre_rollout`,
the API and the worker keep their current tasks, and nothing else in
the plan that comes after the services was applied. The failed run is
tainted in the state, so every later plan runs it again. Read the task's log
in the API's log group (`/acme/production/api`, stream prefix `api`).
Fix on `main` (it runs on staging first, the same way), release again;
the new plan runs the migration again from where the chain stopped
(`acme-api migrate --all` is idempotent per revision).

A rollout that ECS rolls back (the circuit breaker) also fails the
apply: the service waits for steady state. The old tasks serve; the
migration has already run, which expand and contract allows.

## Branch protection on `release`

Set by hand, once, as a ruleset on the repository (Settings, Rules,
Rulesets), target branch `release`:

- Restrict creations, restrict deletions, block force pushes.
- Restrict updates: nobody pushes, except the bypass actors.
- Require status checks: `no pull request into release`, so a pull
  request into `release` cannot merge (the check always fails).
- Bypass list: the deploy key below, and the repository admin role for
  the by-hand rollback push (bypass actors may force-push; keep the
  list to those two).

The workflow's own token cannot be a bypass actor, so a ruleset that
restricts updates blocks `release.yml`'s push unless it pushes with a
deploy key: generate a key pair (`ssh-keygen -t ed25519 -f release_key
-N ''`), add the public key as a deploy key with write access (Settings,
Deploy keys), add it to the ruleset's bypass list under "Deploy keys",
and store the private key as the `RELEASE_DEPLOY_KEY` repository secret.
A push with it fires the push event `deploy-production` listens for.
Without the secret, `release.yml` pushes with its token, which works
only while no ruleset restricts `release` (and fires no workflow, so
the job dispatches `deploy-production` itself).

The `production` environment: Settings, Environments, `production`,
Required reviewers, the owner; or

```bash
gh api -X PUT repos/{owner}/{repo}/environments/production \
  --input - <<'JSON'
{"reviewers": [{"type": "User", "id": <the owner's user id>}]}
JSON
```

The `staging` and `production-plan` environments exist with no rule;
each role trusts the name of the one it belongs to.

## What to expect

- A waiting production run holds the `deploy-production` concurrency
  group, so a second release queues behind it until the first is
  approved or rejected. GitHub cancels a waiting job after 30 days.
- Staging runs queue in `deploy-staging`; two merges in a row deploy
  one after the other.
- Staging and production each take the migration inside their apply; a
  staging apply that fails on it is the same signal one commit earlier.

## When it fails

- An apply fails with "Error acquiring the state lock" after a run was
  killed: once that run is certainly gone, dispatch
  `state-unlock.yml` on the environment's branch (`main` for staging,
  `release` for production, where the reviewer approves it) with the
  lock's id from the log. It prints the lock, refuses a different id, and
  removes it; it touches no state.
- `resolve` refuses because a `deployed/` status is missing or differs:
  either staging never deployed that commit, or the copy in production's
  account is not what staging built. The second is an incident: the
  copy changed after staging deployed it. Do not release until the
  cause is known.

- `deploy-staging` skipped every cloud job although the account exists:
  the `staging` environment's variables are empty. Run
  `scripts/cloud_create.sh staging` again; it sets them (the table in
  "One account per environment, one credential per job").
  `deploy-production` reads the same names from `production-plan` and
  `production`, and fails, not skips, when one is empty.
- `resolve` says production's registry has no image for the commit:
  staging never built it, or built it before the replication into
  production was on. Release a commit `deploy-staging` built after the
  second staging run of `cloud_create.sh`.
- `guard` says `release` is not an ancestor of `main`: someone committed
  to `release`. Reset it to a `main` commit by hand
  (`git push --force origin <main commit>:release`, by a bypass actor)
  and dispatch `release` again.
- `guard` says the production environment has no required-reviewers
  rule: add it (above) before releasing; the run refused to plan.
- `resolve` says staging never built the commit: the commit's
  `deploy-staging` run did not reach the cloud (skipped, failed, or was
  older than the registry's 30 kept images). Rerun `deploy-staging` on
  that commit from Actions, or release a newer one. A rerun is safe on a
  commit whose images are already pushed: the tags are immutable, so the
  `images` job resolves the digests that are there instead of pushing
  the same tag twice.
- `apply` says the saved plan is stale: someone applied production in
  between. Rerun the workflow; a fresh plan comes back for review.
- A job fails at `configure-aws-credentials` with "Not authorized to
  perform sts:AssumeRoleWithWebIdentity": the token's subject is not
  the one the role trusts. Either the job lost its `environment:` line,
  or it is running on a ref the role does not allow (`main` for
  staging, `release` for production), or the environment was renamed.
  A `workflow_dispatch` of `deploy-staging` on a branch other than
  `main` fails here by design.
- `release.yml`'s push is refused by a ruleset: set `RELEASE_DEPLOY_KEY`
  as above.
