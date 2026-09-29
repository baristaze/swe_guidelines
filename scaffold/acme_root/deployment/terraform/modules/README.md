# Terraform

Every cloud resource is declared here; nothing is clicked into place.
Three kinds of root live under this folder:

- `environments/<name>/`: one root per environment (`staging`, `prod`;
  the process reads the name as `ACME_ENVIRONMENT`, `staging` or
  `production`). A root is thin: its backend, its providers, and one call
  to the `environment` module with its parameter set. The graph lives in
  the module, so a resource is added in one place and the environments
  cannot drift; a new environment is another root, never a copy. Both
  take the image digests as variables: `deploy-staging.yml` passes what
  it built, `deploy-production.yml` the digests it resolves from the
  registry by the commit `release` points at. Each pins its provider to
  its environment's account.
- `bootstrap/<name>/`: one root per environment's AWS account (`staging`,
  `prod`). Each environment has an account of its own, and
  `deployment/cloud/environments.json` names it. A bootstrap root calls
  the `account` module and adds the roles its deploy workflow assumes,
  each trusting a single subject (the deploy runbook has the table).
  Staging's also replicates every image and static build into
  production's account; production's grants those two writes and nothing
  else. `scripts/cloud_create.sh` applies it, under the account's
  administrator profile.
- `modules/`: one module per resource family, each with `versions.tf`,
  `variables.tf`, `main.tf`, and `outputs.tf`.

| Module          | Declares                                                        |
|-----------------|-----------------------------------------------------------------|
| `environment`   | One environment whole: every module below, wired                |
| `account`       | One environment's account before its first deploy: the registry, the state bucket, the OIDC provider, the task boundary, the investigate role, the budget and anomaly monitor, a hosted zone for the API's name and one for the app's, and the company site's certificate |
| `deploy_role`   | One environment's deploy role: its OIDC trust and its fences     |
| `investigate_role` | One environment's read-only role: ReadOnlyAccess plus the signal reads, fenced off secrets, data, the database, the other environment, and IAM |
| `network`       | VPC, public and private subnets, NAT, the security groups        |
| `cluster`       | The container cluster services and workers run on               |
| `database`      | Postgres, its subnet group, the generated master password       |
| `cache`         | Valkey (cache scopes and the topic bus), encrypted in transit   |
| `queue`         | One SQS queue and dead-letter queue per `Queues` member, IAM    |
| `buckets`       | One private versioned bucket per `Buckets` member, IAM          |
| `secrets`       | The four database URLs (master, migration, runtime, system), the Sentry DSN, the Slack app's client and signing secrets, the TOTP encryption key, the two operator token secrets, the application secrets policy |
| `load_balancer` | The load balancer at the API's domain name: HTTPS, HTTP redirects |
| `static_site`   | A static site's private bucket and its CloudFront distribution at one domain name, under the security headers; called twice, for the portal and for the company site |
| `certificate`   | A DNS-validated ACM certificate for one name                    |
| `domain_records`| The API's and the portal's alias records                        |
| `service`       | One process: log groups, roles, task definition with an ADOT collector sidecar, service, and its autoscaling target and policy behind the switch |
| `task`          | One one-off task (the migration, the operator grant): its log group, roles, and task definition, run by `aws ecs run-task` |
| `alarms`        | The default alarm set to one SNS topic: the edge, the reads with a latency of their own, the database, each inbound queue's backlog and dead letters, the sweep's pass duration, the work queue's backlog and dead letters and the outbox's lag and dead letters from the sweep's line, each service's task count |
| `dashboard`     | The CloudWatch dashboard, from a JSON template carrying the local Grafana dashboard's panels by title |

The `environment` module is the graph itself, and the only module a root
calls. It takes the `aws.us_east_1` provider alias as well as the default
one, because CloudFront reads certificates from that region alone. Its
inputs are the whole difference between two environments: the address
space, the name prefixes, the instance classes, the replica counts, the
database's pool size, and the database's multi-az and deletion
protection. What each set of numbers costs is in
[../../cloud/README.md](../../cloud/README.md). Reading the two module
calls side by side is how the environments are compared.

