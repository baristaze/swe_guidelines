# Acme

Acme is the domain-agnostic core of a multi-tenant system, in the shape
the Software Design and Architecture Guidelines prescribe
([the pin](specs/architecture.md)). It holds what every product needs
before its first domain screen, and [what Acme is made of](om/README.md)
names each part. A product renames it and builds its domain on top.

## Quick start

Requirements: uv, Node (see `.nvmrc`), and Docker. `make setup`
installs pnpm, at the version `package.json` names, when it is missing.

```bash
make up       # the whole stack in containers, migrated and seeded; prints the URLs
make stop     # pause it: the ports free up, the containers and the data stay
make start    # resume it, with no build, migration, or seed; `make up` on a new tree
make down     # remove the containers; the data stays
make reset    # wipe the data and the containers, then `make up`
make urls     # print the URLs and the sign-ins again
```

`make up` ends by printing every local URL and the seeded sign-ins,
and `make urls` prints them again. The portal's `/login/dev` signs in
a seeded person by address alone; `/login` goes through WorkOS once
`ACME_WORKOS_API_KEY` is set. [The local stack](deployment/local/README.md#ports)
has every port, each a knob in `.env.example`: set it in `.env` when a
port is taken.

## Set up and check

```bash
make setup             # Python and TypeScript dependencies
make infra-up          # Postgres (one per database role), Valkey, ElasticMQ, and MinIO alone
make migrate           # the database logins, then every role's migration chain
make seed              # the two orgs, their people, and the local operators
make check             # lint, format, types, arch-check, unit tests
make migrate-check     # every role's ORM metadata against the migrated schema
make test-integration  # the storage contracts over Postgres
```

`make help` lists every target and what it does.

`scripts/dev.sh` runs the processes on the host with hot reload. A
variable exported in the shell wins over `.env`, so a second checkout
points all eight database URLs at a database of its own: the four
shared `ACME_DATABASE_*` URLs and the four `ACME_DATABASE_URL_<ROLE>`,
since a role's own URL wins.
[deployment/local/README.md](deployment/local/README.md) works on one
service at a time.

## Deploy and operate

`main` is staging and `release` is production. A merge to `main`
deploys staging. A person fast-forwards `release` and approves the
production plan. [The deploy runbook](docs/runbooks/deploy.md) has the
steps and the rollback. A deployed environment has no seed: a person
signs in through WorkOS, and a first sign-in is the sign-up.

Each operational task is a skill a person runs with an agent, and the
credential the skill holds is its boundary. [ops/README.md](ops/README.md)
names the roles, the skills, and the audits.
[The runbooks](docs/runbooks/README.md) hold the procedures a person
follows by hand.

## Layout

- [om/](om/README.md): the object model: namespaces, storage, migrations.
- [infra/](infra/README.md): the capabilities the system asks for and
  never implements itself, each with its twin.
- [integrations/](integrations/README.md): the outside providers, their
  twins, and the webhook check.
- `services/`, `workers/`, and `apps/`: one folder per service, worker,
  and client app, each with its README. The API is
  [services/api/](services/api/README.md), the work queue's worker
  [workers/maintenance/](workers/maintenance/README.md), and the shell
  every screen sits in [apps/portal/](apps/portal/README.md).
- `clients/`: [openapi/](clients/openapi/), each namespace's committed API
  document; [typescript/](clients/typescript/README.md), the one client every
  browser app imports; and [python/](clients/python/README.md), the one Python
  client.
- [deployment/](deployment/README.md): compose, images, and Terraform.
- [ops/](ops/README.md): the operators' package, skills, and stress scenarios.
- `docs/`: [adr/](docs/adr/README.md), the decisions, and
  [runbooks/](docs/runbooks/README.md);
  [specs/architecture.md](specs/architecture.md) pins the guideline and
  lists the deviations.
- [llms.txt](llms.txt): the knowledge map, each document by the reader
  it serves.
