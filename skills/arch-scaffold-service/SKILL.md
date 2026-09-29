---
name: arch-scaffold-service
description: "Create a second web service beside the API: a domain service hosting some namespaces, or an app-specific service for one app, with its container, settings, routers, image, deployment, and tests, in the shape of the scaffold's API. Python (FastAPI)."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make openapi), Bash(uv run:*), Bash(uv sync:*), Bash(git status:*)
---

# arch-scaffold-service

A path that starts with `../` is read from this skill's folder
as `realpath` resolves it.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../architecture.md`: Interfaces
(Composition by decoration), The Network Layer (How It Starts and Where
It Goes, Web Services as Scalability Units, Domain Services vs
App-Specific Services, Service Interfaces and Impls, The Gateway,
Intra-Service Communication, Direction of Calls), Deployment (Cloud:
AWS, Infrastructure as Code), Cross-Cutting Conventions (Configuration,
The App Container).

## Input

`<service-name> [--namespaces ns1,ns2] [--app portal|admin|cli] [--container]`

`<service-name>` is required; `<svc>` is its snake case. `--namespaces`
makes a domain service hosting those namespaces. `--app` makes an
app-specific service that composes domain operations for one app.
`--container` adds it to the compose file that runs the application in
containers. One of `--namespaces` and `--app` is required; ask when
neither is given.

The first form of a split is not this skill. It is the API's own image,
deployed again with `<NAME>_NAMESPACES` naming the routers it mounts
(`routers/__init__.py` of the API), a deployment change and no code.
Run this skill when the service needs code of its own.

## Created

The shape is the API, `services/api/`. Under `services/<service-name>/`:

| File | Holds |
|------|-------|
| `pyproject.toml`, `README.md` | the distribution `<name>-<service-name>` with its console entry point, and what the service serves |
| `src/<name>/services/<svc>/` | `entry.py`, `main.py`, `app.py`, `container.py`, `settings.py`, and `types/`, `services/`, `services/impl/`, `routers/` for the hosted namespaces or the app's operations |
| `tests/` | `conftest.py`, the health, container, and settings tests, and one module per hosted namespace |
| `deployment/docker/<service-name>.Dockerfile` | the image, shape `api.Dockerfile` |

## Changed

| File | Change |
|------|--------|
| `gateway/`, `pyproject.toml` (root), `services/api/` | the API's `gateway/` package moved into a root distribution, `<name>-gateway`, which both services import; moved, never copied |
| `pyproject.toml` (root) | the member in `[tool.uv.workspace] members` |
| `Makefile` | `openapi` emits this service's document to `services/<service-name>/openapi.json`; `clients/typescript/openapi.json` stays the API's |
| `scripts/dev.sh`, `README.md` (root) | the service started on its port, and its docs in the local URLs |
| `deployment/local/docker-compose.full.yml` (with `--container`) | the service as a container |
| `.env.example` | every field of its settings under the prefix |
| `deployment/terraform/modules/environment/main.tf`, `deployment/terraform/environments/*/` | one instance of the service module beside `module "api"`, with its load balancer route |
| `deployment/terraform/modules/account/variables.tf` | its image repository in `images` |
| `.github/workflows/deploy-staging.yml`, `deploy-production.yml` | its image built once by staging and promoted by digest, beside the API's |

## Procedure

1. Write `pyproject.toml`, add the member, and run `uv sync`. Then move
   the gateway, then write the container, the app, and the routers, in
   that order.
2. The gateway is written once. The move keeps every module and test of
   `services/api/src/<name>/services/api/gateway/`, and both services'
   imports change to the new distribution.
3. A router declares its route and its dependencies, makes one call to
   its service impl, and returns what it returns. The impl translates
   and never decides; a decision moves into a manager, and the output
   says so.
4. When the API stops holding a namespace this service now hosts (its
   code or its database role), the API wires that service's interface
   to its remote impl: the typed client of `clients/python/`, which
   mints the internal credential for each call, carries its timeout,
   and sits behind the breaker of `infra/src/<name>/infra/breaker.py`.
   The routers do not change. A wire hop to a sibling this process
   could call in-process is a decision, recorded as an ADR.
5. An app-specific service sets `AppType.<APP>` on every context it
   builds, and its gateway refuses a request whose app header names
   another app. It composes managers or domain services, and never
   calls another app-specific service.
6. The deployed service holds the runtime and the system logins'
   database URLs as secrets, never the migration login's, which the
   migration task alone holds. Its image repository is a bootstrap
   change, applied by a create run in each account before the first
   deploy that ships the image.

## Output

As `../_shared/scaffold-conventions.md` states.