Three inputs of the `environment` module are operations rather than
scale. `alarm_email` is where the environment's alarms deliver.
`autoscaling_enabled` is the one flip: each service's lever under it
(`api_autoscaling`, `maintenance_autoscaling`: a ceiling, a CPU target,
and `enabled = true` by default) takes effect only when it is true, and
both roots declare it false (docs/runbooks/scale.md). `destroyable` is
the nuke's flag, false everywhere but on `scripts/cloud_nuke.sh`'s way
down: it lets the buckets empty on destroy and lifts the database's
protection and final snapshot.

The `service` module is instantiated once per process. A worker passes
`deployment_maximum_percent = 100` so a rollout never runs more workers
than desired, because a worker holds leases. The API passes
`pre_rollout`, the migration: the module runs
`acme-api migrate ensure-logins`, then `acme-api migrate --all`, in
one one-off task on the migrate task's new definition (`pre_rollout.sh`,
from the machine that applies, with its credentials), and the service
depends on it, so a step that fails ends the apply with the old tasks
still serving. A task that exits 75, a migration that gave up waiting
for a lock, runs again, three runs in all (ADR 0071). It runs when the release brings the database something it
lacks: the fingerprint of the files `deployment/migration-inputs.json`
names (the migrations, and the runner and logins code the command
imports), the database's resource id, the password version, or the
migrate task's secrets differ from what the last successful run recorded
in the state. A release that changes none of them rolls with no one-off
task, and a run that fails is run again by the next apply.
The worker passes the API's `rollout_gate` as `rollout_after`, so it
rolls after the migration ran. Every service waits for its new tasks to
serve (`wait_for_steady_state`): a rollout ECS rolls back fails the
apply instead of leaving it green over old tasks. The API's health-check
grace period, 150 seconds, covers a task's start on a fresh Fargate host;
its target group checks `/healthz` every 10 seconds and drains a
deregistered target for 15. Each number's reason is beside it.

The `task` module is instantiated twice, and nothing keeps either
running. The migrate task is the one place the master's URL and the
migration login's URL are injected. The grant task connects as the
runtime and system logins, and its role holds the one write on a
platform secret a task has: `PutSecretValue` on
`acme-<environment>-provisioner-token` and
`acme-<environment>-smoke-token`. The serving tasks write only the
orgs' own secrets (below). Serving tasks connect as the
runtime and system logins only. The environment root's outputs
(`cluster_name`, `grant_task_definition_arn`, `grant_container_name`,
`private_subnet_ids`, `app_security_group_id`) are what
`aws ecs run-task` needs to start one.

## State and credentials

Every root declares an empty `backend "s3"` block and receives bucket,
key, and region as `-backend-config` arguments:

```bash
terraform -chdir=deployment/terraform/environments/staging init \
  -backend-config="bucket=acme-state-<account>" \
  -backend-config="key=environments/staging/terraform.tfstate" \
  -backend-config="region=us-west-2" \
  -backend-config="use_lockfile=true"
```

The `use_lockfile` option needs Terraform 1.10 or later, which the
roots require. Each account has its own state bucket,
`acme-state-<account>`: the bootstrap root's state sits at
`bootstrap/terraform.tfstate`, the environment root's under
`environments/staging/` or `environments/prod/`. A bootstrap root creates
its bucket itself: it applies once with local state, then runs `init
-migrate-state` against the bucket it made. No root holds credentials; a
person's Identity Center profile or an environment's deploy role
provides them through its OIDC session. A bootstrap root is applied by
its account's administrator and never by a deploy run: every deploy role
denies the calls that would change the registry, its replication, the
state bucket, or the trust that issues the roles.

## Domains

Each environment has three public names:

| Input | staging | production | Where it lives |
|-------|---------|------------|----------------|
| `api_domain_name` | `api.staging.acme.example` | `api.acme.example` | a Route 53 zone of its own, delegated from Cloudflare |
| `app_domain_name` | `app.staging.acme.example` | `app.acme.example` | a Route 53 zone of its own, delegated from Cloudflare |
| `site_domain_name` | `staging.acme.example` | `acme.example` | a CNAME in the Cloudflare zone, DNS only |

