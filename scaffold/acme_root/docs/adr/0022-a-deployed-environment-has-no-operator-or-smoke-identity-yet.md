# ADR 0022: A deployed environment has no operator or smoke identity yet

**Status**: closed (2026-09-22) by
[ACME-53](https://tracker.example/issue/ACME-53). The grant job and the
smoke identity exist; see Closed below. The record stays for the
interval it covers.

## Context

Guideline v0.28.0 adds two rules that need an identity inside a
deployed environment's database.

- "Migrating a Deployed Database" (`DEL-45`): the first operator of a
  deployed environment is granted by a one-off task on the deployed
  image, the same kind of task that runs the migration.
- "The Telemetry Round Trip" (`OPS-22`): the deployed smoke test signs
  in as an identity and a tenant named for it, and never as a real
  tenant's user.

Acme runs the migration as that one-off task already (the service
module's `pre_rollout.sh`). It has no command that grants an operator
on a deployed database, and so no smoke identity either. The seed is
local only, and a deployed environment is entered through sign-up.

## Decision

Neither rule is met yet. The deployed smoke test the create script
prints is one unauthenticated request, `GET /v1/me`, answered `401`
with a request id, followed by the investigator's read of that
request's signals. It fails nothing on purpose and signs in as no one,
so it touches no tenant. What it cannot exercise is an authenticated
path.

ACME-53 closes both: a `grant-operator` subcommand beside `bootstrap`
and `add-member`, run as a one-off task from a workflow under the
environment's deploy role. The smoke identity and its tenant are made
the same way.

## Consequences

- Until ACME-53, the operator-plane skills (`ops-root-cause`, the
  traffic generator's provisioner) have no identity in a deployed
  environment, and they work against the local stack only.
- The smoke test proves the edge, the logs, the metric, and the trace
  of one request. It does not prove a sign-in.

## Closed

Both rules hold now.

- `acme-api grant-operator` grants an operator `read` or `write`,
  disables one, or mints a token for the provisioner or the smoke
  identity into `acme-<env>-provisioner-token` or
  `acme-<env>-smoke-token`, never printing it. It runs as a one-off
  task on the grant task definition, which holds the runtime and system
  logins' URLs and `PutSecretValue` on those two secrets alone.
  `grant-operator.yml` starts it, dispatched by a person on the
  environment's branch under the deploy role; production's run waits
  for the same approval as its apply. `bootstrap --operator` is local
  only.
- The smoke test signs in. After each rollout `scripts/cloud_smoke.sh`
  mints the smoke identity's read token through the grant task and
  calls `GET /v1/admin/me` with it through the edge. It skips, with a
  notice, until the environment's `SMOKE_EMAIL` names an identity a
  grant made.

`docs/runbooks/deploy.md` says how to grant each of the three.
