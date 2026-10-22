# The API process

The one HTTP process of Acme. It serves the product's routes under `/v1`,
the realtime socket, the identity provider's webhook at
`/webhooks/identity`, and the probes `/healthz`, `/readyz`, and
`/metrics`. Every replica is the same process. `ACME_NAMESPACES` mounts a
subset of the namespaces, so one namespace can run as a service of its own
with no code change.

A request passes four layers, each in its own folder under
`src/acme/services/api/`:

- **Gateway** (`gateway/`). The middleware and dependencies every request
  meets: the request id and access log, the trusted proxy hops, admission
  and the request's deadline, the credential, rate limits, idempotency,
  the error envelope, and the webhook's signed body.
- **Routers** (`routers/`). One module per namespace, named by it, and
  `webhooks` for the providers' deliveries; `HOSTED` in
  `routers/__init__.py` lists the namespaces the image serves. A module
  tags its routes with its namespace and keeps its operator routes
  beside them.
  Each route makes one call into a service.
- **Services** (`services/`). One interface per namespace, and its impl in
  `services/impl/`. An impl translates the request, calls a manager or a
  provider, and returns a view from `types/`.
- **Realtime** (`realtime/`). The socket, its ticket, its bounded send
  lanes, and the recheck that closes it when its credential ends.

