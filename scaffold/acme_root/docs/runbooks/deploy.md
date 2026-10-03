# Deploy: staging is `main`, production is `release`

A merge to `main` deploys staging with no approval. Production is the
`release` branch, moved only by a fast-forward to the last commit staging
deployed, and applied only after a person approves its plan
([ADR 0008](../adr/0008-main-is-staging-release-is-production.md)).

- `deploy-staging.yml` follows every green `ci` run on `main`. It builds
  the images once, keeps the portal's and the site's builds by the commit,
  plans and applies staging, runs the smoke test, and records what it
  deployed as commit statuses (`deployed/...`).
- `release.yml`, dispatched by a person, fast-forwards `release`.
- `deploy-production.yml` runs on the push to `release`. It checks that
  `release` is an ancestor of `main`, resolves the copies staging's
  account replicated into production's and compares them with the
  recorded digests
  ([ADR 0024](../adr/0024-what-staging-hands-production-is-recorded-and-verified.md)),
  plans, waits for the `production` environment's reviewer, and applies
  exactly the approved plan.

## One account and one credential per job

Each environment has an AWS account of its own, named in
[deployment/cloud/environments.json](../../deployment/cloud/environments.json)
([ADR 0021](../adr/0021-each-environment-has-an-aws-account-of-its-own.md)).
Each role trusts one GitHub environment on one branch
([ADR 0013](../adr/0013-each-environment-has-its-own-deploy-credential.md)):

| Role | Account | Job declares | Branch | May |
|------|---------|--------------|--------|-----|
| `acme-build-staging` | staging | `staging-build` | `main` | push images, keep builds |
| `acme-deploy-staging` | staging | `staging` | `main` | apply staging |
| `acme-plan-production` | production | `production-plan` | `release` | read production, plan it |
| `acme-deploy-production` | production | `production` | `release` | apply production |

The repository issues GitHub's immutable OIDC subject
(`use_immutable_subject` in the repository's OIDC settings), so the
subject carries the owner's and the repository's ids beside their names,
and a renamed or recreated repository never matches it. A role that
expects the name-only form refuses every job with `AccessDenied`.

Each GitHub environment holds `AWS_ROLE_ARN` and `TF_STATE_BUCKET` under
the same names. `staging` also holds `API_DOMAIN_NAME`,
`APP_DOMAIN_NAME`, `ALARM_EMAIL`, and optionally `SITE_DOMAIN_NAME` and
`PORTAL_SENTRY_DSN`. No AWS secret is stored in GitHub.

## Create an environment

The organization, the two accounts, Identity Center, the local profiles,
and the Cloudflare token come first, by hand, once:
[deployment/cloud/first_time_manual.md](../../deployment/cloud/first_time_manual.md).

`scripts/cloud_create.sh <staging|production>` does the rest, under the
environment's administrator profile (`acme-staging-admin`,
`acme-prod-admin`). It takes `OWNER_EMAIL`, `ALARM_EMAIL`, and
`CLOUDFLARE_API_TOKEN`, and `--dry-run` prints every command and runs
none. The `ops-cloud-deployment-create` skill narrates it.

Run it three times: staging, production, then staging again. The second
staging run turns on the replication into production, whose bucket must
exist first. Each run:

1. Checks who it is (`aws sts get-caller-identity`, `gh auth status`).
2. Applies the account's bootstrap root: the state bucket, the registry,
   the OIDC trust, the deploy roles, the investigate role, the budget,
   the anomaly monitor, and a hosted zone per public name.
3. Delegates each public name at Cloudflare.
4. Appends the investigate profile to `~/.aws/config`.
5. Creates the GitHub environments and sets their variables.
6. Writes `~/.config/acme/ops/<environment>.env`, mode 600, with empty
   token lines.
7. Starts the first deploy through the pipeline.
8. Prints the provider secrets to write
   ([providers/workos.md](providers/workos.md)) and the grants to
   dispatch (below).

A release is a commit merged to `main` after the second staging run:
replication copies only what staging pushes once it is on.

## Grant an operator

The allowlist of a deployed environment changes through one workflow,
dispatched by a person on the environment's branch. It runs
`acme-api grant-operator` as a one-off task under the deploy role;
production's run waits for the same reviewer as its apply.

```bash
gh workflow run grant-operator.yml --ref main -f environment=staging \
  -f email=<email> -f permission=read               # or write
gh workflow run grant-operator.yml --ref main -f environment=staging \
  -f email=<email> -f permission=none -f disable=true
gh workflow run grant-operator.yml --ref main -f environment=staging \
  -f email=<provisioner> -f permission=none -f mint_token=provisioner
```

Production's runs take `--ref release -f environment=production`. The
identity signs up first, like any person. A person then enrols a second
factor and writes a token ([operator.md](operator.md)). The provisioner
is granted `write`, and its token is minted before each traffic run. The
smoke identity is granted `read` and named in the environment:

```bash
gh variable set SMOKE_EMAIL --env staging --body <smoke identity>
```

## The smoke test

Every deploy runs `scripts/cloud_smoke.sh` after the rollout. It mints
the smoke identity's token through the grant task and calls
`GET /v1/admin/me` through the edge. While `SMOKE_EMAIL` is empty the
step is skipped with a notice. A staging run whose smoke failed is not
one `release.yml` takes. To read one request's signals by hand:

