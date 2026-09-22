---
name: arch-scaffold-service
description: "Create a web service: the app factory, container, gateway, routers and wire types, service interfaces, health endpoints, the ops CLI, the image, and tests. Python (FastAPI)."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make openapi), Bash(uv run:*), Bash(uv sync:*), Bash(git status:*)
---

# arch-scaffold-service

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Interfaces
(Composition by decoration), OpContext (The Operator Context), The
Business Layer (Shape of an
Operation), The Network Layer (How It Starts and Where It Goes, Web Services as Scalability Units, Domain Services vs
App-Specific Services, Service Interfaces and Impls, The Gateway,
Intra-Service Communication, Public Types, Realtime at the Edge),
Deployment (Cloud: AWS, Infrastructure as Code), Operations (Operator
Roles, Operational Skills), Monorepo Folder Structure (Layout
Conventions), Cross-Cutting Conventions (Exceptions, Configuration,
The App Container).

## Input

`[service-name] [--namespaces ns1,ns2] [--app portal|admin|cli] [--realtime] [--container]`

`<service-name>` defaults to `api`. With no flags the service hosts
every existing namespace: the first API process of a system.
`--namespaces` makes a domain service hosting only those namespaces.
The first form of a split is not this skill: it is the API process's
own image with its `namespaces` setting naming the routers it mounts,
as The Network Layer (Web Services as Scalability Units) states. Run
this skill when the service needs code of its own.
`--app` makes an app-specific service that composes domain operations
for one app; it sets `AppType.<APP>` on every context it builds and the
gateway rejects a request whose app header names another app.
`--realtime` adds the realtime channel; without it the service has no
socket, and the portal cannot connect to it until one is added.
`--container` adds the service to the second compose file, the one
that runs the application in containers for the case that asks for it;
`scripts/dev.sh` starts it on the host either way, as Deployment
(Local: Docker Compose) states.

`<svc>` is the service name in snake case, and `<ns_singular>` a
namespace's singular in snake case, as `arch-scaffold-namespace`
names it.

## Created

Under `services/<service-name>/`. The file-by-file lists are long, so
each one lives beside this file and is read by the step that names it,
when that step runs and not before.

| Reference                                      | Holds                                                                                                                                                       | Read by                  |
|------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------|
| `${CLAUDE_SKILL_DIR}/references/process.md`    | the distribution and its settings, the container, the app factory and its middleware order, the console entry point and its subcommands, the image, and the process tests | step 1                   |
| `${CLAUDE_SKILL_DIR}/references/gateway.md`    | the `gateway/` package: credential parsing and the context dependencies, the operator gate, the error handlers, the rate limit, edge idempotency, and the request-id middleware | step 1                   |
| `${CLAUDE_SKILL_DIR}/references/routes.md`     | the routers, the wire types, the service interfaces and their in-process impls, and the tests that drive them                                                    | step 2                   |
| `${CLAUDE_SKILL_DIR}/references/realtime.md`   | the `realtime/` package (ticket, socket, send buffer, envelopes), the replay route, and their tests                                                              | step 2, with `--realtime` |

A reference file is detail. These are the lines a run must never miss,
so they stay here:

- The gateway is written once. A later service imports the root
  `gateway/` distribution the `Changed` table moves it into and writes
  none of those files, because an edge concern is done once.
- The service interface and its impl exist from the single-process
  start, so a split later moves a module instead of extracting one;
  routers hold no logic of their own at any stage.
- Every creating route declares the gateway's `Idempotency-Key`
  dependency, in every hosted namespace and not only the first's, as
  the conventions state.
- `/healthz` is liveness and `/readyz` is readiness. The readiness
  route awaits the storage healthcheck under a deadline of its own,
  shorter than the interval it is polled on, and answers negative
  rather than hanging; every image's healthcheck is on `/healthz`.
- Admission is the process defending itself, so it fails closed where
  the rate limit fails open, and the health, readiness, and metrics
  routes are outside both budgets.
- The interactive API documentation is served only when the settings
  name the `local` environment, and is `None` in every deployed one.
- No socket frame carries a field of the entity. A frame is a hint,
  and a client reads the entity through the authorized read.

## Changed

