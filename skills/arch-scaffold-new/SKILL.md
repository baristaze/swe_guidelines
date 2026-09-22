---
name: arch-scaffold-new
description: "Bootstrap a whole new system in the guideline's shape into an empty folder: the monorepo skeleton, the first API, a worker, a portal, deployment, CI, then the first namespace and entity."
allowed-tools: Read, Grep, Glob, Write, Edit, Agent, Bash(make setup), Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make seed), Bash(make test-integration), Bash(make openapi), Bash(make devx-up), Bash(make test-telemetry), Bash(make traffic PROFILE=light DURATION=30), Bash(uv sync:*), Bash(uv run:*), Bash(pnpm install:*), Bash(pnpm run:*), Bash(pnpm --filter:*), Bash(git init:*), Bash(git status:*), Bash(git rev-parse:*), Bash(python3:*), Bash(git diff:*), Bash(git log:*), Bash(git merge-base:*), Bash(git symbolic-ref:*)
---

# arch-scaffold-new

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Naming
Entities, OpContext (Stages, Scopes, The Operator Context), The Storage
Layer (Namespace Shape, Storage Root, Defining ORM Classes,
Translation, A Storage Impl, Database Roles, The Second Fence,
Migrations), Infrastructure (InfraInterface Root), The Network Layer
(The Gateway; Auth: the Gateway Verifies, the Tenancy Domain Owns;
Realtime at the Edge), Deployment (Cloud: AWS, Infrastructure as Code,
Local: Docker Compose, Twins for External Services, What a Process
Refuses), Operations, Monorepo Folder Structure (Layout Conventions),
Documentation as Code, Telemetry, Cross-Cutting Conventions
(Exceptions, Configuration, Records of Decisions, Tests), Technology
Choices and How to Override Them (Versions, Overriding a Choice).

## Input

`<target-dir> <root-package> [--first <namespace> <Entity> [field:type ...]] [--no-portal] [--no-worker]`

Example: `./acme acme --first inventory Warehouse address:str`. Both
positional arguments are required; ask for them when missing.
`<target-dir>` must not exist, or must be empty, or be a fresh
repository holding nothing but `.git`, `README.md`, `LICENSE`, and
`.gitignore` (the shape a hosting service creates); refuse otherwise.
In the fresh-repository case `README.md` and `.gitignore` are replaced,
`LICENSE` is kept, and the `git init` of step 1 is skipped. Those two
replacements are the one exception to the collision rule of the
conventions; any other path that exists is a collision. Refuse when a `.git`
directory exists in a parent of `<target-dir>` (`git rev-parse
--show-toplevel` from it names one), because `git init` never runs
inside an existing repository.
`<root-package>` must not shadow a standard-library module. In this
skill `<root>` is `<root-package>`.

## Created

Everything below is under `<target-dir>/`.

Skeleton:

| File                                              | Holds                                                                                     |
|---------------------------------------------------|-------------------------------------------------------------------------------------------|
| `pyproject.toml`                                  | `[tool.uv] package = false`, `[tool.uv.workspace] members` (with a comment citing the root-package ADR by number), `[tool.uv.sources]`, pytest markers `integration`, `e2e`, `slow`, `telemetry` (needs the `devx` profile), and `[tool.arch-check]` with `package = "<root-package>"` and no other key; below it the two option tables the tree's own names need, `[tool.arch-check.options.CTX-26] sites` and `[tool.arch-check.options.CTX-12] tenantless`, which the `tests/unit/` row fills, and no other option |
| `ruff.toml`, `pyrightconfig.json`, `.python-version` | shared Python lint, format, and type config; `.python-version` and `requires-python` name the latest stable Python release that has a patch release behind it, never one published today, as Technology Choices and How to Override Them (Versions) states |
| `package.json`, `pnpm-workspace.yaml`, `.nvmrc`, `tsconfig.base.json`, `eslint.config.js` (unless `--no-portal`) | the pnpm workspace over `apps/*` and `clients/*`, and the TypeScript and lint config every member extends; `.nvmrc` names the current active Node LTS release and `packageManager` the latest stable pnpm |
| `.gitignore`, `.env.example`                      | ignores; every setting knob documented with its prefix, the `devx` dashboard ports included, the error-tracking DSN pointing at the seeded local GlitchTip key, and the development seed (`SEED_ORG_NAME`, `SEED_ORG_SLUG`, `SEED_OWNER_EMAIL=owner@<root-package>.example`, `SEED_OWNER_PASSWORD=pswd_1234`, and the two local operator identities, `SEED_OPERATOR_EMAIL=operator@<root-package>.example` on a `read` entry and `SEED_PROVISIONER_EMAIL=provisioner@<root-package>.example` on a `write` entry, each with its password, each under the product prefix) |
| `Makefile` | every target the developer and the gate run, self-documented; the full list is below the table |
| `README.md`                                       | how to set up, run, and check, a quick start (`make up`, the dependencies in containers and the application on the host, with `make down`, `make reset`, and `make urls` beside it, and the steps it wraps for running one at a time: `make setup`, `make infra-up`, `make migrate`, `make seed`, `scripts/dev.sh`) followed by the seeded sign-in (org, owner email, password, all development-only, and local only: a deployed environment is entered through sign-up), and a `Local URLs` table (`http://localhost:<port>`, the port from `.env.example`) listing every `devx` dashboard; the service and app scaffolds of steps 2 and 5 add their rows |
| `docs/architecture.md`                            | a one-page "as built" stub linking to the guideline                                       |
| `specs/architecture.md`                           | the pointer to the guideline pinned at the release found before writing (the `version` in `plugin.json`), with empty `Substitutions` and `Deviations` tables, as the guideline's adopting guide (`docs/adopting.md` next to it) shows |
| `docs/adr/0001-root-package.md`                   | the root package decision                                                                 |
| `docs/adr/0002-technology-choices.md`             | the stack as adopted: every technology the guideline names, and per substitution the substitute, the reason, and the rules it must still satisfy |
| `docs/runbooks/README.md`                         | where runbooks go                                                                         |
| `docs/runbooks/tenant-isolation.md`               | the negative control of Cross-Cutting Conventions (Tests): how it is run (the predicate taken out of one query, the suite run with the policy in place and again with it off for that table), and the record of the last run, written by step 8: the query, the table, and what the suite reported each time |
| `.github/workflows/ci.yml`, `deploy-staging.yml`, `deploy-production.yml`, `release.yml`, `grant-operator.yml`, `state-unlock.yml` | the fast gate and the integration job, the staging and production deploys, the release, the first-operator grant, and the state unlock; the full list is below the table |
| `deployment/local/docker-compose.yml`             | Postgres, Valkey as the cache, a queue, an object store, each image tagged at its latest stable release; host ports read from `.env` with non-default values, so a second project on the same machine does not collide; a `devx` profile with pgweb, Valkey Admin, the object store's and the queue's consoles where their local images ship one, Jaeger for traces, GlitchTip for errors (seeded with a fixed project key so the local DSN in `.env.example` works without its UI), and the metrics view, on ports from `.env` too |
| `deployment/local/docker-compose.full.yml`        | the same plus the application containers, for the case that asks for them and never the default |
| `deployment/docker/entrypoint.sh`                 | the shared image entrypoint                                                               |
| `deployment/terraform/modules/`, `deployment/terraform/bootstrap/{staging,prod}/`, `deployment/terraform/environments/{staging,prod}/`, `deployment/cloud/environments.json` | the modules, the two bootstrap roots, the two environment roots, and the environments' file; the full list is below the table |
| `scripts/dev.sh`                                  | starts every application process on the host                                              |
| `scripts/cloud_create.sh`, `scripts/cloud_nuke.sh` | the hands of the create and nuke skills; the full list is below the table |
| `.claude/skills/<name>/SKILL.md`, one per operational skill: `ops-investigate`, `ops-watch`, `ops-root-cause`, `ops-infra-as-code`, `ops-cloud-deployment-create`, `ops-cloud-deployment-nuke`, `ops-simulate-traffic`, `stress-test-create-or-update`, `stress-test-run` | copied from `${CLAUDE_SKILL_DIR}/../_shared/ops-skills/<name>.md` with every `acme` replaced by `<root>` (and `ACME` by its upper case, `Acme` by its capitalized form); the full list is below the table |
| `clients/python/`                                 | the Python client, `<root>-client`, in the shape `arch-scaffold-app` defines it, generated from the OpenAPI document step 3's `make openapi` emits, whether or not `--no-portal`, so its operator-plane calls exist before `ops/` is written; the ops package and a remote service impl import it, and the app skill finds it present and skips its row |
| `ops/` | the workspace member `<root>-ops`; the full list is below the table |
| `ops/README.md`, `ops/stress/README.md`           | how the platform is operated: the roles and profiles, the env file, the nine skills and what each needs, the generator and its profiles; and, under `stress/`, what a scenario holds (profile, duration, ramp, soak, the target p95 and error ratio, the weighted session steps) and that the numbers are the team's |
| `om/README.md`                                    | the nouns of the object model and how they relate, written for a reader with no code: what a tenant is, who a user and a member are, what the product's entities are and which belong to which, in the product's language, with a link one level down to each namespace's README; no developer instruction and no operator instruction in it |
| `deployment/README.md`                            | how it runs: the local stack and its profiles, the two environments and their base domains, the pipeline (a merge deploys staging, a fast-forward to `release` plans production behind an approval), the dashboard and the alarm topic, the two switches, the budget |
| `llms.txt`                                        | the knowledge map at the root: one section per audience (platform developers, platform operators, tenant users and admins), each listing the documents that audience is served, one link per document; exposure is by listing, never by folder, and the product's language, not the team's |

`Makefile`:

- `setup` (`uv sync`, and `pnpm install` when a `package.json` exists), `infra-up`, `infra-down`, `infra-reset` (the dependencies recreated with their volumes removed and nothing else started, the step a gate and `arch-upgrade-deps` take where a developer takes `reset`), `migrate` (every role, `--all`), `migrate-check` (ORM metadata against the migrated schema, per role), `migrate-roundtrip` (downgrade the latest revision of every role, then upgrade it), `lint` (`ruff check`), `format-check` (`ruff format --check`), `typecheck` (`pyright`), `arch-check` (the guideline's static checker, `ARCH_CHECK ?= uvx --python "$(shell cat .python-version)" --from "git+https://github.com/baristaze/swe_guidelines@v<version>\#subdirectory=checkers" arch-check` at the release found before writing, the same tag `specs/architecture.md` pins; `?=` so a developer offline points it at a local checkout), `check` (`lint`, `format-check`, `typecheck`, `arch-check`, `test-unit`), `test-unit`, `test-integration`, `openapi` (the API's document, then `clients/python/` regenerated from it once it exists), `devx-up` (the stack plus the `devx` profile; `infra-down` stops both), `seed` (the API process's `bootstrap --seed`, the org, its owner, the local read operator, and the local provisioner; then, when absent, the owner-only env file `~/.config/<root>/ops/local.env`, its folder `0700` and the file `0600`, written by the recipe from `.env`: `<ROOT>_API_URL` at the local API, the read operator as `<ROOT>_OPERATOR_EMAIL` and `<ROOT>_OPERATOR_PASSWORD`, the provisioner as `<ROOT>_PROVISIONER_EMAIL` and `<ROOT>_PROVISIONER_PASSWORD`, the local GlitchTip as `<ROOT>_ERROR_TRACKER_URL` with the seeded `<ROOT>_ERROR_TRACKER_TOKEN`, and the twins `<ROOT>_PROMETHEUS_URL` and `<ROOT>_JAEGER_URL`, as Operations (Operator Credentials) makes the local stack an environment too), `up` (the dependencies and the `devx` profile in containers, then `migrate`, `seed`, the application on the host through `scripts/dev.sh`, and `urls`; the second compose file is for the case that asks for it and never the default), `down` (stops every container and the host processes, keeps the volumes), `reset` (`down` with the volumes removed, then `up`), `urls` (prints every local URL from `.env`), `test-telemetry` (the round-trip test under the `telemetry` marker, against the `devx` profile), `traffic` (`make traffic PROFILE=light DURATION=30`: the generator through the `<root>-ops` binary, the thirty-second light run being CI's wiring check), self-documented