`container.py` builds everything once per process, `app.py` assembles the
app, and `main.py` is the `acme-api` command, whose subcommands are
[below](#the-subcommands); `acme-api --help` lists them.

## Routes, by namespace

Each namespace's routes are in its OpenAPI document,
[`clients/openapi/<namespace>.json`](../../clients/openapi/), under the
namespace's tag. The tag's description is the router module's
docstring, which tells the namespace's routes by area. `make openapi`
writes the documents from the code, so they hold what runs. Two kinds of
route are in no document:

- **Realtime.** The live channel, a websocket opened with a
  single-use ticket (`/v1/realtime`).
- **Operational.** Liveness (`/healthz`, the process alone),
  readiness (`/readyz`, asks the database under a deadline shorter
  than the probe's interval), and metrics (`/metrics`), never refused
  by admission; and, for a developer, the interactive document with
  its UI (`/docs`).

## What the gateway guarantees

- **A request id on every answer.** The `x-request-id` header carries
  it. A caller may send one; otherwise the gateway mints it. Every
  log line of the request, its trace, and its error report carry the
  same id, and a refusal quotes it.
- **An idempotency key on every create.** A creating `POST` may carry
  `Idempotency-Key`, one to 255 characters; every client this repository
  ships sends one, and a create without it runs once per request. The
  same key with the same request gets the first answer back, marked
  `Idempotent-Replayed: true`; the same key with a different request is
  refused. A secret is in the first answer only. A 429 is an answer
  about now and is not kept: the retry of the same create runs again.
- **The calling app and its version.** `x-app` and `x-app-version`
  name the client, and travel into the provenance of every write.
- **One error envelope.** Every refusal, the framework's own included,
  is one shape: a code, a message, and the request id, under the HTTP
  status. A 5xx says `internal error` and the real message goes to the
  log under the request id; the admission 503 alone says which bound it
  hit, with a `Retry-After`.
- **Bearer by prefix.** The credential's prefix says what it is: an
  auth session token, an API key, a login, a ticket, or an operator token. A missing or
  invalid one is a 401; a route asked with the wrong kind is refused.
- **The answer never waits for the relay.** A write commits its
  outbox rows before it answers. Their relay, the push and the queued
  work, runs once the answer is sent, under the same request id and
  trace. A relay that fails, or whose push the bus drops, is logged,
  and the maintenance sweep relays the rows. The latency a route reports is the time to its
  answer.
- **Admission.** The process bounds what it has in flight, in three
  lanes: reads, writes, and a holder's calls. Past a lane's bound a
  request is refused at once with a 503 and a `Retry-After`, so a
  saturated process answers and says why instead of queueing work it
  cannot start. Admission fails closed and is counted in the process's
  own memory.
- **A holder's calls.** A route that keeps or settles what its caller
  holds carries `HOLDER_CALL` in its dependencies: the lease's renewal
  and its release. It is counted in the holds lane
  (`ACME_ADMISSION_LIMIT_HOLDS`) and spends its credential's holds
  budget (`ACME_CREDENTIAL_RATE_LIMIT_HOLDS`), so a write budget the
  holder's own writes have spent does not refuse it at the rate limit,
  and a full write lane does not refuse it at admission
  ([ADR 0106](../../docs/adr/0106-a-holders-calls-are-counted-apart-from-its-writes.md)).
- **A deadline on every admitted request.** A request gets one when it
  takes its slot, `ACME_REQUEST_DEADLINE_SECONDS` (20) from then, and
  every call it makes to WorkOS or AWS shares it. A
  provider that hangs costs the request its deadline, not the call's
  timeouts times its retries, and the answer is the one a provider that
  does not answer gets: `503 unavailable`. It sits under
  the 30 seconds the portal and the command line wait
  ([ADR 0069](../../docs/adr/0069-a-request-has-a-deadline-its-provider-calls-share.md)).
- **Rate limits.** Counted in the shared cache, so every replica
  shares one budget. Past a budget the answer is a 429,
  `rate_limited`, in the one envelope, with a `Retry-After` in
  seconds: the time left on the window. There are three budgets.
  - **Per credential.** Every auth session and API key has a budget of
    reads (`GET`, `HEAD`), one of writes, and one of a holder's calls,
    spent once the credential resolves, in the lane admission counted
    the request in (`ACME_CREDENTIAL_RATE_LIMIT_READS`, `_WRITES`,
    `_HOLDS`, over `ACME_CREDENTIAL_RATE_WINDOW_SECONDS`). Two people
    behind one address never share it.
  - **Per address, on failed authentications.** A bearer that is
    unknown, expired, or revoked answers 401 and counts against the
    client address (`ACME_FAILED_AUTHENTICATION_LIMIT`). Once that is
    spent, every request from the address answers 429, a live
    credential's included, and nothing is looked up until the window
    ends. The refusal says so in its message.
  - **Per address, on sign-in.** The sign-in routes share
    `ACME_LOGIN_RATE_LIMIT`, since no credential exists yet.

  Rate limits fail open: they are fairness, not a security boundary
  ([ADR 0059](../../docs/adr/0059-authenticated-routes-have-limits.md)).
- **The client address behind a load balancer.** The forwarded address
  is trusted only from the peers the settings name, never from
  everyone, so a caller cannot pick its own address. Through the
  portal's CDN edge it is one hop further in, and only a request that
  carries the edge's secret (`ACME_EDGE_SECRET`, in `X-Acme-Edge`)
  gets that hop; the header never reaches a route.
- **A socket's trust is bounded.** It closes at once when the
  credential behind its ticket is revoked, and at its expiry whatever
  the client does. The revocation rides a bus that may lose it, so the
  socket also re-checks the credential every
  `ACME_REALTIME_RECHECK_SECONDS` and closes when it is refused. Each
  of these closes with code 4401, which every client reads as "sign in
  again". A change of the member's role closes the socket with 1012,
  which every client reads as "reconnect", and the new socket carries
  the new role.
- **A socket carries the org's whole stream.** A subscription names no
  audience, and a narrower view is the client's filter. Every push carries the
  org's stream position, and each client holds a contiguous cursor, so
  a push the server withheld would be a gap. The replay of
  `/v1/change-events` that fills a gap is unscoped, and it would hand back
  what was withheld. Filtering at the server needs a scoped replay and
  a frame counter per subscription, a second protocol, and it waits
  until the channel's traffic makes the filter worth that protocol.
- **The socket ticket is never logged.** Access logging is the
  gateway's own, by route template, with no query string.

## The subcommands

`acme-api` is one entry point with subcommands. Each boots the same
container the server does.

| Subcommand | Does |
|------------|------|
| `serve` | Runs the process. |
| `migrate` | Applies every role's migration chain (`--all`) or one role's. Idempotent per revision. `migrate ensure-logins` makes the database logins, as the master, on each database a role lives on. |
| `bootstrap` | Seeds a fresh local environment with one org and its owner; `--operator` puts the owner on the operator allowlist with write. Local only, like every seed. |
| `grant-operator` | The grant job: `--email <e> --permission read\|write` puts an identity on the operator allowlist, `--email <e> --disable` takes it off, and `--email <e> --mint-token provisioner\|smoke [--expires-in N]` mints that identity's operator token into the secret store as `acme-<env>-<holder>-token`, never printed in the cloud. On a local database it prints the token instead, and `--mint-token operator` mints the local read operator's `read` token, which `make seed` writes into `~/.config/acme/ops/local.env`; a cloud refuses it. An operator signs up first; the system's own identities (`@platform.acme.invalid`) are made by their first grant. |
| `add-member` | Seeds a person into an existing org; a no-op for a member. |
| `openapi` | Emits the OpenAPI document the clients are generated from. |

The migration runs inside the cloud deploy that brings one, as a one-off
task before the service rolls; a failed migration leaves the old tasks
serving. A deploy whose release changes none of the files
`deployment/migration-inputs.json` names runs no migrate task.
