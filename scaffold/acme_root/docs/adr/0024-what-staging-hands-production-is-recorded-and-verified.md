# ADR 0024: What staging hands production is recorded outside the cloud and verified

**Status**: accepted (2026-09-28)

## Context

Staging is the less trusted account. Every merge deploys it with no
approval, and its build jobs run third-party install scripts.
Production releases what staging replicates into its account
([ADR 0021](0021-each-environment-has-an-aws-account-of-its-own.md)).
So production needs a record of what staging built that staging's
account cannot rewrite, and a check of every copy against it.

## Decision

- **Build and apply are two credentials.** `acme-build-staging` pushes
  the images and keeps the static builds, and applies nothing. It trusts
  the `staging-build` GitHub environment on `main`, which no job that
  applies declares. `acme-deploy-staging` applies and pushes nothing.
- **What staging deployed is recorded on the repository host.** After a
  successful apply, `deploy-staging.yml`'s `record` job writes commit
  statuses: `deployed/<image>` for each image, `deployed/portal`, and
  `deployed/site`, each a digest. It installs nothing, and it is the
  only job of the run with `statuses: write`.
- **Production verifies before it plans.** `resolve` compares each image
  in production's registry, and a fresh download of each static build
  (`scripts/build_digest.sh`), with the recorded digests. It refuses a
  missing record or a mismatch. `apply` compares again before it
  publishes.
- **Only replication writes a kept build.** Production's artifacts
  bucket refuses every direct write and delete under `builds/`, the
  administrator's included. The bucket is versioned, with no object lock
  and no pick of a version by its hash, so a changed source object still
  replicates as a new version. The recorded digest catches it, and
  `resolve` refuses it.
- **The fast rollback is DEL-50's: the previous release, and only that
  one.** `deploy-production.yml` takes an optional `rollback_to`: the
  newest commit of `release` before its tip whose `released/production`
  status is a success. Behind the same approval it swaps each service's
  image back to that release's digest and publishes that release's
  builds. It runs no migration, plans no Terraform, and never moves
  `release`. Anything older rolls forward through a revert on `main`.
  Every release's apply writes `released/production` once the release
  answers ready.
- **Every trust matches the repository by id.** Each deploy, plan, and
  build role matches `repository_id` and `repository_owner_id` beside
  the subject and the ref, so a renamed repository's name cannot be
  claimed.
- **Break-glass for a stale state lock.** `state-unlock.yml` removes a
  lock by its id, under the environment's own deploy role, behind the
  same gate its deploy has.
- **Audit and transport.** One CloudTrail trail per account, every
  region, with log file validation, into a bucket of its own. Every
  database URL requires TLS (`ssl=require`).
- **`main` takes a merge only through a pull request whose checks
  passed.** The checks are strict: the branch is up to date with `main`,
  so they ran on its merge with the current `main`. The create script
  sets that ruleset, and `.github/CODEOWNERS` names the owners of
  `deployment/`, `.github/`, and the cloud scripts.
- **The release push is a deploy key's.** `release.yml` pushes the
  fast-forward with the `RELEASE_DEPLOY_KEY` secret when it is set: a
  deploy key with write access, listed as the one bypass actor of the
  ruleset that locks `release`, which the production create run
  sets. Without it, the push uses the workflow's
  own token. That works only while no ruleset restricts `release`, and
  such a push fires no workflow, so the job dispatches
  `deploy-production.yml` itself.

## Deviations

- **DEL-38** asks for the repository host's app as the one actor that
  pushes to `release`, its key held in an environment that admits `main`
  alone, and calls a deploy key a violation. The tree takes the deploy
  key, whose setup is one secret and one ruleset entry. It moves to an
  app when the repository belongs to an organization that runs one.
- **DEL-31** asks for a locked, versioned bundle prefix and a read of
  the version whose hash matches the record. The tree refuses direct
  writes and compares the digest instead. A changed copy is refused
  before any plan either way; what the lock would add is that the
  changed version never lands.

## Positions, chosen and not missed

- **No WAF.** The edge is a load balancer and CloudFront, and the rate
  limits live in the API and fail open
  ([ADR 0059](0059-authenticated-routes-have-limits.md)). A WAF is the
  answer to abusive traffic the budget notices first.
- **No GuardDuty or Security Hub.** They cost per account per month, and
  the trail is there to read. They come with the first customer's data.

## Consequences

- A release needs a commit that staging deployed while replication is
  on, since only such a commit carries `deployed/` statuses and copies.
- A new image repository or a changed build permission is a bootstrap
  change: the administrator runs the create script again for staging,
  and for production when the registry changes.
- Rotating a database password rolls every service, because the secret
  version is in the task definition. Between the change and the roll,
  running tasks fail their next new connection, so a rotation belongs at
  a quiet hour.
