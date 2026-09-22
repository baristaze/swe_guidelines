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

`<svc>` is the service name in snake case.

## Created

Under `services/<service-name>/`:

| File                                          | Holds                                                                                          |
|-----------------------------------------------|------------------------------------------------------------------------------------------------|
| `pyproject.toml`                              | the distribution; dependencies on `<root>-om`, `<root>-infra`, `fastapi`, `uvicorn`, `pydantic-settings`, `httpx` (dev); `[tool.uv.sources]` for the workspace members; a console entry point |
| `src/<root>/services/<svc>/__init__.py`       | empty                                                                                          |
| `src/<root>/services/<svc>/settings.py`       | one `BaseSettings` with the product prefix, including `namespaces` (the routers this process mounts; empty means every hosted one, so the first form of a split is a deployment change and no code change, as The Network Layer (Web Services as Scalability Units) states), `app_type` for `--app`, `allowed_origins` (the browser apps' origins; empty refuses every cross-origin request), and one timeout per transport client the service holds (a sibling service's typed client at a split), read by the client's constructor so no call goes out without one, and `request_deadline`, the bound the gateway puts on every request, so nothing runs unbounded (a work handler is bounded by its lease), `max_in_flight_reads` and `max_in_flight_writes`, the two admission budgets, one for `GET` and `HEAD` and one for every other method, and the control lane's own bound with `--realtime`, the deadline the readiness probe puts on its healthcheck, and, per transport client, the breaker's consecutive-failure bound and its cool-down; on the service that hosts `tenancy`, `signup_enabled` (true by default, since a deployed environment has no other door; false closes sign-up), which the container hands to the tenancy manager's options |
| `src/<root>/services/<svc>/container.py`      | `AppContainer.build(settings)` (storage, then infra, then managers), `for_tests(storage, infra)`, `start()`, `close()` |
| `src/<root>/services/<svc>/app.py`            | `create_app(container=None)`: settings first, then logging, error reporting, trust store, and tracing (one `boot(settings)` helper every subcommand calls), then the container, then middleware in fixed order (CORS from `allowed_origins` first; the request-id middleware opens the server span; the deadline middleware bounds the request at `request_deadline` from settings; the admission middleware refuses past `max_in_flight_reads` for `GET` and `HEAD` and `max_in_flight_writes` for everything else, both from settings, at once and in the unavailable shape, since admission is the process defending itself and fails closed where the rate limit fails open, and a storm of reads must not take every slot from the commands; the health, readiness, and metrics routes are outside both budgets), routers under `/v1`, health routes (`/readyz` awaiting the storage healthcheck under a deadline of its own, shorter than the interval it is polled on, a timeout answering negative and never hanging), lifespan calling `start()` and `close()` |
| `src/<root>/services/<svc>/gateway/__init__.py` | empty                                                                                        |
| `src/<root>/services/<svc>/gateway/auth.py`   | credential parsing by prefix (the `internal` kind included: the short-lived credential a peer service mints per call, naming the principal, the tenant, and the request id, from which this gateway rebuilds `OpContext` like any other kind; no bare header is trusted), the `request_context` dependency minting `RequestContext` from the middleware's request id, the app headers, and the span (`Rctx` alias, for the sign-in route), `current_context` running `tenancy.authenticate(rctx, bearer)` and raising `NotAuthenticated` (401) when no credential or an invalid one is presented (`Ctx` alias), `current_identity` running `tenancy.authenticate_login(rctx, bearer)` into `IdentityContext` from the login credential or from a live session, as the session's own identity, a revoked or expired session refused (`Identity` alias, for the memberships and exchange routes and the operator gate), the app-header check; `socket_context` (with `--realtime`), the websocket route's dependency: it mints the request stage from the websocket scope and redeems the ticket through the tenancy manager, which returns `OpContext` by the same rules as `current_context` (the app-header check included, so an unknown app is refused); the socket route never parses the query itself |
| `src/<root>/services/<svc>/gateway/admin.py`  | the operator gate over `current_identity`, running `tenancy.admit_operator(identity)` into `OperatorContext` for `/v1/admin/*`, which refuses an identity established from a session, since the operator plane admits only the person's own sign-in, `OperatorCtx` alias |
| `src/<root>/services/<svc>/gateway/errors.py` | the `PlatformException` and `InfraException` handlers and the catch-all, all writing `{"error": {code, message, request_id}}` with the status and the code the exception carries |
| `src/<root>/services/<svc>/gateway/ratelimit.py` | `rate_limited(route)` reading the route's limit and window from one frozen options object built from settings at boot and held on the container, over `CacheInterface.increment` under the system scope, keyed on the credential id (an unauthenticated route keys on the client address, an inbound-webhook route on a digest of its path token), failing open |
| `src/<root>/services/<svc>/gateway/idempotency.py` | the `Idempotency-Key` dependency over the OM's idempotency manager: `begin` before the call (it writes the pending marker with a digest of the request, the id the create will use, and an attempt token minted with it; a replay returns the stored response; a duplicate in flight is a conflict; another request digest under the same key is refused; a `5xx` releases the marker instead of being stored, the release keeping the marker with its digest and its `target_id` and clearing only the attempt, so the retry runs again and finds a row that landed by the same id; a marker whose attempt is past the pending lease is taken over in one conditional write that stamps an attempt token of its own, the lease measured from the attempt token, a `uuid_v7` carrying the moment it was minted, and never from the marker's `created_at`, so a marker handed on twice is not stale for having been minted long ago, and the call runs again with the marker's `target_id`, the id minted before `begin` that the create uses) and `finish` after it, with `finish`, the release, and the one write of a rerun that changes what is stored (the re-mint of a secret) each conditional on the attempt token in the statement itself, so the attempt that lost the marker is refused like a worker whose lease has passed and cannot overwrite the secret the retry issued; keyed per tenant and user on a durable unique index; the cache is at most a read-through in front of it. The outcome stored for a create that issued a secret (an API key, a session token, a socket ticket) is the view with the secret absent, so a replay answers with the row and no secret and says so in its header; the secret exists in one place, as a digest, and a client that lost the first response revokes and issues another. Every creating route in every hosted namespace declares it, not only the first namespace's, so a retried create returns the stored response; creating is what the request leaves behind and not what it answers with, so a `POST` that writes a durable row declares it whether it answers 201 with the row or 202 with the id of work now running |
| `src/<root>/services/<svc>/gateway/observability.py` | request id middleware (accept or mint, stamp, echo, log context, span) over HTTP and websocket scopes alike, so a socket's context carries a request id too, metrics            |
| `src/<root>/services/<svc>/routers/__init__.py` | `all_routers(namespaces=())`, returning every hosted namespace's router when the setting is empty and only the named ones otherwise; `app.py` passes `settings.namespaces`, and a name the process does not host refuses the boot |
| `src/<root>/services/<svc>/routers/<ns>.py`   | one module per hosted namespace: each route declares its path, verb, status, and dependencies (the context, and the `Idempotency-Key` on a route that writes a durable row), calls one operation of the service impl, and returns what it returns; no translation, no decision; the tenancy router (the service that hosts `tenancy`) declares the sign-up route `POST /v1/auth/signup` under `Rctx`, rate-limited like the sign-in and with no `Idempotency-Key` (body `email`, `password`, `display_name`, `org_name`, and `org_slug`; answered `200` with the sign-in's answer; an email or a slug already held is `409`; `404` while `signup_enabled` is false), the sign-in route `POST /v1/auth/login` under `Rctx` (body `email` and `password`; the answer's `token` is the identity-stage bearer and its `memberships` lists a `MembershipChoiceView` per membership: `org_id`, `org_name`, `org_slug`, `role`), the memberships route `GET /v1/auth/memberships?limit=` under `Identity` (the same views, bounded like every list), and the exchange route `POST /v1/auth/exchange` under `Identity` (body `org_id`, answered with the tenant session's `token`; presented with a session, it ends that session in the same write), which the operational skills rely on |
| `src/<root>/services/<svc>/routers/admin.py`, `types/admin.py`, `services/admin.py`, `impl/admin.py` (the service that hosts `tenancy`) | the operator plane: every route under `/v1/admin/*` behind the operator gate's `OperatorCtx`, calling one operation of `AdminServiceImpl`, which calls the tenancy manager's operator operations: `GET /v1/admin/me` (the identity and its `operator_role`, `read` or `write`), `GET /v1/admin/size` (tenants, users, and the rows written in the last day), `GET /v1/admin/orgs` and `GET /v1/admin/orgs/{org_id}`, `GET /v1/admin/orgs/{org_id}/members`, `GET /v1/admin/orgs/{org_id}/events?after_seq=&limit=` (the operator's feed, carrying `request_id` and `app` beside the actor), and one `GET /v1/admin/orgs/{org_id}/<entities>` per entity the product exposes on the plane; the provisioning a `write` allowlist entry is for, `POST /v1/admin/orgs` and `POST /v1/admin/orgs/{org_id}/members`, each declaring the `Idempotency-Key` dependency like every creating route and refused for a `read` entry; the tenant is a parameter of every read and never a context, since no `OpContext` exists on this path, and each read of a tenant's rows is logged with the tenant and the operator, so support access has a trail, as OpContext (The Operator Context) and Operations (Operational Skills) state |
| `src/<root>/services/<svc>/types/common.py`   | `View`, `RequestBody`, `ErrorBody`, `ErrorResponse`                                            |
| `src/<root>/services/<svc>/types/<ns>.py`     | views and requests per hosted namespace                                                        |
| `src/<root>/services/<svc>/services/<ns>.py`  | `<Ns>ServiceInterface`, the network operations of the namespace, and `ServicesInterface` (the root with one getter per service) in `services/__init__.py` |
| `src/<root>/services/<svc>/impl/<ns>.py`      | `<Ns>ServiceImpl`, the in-process impl of `<Ns>ServiceInterface`, which exists from the first day: every router calls one operation of it, and each operation translates, building the entity or the arguments from the request, calling one manager the container wired, and projecting the result onto a view, as The Network Layer (Service Interfaces and Impls) states; it composes across services where a workflow needs it and never decides. When this process stops holding what a sibling needs (its code, its database role), that sibling's `*ServiceInterface` is wired to its remote impl instead, the typed client of `clients/python/`, which mints the internal credential for each call and carries the timeout its settings name, and the container wires that remote impl behind a breaker, an impl of the same interface holding it that counts consecutive failures, refuses at once for a cool-down past a bound from settings, and lets one call through to decide whether to close, as Interfaces (Composition by decoration) states, since a sibling that is down otherwise turns every call into a full timeout and the timeouts exhaust this process's pool; the routers do not change, and a wire hop to a sibling this process could call in-process is a recorded decision |
| `src/<root>/services/<svc>/realtime/` (with `--realtime`) | `ticket.py` (the route that asks the tenancy manager to issue the ticket; redemption is the manager's, atomic, and re-checks the credential named by `ctx.security.credential_id`), `socket.py` (the route, resolving its context through the gateway's `socket_context` dependency and never parsing the query itself, then subscribe, unsubscribe, ping; the socket is bounded by its session's expiry, a deadline set when the ticket is redeemed that closes it at that instant whatever the client does, and it is closed on the `ENTITY_CHANGED` frame that names its session revoked or its user's membership ended, which every process holding sockets reads from the topic bus; the expiry covers a frame that was missed), `send_buffer.py` (the per-socket send buffer and drainer, bounded in two lanes: control frames first, a full buffer dropping the oldest stream frame and never a control frame, the control lane bounded on its own from settings and its overflow logged as such, the revocation close and the transport keepalive staying outside the buffer, as The Network Layer (Realtime at the Edge) states), `envelopes.py` (typed envelopes on the `View` base with a curated payload view, never a dumped internal payload, carrying the event's `seq`; the portal's `envelopes.ts` mirrors it by hand, since socket frames are not in OpenAPI) |
| `src/<root>/services/<svc>/routers/events.py` (with `--realtime`) | `GET /v1/events?after_seq=&limit=` over the events manager, the replay a reconnecting client uses |
| `src/<root>/services/<svc>/main.py`           | `serve`, `migrate` (forwards to the OM migration CLI), `bootstrap` (org, slug, email, password, display name, `--operator`; with `--seed`, reads the development org and owner from settings, and the two local operator identities, one on a `read` allowlist entry and one on a `write` entry, refuses at start when the database URL is not a local address, and exits without writing when the org's slug already exists), `openapi` subcommands |
| `tests/conftest.py`                           | the app over `AppContainer.for_tests(StorageMemoryImpl(), InfraLocalImpl(tmp_path))` and `httpx.AsyncClient(transport=ASGITransport(app=app))`, run inside the lifespan |
| `tests/test_health.py`                        | `/healthz` and `/readyz`, the readiness probe answering negative when the storage healthcheck passes its deadline          |
| `tests/test_container.py`                     | the build-once test of Cross-Cutting Conventions (The App Container): the managers build once for any number of requests, and every root member exists after `build`, none built on first use |
| `tests/test_settings.py`                      | every field of the service's settings appears in `.env.example` under its prefix, and every environment under `deployment/terraform/environments/` sets or wires as a secret every field that has no local default |
| `tests/test_<ns>_api.py`                      | one round trip per hosted namespace (seeding an org and its owner through the tenancy manager first), plus the error envelope and one rate-limited route; on the service that hosts `tenancy`: a sign-up that answers as a sign-in with one membership and enters through the exchange, a second sign-up with the same email refused `409`, a closed sign-up answered `404`, the memberships listed under the login credential and under a live session, and a switch by a second exchange after which the presented session is refused `401` |
| `tests/test_admin_api.py` (the service that hosts `tenancy`) | an identity off the allowlist refused, a portal session refused though its identity is on the allowlist, a `read` entry refused on each provisioning route, a `write` entry creating an org and a member, and a read of a tenant's rows leaving a log record naming the tenant and the operator |
| `tests/test_realtime_timeouts.py` (with `--realtime`) | asserts the ping interval and idle timeout against `deployment/realtime-timeouts.json`, the service half of the shared-file rule |
| `tests/test_socket_close.py` (with `--realtime`) | a socket is closed when its session's expiry passes while the client keeps pinging, and when the revocation or membership-end frame arrives on the topic bus |
| `deployment/docker/<service-name>.Dockerfile` | two stages on a base image at the Python release `.python-version` names, locked install of this package, non-root, healthcheck on `/healthz` |

The `gateway/` rows are the first service's only. A later service
imports the root `gateway/` distribution the `Changed` table moves it
into and writes none of those files, because an edge concern is done
once.

The service interface and impl exist from the single-process start,
so a split later moves a module instead of extracting one; routers
hold no logic of their own at any stage.

## Changed

| File                                    | Change                                                                          |
|-----------------------------------------|---------------------------------------------------------------------------------|
| `pyproject.toml` (root)                 | the member added to `[tool.uv.workspace] members`                                |
| `Makefile`                              | the `openapi` target emits this service's document into `apps/<portal>/openapi.json` when a browser app exists, else next to the service; the app skill moves it |
| `scripts/dev.sh`                        | starts the service on its port                                                   |
| `README.md` (root)                      | a row in the `Local URLs` table: the service's interactive API docs at `http://localhost:<port>/docs` |
| `deployment/local/docker-compose.full.yml` (with `--container`) | the service as a container                                 |
| `deployment/realtime-timeouts.json` (with `--realtime`, when absent) | the ping interval and the load balancer idle timeout, the one shared file a service test and a client test both assert against; the portal asserts its half from the client side |
| `.env.example`                          | every field of the service's settings under its prefix, with its local value, which `tests/test_settings.py` holds |
| `deployment/terraform/modules/`, `deployment/terraform/environments/*/` (unless the environment already declares this process) | one instance of the service module per environment for this process: its ECS service with rollout limits, its log group with retention, its target-tracking autoscaling behind the root switch `autoscaling_enabled`, its running-tasks-below-desired alarm on the environment's alarm topic, its load balancer route, and every setting without a local default passed as a variable or wired as a secret, as Deployment (Infrastructure as Code) states for every process |
| `.github/workflows/deploy-staging.yml`, `deploy-production.yml` | the service's image: built and pushed under the commit by the staging workflow, which records its digest by commit; promoted by that digest for the release commit in the production workflow's plan, never rebuilt |
| `services/<existing>/gateway/`, `gateway/pyproject.toml`, `pyproject.toml` (root) (when a service already exists) | the first service's `gateway/` package is moved into a root distribution `gateway/` of its own, `<root>-gateway`, with its `pyproject.toml`, added to `[tool.uv.workspace] members` and declared under `[tool.uv.sources]` by both services, which import it; it is moved, never copied, as The Gateway states, so this service writes no `gateway/` package of its own and the `Created` rows for one are skipped |

## Procedure

1. Write `pyproject.toml`, add the member to the workspace, and run
   `uv sync`; then the container before the app, the app before the
   routers.
2. A router function declares the route (path, verb, status, and the
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
