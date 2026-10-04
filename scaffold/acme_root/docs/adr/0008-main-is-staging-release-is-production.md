# ADR 0008: `main` is staging, `release` is production

**Status**: accepted (2026-09-28)

## Context

The guideline (Cloud: AWS) makes the smaller environment staging, and
staging is `main`: every merge deploys it with no approval. Production
is the `release` branch, moved only by a fast-forward from `main`, so a
release is a `main` commit that has run on staging. A push to `release`
plans production, waits for a person's approval of that plan, and
applies it, promoting what staging built for that commit. A commit
staging never built is refused.

## Decision

The convention is adopted as written.

- `deploy-staging.yml` follows `ci` on `main` and applies with no
  approval.
- `release.yml` is the one way `release` moves. It fast-forwards
  `release` to the last commit staging deployed.
- `deploy-production.yml` refuses a `release` tip that is not on
  `main`, refuses a commit staging never built, and applies exactly the
  plan a reviewer approved. The reviewer is the `production`
  environment's required reviewer, and the workflow checks the rule is
  in place before it plans.

Two choices the guideline leaves open are made here.

- **The builds are kept by commit in the artifacts bucket.** The portal
  and the company site are built once on staging and kept under the
  commit in staging's artifacts bucket, which replicates them into
  production's ([ADR 0021](0021-each-environment-has-an-aws-account-of-its-own.md),
  [ADR 0024](0024-what-staging-hands-production-is-recorded-and-verified.md)).
  A workflow artifact is refused: it expires, it belongs to one run, and
  a public repository's artifacts are downloadable by anyone.
- **The migration runs inside the apply.** It runs on the new image as a
  one-off task the services depend on, so it precedes the roll under a
  saved plan too, and a failure ends the apply with the old tasks still
  serving. A workflow step between the plan and the apply cannot run the
  new image before the new task definition exists, short of a second
  plan.

## Consequences

Every migration is compatible with the release before it (expand and
contract): the old tasks serve the new schema until the roll ends, and
an earlier release runs against a newer schema after a rollback.

A rollback to the previous release is the fast rollback of ADR 0024.
Anything older rolls forward through a revert on `main`.

The protection on `release` and the reviewer on `production` are
settings outside the repository, listed in the deploy runbook. The
production workflow fails when the reviewer rule is missing, and a pull
request into `release` fails its check.