| File                                    | Change                                                                          |
|-----------------------------------------|---------------------------------------------------------------------------------|
| `pyproject.toml` (root)                 | the member added to `[tool.uv.workspace] members`                                |
| `Makefile`                              | the `openapi` target emits this service's document into `services/<service-name>/openapi.json`, one file per service, so no service overwrites another's; the document of the service a browser app calls is the one the app skill copies into `apps/<portal>/openapi.json`, and a later service never writes that file |
| `scripts/dev.sh`                        | starts the service on its port                                                   |
| `README.md` (root)                      | a row in the `Local URLs` table: the service's interactive API docs at `http://localhost:<port>/docs` |
| `deployment/local/docker-compose.full.yml` (with `--container`) | the service as a container                                 |
| `deployment/realtime-timeouts.json` (with `--realtime`, when absent) | the ping interval and the load balancer idle timeout, `{"ping_interval_seconds": 25, "idle_timeout_seconds": 60}`, the ping well inside the timeout, the one shared file a service test and a client test both assert against; the portal asserts its half from the client side |
| `.env.example`                          | every field of the service's settings under its prefix, with its local value, which `tests/test_settings.py` holds |
| `deployment/terraform/modules/`, `deployment/terraform/environments/*/` (unless the environment already declares this process) | one instance of the service module per environment for this process: its ECS service with rollout limits, its log group with retention, its target-tracking autoscaling behind the root switch `autoscaling_enabled`, its running-tasks-below-desired alarm on the environment's alarm topic, its load balancer route, the runtime login's and the system login's database URLs wired as secrets, never the migration login's, which the one-off migration task alone holds, and every setting without a local default passed as a variable or wired as a secret, as Deployment (Infrastructure as Code) states for every process |
| `.github/workflows/deploy-staging.yml`, `deploy-production.yml` | the service's image: built and pushed under the commit by the staging workflow's push-only build job, which records its digest on the repository host's deployment record; promoted by that digest for the release commit in the production workflow's plan, checked against the record, never rebuilt; a new service's image repository is a bootstrap root change, applied by a create run in each account |
| `services/<existing>/gateway/`, `gateway/pyproject.toml`, `pyproject.toml` (root) (when a service already exists) | the first service's `gateway/` package is moved into a root distribution `gateway/` of its own, `<root>-gateway`, with its `pyproject.toml`, added to `[tool.uv.workspace] members` and declared under `[tool.uv.sources]` by both services, which import it; it is moved, never copied, as The Gateway states, so this service writes no `gateway/` package of its own and the `Created` rows for one are skipped |

## Procedure

1. Read `${CLAUDE_SKILL_DIR}/references/process.md` and
   `${CLAUDE_SKILL_DIR}/references/gateway.md`. Then write
   `pyproject.toml`, add the member to the workspace, and run
   `uv sync`; then the container before the app, the app before the
   routers, and the gateway before the routers that depend on it.
2. Read `${CLAUDE_SKILL_DIR}/references/routes.md`, and, with
   `--realtime`, `${CLAUDE_SKILL_DIR}/references/realtime.md`.
   A router function declares the route (path, verb, status, and the
   dependencies that mint the context and the idempotency key), calls
   one operation of its service impl with the context, the request
   type, and, on a creating route, the id the `Idempotency-Key`
   dependency minted before the marker, and returns what the impl
   returns; it translates nothing and
   decides nothing. The impl translates: it builds the entity or the
   arguments from the request, calls one manager, and projects the
   result onto a view. The router calls through the service interface
   from the first day, so a split is a wiring change and never a
   rewrite of the routers. A partial update is the impl's translation:
   it reads the current entity through the manager's `get_*`, copies
   the request's set fields onto it, an absent field meaning unchanged
   and an explicit null meaning cleared where the field is optional,
   and hands the whole entity to the manager; that policy is the
   request type's contract, as The Business Layer (Shape of an
   Operation) states. When a router or an impl starts deciding, move
   the decision into a manager and say so in the output.
3. An app-specific service composes managers or sibling domain service
   clients for one app only and never calls another app-specific
   service.
4. Emit the OpenAPI document with `make openapi` so the consuming apps
   regenerate their types.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
