# ADR 0082: A deploy asks a person once, and the long-lived branches are never deleted

**Status**: accepted (2026-10-04)

## Context

DEL-38 holds production's apply until a person approves its plan, and
OPS-28 puts the reviewer on the environment of production's apply. The
deploy role trusts that environment's subject
([ADR 0013](0013-each-environment-has-its-own-deploy-credential.md)), so
the reviewer gates the credential itself.

The repository host asks once for each job that declares an environment
with a required reviewer. Its token carries no claim that a person
approved. So an approval in a job of its own, ahead of a job that holds
the credential in an environment with no reviewer, would leave that
credential to any job that declares the environment.

Some gates hold no credential of their own environment, such as a
publish with the run's own token. They need an approval all the same.

## Decision

- **One job per run waits, and it is the job that holds the credential.**
  In `deploy-production.yml` that is `apply` on a release and `rollback`
  on a rollback. Its own wait is the approval, never a job of its own
  before it. The smoke steps stay in `apply` for that reason.
- **Every job that declares `production` runs the rule check first,**
  before its credential: the check reads the environment's protection
  rules and refuses when there is no required-reviewers rule, since
  without it nothing waits. The guard runs it for `apply` and
  `rollback`; `grant-operator.yml` and `state-unlock.yml` run it
  in their job.
- **`human-approval.yml` is the reusable approval step,** for a gate
  whose critical job holds no credential of its own environment. A job
  that `needs:` a call of it runs only after a reviewer of the named
  environment (`human-approval` by default) approved the run. Its rule
  job runs the check before anything waits. Every copy of the check is
  its own, word for word.
- **`main`, `release`, and `scaffold` are never deleted and never
  rewritten.** `scripts/branch_rulesets.sh` sets a ruleset on each that
  blocks a deletion and a force push, with no bypass actor. It sits
  beside the rulesets that hold how `main` and `release` move.

## Consequences

`infra/tests/test_cloud_scripts.py` holds the rule: in each mode of
`deploy-production.yml`, exactly one job declares a reviewer
environment and it assumes the deploy role; every job that declares
`production` runs the check before its credential; and the check refuses
an environment with no reviewer.

A private repository can have the required-reviewers rule only under
GitHub Enterprise, so on Free, Pro, or Team its production deploy
refuses every run, and the check's error says why.

A reset of `release` turns off both of its rulesets for that push (the
deploy runbook). A rollback is its own run, and it asks once.