`.github/workflows/ci.yml`, `deploy-staging.yml`, `deploy-production.yml`, `release.yml`, `grant-operator.yml`, `state-unlock.yml`:

- `ci.yml` sets up uv and pnpm at the releases `.python-version` and `.nvmrc` name and runs `make check`, then `make openapi` and `git diff --exit-code` so a committed OpenAPI document or generated client that drifted fails, then the integration job over the compose stack (`make migrate`, `make migrate-check`, `make migrate-roundtrip`, `make test-integration`, then `make seed`, the API started on the host and waited for on `/healthz`, and `make traffic PROFILE=light DURATION=30`, the gate's thirty-second light run of Operations (Traffic and Stress), the API's log printed on a failure), `terraform fmt -check` and `validate` per root, both bootstrap roots and both environment roots, and an image build
- `deploy-staging.yml` runs on every push to `main`, and on `workflow_dispatch` for the first deploy `scripts/cloud_create.sh` dispatches, with no approval, in the concurrency group `deploy-staging` that never cancels a run in progress: every job that touches the cloud declares the `staging` environment and reads that environment's variables, so it holds staging's role and nothing else
- The build jobs hold a push-only role (`<root>-build-staging`, which pushes images and writes the bundle prefix and nothing else) and build the images and the bundles under the commit, push the images and keep the bundle by commit in staging's artifacts bucket, both of which replicate into production's account
- The apply job alone holds the deploy role and applies `environments/staging/`, whose migration runs as a one-off task on the new image before the rollout, so a merge is the deployment, then reads `/readyz` through the edge and records a deployment on the repository host naming the commit, each image's digest, and the bundle's hash
- `deploy-production.yml` runs on a push to `release`, and on `workflow_dispatch` on `release` for the first deploy, under the same checks and the same approval, in the concurrency group `deploy-production` that never cancels a run in progress: it checks that `release` is an ancestor of `main` and stops otherwise, declares `production-plan` for the jobs before the approval and `production` for the apply, each reading its own environment's variables, reads the release commit's staging deployment record, looks up the digests and the bundle in production's own registry and artifacts bucket, where replication put them, waiting a bounded time for the copy, and refuses a commit with no successful staging deployment or a copy whose digest or hash differs from the record
- A `workflow_dispatch` input names the previous release commit for a guarded redeploy, accepted only when it is an ancestor of `release` production ran before, planned behind the same approval, `release` left where it is
- Saves the plan of `environments/prod/` in production's state bucket under the apply's access (never as a workflow artifact; the masked text rendering is the reviewer's copy), and applies that plan in a job behind the production environment's approval, the migration running as a one-off task on the new image before the rollout, never building
- `release.yml` runs on dispatch and fast-forwards `release` to the commit of the last successful `deploy-staging` run, read from that run's deployment record, the only push to `release` there is, made with a token of the repository host's app, which the `release` ruleset admits as its one bypass actor
- The job declares a `release` environment whose deployment-branch policy admits `main` alone and which holds the app's key, and the app's push starts `deploy-production.yml`
- `grant-operator.yml` is dispatched by a person on an environment's branch, declares that environment, and runs the API image's `grant-operator` subcommand as a one-off task under the deploy role, putting the first identity on the operator allowlist (production's behind the same approval)
- `state-unlock.yml` is dispatched with a root and a lock id and runs `terraform force-unlock` for that one lock under the environment's deploy role

`deployment/terraform/modules/`, `deployment/terraform/bootstrap/{staging,prod}/`, `deployment/terraform/environments/{staging,prod}/`, `deployment/cloud/environments.json`:

- One module per resource the settings name (database, cache, queue, buckets, secrets, service with rollout limits so a worker never exceeds its desired count, a log group with retention, and a non-essential OpenTelemetry collector beside each task that adds the service and the environment as dimensions and no other beyond a metric's bounded labels; the load balancer answers `/metrics` with a 404, served at `api.<base_domain>` with its certificate and DNS record), wired in both environments, each passing its `base_domain` (production the product's domain, staging a `staging.` subdomain of it), the API's allowed origins, and every prefix the settings read (buckets, queues, secrets) as variables, the process environment naming each of them, and the database URL wired as its own secret (never the password alone), with no secret value in state or a plan: the master password from an ephemeral generator through the database's write-only password attribute, or managed by the database service, and the URL's secret version written through its write-only attribute, never computed as an output
- The target group's health check and each task definition's own health check on `/healthz`, never `/readyz`
- The ECS deployment circuit breaker with rollback on every service
- The API's migration as a one-off task on the new image before its rollout, which the rollout depends on
- Environment names match the settings' cloud-environment set
- `deployment/cloud/environments.json` naming the region and, per environment, its `account_id`, its `admin_profile` (`<root>-<env>-admin`), its `sso_profile` (`<root>-<env>`), and its public names, read by every root and both scripts, with every provider's `allowed_account_ids` pinned to the environment's account
- One bootstrap root per account, `bootstrap/<env>/`, over a shared `account` module: the state bucket `<root>-state-<account id>`, versioned, holding state and nothing else, the artifacts bucket `<root>-artifacts-<account id>`, versioned, holding the bundles kept by commit and nothing else, with a lifecycle that outlasts the last few releases and, in production, a refusal of a second write to a key under the bundle prefix, the image registry with immutable tags, scan on push, and a lifecycle that keeps a bounded number of images and never one tagged `prod-`, the repository host's identity federation and the roles it trusts (staging's push-only build role and its deploy role, production's two, one that plans and one that applies, so the approval gates the one that writes, each trusting its repository-host environment, the ref of its branch, and the repository by `repository_id` and `repository_owner_id`), the permissions boundary every role a deploy role creates must carry, each deploy role denied a role created without it and every change to the boundary, to the deploy roles, to the bootstrap's roles and trust, and to the bootstrap's state key, a trail of the account's API calls in a bucket of its own, the investigate role `<root>-investigate-<env>` (read-only over every signal and every resource description, the state prefix readable so `terraform plan -lock=false -refresh=false` runs, and fences denying every secret value, every data bucket's objects, the database connect, and the other environment's tags) trusting the identity center's everyday role of its own account by pattern, with no cloud user and no access key anywhere, the budget (`monthly_budget_usd`, alerts at 50, 90, and 100 percent actual and 100 forecast to `owner_email`) and the anomaly monitor, and one hosted zone per public name
- Staging's bootstrap root replicates its registry and its artifacts bucket into production's account under `replicate_to_production`, and production's grants those two writes, scoped to its repositories and to the bundles' prefix, and nothing else
- Per environment root, the alarm topic `<root>-<env>-alarms` with its email subscription and the default alarm set (load balancer 5xx ratio, unhealthy targets, database CPU, database free storage, running tasks below desired for the API and the worker, load balancer p95), the dashboard `<root>-<env>` with the same panels as the local metrics view, target-tracking autoscaling on every service behind the root switch `autoscaling_enabled` (off by default, every lever below it on, so one flip scales the environment), the root switch `destroyable` (off by default: `force_destroy` on the buckets, and outside production `skip_final_snapshot` on the database; production's database always leaves its final snapshot and keeps its automated backups, `delete_automated_backups = false`), the database's deletion protection under a variable of its own, `database_deletion_protection` (`true` in production, so a merged change and never a script turns it off), outside production no recovery window on the secrets, the services' `desired_count` ignored once autoscaling owns it, private subnets with a named egress (a NAT gateway, or the private endpoints of the registry, the secret store, the logs, the object store, and the queue), encryption at rest on every store and TLS required by the database, point-in-time recovery with a declared retention and two zones in production once customers depend on it, production's load balancer and distribution behind the managed web firewall or an ADR saying why not, retention on every log group, and the environment tag through the provider's `default_tags`

`scripts/cloud_create.sh`, `scripts/cloud_nuke.sh`:

- The hands of the create and nuke skills, both with `--dry-run` printing every command they would run: both clear the keys exported in the shell, take `<env>` under the `admin_profile` the environments' file names for it, and refuse unless `aws sts get-caller-identity` answers that environment's `account_id`, asked again before every apply
- Create applies `bootstrap/<env>/` with local state and moves it into the bucket it made (staging's root turning `replicate_to_production` on once production's bucket exists), writes each public name's NS delegation at the domain's DNS host through its API with a token read from the environment, the run's one credential outside the account, scoped to the domain's zone and held only for the run, writes the profile `<root>-<env>-investigate` into `~/.aws/config` chained by `source_profile` from the `sso_profile`, writes the owner-only env file `~/.config/<root>/ops/<env>.env` (`<ROOT>_API_URL`, and the lines `<ROOT>_OPERATOR_EMAIL`, `<ROOT>_OPERATOR_PASSWORD`, `<ROOT>_PROVISIONER_EMAIL`, `<ROOT>_PROVISIONER_PASSWORD`, `<ROOT>_ERROR_TRACKER_URL`, and `<ROOT>_ERROR_TRACKER_TOKEN` left empty, since no operator exists until the pipeline's first-operator job has run), creates the GitHub environments (`staging`; `production-plan` with no reviewer and `production` with the required reviewer), each with its deployment-branch policy (`staging` from `main`, both production environments from `release`), and sets each one's variables under the same names (`AWS_ROLE_ARN`, `TF_STATE_BUCKET`, `ARTIFACTS_BUCKET`, the public names, the alarm address), dispatches staging's first deploy through the pipeline and, for production, names the order that comes first (staging again, a merge to `main`, then the release), and prints the smoke test
- Nuke takes `<env>`, refuses `production` unless `--confirm production` is typed, the root's `database_deletion_protection` reads `false` on `release`, and the applied state no longer protects the database (the released change is what lifted it; the script never sets that variable), checks out the environment's exact origin commit (`release` for production, `main` for staging) into a clean worktree of its own and refuses a dirty one, applies the `destroyable` switch from there, empties the data buckets, destroys the environment root, and prints what remains (production's final snapshot, and the bootstrap root: the zones, the state prefix, the images, the roles)

`.claude/skills/<name>/SKILL.md`, one per operational skill: `ops-investigate`, `ops-watch`, `ops-root-cause`, `ops-infra-as-code`, `ops-cloud-deployment-create`, `ops-cloud-deployment-nuke`, `ops-simulate-traffic`, `stress-test-create-or-update`, `stress-test-run`:

- Copied from `${CLAUDE_SKILL_DIR}/../_shared/ops-skills/<name>.md` with every `acme` replaced by `<root>` (and `ACME` by its upper case, `Acme` by its capitalized form), nothing else edited: each states its role as Operations (Operational Skills) names it, the credential it holds, what it reads, what it never does, and its report. The investigator and supporter skills hold `<root>-<env>-investigate` and read the owner-only env file `~/.config/<root>/ops/<env>.env`, except `ops-infra-as-code`, which plans against the cloud and reads no env file
- Create and nuke hold the environment's `admin_profile` alone, compared with its `account_id` in `deployment/cloud/environments.json`
- The traffic and stress skills have no role of their own, and `ops-simulate-traffic` and `stress-test-run` read the env file for the provisioner identity and hold the investigate profile only to read the signals back from a cloud environment
- `stress-test-create-or-update` writes a file and holds no profile and no env file. Every one takes `--env staging\|production`, and every one but create and nuke also takes `local`, reading the `devx` stand-ins

`ops/`:

- The workspace member `<root>-ops`, package `<root>.ops`, binary `<root>-ops`: `traffic --env <env> --profile light\|regular\|heavy\|stress [--duration S] [--orgs N] [--report path]`, the one generator (its tenants and their members created through `POST /v1/admin/orgs` and `POST /v1/admin/orgs/{org_id}/members` by the provisioner identity of the env file and named with the run id; `--orgs 0` drives the seeded people and needs no provisioner), riding `clients/python/` and the operator plane, driving the edge and never a manager, with realistic sessions (sign in, list, add a handful, edit, complete, reopen, move, list, delete one, read events, one socket that sees its own change, sign out) and four profiles differing by tenants, members, concurrency, and think time, reporting requests by route and status, p50, p95, p99, and the error ratio
- `stress --scenario ops/stress/<name>.yaml --env <env> [--report path]`, the same generator at the scenario's profile with its ramp, soak, and target
- `signals check --env <env> --request-id <id> [--since-minutes N] [--log-file PATH]` over `SignalsInterface` (`log_lines`, `metric_delta`, `trace`, `error_event`) with `SignalsLocalImpl` (the Prometheus HTTP API, the Jaeger HTTP API, GlitchTip's REST API with the token the seed creates, the captured stdout) and `SignalsCloudImpl` (CloudWatch Logs Insights, `GetMetricData` on the product's namespace, X-Ray, the error tracker's REST API)
- `size --env <env>` (tenants, users, the main entity written in the last day, through `GET /v1/admin/size`)
- `ops/tests/test_telemetry_roundtrip.py` under the `telemetry` marker, which starts the API as a real process with the OTLP endpoint and the DSN set, drives one session, and reads every signal back by request id, so the same test runs as the deployed smoke test with a different base

OM distribution, under `om/`:

| File                                   | Holds                                                                                         |
|----------------------------------------|-----------------------------------------------------------------------------------------------|
| `pyproject.toml`                       | `<root>-om`; `pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, and `<root>-infra` as a workspace source (`build_managers` takes `InfraInterface`; the OM depends on infra and never the reverse) |
| `src/<root>/om/base.py`                | `Platform`, the five mixins with `PROVENANCE_FIELDS` beside them (the constant naming `created_at`, `created_by`, `deleted_at`, and `deleted_by`, which every copy on update leaves as stored), `FrozenMapping`, `new_id`, `utcnow`, `EMPTY_UUID` |
| `src/<root>/om/opcontext.py`           | `SecurityContext` with `user_id`, `org_id`, `role`, `permissions`, `teams`, `credential_kind`, and `credential_id` (ids and facts, never a `User` or `Org` entity; a socket ticket re-checks the credential by its id), `AppContext`, the stages `RequestContext` (request id, app, trace id, the `traceparent` a handoff carries on, the causing request a handoff names), `IdentityContext(RequestContext)` (identity id, email, credential), `OpContext(RequestContext)` (with the `org_id`, `user_id`, `credential_kind`, and `credential_id` properties), and `OperatorContext(IdentityContext)` (with the `permissions` its allowlist entry grants, read or read and write), each produced by one transition on the tenancy manager; the scopes `RequestScope`, `TenantScope`, `ActorScope(TenantScope)`, `CredentialScope`, and `ProvenanceScope(ActorScope, RequestScope)` as `Protocol`s of read-only properties; `Role`, `Permission`, `OperatorPermission`, `CredentialKind`, and `AppType` are declared here, and the role-to-permission table in `tenancy/types/` reads them, so this module imports nothing above `base.py` and no module needs `TYPE_CHECKING` to stay acyclic |
| `src/<root>/om/exceptions.py`          | `PlatformException` with `http_status` and `code`; `NotFound`, `Conflict`, `ValidationFailed`, `NotAuthorized`, `NotAuthenticated`, `Unavailable` (the shape of a dependency that cannot be reached: an open breaker, a refused admission, a backend that is down) |
| `src/<root>/om/root.py`                | `build_managers(storage, infra) -> Managers`                                                   |
| `src/<root>/om/storage/root.py`        | `StorageInterface` with `healthcheck` and `close`, re-exported from `storage/__init__.py`, since the guideline puts the root at `<root>.om.storage` |
| `src/<root>/om/storage/roles.py`       | `DatabaseRole`, the table-to-role map `TABLE_ROLES`, `TenancyScope` (`system`, `org`, `identity`, `both`), and the table-to-scope map `TABLE_SCOPES` beside it (the names `arch-check` reads by default), naming the column the policy rests on for an `identity` or `both` table, as The Storage Layer (The Second Fence) states |
| `src/<root>/om/events/`                | the `events` namespace of the guideline's Realtime at the Edge: `Event(Identifiable)` with `org_id`, `seq`, `kind`, `target_id`, `actor_id` (the principal of the write, `EMPTY_UUID` for the platform), and a typed payload, its `activity`-role table, storage with the named atomic `append_event` that assigns `seq` (per tenant, gapless, from a `cursors` row per tenant in the same role, `UPDATE ... SET head = head + 1 ... RETURNING head` inside the append's transaction, the row inserted on the tenant's first event; never `MAX(seq) + 1` with a retry), `read_head(org_id)` from the same row, and `read_after(org_id, after_seq, limit)`, and a manager the outbox relay calls to record one event per entity write, because every push is also a record |
| `src/<root>/om/audit/`                 | the `audit` namespace, the cross-cutting swimlane of Namespaces as Swimlanes: `AuditEntry(Identifiable)` with the same shape as an `Event` plus the request id and the app (`org_id`, `seq`, `kind`, `target_id`, `actor_id`, a typed payload, `request_id`, `app`), as Realtime at the Edge states; its `activity`-role table, storage with the named atomic `append_audit_entry` that assigns `seq` and `read_after(org_id, after_seq, limit)`, and `AuditManagerInterface`, which the dead-letter path of a worker and the operator plane write through |
| `src/<root>/om/outbox/` | the transactional outbox of Database Roles; the full list is below the table |
| `src/<root>/om/idempotency/`           | the edge idempotency marker: `IdempotencyMarker(Identifiable, Created)`, as The Network Layer (The Gateway) declares it, its `core`-role table unique on `(org_id, user_id, key)`, storage, and a manager with `begin`, `finish`, the release, and the take-over, each one conditional write with its guard in the statement, as the marker table of The Network Layer (The Gateway) shows, so a replayed creating request dedupes on a durable unique index like every queue handler, and the cache is only a read-through |
| `src/<root>/om/{tenancy,events,audit,outbox,idempotency}/README.md` | one per namespace, one level below `om/README.md` and linked from it, in the product's language: what its nouns are, what can happen to them, and which rules hold (for `tenancy`, the org, the member, the role, the session, the key, the operator allowlist), and no developer or operator instruction, as Documentation as Code (A README at Every Level) states |
| `src/<root>/om/storage/migrate.py`     | `run_sql(role, file)`, the check that a SQL file names only tables of its role, the ORM-versus-schema comparison, and the migration CLI (`upgrade --role <role>` or `--all`, `check`) |
| `src/<root>/om/storage/tables/base.py` | `Base` deriving the schema from the role map, the mixins with sort-order bands, `GlobalIdentifiableMixin`, and `FeedIdentifiableMixin`, whose `org_id` carries no single-column index (a feed table composes it instead of redeclaring `org_id`) |
| `src/<root>/om/storage/utils/translation.py` | `to_row`, `to_model`, `apply_row`                                                         |
| `src/<root>/om/storage/impl/pg_base.py`, `postgres.py`, `memory_base.py`, `memory.py` | the Postgres base with `_upsert(table, org_id, entity, outbox_rows=())` (the outbox rows inserted in the same commit), `_insert` (doing nothing on an existing id and reporting it, the outbox rows landing only when the insert won, so a create returns the row as stored on a retry), and per-statement role routing, and one session funnel, `_session_for(stmt, org_id, user_id=None)`, that routes the statement to its role and sets `app.org_id`, `app.user_id`, and `app.identity_id` with `set_config(..., true)` so they die with the transaction; the memory base with the same two, landing the outbox rows in the outbox memory storage the root hands it; both roots, `StoragePostgresImpl` and `StorageMemoryImpl`, named like every other impl; `StoragePostgresImpl` opens one engine per distinct role URL and every pool declares `pool_size` and `pool_checkout_timeout` from settings, per role and defaulting to the shared value the way the role URLs do, so a checkout that waits past the bound fails instead of queueing without end, and every session applies `statement_timeout` from the same settings, so a statement past its deadline is cancelled and surfaces as a failure rather than holding its connection, as The Storage Layer (Database Roles, A Storage Impl) states |
| `src/<root>/om/tenancy/` | the tenancy namespace: its entities, its manager's operations, storage impls, and tables; the full list is below the table |
| `migrations/alembic.ini`, `migrations/env.py`, `migrations/sql/{core,activity}/`, `migrations/versions/{core,activity}/` | one chain per role: the initial `core` migration (tenancy, the outbox row, the idempotency marker) and the initial `activity` migration (events with its per-tenant cursor row, audit entries), since `make migrate` (every role) and `make migrate-check` of step 7 run every role a table was declared in; every table's policy is created in the same migration as the table, with `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`, in the shape its tenancy scope implies, and dropped in the down file; the `queue` chain arrives with `arch-scaffold-worker` |
| `tests/contracts/`                     | the storage contract cases as plain modules, parameterised by a storage fixture, so the unit suite runs them over memory and the integration suite over Postgres (pytest `--import-mode=importlib`, since two distributions each have a `tests/`); every named atomic method is raced, not only called: two callers at once against the ticket redemption, the idempotency take-over, and later the claim, asserting that exactly one wins, over memory and over the engine |
| `tests/unit/`                          | tenancy on write, both roots built whole (every getter of `StoragePostgresImpl` and `StorageMemoryImpl`, every capability of the infra root, and every field of `Managers` exists after construction; none is built on first use), the request-stage list (every manager method that takes the request stage instead of a later one, enumerated, `sign_up` and the sign-in among them, so a new one fails the test), the contract cases over memory. The role map, the construction sites of the stages, the tenant-less storage methods, the import direction, the interface check, and the migration heads are not tests: `arch-check` decides them in `make check`, and the root `pyproject.toml` lists the sites under `[tool.arch-check.options.CTX-26] sites` and the tenant-less methods under `[tool.arch-check.options.CTX-12] tenantless`, each list held both ways |
| `tests/integration/`                   | the migration check (ORM metadata against the migrated schema, per role), the tenancy policy check (`pg_class` and `pg_policies` read for every table in the scope map and asserted against the scope it declares, since the schema diff does not see policies), the login check (`current_user` on the live connection is neither superuser nor `BYPASSRLS`), and the contract cases over Postgres |

`src/<root>/om/outbox/`:

- The transactional outbox of Database Roles: `OutboxRow(Identifiable, Created)`, as The Storage Layer (Namespace Shape) declares it, with `org_id`, `kind`, `target_id`, `payload` (a `FrozenMapping`), `actor_id` (the principal of the write, `EMPTY_UUID` for the platform), `request_id`, `traceparent` (the causing request's trace context, the header and not the id, empty when no tracer was configured), `app` (an `AppContext`), and `done_at`, and no `created_by`, since the row names the actor of the write it announces
- The helper `outbox_row(ctx, kind, target_id, payload)` that builds it from a `ProvenanceScope`, which every manager write uses
- Its `core`-role table, storage with `read_pending(limit)`, `mark_done(org_id, row_id)`, and `purge_done(before)`, and `OutboxRelayInterface.relay(org_id, row)` with its impl, which dispatches on the row's `kind` and marks the row done, idempotent on the row's id: a row announcing an entity change appends the `Event` with the row's actor through the events manager and publishes `ENTITY_CHANGED`, and a row whose kind is `work.<kind>` enqueues the item through the work manager's `enqueue_relayed(org_id, row)`, which publishes `WORK_AVAILABLE` as every enqueue does (`arch-scaffold-worker` creates that namespace; until it does, the relay has the one branch)
- A manager takes the relay by interface and calls it after every write, and the worker sweep relays what `read_pending` returns and purges done rows past retention

`src/<root>/om/tenancy/`:

- The namespace shape with `Org`, `Identity`, `User`, `Membership`, `Session`, `ApiKey`, its manager (sign up: `sign_up(rctx, email, password, display_name, org_name, org_slug)` creates the identity, its first org, and the owner membership in one transaction and answers as the sign-in does, refusing an email or a slug already held with `Conflict` and raising `NotFound` while its options' `signup_enabled` is false; list an identity's memberships, `get_memberships(ictx, limit)`, bounded, the same `MembershipChoiceView`s the sign-in answers with; authenticate a credential, the identity stage from the login credential or a live session; issue and exchange tokens, an exchange presented with a session ending that session in the same write (an API key is issued with a required `expires_in`, capped by a settings option and never `None`; the wire request defaults inside the cap), bootstrap the first org and operator and return the owner's context, authenticate an operator, one service context per live tenant for sweeps, issue and atomically redeem the socket ticket, and for every trait an entity composes the operation that exercises it: update a member's role, remove a member, update a user, revoke a session, delete an org on the operator plane; the operator plane's own operations, each taking `OperatorContext` first and the tenant as a parameter and never a context, which `routers/admin.py` of the service calls: `get_operator(octx)`, `get_size(octx)` (tenants, users, and the rows written in the last day, its cross-tenant storage reads listed as tenant-less), `get_orgs(octx, limit)`, `get_org(octx, org_id)`, `get_members(octx, org_id, limit)`, and the tenant's events feed through the events manager's `get_events_for_operator(octx, org_id, after_seq, limit)` with `request_id` and `app` on each entry, each read of a tenant's rows logged with the tenant and the operator; `create_org(octx, ...)` and `add_member(octx, org_id, ...)`, the provisioning, each opening with `octx.require(OperatorPermission.WRITE)`, so a `read` entry is refused; every copy on update starts from the stored row, the caller's fields with `PROVENANCE_FIELDS` excluded, and the create that issues an API key re-mints the secret on a rerun that finds the row, in the same named atomic write, guarded by the attempt the idempotency marker holds, returning a fresh `Issued...View` with the same id), storage impls, tables. An entity composes only the mixins a manager operation exercises

Infra distribution, under `infra/`:

| File                                        | Holds                                                                                   |
|---------------------------------------------|-----------------------------------------------------------------------------------------|
| `pyproject.toml`                            | `<root>-infra`; `valkey-glide`, `aioboto3`, `opentelemetry-sdk`, `prometheus-client`, `sentry-sdk`, `truststore`; no dependency on `<root>-om`: `TopicPayload` is a frozen base declared here with `extra="ignore"`, and the system scope is the zero UUID checked by value |
| `src/<root>/infra/root.py`                  | `InfraInterface` with `start` and `close`                                                |
| `src/<root>/infra/exceptions.py`            | `InfraException` with `http_status` and `code`, the root of every exception infra raises, since infra imports nothing from the OM, as Cross-Cutting Conventions (Exceptions) states |
| `src/<root>/infra/{cache,buckets,topics,queues,secrets}/` | each: the interface (with `start()` and `close()`, returning `None` where an impl holds nothing), a memory or local impl that behaves like the hosted one (a queue twin does not deduplicate), the cloud impl translating driver errors into an `InfraException` and counting outcomes; `topics/` defines `Topics.WORK_AVAILABLE` and `Topics.ENTITY_CHANGED` (kind, target id, seq) with their payloads, as the guideline's Topics names them, the two every later step produces or routes |
| `src/<root>/infra/observability.py`, `trust.py` | logging with the filter that puts the service, the environment, the request id, and the causing request on every record, error reporting (on only when the DSN is set and not `off`; events tagged with service, release, request id), tracing, the OS trust store                    |
| `src/<root>/infra/impl/settings.py`, `impl/configured.py`, `impl/local.py` | settings, the configured root that picks impls and refuses unsafe combinations, the all-local root |
| `tests/`                                    | every capability over the local impls, one test per operation of its interface; and the settings check: every field of `InfraSettings` appears in `.env.example` under its prefix (the test reads the file, so a knob added to settings and not documented fails the fast gate) |

## Changed

| File | Change |
|------|--------|
| (none) | The tree is new; every later step appends to the files above. |

## Procedure

1. `git init` in `<target-dir>`, nothing staged (skipped when the
   target was a fresh repository). It comes first so that every step
   after it, and every skill this one follows, lists its files from
   `git status`. Then write the skeleton (leaving `clients/python/` and
   `ops/` with its `README.md` to step 3), then the OM distribution
   and the infra distribution, and run `make setup`. The fast gate runs from
   step 2 on. The nine operational skills are part of the skeleton:
   copy each template under `${CLAUDE_SKILL_DIR}/../_shared/ops-skills/` to
   `.claude/skills/<name>/SKILL.md` with `acme` substituted, as the
   Created table states, and change nothing else in them.
2. Read `${CLAUDE_SKILL_DIR}/../arch-scaffold-service/SKILL.md` and
   follow its Created, Changed, and Procedure with these arguments:
   `api --realtime --container` (omit `--realtime` with `--no-portal`).
   The operator plane's routes arrive with it.
3. `make openapi`, then write `clients/python/` generated from the
   document it emitted, whether or not `--no-portal`; then write
   `ops/` and `ops/README.md` over that client, add both members to
   the workspace, and run `uv sync`. The ops package rides the client
   and the operator plane, so it is written after both exist.
4. Unless `--no-worker`, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-worker/SKILL.md` and follow
   it with `maintenance NOOP --container`: a worker whose only work is
   the maintenance sweep, ready for real kinds.
5. Unless `--no-portal`, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-app/SKILL.md` and follow it
   with `portal --kind portal`, including its Terraform and deploy
   rows: the portal's bucket and distribution exist in every
   environment before this step is done. Its Python client row is
   skipped, since step 3 wrote the client.
6. With `--first`, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-namespace/SKILL.md` and follow
   it with `<namespace> <Entity> <field:type ...>`.
7. `make check` and `make openapi`, so the portal's generated types
   and the Python client carry the routes of step 6; then, when Docker
   is available, `make infra-up`, `make migrate`, `make migrate-check`,
   `make seed` twice (the second run changes nothing, and leaves
   `local.env` as the first wrote it), and `make test-integration`,
   only against the
   compose stack of step 1: refuse when the effective database URL
   (the environment, `.env`, or the settings default) is not a local
   address.
8. When Docker is available, run the negative control of
   Cross-Cutting Conventions (Tests) once. Take the tenant predicate
   out of one query of a storage impl over Postgres (the first
   entity's list with `--first`, else a tenancy list). Run
   `make test-integration` with the table's policy in place: it stays
   green, the second fence holding. Turn the policy off for that
   table (`ALTER TABLE ... NO FORCE ROW LEVEL SECURITY` and `DISABLE
   ROW LEVEL SECURITY`, through `uv run` over the local database
   URL), and run `make test-integration` again: it fails, and the
   failures name the cross-tenant case of that method beside the
   policy check. Put the predicate back, turn the policy on again the
   same way (`ENABLE` and `FORCE ROW LEVEL SECURITY`), and run
   `make test-integration` green. Record both runs in
   `docs/runbooks/tenant-isolation.md`: the query, the table, and
   what the suite reported each time. A run two that stays green is a
   defect of the suite: name it in the output and stop.
9. When Docker is available, `make devx-up`, then
   `make test-telemetry`: the round trip starts the API as a real
   process, drives one session, and reads the counter, the trace, the
   error event, and the log line back by request id through the
   `devx` twins. Then `make traffic PROFILE=light DURATION=30`, the
   thirty-second light run, the same one CI's integration job runs.
   Both are a wiring check of the edge, the
   client, the generator, and the signals, and never a stress test;
   a stress test has a scenario and a target, and is the platform
   developer's to run.
10. Before the review, sweep the tree for the four misses a fresh
    scaffold makes most, and fix each: a setting the Terraform root
    does not pass to the service, a mutating manager operation whose
    first line is not `ctx.require(...)`, a socket route mounted
    outside the gateway, a route that writes a durable row (201 or 202)
    without the `Idempotency-Key` dependency. Then read
    `${CLAUDE_SKILL_DIR}/../arch-review-full/SKILL.md`
    and run it over the whole tree; the checker run and the git reads
    it takes are in this skill's tools for that step. Close every high
    finding and rerun `make check`; list the rest in the output for the
    person. A high
    finding on a fresh tree is a defect of this skill: name it in the
    output so it can be closed at the source.

Stop at the first step whose gate fails and report where it stopped.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states,
plus one line: the tree is uncommitted, and the first commit is the
user's.
