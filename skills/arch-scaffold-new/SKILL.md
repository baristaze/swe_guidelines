---
name: arch-scaffold-new
description: "Bootstrap a whole new system in the shape the Software Design and Architecture Guidelines prescribe, into an empty target directory, by building the monorepo skeleton (uv workspace, om, infra, the first API process, a worker, a portal, deployment folders, Makefile, CI) and then following the other scaffold skills for the first namespace and entity. Stack: TypeScript (React, Vite) or Python."
allowed-tools: Read, Grep, Glob, Write, Edit, Agent, Bash(make setup), Bash(make check), Bash(make test-unit), Bash(make infra-up), Bash(make migrate), Bash(make seed), Bash(make test-integration), Bash(make openapi), Bash(uv init:*), Bash(uv sync:*), Bash(uv add:*), Bash(uv run:*), Bash(pnpm install:*), Bash(pnpm run:*), Bash(git init:*), Bash(git status:*), Bash(git diff:*), Bash(git rev-parse:*)
---

# arch-scaffold-new

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Naming
Entities, OpContext (The Operator Context), The Storage Layer (Storage
Root, Defining ORM Classes, Translation, Database Roles, Migrations),
Infrastructure (InfraInterface Root), The Network Layer (Auth: the
Gateway Verifies, the Tenancy Domain Owns), Deployment (Local: Docker
Compose, What a Process Refuses), Monorepo Folder Structure (Layout
Conventions), Cross-Cutting Conventions (Exceptions, Configuration,
Records of Decisions), Technology Choices and How to Override Them
(Versions, Overriding a Choice).

## Input

`<target-dir> <root-package> [--first <namespace> <Entity> [field:type ...]] [--no-portal] [--no-worker]`

Example: `./acme acme --first inventory Warehouse address:str`. Both
positional arguments are required; ask for them when missing.
`<target-dir>` must not exist, or must be empty, or be a fresh
repository holding nothing but `.git`, `README.md`, `LICENSE`, and
`.gitignore` (the shape a hosting service creates); refuse otherwise.
In the fresh-repository case `README.md` and `.gitignore` are replaced,
`LICENSE` is kept, and step 7 is skipped. Refuse when a `.git`
directory exists in a parent of `<target-dir>`, because `git init`
never runs inside an existing repository.
`<root-package>` must not shadow a standard-library module. In this
skill `<root>` is `<root-package>`.

## Created

Everything below is under `<target-dir>/`.

Skeleton:

| File                                              | Holds                                                                                     |
|---------------------------------------------------|-------------------------------------------------------------------------------------------|
| `pyproject.toml`                                  | `[tool.uv] package = false`, `[tool.uv.workspace] members` (with a comment citing the root-package ADR by number), `[tool.uv.sources]`, pytest markers `integration`, `e2e`, `slow` |
| `ruff.toml`, `pyrightconfig.json`, `.python-version` | shared Python lint, format, and type config; `.python-version` and `requires-python` name the latest stable Python release |
| `package.json`, `pnpm-workspace.yaml`, `.nvmrc`, `tsconfig.base.json`, `eslint.config.js` (unless `--no-portal`) | the pnpm workspace over `apps/*` and `clients/*`, and the TypeScript and lint config every member extends; `.nvmrc` names the current active Node LTS release and `packageManager` the latest stable pnpm |
| `.gitignore`, `.env.example`                      | ignores; every setting knob documented with its prefix, the `devx` dashboard ports included, the error-tracking DSN pointing at the seeded local GlitchTip key, and the development seed (`SEED_ORG_NAME`, `SEED_ORG_SLUG`, `SEED_OWNER_EMAIL=owner@<root-package>.example`, `SEED_OWNER_PASSWORD=pswd_1234`, each under the product prefix) |
| `Makefile`                                        | `setup`, `infra-up`, `infra-down`, `migrate` (every role, `--all`), `migrate-check` (ORM metadata against the migrated schema, per role), `migrate-roundtrip` (downgrade the latest revision of every role, then upgrade it), `lint` (`ruff check`), `format-check` (`ruff format --check`), `typecheck` (`pyright`), `check` (`lint`, `format-check`, `typecheck`, `test-unit`), `test-unit`, `test-integration`, `openapi`, `devx-up` (the stack plus the `devx` profile; `infra-down` stops both), `seed` (the API process's `bootstrap --seed`), `up` (both compose files and the `devx` profile, then `migrate`, `seed`, `urls`), `down` (stops every container, keeps the volumes), `reset` (`down` with the volumes removed, then `up`), `urls` (prints every local URL from `.env`), self-documented |
| `README.md`                                       | how to set up, run, and check, a quick start (`make up`, with `make down`, `make reset`, and `make urls` beside it; for editing code on the host, `make setup`, `make infra-up`, `make migrate`, `make seed`, `scripts/dev.sh`) followed by the seeded sign-in (org, owner email, password, all development-only), and a `Local URLs` table (`http://localhost:<port>`, the port from `.env.example`) listing every `devx` dashboard; the service and app scaffolds of steps 2 and 4 add their rows |
| `docs/architecture.md`                            | a one-page "as built" stub linking to the guideline                                       |
| `specs/architecture.md`                           | the pointer to the guideline pinned at the release found before writing (`plugin.json` and the changelog agreeing; a snapshot is left for the person to pin), with empty `Substitutions` and `Deviations` tables, as the guideline's adopting guide (`docs/adopting.md` next to it) shows |
| `docs/adr/0001-root-package.md`                   | the root package decision                                                                 |
| `docs/adr/0002-technology-choices.md`             | the stack as adopted: every technology the guideline names, and per substitution the substitute, the reason, and the rules it must still satisfy |
| `docs/runbooks/README.md`                         | where runbooks go                                                                         |
| `.github/workflows/ci.yml`, `deploy.yml`         | `ci.yml` sets up uv and pnpm at the releases `.python-version` and `.nvmrc` name and runs `make check`, then the integration job over the compose stack (`make migrate`, `make migrate-check`, `make migrate-roundtrip`, `make test-integration`), `terraform fmt -check` and `validate` per environment, and an image build; `deploy.yml` deploys the smaller environment and promotes its images to production by digest behind an approval gate |
| `deployment/local/docker-compose.yml`             | Postgres, Valkey as the cache, a queue, an object store, each image tagged at its latest stable release; host ports read from `.env` with non-default values, so a second project on the same machine does not collide; a `devx` profile with pgweb, Valkey Admin, the object store's and the queue's consoles where their local images ship one, Jaeger for traces, GlitchTip for errors (seeded with a fixed project key so the local DSN in `.env.example` works without its UI), and the metrics view, on ports from `.env` too |
| `deployment/local/docker-compose.full.yml`        | the same plus the application containers                                                  |
| `deployment/docker/entrypoint.sh`                 | the shared image entrypoint                                                               |
| `deployment/terraform/modules/`, `deployment/terraform/environments/{dev,prod}/` | one module per resource the settings name (database, cache, queue, buckets, secrets, service with rollout limits so a worker never exceeds its desired count, a log group with retention, and a non-essential OpenTelemetry collector beside each task that adds only service and environment as dimensions; the load balancer answers `/metrics` with a 404, served at `api.<base_domain>` with its certificate and DNS record), wired in both environments, each passing its `base_domain` (production the product's domain, `dev` a `dev.` subdomain of it), the API's allowed origins, and every prefix the settings read (buckets, queues, secrets) as variables, the process environment naming each of them, and the database URL wired as its own secret (never the password alone); environment names match the settings' cloud-environment set |
| `scripts/dev.sh`                                  | starts every application process on the host                                              |

OM distribution, under `om/`:

| File                                   | Holds                                                                                         |
|----------------------------------------|-----------------------------------------------------------------------------------------------|
| `pyproject.toml`                       | `<root>-om`; `pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `uuid-utils`, and `<root>-infra` as a workspace source (`build_managers` takes `InfraInterface`; the OM depends on infra and never the reverse) |
| `src/<root>/om/base.py`                | `Platform`, the four mixins, `FrozenMapping`, `new_id`, `utcnow`, `EMPTY_UUID`                 |
| `src/<root>/om/opcontext.py`           | `SecurityContext` with `user_id`, `org_id`, `role`, `permissions`, `teams`, `credential_kind`, and `credential_id` (ids and facts, never a `User` or `Org` entity; a socket ticket re-checks the credential by its id), `AppContext`, the stages `RequestContext` (request id, app, trace), `IdentityContext(RequestContext)` (identity id, email, credential), `OpContext(RequestContext)` (with the `org_id`, `user_id`, `credential_kind`, and `credential_id` properties), and `OperatorContext(IdentityContext)`, each produced by one transition on the tenancy manager; the scopes `RequestScope`, `TenantScope`, `ActorScope(TenantScope)`, `CredentialScope`, and `ProvenanceScope(ActorScope, RequestScope)` as `Protocol`s of read-only properties; `build_context(rctx, ...)`; `Role`, `Permission`, `CredentialKind`, and `AppType` are declared here, and the role-to-permission table in `tenancy/types/` reads them, so this module imports nothing above `base.py` and no module needs `TYPE_CHECKING` to stay acyclic |
| `src/<root>/om/exceptions.py`          | `PlatformException` with `http_status` and `code`; `NotFound`, `Conflict`, `ValidationFailed`, `NotAuthorized`, `NotAuthenticated` |
| `src/<root>/om/root.py`                | `build_managers(storage, infra) -> Managers`                                                   |
| `src/<root>/om/storage/root.py`        | `StorageInterface` with `healthcheck` and `close`                                             |
| `src/<root>/om/storage/roles.py`       | `DatabaseRole`, the table-to-role map                                                          |
| `src/<root>/om/events/`                | the `events` namespace of the guideline's Realtime at the Edge: `Event(Identifiable)` with `org_id`, `seq`, `kind`, `target_id`, and a typed payload, its `activity`-role table, storage with the named atomic `append` that assigns `seq` (per tenant, gapless) and `read_after(org_id, after_seq, limit)`, and a manager the outbox relay calls to record one event per entity write, because every push is also a record |
| `src/<root>/om/outbox/`                | the transactional outbox of Database Roles: `OutboxRow(Identifiable)` with `org_id`, `kind`, `target_id`, `payload` (a `FrozenMapping`), and `done_at`, its `core`-role table, storage with `read_pending(limit)` and `mark_done(org_id, row_id)`, and `OutboxRelayInterface.relay(org_id, row)` with its impl, which appends the `Event` through the events manager, publishes `ENTITY_CHANGED`, and marks the row done, idempotent on the row's id; a manager takes the relay by interface and calls it after every write, and the worker sweep relays what `read_pending` returns |
| `src/<root>/om/idempotency/`           | the edge idempotency record: `core`-role table unique on `(org_id, user_id, key)`, storage, and a manager with `begin` and `finish`, so a replayed creating request dedupes on a durable unique index like every queue handler, and the cache is only a read-through |
| `src/<root>/om/storage/migrate.py`     | `run_sql(role, file)`, the check that a SQL file names only tables of its role, the ORM-versus-schema comparison, and the migration CLI (`upgrade --role <role>` or `--all`, `check`) |
| `src/<root>/om/storage/tables/base.py` | `Base` deriving the schema from the role map, the mixins with sort-order bands, `GlobalIdentifiableMixin`, and `FeedIdentifiableMixin`, whose `org_id` carries no single-column index (a feed table composes it instead of redeclaring `org_id`) |
| `src/<root>/om/storage/utils/translation.py` | `to_row`, `to_model`, `apply_row`                                                         |
| `src/<root>/om/storage/impl/pg_base.py`, `postgres.py`, `memory_base.py`, `memory.py` | the Postgres base with `_upsert(table, org_id, entity, outbox_row=None)` (the outbox row inserted in the same commit), `_insert` (doing nothing on an existing id and reporting it, the outbox row landing only when the insert won, so a create returns the row as stored on a retry), and per-statement role routing; the memory base with the same two, landing the outbox row in the outbox memory storage the root hands it; both roots |
| `src/<root>/om/tenancy/`               | the namespace shape with `Org`, `Identity`, `User`, `Membership`, `Session`, `ApiKey`, its manager (authenticate a credential, issue and exchange tokens (an API key is issued with a required `expires_in`, capped by a settings option and never `None`; the wire request defaults inside the cap), bootstrap the first org and operator and return the owner's context, authenticate an operator, one service context per live tenant for sweeps, issue and atomically redeem the socket ticket, and for every trait an entity composes the operation that exercises it: update a member's role, remove a member, update a user, revoke a session, delete an org on the operator plane), storage impls, tables. An entity composes only the mixins a manager operation exercises |
| `migrations/alembic.ini`, `migrations/env.py`, `migrations/sql/core/`, `migrations/versions/core/` | one chain per role, the initial `core` migration |
| `tests/contracts/`                     | the storage contract cases as plain modules, parameterised by a storage fixture, so the unit suite runs them over memory and the integration suite over Postgres (pytest `--import-mode=importlib`, since two distributions each have a `tests/`) |
| `tests/unit/`                          | tenancy on write, the role map, both roots, the tenant-first exceptions list, the import direction (nothing under the OM or infra imports a service or a worker; nothing under infra imports the OM), the interface check (every class named `*Interface` under the OM and infra is an `ABC` whose public methods are abstract), the contract cases over memory |
| `tests/integration/`                   | the migration check (ORM metadata against the migrated schema, per role) and the contract cases over Postgres |

Infra distribution, under `infra/`:

| File                                        | Holds                                                                                   |
|---------------------------------------------|-----------------------------------------------------------------------------------------|
| `pyproject.toml`                            | `<root>-infra`; `valkey-glide`, `aioboto3`, `opentelemetry-sdk`, `prometheus-client`, `sentry-sdk`, `truststore`; no dependency on `<root>-om`: `TopicPayload` is a frozen base declared here with `extra="ignore"`, and the system scope is the zero UUID checked by value |
| `src/<root>/infra/root.py`                  | `InfraInterface` with `start` and `close`                                                |
| `src/<root>/infra/{cache,buckets,topics,queues,secrets}/` | each: the interface (with `start()` and `close()`, returning `None` where an impl holds nothing), a memory or local impl that behaves like the hosted one (a queue twin does not deduplicate), the cloud impl translating driver errors into a platform exception and counting outcomes; `topics/` defines `Topics.WORK_AVAILABLE` and `Topics.ENTITY_CHANGED` (kind, target id, seq) with their payloads, as the guideline's Topics names them, the two every later step produces or routes |
| `src/<root>/infra/observability.py`, `trust.py` | logging with the request-id filter, error reporting (on only when the DSN is set and not `off`; events tagged with service, release, request id), tracing, the OS trust store                    |
| `src/<root>/infra/impl/settings.py`, `impl/configured.py`, `impl/local.py` | settings, the configured root that picks impls and refuses unsafe combinations, the all-local root |
| `tests/`                                    | every capability over the local impls, one test per operation of its interface; and the settings check: every field of `InfraSettings` appears in `.env.example` under its prefix (the test reads the file, so a knob added to settings and not documented fails the fast gate) |

## Changed

Nothing; the tree is new. Every later step appends to the files above.

## Procedure

1. Write the skeleton, the OM distribution, and the infra distribution,
   then run `make setup`. The fast gate runs from step 2 on.
2. Read `${CLAUDE_SKILL_DIR}/../arch-scaffold-service/SKILL.md` and
   follow its Created, Changed, and Procedure with these arguments:
   `api --realtime --container` (omit `--realtime` with `--no-portal`).
3. Unless `--no-worker`, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-worker/SKILL.md` and follow
   it with `maintenance NOOP --container`: a worker whose only work is
   the maintenance sweep, ready for real kinds.
4. Unless `--no-portal`, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-app/SKILL.md` and follow it
   with `portal --kind portal`, including its Terraform and deploy
   rows: the portal's bucket and distribution exist in every
   environment before this step is done.
5. With `--first`, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-namespace/SKILL.md` and follow
   it with `<namespace> <Entity> <field:type ...>`.
6. `make check`; then, when Docker is available, `make infra-up`,
   `make migrate`, `make seed` twice (the second run changes nothing),
   and `make test-integration`, only against the
   compose stack of step 1: refuse when the effective database URL
   (the environment, `.env`, or the settings default) is not a local
   address.
7. `git init` in `<target-dir>`, nothing staged (skipped when the
   target was a fresh repository).
8. Read `${CLAUDE_SKILL_DIR}/../arch-review-full/SKILL.md` and run it
   over the whole tree. Close every high finding and rerun `make
   check`; list the rest in the output for the person. A fresh
   scaffold passes the gates and still carries findings the gates
   cannot see: a setting Terraform does not pass, a write without its
   authorization line, a socket route outside the gateway, a creating
   route without its idempotency key.

Stop at the first step whose gate fails and report where it stopped.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states,
plus one line: the tree is uncommitted, and the first commit is the
user's.
