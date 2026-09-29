# Acme

A multi-tenant to-do application for teams of people and the agents
that work alongside them. The system is built in the shape the Software
Design and Architecture Guidelines prescribe: one object model library
at the center (`om/`), one infrastructure toolkit (`infra/`), services
and workers around them, and apps at the edge.

<p align="center">
  <img src="docs/media/realtime-demo.gif" width="876" alt="Two portal windows side by side, Bob on the left and the owner on the right, both on Ajax's Team list. Bob adds three tasks and opens the second to give it a due date; he opens the third, assigns it to the owner, and attaches an image, which shows as a preview. The owner opens the same task and sees the image. Bob completes the first task. Each change appears in the owner's window at once.">
</p>

The portal in two windows, signed in through the local sign-in as two of the
people `make seed` creates: `bob@example.test` on the left,
`owner@example.test` on the right, both in the seeded team org, Ajax, on
its Team list. Bob adds a task from the one text box, adds a second and
opens it to give it a due date, then adds a third. He opens the third,
assigns it to the owner, and attaches an image, which the task shows as a
preview; the owner opens the same task and sees it too. Then Bob completes
the first. Every change reaches the owner's window over the realtime channel
as it happens. Each window is narrower than half a laptop screen, and every
task stays on one line there. `make demo-gif` records it again from a running `make up` stack (`scripts/record_demo.py`), and refuses
to when a row would wrap; `make demo-gif-dark` records the dark theme's copy,
which the company site shows on a dark system.

<p align="center">
  <img src="docs/media/cli-demo.gif" width="876" alt="Two terminals side by side. On the left Bob signs in with acme login, adds a task due tomorrow, adds another and attaches an image to it, lists the open tasks, and completes the first. On the right the owner runs acme listen, and every change appears as a line the moment it happens.">
</p>