The names are written once, in `deployment/cloud/environments.json`. The
domain itself is registered at Cloudflare, which keeps its zone. The
bootstrap root makes a zone for the API's name and one for the app's,
and `scripts/cloud_create.sh` delegates each there with NS records
naming its zone's servers. The create script also sets the three names
as the `API_DOMAIN_NAME`, `APP_DOMAIN_NAME`, and `SITE_DOMAIN_NAME`
variables of the environment's GitHub environments, and the deploy
workflows pass them in. The environment root finds each zone by its
name. Terraform does the rest for those two: a DNS-validated certificate
per name (the portal's in us-east-1, where CloudFront reads them), the
alias records, and the API's CORS origin, which is always the app's name
(the portal itself calls the API same-origin; see below).

The company site's name cannot be delegated. In production it is the
domain's apex, the apex of Cloudflare's own zone, where an NS record
cannot sit; in staging a delegation of `staging.acme.example` would hide
the `app.staging` and `api.staging` delegations beneath it. So its
records are in the Cloudflare zone, and only the create run writes
there, since no deploy holds the Cloudflare token. The `account`
module (the bootstrap root) requests the site's certificate in
us-east-1; the create run writes its validation record at Cloudflare and
waits for it to be issued. The environment root reads the issued
certificate by the site's name (`data "aws_acm_certificate"`). The site
is optional: `site_domain_name` defaults to empty, and with no name the
`site` module and the certificate lookup have no instance, so the rest
plans and applies unchanged. The deploy workflows pass the name only once
`SITE_DOMAIN_NAME` is set and the certificate is issued, and say so when
they leave it out; `terraform test` in `environments/staging` plans the
environment both ways. A deploy makes the distribution, and
the next create run writes the site's name as a CNAME to it, DNS only,
so CloudFront serves TLS with its own certificate; at the apex Cloudflare
flattens the CNAME. The order and which run writes which record are in
the [first-time manual, 18a](../../cloud/first_time_manual.md).

## The portal and the company site

Both are static files behind CloudFront, so both are the `static_site`
module, called twice by `environment`: one module, two parameter sets,
and the same bucket, origin access control, security headers, and
distribution for each. What differs is a handful of inputs. The portal
passes `api_domain_name` and `api_edge_secret` (its distribution serves
the API's paths), `sentry_dsn` (its Content-Security-Policy lets the page
reach the error reporter), `runtime_config` (written as
`/config.json`), and `client_routes = true`. The site passes none of
those: it reaches its own origin alone, has no config, and passes
`not_found_page = "/404.html"`, which a missing path gets with a 404.

The portal is static files: a private S3 bucket that only its CloudFront
distribution can read (origin access control), served at `app_domain_name`.
Client routes such as `/settings` get `index.html` from a CloudFront
Function; hashed assets are cached for a year; `index.html` and `config.json`
revalidate on every load.

The API's paths are behind this distribution too. `/v1/*` goes to the
load balancer at `https://<api_domain_name>`, uncached (the managed
CachingDisabled policy), with every method and every viewer header,
cookie, and query string but Host (the managed AllViewerExceptHostHeader
policy), HTTPS only, and no compression. The realtime socket is a request
under `/v1` and upgrades through the same behavior. So the portal calls
the API on its own origin, `/config.json` names an empty `apiUrl`, and no
browser sends a CORS preflight. The Content-Security-Policy's `connect-src`
is `'self'` and `wss://<app_domain_name>`, beside the object store and the
error reporter. `api_domain_name` still serves everything on its own name:
the Stripe and Slack deliveries, the command line, and the operators.

The origin's read timeout is the load balancer's idle timeout (60 seconds)
and its keep-alive timeout five seconds less, both read from
`deployment/realtime-timeouts.json`. CloudFront closes a socket that has
carried no byte from the origin for ten minutes, a fixed quota; the
server's protocol ping every 20 seconds is such a byte, and the load
balancer's 60 seconds is the tighter bound the pings are held to.

The distribution adds `X-Acme-Edge` to every request it sends the API,
with the value of the `edge_secret` the secrets module generates (in the
state, since the distribution's configuration holds it in the clear). The
API reads the same value as `ACME_EDGE_SECRET` and, beside it, takes the
address CloudFront appended to `X-Forwarded-For` as the client, so the
login rate limit counts per viewer and not per edge. Raising the secrets
module's `edge_secret_version` writes a new value to both in one apply.
The load balancer's security group is unchanged: it accepts what it
accepted, from anywhere on 443.

The build carries no environment. Terraform writes `/config.json` per
environment (`apiUrl`, `sentryDsn`, `environment`). `deploy-staging.yml`
builds the portal once, publishes it with `scripts/deploy_static.sh portal`
after the apply, and keeps the build by the commit in staging's artifacts bucket
(`acme-artifacts-<account>`, under `builds/portal/<sha>/`). The state
bucket holds state and nothing else. The artifacts bucket replicates the
build into production's, and
`deploy-production.yml` publishes those same files to production from
there. `portal_sentry_dsn`, the root's variable, turns browser error
reporting on; the deploy workflows pass it from the `PORTAL_SENTRY_DSN`
variable of the environment's GitHub environment, and an unset one
leaves reporting off. The `api_url` and `portal_url` outputs are
where an environment answers.

The company site (`apps/site`) is one page and a not-found page, HTML and
CSS with no script, served at `site_domain_name`. It calls nothing at
runtime, so it has no `/config.json`: its links (the app's sign-in, the
repository) are written into its HTML at build time, from
`deployment/cloud/environments.json`. `deploy-staging.yml` therefore
builds it once for both environments (`apps/site/dist/staging` and
`apps/site/dist/production`), keeps that one build under
`builds/site/<sha>/`, records its digest as `deployed/site`, and publishes
the staging page with `scripts/deploy_static.sh site`. A release
publishes the production page from the same replicated build, after its
digest matches. The `site_url` output is where the site answers.

## Telemetry and error reporting

Each task's collector sidecar scrapes the process's `/metrics` over
localhost every 30 seconds into CloudWatch metrics (namespace `Acme`,
dimensions `service`, `environment`, the metric's own labels, and the
exporter's `OTelLib`) and
forwards the traces the process sends to `127.0.0.1:4318` on to X-Ray. The
load balancer answers `/metrics` with a 404, so the endpoint never leaves
the task.

There is one tracker project for the product, and every environment reports
into it. The environment is a property of each event: every process sends
`environment` on everything it reports (`configure_error_reporting`), and the
portal sends the `environment` of its runtime config, so the events separate
themselves and a read filters on `environment:<name>`. No project is named
after an environment.

Error reporting stays off until the DSN secret holds a real value. Create the
one project in sentry.io or a hosted GlitchTip, then write its DSN into each
environment's secret. The secret is per environment because a secret is per
account and production's account cannot read staging's; the value written
into each is the same:

```bash
aws secretsmanager put-secret-value \
  --secret-id acme/staging/sentry_dsn --secret-string 'https://<key>@<host>/<project>'
```

Tasks read the secret when they start, so roll the services afterwards
(`aws ecs update-service --force-new-deployment`, or the next deploy).
Terraform never overwrites the value; `off` turns reporting off again.

The Slack app's two secrets take the same path, one secret each, both
created as `off`: the client secret, which trades an install's code for
the workspace's token and renews it, and the signing secret, which
checks every call Slack makes in. Both reach the API, the worker, and
the one-off tasks as `ACME_SLACK_CLIENT_SECRET` and
`ACME_SLACK_SIGNING_SECRET`. The app's client id is not a secret: the
environment root commits it as `slack_client_id`. With any of the three
unset, Slack is unconfigured: "Add to Slack" and every call from Slack
answer 503, and the processes stay healthy.

```bash
aws secretsmanager put-secret-value \
  --secret-id acme/staging/slack_client_secret --secret-string '...'
aws secretsmanager put-secret-value \
  --secret-id acme/staging/slack_signing_secret --secret-string '...'
```

An installed workspace's bot token is no process credential. It is the
org's own secret, which the application writes under
`acme/<environment>/app/org/<org_id>/`, so the application policy and
the account's task boundary give the serving tasks `CreateSecret`,
`PutSecretValue`, and `DeleteSecret` on that part of the prefix alone.
The steps are in
[the Slack runbook](../../../docs/runbooks/providers/slack.md).

## Checks

CI runs `terraform fmt -check -recursive` over this folder,
`terraform init -backend=false && terraform validate` in every root, and
`terraform test` in `modules/static_site` (the portal's security headers,
and the site's policy, its missing config and routes, and its not-found
page), in
`modules/service` (the autoscaling switch), in `modules/dashboard`
(the body's shape), and in `environments/staging` (the environment plans
whole with the company site left out, and with it), all offline under a mock provider. `infra/tests/test_dashboard_parity.py` holds the dashboard
template's panel titles equal to the local Grafana dashboard's.