```bash
id=$(curl -s -o /dev/null -D - "https://<api host>/v1/me" \
  | awk 'tolower($1) == "x-request-id:" { print $2 }' | tr -d '\r')
uv run acme-ops signals check --env staging --request-id "$id"
```

## Cut a release

1. Pick the commit: its `deploy-staging` run is green, smoke included,
   and staging looks right.
2. Actions, `release`, Run workflow on `main`. The summary lists the
   commits `release` gains.
3. `deploy-production` starts. Its `guard`, `resolve`, and `plan` jobs
   run, and the run waits at the apply with "Review deployments".

## Approve a plan

Read the `plan` job's summary before approving:

- The `Plan:` counts match what the release meant. A destroy of a
  database, a cache, or a bucket is never routine: reject and look.
- The images are the digests `resolve` named.
- Nothing that holds data is replaced (`-/+`). The replacement of
  `terraform_data.pre_rollout` is the migration; it appears when the
  release changes a file `deployment/migration-inputs.json` names.

Approve, and the apply runs the migration as a one-off task on the new
image, rolls the services, waits until they serve, and publishes the
static builds. Reject, and nothing is applied: a fix is a new commit on
`main` and another release.

## Roll back

Neither path moves `release` backwards.

- **The fast rollback, to the previous release only.** For a release
  that is healthy but wrong. The previous release is the newest commit
  of `release` before its tip whose `released/production` status is a
  success.

  ```bash
  gh workflow run deploy-production.yml --ref release -f rollback_to=<previous release commit>
  ```

  After the same approval, the `rollback` job swaps each service back to
  the digest production ran for that release and publishes that
  release's builds. It runs no migration and plans no Terraform: the
  previous release runs on the newer schema, which expand and contract
  allows. Any other commit is refused.
- **A revert through `main`.** The rollback of record, and the only one
  for anything older. A revert never removes a migration that ran: the
  schema rolls forward with a new one.

## A failed migration stops the rollout

The migration runs before either service rolls. It waits 5 seconds at
most for a lock (`ACME_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS`). One
that gives up applies nothing of its role and exits 75, and the task runs
again, three runs in all
([ADR 0071](../adr/0071-a-migration-waits-briefly-for-a-lock.md)).
Any other failure, or a third 75, fails the apply at
`terraform_data.pre_rollout`, and the old tasks keep serving. Read the
task's log in `/acme/<environment>/api`, fix on `main`, and release
again: `acme-api migrate --all` resumes where the chain stopped.

## The protection on `release`

A ruleset on `release`, which the production create run sets (its step
5c): restrict creations, updates, and deletions; block force pushes;
require the `no pull request into release` check. Its one bypass actor
is a deploy key with write access, and since a ruleset cannot name one
key, every write deploy key passes: the run refuses while any but
`release` exists.

`release.yml` pushes with that deploy key, whose private half the run
stores as the `RELEASE_DEPLOY_KEY` secret without printing it, and that
push starts `deploy-production`. A key without the secret, or the secret
without the key, is made again on the run's next pass. Without the
secret, the job pushes with its own token, which works only while no
ruleset restricts `release`, and dispatches `deploy-production` itself.

The `production` environment carries the owner as its required
reviewer. `staging` and `production-plan` carry no rule: a reviewer on
the plan would hold the plan the reviewer is meant to read.

## Nuke

`scripts/cloud_nuke.sh <environment>` destroys an environment from the
commit it runs, under its administrator profile. `--dry-run` prints
every command. Production also needs `--confirm production`, and a
released `database_deletion_protection = false`; its final snapshot and
automated backups stay. After the destroy it removes what the state
does not hold: the secrets the application wrote (a tenant's stay while
the final snapshot does) and the leftovers AWS made for the destroyed
resources. A run that stopped after its destroy is finished by running it again.
It prints what remains: the bootstrap root, the state, the static builds,
the profile, and the env file.

## When it fails

- **"Error acquiring the state lock" after a killed run.** Once that run
  is certainly gone, dispatch `state-unlock.yml` on the environment's
  branch with the `lock_id` from the log.
- **`resolve` refuses a missing or different `deployed/` status.** Staging
  never deployed that commit, or the copy changed after it did. The
  second is an incident: do not release until the cause is known.
- **`resolve` finds no image for the commit.** Staging built it before
  replication was on, or never built it. Release a newer commit, or rerun
  `deploy-staging` on it.
- **`deploy-staging` skipped every cloud job.** The `staging`
  environment's variables are empty: run `scripts/cloud_create.sh
  staging` again.
- **`guard` says `release` is not an ancestor of `main`.** Someone
  committed to `release`. An admin turns the `release` ruleset off for
  the reset (Settings, Rules, Rulesets), resets it
  (`git push --force origin <main commit>:release`), turns the ruleset on
  again, then dispatches `release` again.
- **`guard` says `production` has no required reviewer.** Add it; the
  run refused to plan.
- **`apply` says the saved plan is stale.** Someone applied in between.
  Rerun for a fresh plan.
- **"Not authorized to perform sts:AssumeRoleWithWebIdentity".** The job
  lost its `environment:` line, runs on another branch, or the
  environment was renamed.
- **`release.yml`'s push is refused by the ruleset.** Set
  `RELEASE_DEPLOY_KEY` as above.
