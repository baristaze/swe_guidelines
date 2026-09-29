# ADR 0008: `main` is staging, `release` is production

**Status**: accepted (2026-09-19)

## Context

The guideline (Cloud: AWS) says the smaller environment is staging and
it is `main`: every merge deploys it with no approval, so a merge is
the deployment. Production is the `release` branch, moved only by a
fast-forward from `main`, so its history is a prefix of `main`'s and a
release is a `main` commit that has run on staging; a push to
`release` plans production, waits for a person's approval on that
plan, and applies it, promoting the digests and the bundle staging
built for that commit, and a commit staging never built is refused.
Nobody pushes to `release` but the fast-forward, and a production
deploy checks that `release` is an ancestor of `main` before it plans.

Before this record one workflow did both: it paused for an approval
before the smaller environment's apply and again before production's,
in the same run, so every merge to `main` needed a person twice and
production could only ever be the commit that had just merged.

## Decision

The convention is adopted as written. `deploy-staging.yml` follows
`ci` on `main` and applies with no approval; `release.yml` is the one
way `release` moves; `deploy-production.yml` refuses a `release` tip
that is not on `main`, refuses a commit staging never built, and
applies exactly the plan a reviewer approved, behind the `production`
environment's required reviewer, which the workflow checks is in place
before it plans.

Two choices the guideline leaves open are made here. The portal build
is kept by the commit in the state bucket, not as a workflow artifact:
an artifact expires and belongs to one run, and a public repository's
artifacts are downloadable by anyone. The migration runs inside the
apply, on the new image, before the services roll: the service module
runs it as a one-off task the service depends on, so it precedes the
roll under a saved plan too, and a failure ends the apply with the old
tasks still serving. The alternative, a workflow step between plan and
apply, cannot run the new image before the new task definition exists
without a second plan.

## Consequences

A rollback is a release of an earlier `main` commit: a force-push of
`release` by hand, since the workflow only fast-forwards, and then the
same approval. Every migration must be compatible with the release
before it (expand and contract), because the old tasks serve the new
schema until the roll ends and an earlier release runs against a newer
one after a rollback; ADR 0006's one-step renames end with the first
deployment. The protection on `release` and the reviewer on
`production` are settings outside the repository, listed in the deploy
runbook; the production workflow fails when the reviewer rule is
missing, and a pull request into `release` fails its check.
