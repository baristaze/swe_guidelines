# Terraform

Every cloud resource is declared here. Three kinds of root live under this
folder:

- `bootstrap/<staging|prod>/`: one per AWS account. It calls the `account`
  module and adds the roles its deploy workflow assumes, each trusting one
  GitHub subject. Staging's replicates every image and static build into
  production's account, and production's grants those two writes and
  nothing else. `scripts/cloud_create.sh` applies it under the account's
  administrator profile; a deploy never does.
- `environments/<staging|prod>/`: one per environment. A root is thin: its
  backend, its providers pinned to its account, the WorkOS client id, and
  one call to the `environment` module with its numbers. The deploy
  workflows pass the image digests and the public names.
- `modules/`: one module per resource family, each with `versions.tf`,
  `variables.tf`, `main.tf`, and `outputs.tf`.

| Module | Declares |
|--------|----------|
| `environment` | One environment whole: every module below, wired. The only module a root calls |
| `account` | An account before its first deploy: the registry, the state and artifacts buckets, the OIDC provider, the task boundary, the investigate role, the budget, the API's and the portal's hosted zones, the company site's certificate |
| `deploy_role` | A deploy role: its OIDC trust and its fences |
| `investigate_role` | The read-only role an agent investigates under |
| `network` | VPC, subnets, NAT, security groups |
| `cluster` | The container cluster |
| `database` | Postgres and its generated master password |
| `cache` | Valkey, encrypted in transit |
| `queue` | One SQS queue and dead-letter queue per `acme.infra.queues.Queues` member |
| `buckets` | One private versioned bucket per `acme.infra.buckets.Buckets` member |
| `secrets` | The four database URLs, the TOTP key, the edge secret, the error tracker's DSN, the WorkOS API key and webhook secret, the operator token secrets, the application secrets policy |
| `load_balancer` | The API's load balancer: HTTPS, and HTTP redirects |
| `static_site` | A private bucket behind CloudFront, under security headers; called for the portal and for the company site |
| `certificate` | A DNS-validated certificate for one name |
| `domain_records` | The API's and the portal's alias records |
| `service` | One process: task definition with a collector sidecar, service, autoscaling, and the migration before a rollout (`pre_rollout.sh`) |
| `task` | One one-off task (migrate, grant), run by `aws ecs run-task` |
| `alarms` | The default alarm set, to one SNS topic |
| `dashboard` | The CloudWatch dashboard, the twin of the local Grafana one by panel title |

## How a change reaches an environment

The API's service runs the migration in a one-off task before it rolls:
`acme-api migrate ensure-logins`, then `acme-api migrate --all`. It runs
when the files `deployment/migration-inputs.json` names change, or the
database, its password version, or the migrate task's secrets do; a task
that exits 75 (a lock not granted in time) runs again, three runs in all.
A step that fails ends the apply with the old tasks serving. The worker
rolls after the migration, one task at a time. Every service waits until
its new tasks serve, so a rollout ECS rolls back fails the apply.

The portal and the company site are built once, by `deploy-staging.yml`,
kept by commit in the artifacts bucket, replicated into production's, and
published with `scripts/deploy_static.sh`. Terraform writes the portal's
`/config.json` per environment. CloudFront serves the portal's `/v1/*`
from the load balancer, so the page calls the API on its own origin; the
API's own name serves the providers' deliveries, the command line, and
the operators.

## State and credentials

Every root has an empty `backend "s3"` block and takes bucket, key, and
region as `-backend-config` arguments:

```bash
terraform -chdir=deployment/terraform/environments/staging init \
  -backend-config="bucket=acme-state-<account>" \
  -backend-config="key=environments/staging/terraform.tfstate" \
  -backend-config="region=<region>" \
  -backend-config="use_lockfile=true"
```

Each account has its own state bucket, `acme-state-<account>`, and every
resource's name is made from the product's, `acme`, and the environment's.
A bootstrap root makes its bucket
itself: it applies once with local state, then moves the state in. No
root holds credentials: a person's Identity Center profile or a deploy
role's OIDC session provides them.

## Domains

`deployment/cloud/environments.json` names each environment's three public
names. The API's and the portal's each get a Route 53 zone of their own,
delegated from the domain's zone at Cloudflare by the create run. The
company site's name is a record in the Cloudflare zone, since the apex
cannot be delegated; the create run writes it and its certificate's
validation record. The site is optional: with no `site_domain_name` the
rest plans and applies unchanged. The order of the runs is in
[the first-time manual](../../cloud/first_time_manual.md#cloudflare-token-for-the-delegation-and-the-sites-records).

## Secrets set by hand

Terraform creates each provider secret as `off` and never writes it
again. `off` leaves that provider unconfigured, and the processes stay
healthy. Write the real value once, then roll the services, since a task
reads its secrets at start:

```bash
aws secretsmanager put-secret-value \
  --secret-id acme/staging/workos_api_key --secret-string '...'
aws ecs update-service --cluster acme-staging --service api --force-new-deployment
```

The secrets are `workos_api_key`, `workos_webhook_secret`, and
`sentry_dsn`. The DSN is the product's one tracker project, the same in
every environment; each event carries its environment. The portal's DSN
is `portal_sentry_dsn`, which the deploy workflows pass from the
`PORTAL_SENTRY_DSN` variable of the GitHub environment.

An org's own secrets live under `acme/<environment>/app/org/<org_id>/`.
The serving tasks may create, replace, and delete there and nowhere else.

## Checks

CI runs `terraform fmt -check -recursive` over this folder, `init
-backend=false` and `validate` in every root, and `terraform test` in
`modules/static_site`, `modules/service`, `modules/dashboard`,
`modules/alarms`, and `environments/staging`, all offline under mock
providers. `infra/tests/test_dashboard_parity.py` holds the cloud
dashboard's titles equal to the local Grafana dashboard's.