The same thing from two terminals. On the left Bob works in command mode,
one `acme` call at a time: he signs in with `acme login --dev-email` (the
local stack's sign-in by address), adds a task with `--due` and tomorrow's date, adds
another and attaches an image to it, lists the open tasks, and completes the
first. On the right the owner runs `acme listen`, and every change reaches
the terminal over the same realtime channel the portal uses, as one line:
who did what to which task. `make demo-cli-gif` records it from a running
`make up` stack (`scripts/record_cli_demo.py`), and refuses to when a line
would wrap.

## Quick start

Requirements: uv, pnpm, Node 24.21.0 (see `.nvmrc`), Docker.

```bash
make up       # everything in containers, migrated and seeded, then prints the URLs
make down     # stop it all; the data stays for the next `make up`
make reset    # wipe all local data and containers, then `make up` again
make urls     # print the URLs and the sign-in again
```

`make up` creates `.env` from `.env.example` if it is missing, and is safe
to rerun: it rebuilds the app images from the working tree and seeds only
once. Once it is up:

| What | URL | Sign-in and what it shows |
|------|-----|---------------------------|
| Portal | http://localhost:55173 | `/login/dev`, the local sign-in by address: `owner@example.test` (owner) or `bob@example.test` (member) of Ajax, or `admin@admin.test` (owner of Fabrikam, admin of Ajax); the three people `make seed` creates. Each also has a personal org, so sign-in shows the org picker; the org chip switches and creates a team org. `/login` signs in through WorkOS when `ACME_WORKOS_API_KEY` is set |
| API | http://127.0.0.1:8000 | Swagger UI at `/docs`, Prometheus metrics at `/metrics` |
| pgweb | http://localhost:58081 | Postgres: schemas `core`, `activity`, `queue`, `admin`; run SQL |
| Valkey Admin | http://localhost:58080 | Valkey: keys, metrics, commands; add a connection to host `valkey`, port `6379`, no username or password |
| ElasticMQ UI | http://localhost:53000 | SQS queues and their messages |
| Grafana | http://localhost:53001 | No sign-in; opens on the Acme overview dashboard over Prometheus, and Jaeger traces |
| Prometheus | http://localhost:59090 | Raw metrics from the api and the worker: scraped as containers, or written in by the devx collector for host processes |
| Jaeger | http://localhost:56686 | Traces, once processes export them (see Dashboards) |
| GlitchTip | http://localhost:58000 | `admin@example.test` / `acme-local`; errors from the api, the worker, and the portal |
| MinIO console | http://localhost:59001 | `acme` / `acmeacme`; the S3 buckets |

This table is the one place the local URLs live; every section below
refers back to it. For one service at a time (rebuild only the API, reset
only the database, open `psql` or `valkey-cli`, follow logs) instead of a
full `down`/`reset`, see
[deployment/local/README.md](deployment/local/README.md). The sections
below are the same steps one at a time, and the host-process alternative
for hot reload.

## The command line

`apps/cli` ships `acme`: one command at a time, or `listen` for what the
team does as it happens. It signs in as a person, through WorkOS in the
browser with a code it shows, or locally as one of the seeded people
above, and keeps one session under `~/.config/acme`; `acme orgs` and
`acme switch <slug>` move it between the orgs a person belongs to.

```bash
uv run acme login --dev-email bob@example.test --org ajax   # the local sign-in; `acme login` goes through WorkOS
uv run acme add "Do groceries"
uv run acme ls
uv run acme done <id>                        # the short id ls shows; also edit, reopen, rm, mv
uv run acme import tasks.csv                 # one task a row; follows the import to its end
uv run acme listen                           # every change, live; --mine for your own tasks
```

See [apps/cli/README.md](apps/cli/README.md) for the conventions and the
exit codes, and [clients/python/README.md](clients/python/README.md) for
the Python client every Python caller goes through.

## Importing, and what ninety days means

A team brings its list along as a CSV file, one task a row: `title`,
and if it likes `notes`, `due_on` (`2026-10-01`), and `assignee_email`,
a member of the org. The portal's Import action beside the task list,
or `acme import`, uploads it, and a worker reads it a hundred rows at
a time while the page shows how far it is. A bad row is skipped and
named. A file too large, too long, or not a CSV file is refused, and
nothing is created. An import that reaches the plan's active tasks
(ten on Free) pauses there, keeping what it made; upgrading resumes it
on its own, and so does Resume after finishing some tasks.

A done task that nobody touched for ninety days is archived once a day.
Each UTC day's cleanup takes the done tasks that were ninety days old
when the day began, so a task that turns ninety during a day goes the
next day. It leaves the Done list and every count, and nothing is deleted: its
notes and its files stay. "Show archived" under the Done list shows it
(`GET /v1/tasks/archived`), and Restore puts it back at the top of Done,
with ninety more days before the next cleanup. Editing or reopening a done task
starts its ninety days again. The number is a setting of the worker,
`ACME_TASKS_ARCHIVE_AFTER_DAYS`.

## Set up

```bash
make setup        # Python and TypeScript dependencies
cp .env.example .env
make infra-up     # Postgres, Valkey, ElasticMQ, MinIO on host ports 55432, 56379, 59324, 59000
make migrate      # every role's migration chain
make seed         # orgs "ajax" and "fabrikam" with the three people of the table above (the SEED_* knobs in .env), and the two local operators, whose tokens go into ~/.config/acme/ops/local.env when it is absent
```

`.env` and `.env.example` are defaults. A variable exported in the shell
wins over both: for every make target, for `scripts/dev.sh`, and for the
processes themselves. A variable on the make command line wins over the
shell. So a second checkout runs its migrations in a database of its own
by exporting the four database URLs at it, and the database `.env` names
is left alone:

```bash
docker exec acme-postgres-1 psql -U postgres -c 'CREATE DATABASE mine OWNER acme'
export ACME_DATABASE_URL=postgresql+asyncpg://acme_runtime:acme_runtime@127.0.0.1:55432/mine
export ACME_DATABASE_SYSTEM_URL=postgresql+asyncpg://acme_system:acme_system@127.0.0.1:55432/mine
export ACME_DATABASE_MIGRATION_URL=postgresql+asyncpg://acme_migration:acme_migration@127.0.0.1:55432/mine
export ACME_DATABASE_MASTER_URL=postgresql+asyncpg://acme:acme@127.0.0.1:55432/mine
make migrate migrate-check seed test-integration
```

## Run

Pick one of two ways; both use the stack and the database from Set up.
Stop one before starting the other, since both use port 8000 for the API.

```bash
scripts/dev.sh    # on the host, with hot reload: API, worker, portal (Vite)
make stack-up     # in containers, built from the working tree: API, worker, portal (nginx)
```

The API is at the same address either way; only the portal's port differs:
`scripts/dev.sh` serves it from Vite on http://localhost:5173, `make
stack-up` from nginx on the port in the table above. Sign in as the
seeded owner and member in two browser windows to see "My tasks" differ from
"Team" and to watch changes arrive live, or as the seeded admin
to switch between orgs, all at `/login/dev`. `/login` is how a person
enters a deployed environment: WorkOS AuthKit (an email code or link,
Google, GitHub, or their company's single sign-on), and a first sign-in
is the sign-up. It lands in the person's personal org, the org chip
creates a team org from there, and the org's settings invite people to
it. Locally it needs the API key of the staging environment's Acme App
application in `ACME_WORKOS_API_KEY`.

### Dashboards

The MinIO console comes with the stack. For debugging, `make devx-up` adds
the developer dashboards of the table above (the compose `devx` profile):
pgweb, Valkey Admin, ElasticMQ UI, Grafana, Prometheus, Jaeger, GlitchTip.
They are wired to the local services and listen on 127.0.0.1 only. Their
ports are the defaults of the `ACME_<DASHBOARD>_PORT` knobs in
`.env.example`; a clash is fixed by setting the knob in `.env`.

Traces are off by default. To send them to Jaeger, uncomment
`ACME_OTEL_ENDPOINT=http://127.0.0.1:54318` in `.env` and restart
`scripts/dev.sh`.

`make down` stops every container, dashboards and app containers included,
and keeps the data for the next `make up`; `make reset` wipes the data too.
Those two are the shortcuts. The steps they wrap run one at a time, which
is what CI takes and what a developer debugging one of them runs:
`make setup`, `make infra-up` and `make infra-down` for the dependencies
alone, `make infra-reset` to recreate them with their volumes removed and
nothing else, `make migrate` and `make seed` for the database, and
`scripts/dev.sh` for the application on the host.

## Check

```bash
make check             # lint, format, types, arch-check, unit tests (the fast gate)
make arch-check        # the guideline's static checks, configured in pyproject.toml
make migrate-check     # every role's ORM metadata against the migrated schema
make test-integration  # the same storage contracts over Postgres, plus migrations
```

## Deploy

`main` is staging and `release` is production. Every merge to `main`
deploys staging with no approval; production moves when a person
dispatches the `release` workflow, which fast-forwards `release` to
`main`, and then approves the production plan. Nobody commits to
`release`. [docs/runbooks/deploy.md](docs/runbooks/deploy.md) has the
steps, the rollback, and the protection to set on the branch.

The company site deploys with them: `https://staging.acme.example` on
staging, `https://acme.example` in production.

A deployed environment carries no seed: `make seed` is local, and so is
the sign-in by address. A person enters staging or production by
signing in at the portal through WorkOS, which creates their identity,
their personal org, and its owner membership the first time, and joins
a team org by invitation. `acme-ops workos-bootstrap` holds each WorkOS
environment's configuration (the redirects, the sign-in page) to
`deployment/workos/environments.yaml`; see
[ops/README.md](ops/README.md).

## Operate

People steer, agents maintain. Every operational task is a skill a
person runs with an agent, and the safety boundary is the credential
the skill holds: an operator reads every signal and writes nothing,
because a change is a pull request. [ops/README.md](ops/README.md) has
the roles, the profiles, the signals in both twins, the `acme-ops`
binary, the nine operational skills (`ops-investigate`, `ops-watch`,
`ops-root-cause`, `ops-infra-as-code`, `ops-cloud-deployment-create`,
`ops-cloud-deployment-nuke`, `ops-simulate-traffic`,
`stress-test-create-or-update`, `stress-test-run`), the six audits
(`audit-retention`, `audit-query-indexes`, `audit-database-calls`,
`audit-credential-lifetimes`, `audit-provider-calls`,
`audit-deploy-time`), which read and report and never fix, and
`tickets-triage`. Each procedure a person follows by hand is a runbook under
[docs/runbooks/](docs/runbooks/README.md).

## Layout

- `om/` the object model: entities, managers, storage, migrations;
  [om/README.md](om/README.md) names the nouns for a reader with no
  code, and each namespace carries a README of its own
- `infra/` cache, buckets, topics, queues, secrets, observability
  ([infra/README.md](infra/README.md)); `integrations/` the third-party
  providers, each an interface, a real client, and a twin: Slack, the
  identity provider, WorkOS, and the payment processor, Stripe
  ([integrations/README.md](integrations/README.md))
- `services/` web services ([services/api/README.md](services/api/README.md));
  `workers/` background roles
  ([workers/maintenance/README.md](workers/maintenance/README.md));
  `apps/` clients: the
  portal, with its generated API types and one transport client under
  `src/api/` (a deadline on every call; the session in the tab's session
  storage, never local storage), the CLI, and the company site
  ([apps/site/README.md](apps/site/README.md)), a static page with no
  script; `clients/python/` the one Python client
- `deployment/` compose, images, Terraform
  ([deployment/README.md](deployment/README.md)); the portal's distribution
  sends the security headers, a `Content-Security-Policy` naming its own
  origin and the API among them
- `ops/` the operators' package and the stress scenarios
  ([ops/README.md](ops/README.md))
- `docs/` as built, ADRs, runbooks; `scripts/` dev.sh, the publisher of the portal and the site, and the by-hand migration runner
- `llms.txt` the knowledge map: which documents each audience is
  served (platform developers, platform operators, tenant users and
  admins); a document is served by being listed there, never by its
  folder
