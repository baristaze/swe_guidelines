---
name: arch-scaffold-service
description: "Create a web service the way the Software Design and Architecture Guidelines prescribe, either the first API process of a system or a domain or app-specific service split out of it: the app factory, container, gateway, per-namespace routers and wire types, service interfaces, health endpoints, the ops CLI entry point, the image, and tests. Stack: Python (FastAPI, Pydantic, SQLAlchemy)."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make test-unit), Bash(make openapi), Bash(uv run:*), Bash(uv sync:*), Bash(git status:*), Bash(git diff:*)
---

# arch-scaffold-service

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: The Network
Layer (How It Starts and Where It Goes, Domain Services vs App-Specific
Services, Service Interfaces and Impls, The Gateway, Public Types,
Realtime at the Edge), Monorepo Folder Structure (Layout Conventions),
Cross-Cutting Conventions (Exceptions, Configuration, The App
Container).

## Input

`[service-name] [--namespaces ns1,ns2] [--app portal|admin|cli] [--realtime] [--container]`

`<service-name>` defaults to `api`. With no flags the service hosts
every existing namespace: the first API process of a system.
`--namespaces` makes a domain service hosting only those namespaces.
`--app` makes an app-specific service that composes domain operations
for one app; it sets `AppType.<APP>` on every context it builds and the
gateway rejects a request whose app header names another app.
`--realtime` adds the realtime channel; without it the service has no
socket, and the portal cannot connect to it until one is added.
`--container` adds the service to the local compose file; without it
`scripts/dev.sh` starts it on the host.

`<svc>` is the service name in snake case.

## Created

Under `services/<service-name>/`:

