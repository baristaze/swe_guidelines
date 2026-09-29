# ADR 0024: What staging hands production is recorded outside the cloud and verified

**Status**: accepted (2026-09-22), amended (2026-09-22): the fast
rollback is the previous release only, as `DEL-50` says.

## Context

Staging is the less trusted account. Every merge deploys it with no
approval, and its build jobs run third-party install scripts. Production
releases what staging replicates into its account. The image tags are
immutable in both registries, but three things were missing:

- The build jobs held staging's applying role, so a build step could apply.
- Nothing outside staging's account recorded what staging built.
- A portal build under a commit's prefix could be replaced by a new version
  after staging deployed it.

## Decision

- **Build and apply are two credentials.** `acme-build-staging` pushes
  the images and keeps the portal build, and applies nothing. It trusts
  the `staging-build` GitHub environment on `main`, which no job that
  applies declares. `acme-deploy-staging` applies and pushes nothing.
- **What staging deployed is recorded on the repository host.** After a
  successful apply, `deploy-staging.yml`'s `record` job writes three commit
  statuses: `deployed/acme-api`, `deployed/acme-maintenance`, and
  `deployed/portal`, each a digest. It installs nothing, and it is the only
  job with `statuses: write`. A commit with these statuses is a commit
  staging deployed.
- **Production verifies before it plans.** `resolve` compares each image
  in production's registry, and a fresh download of the portal build
  (`scripts/build_digest.sh`), with the recorded digests. It refuses a
  missing record or a mismatch. `apply` compares again before it
  publishes.
- **The fast rollback is `DEL-50`'s: the previous release, and only
  that one.** `deploy-production.yml` takes an optional `rollback_to`:
  the newest commit of `release` before its tip whose
  `released/production` status is a success. Behind the same approval,
  it swaps each service's image back to the digest production ran for
  that release and publishes that release's portal build. It runs no
  migration, plans no Terraform, and never moves `release`. Anything
  older rolls forward through a revert on `main`. Every release's apply
  writes `released/production` once the release answers ready.
- **Every trust matches the repository by id.** Each deploy, plan, and
  build role matches `repository_id` and `repository_owner_id` beside the
  subject and the ref, so a renamed repository's name cannot be claimed.
- **Break-glass for a stale state lock.** `state-unlock.yml` removes a
  lock by its id, under the environment's own deploy role and behind the
  same gate its deploy has.
- **Audit and transport.** One CloudTrail trail per account, every region,
  with log file validation, into a bucket of its own. The database URL
  requires TLS (`ssl=require`).
- **Main takes a merge only through a pull request whose checks passed.**
  The checks are strict: the branch is up to date with `main`, so they
  ran on its merge with the current `main`. The create script sets that
  ruleset, and `.github/CODEOWNERS` names the owner of the deployment
  paths.

Three positions, chosen and not missed:

- **No WAF.** The edge is a load balancer and CloudFront; the rate limits
  live in the API and fail open, as "The Gateway" says. A WAF is the
  answer to abusive traffic the budget notices first.
- **No GuardDuty or Security Hub.** They cost per account per month, and a
  demo platform has CloudTrail to read. They come with the first customer.
- **The release push stays on the workflow token.** No ruleset restricts
  who may push to `release`; the one on every branch forbids force pushes
  and deletion, so the fast-forward is allowed and the workflow dispatches
  the production deploy itself. The guideline's GitHub App as the one
  bypass actor is the shape for a repository that locks `release`. Acme
  adopts it when it does.

## Consequences

- The first release waits for a staging deploy made after this change,
  because an older commit carries no `deployed/` statuses.
- A new image repository or a changed build permission is a bootstrap
  change: the administrator runs the create script again for staging, and
  for production when the registry changes.
- Rotating the database password rolls every service, because the version
  is in the task definition. Between the password change and the roll,
  running tasks fail their next new connection, so a rotation belongs at a
  quiet hour.
