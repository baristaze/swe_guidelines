# The cloud

Acme runs in AWS as two environments built from one graph: staging from
`main` and production from `release` ([../README.md](../README.md)). Each
environment has an AWS account of its own.

| File | What it is for |
|------|----------------|
| [environments.json](environments.json) | Every value of one deployment, read by the Terraform roots, the create and nuke scripts, and the site build |
| [first_time_manual.md](first_time_manual.md) | The AWS organization, the accounts, Identity Center, and the profiles, set up once by hand |

## environments.json

The file ships with placeholders. Replace each before the first real
`scripts/cloud_create.sh`, which refuses to run while one is left.

| Field | What it names | Placeholder |
|-------|---------------|-------------|
| `region` | The region both environments run in; the deploy workflows name the same one | `us-west-2` |
| `domain` | The domain, whose zone stays at Cloudflare | `acme.example` |
| `github_repository`, `github_repository_id`, `github_repository_owner_id` | The repository whose workflows deploy, by name and by id, which the OIDC trust binds to | `acme-org/acme`, `0`, `0` |
| `environments.<env>.account_id` | The environment's AWS account | `111111111111`, `222222222222` |
| `environments.<env>.admin_profile`, `sso_profile`, `sso_role_name` | The profiles the create run and an operator use, and the permission set the investigate role trusts | `acme-staging-admin`, `acme-staging`, ... |
| `environments.<env>.bootstrap_root`, `environment_root` | The Terraform roots of the environment | `bootstrap/staging`, `environments/staging`, ... |
| `environments.<env>.api_domain_name`, `app_domain_name`, `site_domain_name` | The API's, the portal's, and the company site's public names | under `acme.example` |

The identity provider's client ids are not here: each is
`workos_client_id` in its environment root's `variables.tf`.

## Sizes

What an environment costs comes from its numbers, never its graph: the
instance classes, the replica counts, and whether the database and the
cache keep a second zone. Each size below is one set of those numbers.
Prices are on-demand list prices, rounded, per month, and include about
$75 that every environment pays whatever its size (the NAT gateway, the
load balancer, public addresses, telemetry). Re-price in the AWS Pricing
Calculator before a change of size.

| Size | API tasks | Worker tasks | Postgres | Valkey | Pool | Ceilings | About a month |
|------|-----------|--------------|----------|--------|------|----------|---------------|
| **XS** | 1 × 0.5 vCPU, 1 GB | 1 × 0.25, 0.5 | `db.t4g.micro`, one zone | 1 × `cache.t4g.micro` | 6 | 2, 1 | $124 |
| **S** | 1 × 0.5, 1 | 1 × 0.25, 0.5 | `db.t4g.small`, one zone | 1 × `cache.t4g.micro` | 10 | 3, 1 | $139 |
| **M** | 2 × 0.5, 1 | 1 × 0.25, 0.5 | `db.t4g.medium`, two zones | 2 × `cache.t4g.small` | 12 | 4, 2 | $260 |
| **L** | 2 × 0.5, 1 | 2 × 0.5, 1 | `db.m6g.large`, two zones | 2 × `cache.m6g.large` | 12 | 6, 2 | $560 |
| **XL** | 4 × 1, 2 | 2 × 1, 2 | `db.m6g.xlarge`, two zones, 100 GB | 2 × `cache.m6g.xlarge` | 12 | 12, 4 | $1,125 |

The ceilings are the autoscaling maximums, the API's first. XS proves a
deploy works. S is one of everything and survives a task restart. M
keeps a standby in a second zone for the first real load. L leaves the
burstable classes. XL is growth: grow only the tier the dashboard shows
is short. The roots ship at XS for staging and S for production; each
root's module call is where its size is set, one line per number.

## The pool follows the database

A serving process opens two pools, the runtime login's and the system
login's, each of up to `database_pool_size` connections. The two one-off
tasks, migrate and grant, hold at most 8 together. The rule every size
keeps: (twice the API's ceiling, since a rollout may double it, plus the
worker's ceiling) × 2 pools × the pool size + 8 stays under the
instance's `max_connections` (about 80 for `db.t4g.micro`, 180 for
`db.t4g.small`, 400 for `db.t4g.medium`). At XS that is 5 × 2 × 6 + 8 =
68. Because the rule holds at the ceilings, turning autoscaling on is one
line ([../../docs/runbooks/scale.md](../../docs/runbooks/scale.md)).
Raise a ceiling, and the pool is the line to check in the same change.

## The budget

Each bootstrap root declares its account's monthly budget,
`monthly_budget_usd`, and a cost anomaly monitor. The budget mails
`owner_email` at 50, 80, and 100 percent of actual spend and when the
forecast crosses 100; the monitor reports a jump of $20 or more in one
service, daily. Neither stops anything. Size the budget so a normal
month lands at 60 to 70 percent of it.

To spend less without changing the graph: leave production unapplied
until it is needed, tear staging down between uses
(`scripts/cloud_nuke.sh staging`, then `scripts/cloud_create.sh
staging`), and commit to reserved capacity once a size is settled.
