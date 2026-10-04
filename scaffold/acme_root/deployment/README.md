# Deployment

Acme runs in two places, and the two are twins: the same processes, the
same backing services behind the same interfaces, the same signals. One
runs on a laptop from compose files. The other runs in AWS from
Terraform. Nothing is clicked into place in either.

## What is here

| Path | What it holds |
|------|---------------|
| `local/` | The compose stack: the backing services, the app containers, and the developer dashboards. [local/README.md](local/README.md) |
| `docker/` | One Dockerfile per process (`api`, `maintenance`, `portal`): two stages, a non-root user, a liveness probe |
| `terraform/` | The bootstrap roots (one per AWS account), the environment roots (staging, production), and the modules they call. [terraform/modules/README.md](terraform/modules/README.md) |
| `cloud/` | `environments.json`, the values of one deployment; the first-time AWS setup; the sizes and what they cost. [cloud/README.md](cloud/README.md) |
| `workos/environments.yaml` | The desired state of the identity provider's application per WorkOS environment, which `acme-ops workos-bootstrap` reconciles |
| `migration-inputs.json` | The files whose change makes a deploy run the migration |
| `realtime-timeouts.json` | The idle and keep-alive timeouts the load balancer, the CDN, and the realtime socket share |

## What runs where

| Piece | Locally (compose) | In the cloud (Terraform) |
|-------|-------------------|--------------------------|
| The API | the `api` container, or a host process (`scripts/dev.sh`) | a container service behind a load balancer |
| The maintenance worker | the `maintenance` container, or a host process | a container service that rolls one task at a time, since it holds leases |
| The portal | nginx in a container, or Vite on the host; each forwards `/v1` to the API | a private bucket behind CloudFront, which serves `/v1` from the load balancer too |
| The company site | Vite on the host | the portal's module, called with the site's parameters |
| Postgres | one container, one schema per database role | a managed instance |
| Valkey (cache, topics) | one container | a managed cluster, encrypted in transit |
| Queues (`webhooks`) | ElasticMQ over the SQS API | SQS, with a dead-letter queue each |
| Buckets | MinIO over the S3 API | S3, private and versioned |
| Secrets | `.env` | Secrets Manager |
| Logs, metrics, traces, errors | Prometheus, Grafana, Jaeger, GlitchTip (the `devx` profile) | CloudWatch, X-Ray, a Sentry-compatible tracker, through a collector sidecar per task |
| Alarms and the dashboard | Grafana's provisioned overview | one CloudWatch dashboard per environment, fifteen alarms to one topic |

## The convention

`main` is staging and `release` is production. That is a choice, and the
one this repository makes.

- A merge to `main` runs the checks, builds the images once, and applies
  staging with no approval.
- `release` moves by a fast-forward from `main` that a person dispatches.
  A push to it plans production, waits for a reviewer's approval of that
  plan, and applies it. Nothing is rebuilt: production runs what staging
  built.
- The migration runs inside the apply, before the services roll. A
  migration that fails fails the apply, and the old tasks keep serving.
- Each environment has an AWS account and credentials of its own.
  Production has two: one reads and plans, one applies.
- Both environments are one graph with different numbers. A new
  environment is another root, never a copy.
- `main`, `release`, and `scaffold` are never deleted and never
  rewritten: `scripts/branch_rulesets.sh` sets a ruleset on each.

The steps, the rollback, and what to check at the approval are in
[the deploy runbook](../docs/runbooks/deploy.md).

## A deployment of your own

1. Set the deployment's values: every placeholder in
   `cloud/environments.json`, `workos_client_id` in each
   `terraform/environments/<env>/variables.tf`, and the owners in
   `.github/CODEOWNERS`.
2. Set up the AWS organization, the accounts, and the profiles once, as
   [cloud/first_time_manual.md](cloud/first_time_manual.md) says.
3. Run `scripts/cloud_create.sh staging --dry-run`, then without
   `--dry-run`. A real run refuses while a placeholder is left, and names
   it.
