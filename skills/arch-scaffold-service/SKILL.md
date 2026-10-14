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
| `src/<name>/services/<svc>/` | `entry.py`, `main.py`, `app.py`, `container.py`, `settings.py`, `resolve.py` (its service getters, shape the API's), and `types/`, `services/`, `services/impl/`, `routers/` for the hosted namespaces or the app's operations |
| `tests/` | `conftest.py`, the health, container, and settings tests, and one module per hosted namespace |
| `deployment/docker/<service-name>.Dockerfile` | the image, shape `api.Dockerfile` |

## Changed

| File | Change |
|------|--------|
| `gateway/`, `pyproject.toml` (root), `services/api/` | the API's `gateway/` package moved into a root distribution, `<name>-gateway`, which both services import; moved, never copied. It imports no service: its container is a `Protocol` it declares in its `resolve.py`, with the members the gateway reads (the settings, the managers, the infra, the rate limits, the refused addresses); the error envelope of the API's `types/common.py` (`ErrorBody`, `ErrorResponse`, and the details they carry) and `SignedDelivery` of its `services/webhooks.py` move into the gateway, and the API imports them from there; the service getters of `gateway/resolve.py` (`MediaService` and the rest) move into the API's own `resolve.py` |
| `services/api/src/<name>/services/api/openapi.py` | moved once into the gateway distribution and generalized: it takes a service's `types` package and its `HOSTED`, and the envelope's module is `common`'s; each service's `openapi` command calls it with its own, never a copy of it |
| `pyproject.toml` (root) | the member in `[tool.uv.workspace] members` |
| `Makefile` | `openapi` emits this service's documents through the one emitter, one per namespace it hosts and `common.json`, to `services/<service-name>/openapi/`; `clients/openapi/` stays the API's |
| `.github/workflows/ci.yml` | the drift step's pathspec names `services/<service-name>/openapi` beside `clients`, in both its `git status --porcelain` calls, so a change or a new file there fails it |
| `scripts/dev.sh`, `Makefile` (`urls`) | the service started on its port, and its docs in the local URLs |
| `deployment/local/docker-compose.full.yml` (with `--container`) | the service as a container |
| `.env.example` | every field of its settings under the prefix; its host and port are its own, `<NAME>_<SVC>_HOST` and `<NAME>_<SVC>_PORT` (`<SVC>` the snake case in capitals, fields `<svc>_host` and `<svc>_port`), never the API's `<NAME>_HOST` and `<NAME>_PORT`, so both run in one environment |
| `deployment/terraform/modules/environment/main.tf`, `deployment/terraform/environments/*/` | one instance of the service module beside `module "api"`, and beside it its `aws_lb_target_group` and an `aws_lb_listener_rule` on the HTTPS listener for the paths it serves, at a priority after `hide_metrics`'s 1; the API stays the listener's default action. `module.<svc>.service_name` joins both `service_names` lists, the dashboard's and the alarms' |
| `deployment/terraform/modules/load_balancer/outputs.tf` | `https_listener_arn`, the listener the rule attaches to |
| `deployment/terraform/modules/environment/variables.tf`, `main.tf`, `environments/*/main.tf`, `deployment/cloud/README.md` | the service's pool, `<svc>_database_pool_size`, passed as its `<NAME>_DATABASE_POOL_SIZE`, and its autoscaling ceiling. Each environment root's comment and the README's rule add twice its ceiling × 2 pools × its pool size, and the sum still stays under the instance's `max_connections` |
| `deployment/terraform/modules/account/variables.tf` | its image repository in `images` |
| `.github/workflows/deploy-staging.yml`, `deploy-production.yml` | its image built once by staging and promoted by digest, beside the API's; production resolves, verifies, and keeps its digest, and the rollback swaps it back to the digest it recorded |
| `.github/workflows/ci.yml`, `scripts/cloud_create.sh` | the service in the image matrix, and `images build (<service-name>)` in the required checks and the service in the loop over `api maintenance` |
| `scripts/cloud_nuke.sh`, `.agents/skills/ops-infra-as-code/SKILL.md` | `-var "<svc>_image=..."` beside `api_image`: the script reads it from the state and refuses when it is missing, as it does `api_image`, and the skill's plan names it |
| `deployment/docker/api.Dockerfile` | the gateway's `pyproject.toml` and its folder, copied as the API's own are |
| `pyrightconfig.json`, `ruff.toml`, `pyproject.toml` (root) | the gateway's and the service's `src` and `tests` in pyright's `include` (the service's tests in `extraPaths`), in ruff's `src`, and in pytest's `testpaths` |
| each test that names the gateway's old path (`grep -rln '<name>.services.api.gateway' .`) | its import, from the gateway distribution |

## Procedure

1. Write `pyproject.toml` and the package's sources, add the member,
   then run `uv sync --all-packages`. A distribution `uv sync` installed
   before its sources existed is installed again with
   `uv sync --all-packages --reinstall-package <its distribution>`. Then
   move the gateway the same way, then write the container, the app, and
   the routers, in that order.
2. The gateway is written once. The move keeps every module of
   `services/api/src/<name>/services/api/gateway/`, and both services'
   imports change to the new distribution. Its tests stay in
   `services/api/tests/`, since they run over the API's app, and the
   gateway gets one test of its own: a static scan, shape
   `infra/tests/test_observability_boundary.py`, that its modules import
   their own package, the object model, infra, and integrations, and no
   service.
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