| File                                          | Holds                                                                                          |
|-----------------------------------------------|------------------------------------------------------------------------------------------------|
| `pyproject.toml`                              | the distribution; dependencies on `<root>-om`, `<root>-infra`, `fastapi`, `uvicorn`, `pydantic-settings`, `httpx` (dev); `[tool.uv.sources]` for the workspace members; a console entry point |
| `src/<root>/services/<svc>/__init__.py`       | empty                                                                                          |
| `src/<root>/services/<svc>/settings.py`       | one `BaseSettings` with the product prefix, including `app_type` for `--app`, `allowed_origins` (the browser apps' origins; empty refuses every cross-origin request), and one timeout per transport client the service holds (a sibling service's typed client at a split), read by the client's constructor so no call goes out without one, and `request_deadline`, the bound the gateway puts on every request, so nothing runs unbounded (a work handler is bounded by its lease) |
| `src/<root>/services/<svc>/container.py`      | `AppContainer.build(settings)` (storage, then infra, then managers), `for_tests(storage, infra)`, `start()`, `close()` |
| `src/<root>/services/<svc>/app.py`            | `create_app(container=None)`: settings first, then logging, error reporting, trust store, and tracing (one `boot(settings)` helper every subcommand calls), then the container, then middleware in fixed order (CORS from `allowed_origins` first; the request-id middleware opens the server span; the deadline middleware bounds the request at `request_deadline` from settings), routers under `/v1`, health routes, lifespan calling `start()` and `close()` |
| `src/<root>/services/<svc>/gateway/__init__.py` | empty                                                                                        |
| `src/<root>/services/<svc>/gateway/auth.py`   | credential parsing by prefix (the `internal` kind included: the short-lived credential a peer service mints per call, naming the principal, the tenant, and the request id, from which this gateway rebuilds `OpContext` like any other kind; no bare header is trusted), the `request_context` dependency minting `RequestContext` from the middleware's request id, the app headers, and the span (`Rctx` alias, for the sign-in route), `current_context` running `tenancy.authenticate(rctx, bearer)` and raising `NotAuthenticated` (401) when no credential or an invalid one is presented (`Ctx` alias), `current_identity` running `tenancy.authenticate_login(rctx, bearer)` into `IdentityContext` (`Identity` alias, for the exchange route and the operator gate), the app-header check; `socket_context` (with `--realtime`), the websocket route's dependency: it mints the request stage from the websocket scope and redeems the ticket through the tenancy manager, which returns `OpContext` by the same rules as `current_context` (the app-header check included, so an unknown app is refused); the socket route never parses the query itself |
| `src/<root>/services/<svc>/gateway/admin.py`  | the operator gate over `current_identity`, running `tenancy.admit_operator(identity)` into `OperatorContext` for `/v1/admin/*`, `OperatorCtx` alias |
| `src/<root>/services/<svc>/gateway/errors.py` | the `PlatformException` handler and the catch-all, both writing `{"error": {code, message, request_id}}` |
| `src/<root>/services/<svc>/gateway/ratelimit.py` | `rate_limited(route)` reading the route's limit and window from one frozen options object built from settings at boot and held on the container, over `CacheInterface.increment` under the system scope, keyed on the credential id (an unauthenticated route keys on the client address, an inbound-webhook route on a digest of its path token), failing open |
| `src/<root>/services/<svc>/gateway/idempotency.py` | the `Idempotency-Key` dependency over the OM's idempotency manager: `begin` before the call (it writes the pending marker with a digest of the request, the id the create will use, and an attempt token minted with it; a replay returns the stored response; a duplicate in flight is a conflict; another request digest under the same key is refused; a `5xx` releases the marker instead of being stored, so the retry runs again; a pending marker past the pending lease is taken over in one conditional write that stamps an attempt token of its own, and the call runs again with the marker's `target_id`, the id minted before `begin` that the create uses) and `finish` after it, both `finish` and the release conditional on the attempt token in the statement itself, so the attempt that lost the marker is refused like a worker whose lease has passed; keyed per tenant and user on a durable unique index; the cache is at most a read-through in front of it. The outcome stored for a create that issued a secret (an API key, a session token, a socket ticket) is the view with the secret absent, so a replay answers with the row and no secret and says so in its header; the secret exists in one place, as a digest, and a client that lost the first response revokes and issues another. Every creating route (every `POST` that answers 201) in every hosted namespace declares it, not only the first namespace's, so a retried create returns the stored response |
| `src/<root>/services/<svc>/gateway/observability.py` | request id middleware (accept or mint, stamp, echo, log context, span) over HTTP and websocket scopes alike, so a socket's context carries a request id too, metrics            |
| `src/<root>/services/<svc>/routers/__init__.py` | `all_routers()`                                                                              |
| `src/<root>/services/<svc>/routers/<ns>.py`   | one module per hosted namespace, translating only                                              |
| `src/<root>/services/<svc>/types/common.py`   | `View`, `RequestBody`, `ErrorBody`, `ErrorResponse`                                            |
| `src/<root>/services/<svc>/types/<ns>.py`     | views and requests per hosted namespace                                                        |
| `src/<root>/services/<svc>/services/<ns>.py`  | `<Ns>ServiceInterface`, the network operations of the namespace, and `ServicesInterface` (the root with one getter per service) in `services/__init__.py` |
| `src/<root>/services/<svc>/impl/<ns>.py`      | `<Ns>ServiceImpl`, the in-process impl of `<Ns>ServiceInterface`, which exists from the first day: every router calls one operation of it, and each operation calls one manager the container wired, as The Network Layer (Service Interfaces and Impls) states; it composes across services where a workflow needs it and never decides. At a split a sibling's `*ServiceInterface` is wired to its remote impl instead, the typed client of `clients/python/`, which mints the internal credential for each call and carries the timeout its settings name; the routers do not change |
| `src/<root>/services/<svc>/realtime/` (with `--realtime`) | `ticket.py` (the route that asks the tenancy manager to issue the ticket; redemption is the manager's, atomic, and re-checks the credential named by `ctx.security.credential_id`), `socket.py` (the route, resolving its context through the gateway's `socket_context` dependency and never parsing the query itself, then subscribe, unsubscribe, ping), `send_buffer.py` (the bounded per-socket send buffer and drainer), `envelopes.py` (typed envelopes on the `View` base with a curated payload view, never a dumped internal payload, carrying the event's `seq`; the portal's `envelopes.ts` mirrors it by hand, since socket frames are not in OpenAPI) |
| `src/<root>/services/<svc>/routers/events.py` (with `--realtime`) | `GET /v1/events?after_seq=&limit=` over the events manager, the replay a reconnecting client uses |
| `src/<root>/services/<svc>/main.py`           | `serve`, `migrate` (forwards to the OM migration CLI), `bootstrap` (org, slug, email, password, display name, `--operator`; with `--seed`, reads the development org and owner from settings, refuses at start when the database URL is not a local address, and exits without writing when the org's slug already exists), `openapi` subcommands |
| `tests/conftest.py`                           | the app over `AppContainer.for_tests(StorageMemoryImpl(), InfraLocalImpl(tmp_path))` and `httpx.AsyncClient(transport=ASGITransport(app=app))`, run inside the lifespan |
| `tests/test_health.py`                        | `/healthz` and `/readyz`                                                                        |
| `tests/test_container.py`                     | the build-once test of Cross-Cutting Conventions (The App Container): the managers build once for any number of requests, and every root member exists after `build`, none built on first use |
| `tests/test_settings.py`                      | every field of the service's settings appears in `.env.example` under its prefix, and every environment under `deployment/terraform/environments/` sets or wires as a secret every field that has no local default |
| `tests/test_<ns>_api.py`                      | one round trip per hosted namespace (seeding an org and its owner through the tenancy manager first), plus the error envelope and one rate-limited route |
| `tests/test_realtime_timeouts.py` (with `--realtime`) | asserts the ping interval and idle timeout against `deployment/realtime-timeouts.json`, the service half of the shared-file rule |
| `deployment/docker/<service-name>.Dockerfile` | two stages on a base image at the Python release `.python-version` names, locked install of this package, non-root, healthcheck on `/healthz` |

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
| `services/<existing>/gateway/` (when a service already exists) | moved into a workspace distribution `gateway/` that every service imports; nothing is copied |

## Procedure

1. Write the container before the app, the app before the routers.
2. A router function resolves `ctx`, builds the entity or the arguments
   from the request, calls one operation of its service impl, which
   calls one manager, and projects the result onto a view; the router
   calls through the service interface from the first day, so a split
   is a wiring change and never a rewrite of the routers. For a partial
   update it reads the current entity through the manager's `get_*`,
   copies the request's set fields onto it, an absent field meaning
   unchanged and an explicit null meaning cleared where the field is
   optional, and hands the whole entity to the manager; that policy is
   the request type's contract, as The Business Layer (Shape of an
   Operation) states. When a router starts deciding, move the decision
   into a manager and say so in the output.
3. An app-specific service composes managers or sibling domain service
   clients for one app only and never calls another app-specific
   service.
4. Emit the OpenAPI document with `make openapi` so the consuming apps
   regenerate their types.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
