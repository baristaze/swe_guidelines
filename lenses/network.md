# Network

Group id: `network`. Covers The Network Layer and Push-First Apps (in
Apps) of `architecture.md`.

This group judges how the system faces its callers: how services are
cut, what the gateway does once at the edge, what crosses the wire, and
how pushes reach an open socket. It leaves service interfaces, roots,
call direction, and router bodies that decide to `contracts`;
credentials, tickets, who builds a context, and the operator gate to
`context`; consumer-side idempotency and long-running orchestration to
`async`; and the "Apps Are Dumb" rule and the client's own code to
`delivery`.

## NET-01 One process first, namespace boundaries from day one

**Principle.** A system starts as one API process with one router
module and one wire-types module per OM namespace behind one gateway;
growth into separate services is mechanical because the namespace
boundary is a module boundary from day one.

**Source.** The Network Layer, How It Starts and Where It Goes; Domain
Services vs App-Specific Services.

**Look for.** The layout of `routers/` and `types/` in the API process:
whether each namespace has exactly one router module and one types
module, and whether an app-specific service in the single-process
start is its own router module and service impl, the impl composing
managers for one app.

**Violation.** A namespace's routes spread across several modules, or
one router module serving several namespaces; wire types for one
namespace scattered across modules; routers grouped by screen rather
than by namespace or by app-specific service.

**Severity.** medium

## NET-02 Services split along namespace lines

**Principle.** A web service is the scalability unit, one per major OM
namespace; the first form of a split is the API image with a
`namespaces` setting. Every service runs the whole OM in-process, a
call across namespaces stays a manager call across a split, and a
wire hop between processes sharing the OM and the database is a
recorded decision.

**Source.** The Network Layer, Web Services as Scalability Units.

**Look for.** Which `acme.om.<ns>` packages each domain service's
routers import; whether a service composes managers locally; the
number and purpose of over-the-wire calls between services.

**Violation.** A domain service whose routers import managers from more
than one namespace package, or two domain services that both wrap the
same namespace; a service that calls a sibling over the wire for what
its own process holds the code and the roles to do, with no decision
recorded; a domain service cut along team or client lines instead of
namespace lines.

**Severity.** medium

## NET-03 Domain services and app-specific services stay distinct

**Principle.** Domain services expose one namespace to any caller; an
app-specific service composes domain services for exactly one client
app and stays thin, with the app type explicit on the context (where
an app's logic lands is DEL-01). External API users call domain
services directly; an app talks to its own backing service.

**Source.** The Network Layer, Domain Services vs App-Specific Services.

**Look for.** Which callers each service accepts; where per-app
aggregation and session shaping live; whether app-aware branches read
the app type from the context or guess from headers or paths; which
service the app's client is pointed at.

**Violation.** An app-specific service method that raises a domain
exception or writes an entity itself rather than calling a domain
service or manager; a domain service with branches keyed on which
app is calling; an app-specific service used by a second app; an app
whose client calls a domain service directly; app-specific behavior
decided from anything other than the context's app type.

**Severity.** medium

## NET-04 Domain services are stateless

**Principle.** A domain service holds nothing that exists nowhere else;
anything in its memory must be rebuildable from storage, cache, or
queues, so a replica can be killed and replaced at any time.

**Source.** The Network Layer, Stateless vs Stateful Services.

**Look for.** A module-level or instance-level `dict`, `list`, or `set`
in a domain service that one request mutates and a later request
reads; in-memory maps of sessions, users, or in-flight work; anything
written to memory that is not also written to a durable source.

**Violation.** A service that remembers a user's progress, a pending
operation, or a session only in RAM; a process whose restart loses
information; a warm cache that is the only copy of a computed result.

**Severity.** medium

## NET-05 A stateful edge holds only the socket and its subscriptions

**Principle.** A service that holds long-lived connections keeps in
memory the open socket, the subscriptions the client registered, and a
bounded buffer of frames waiting to be written, and nothing else.

**Source.** The Network Layer, Stateless vs Stateful Services; Realtime
at the Edge.

**Look for.** The per-connection state kept by the socket handler;
whether session data, preferences, or accumulated context live next to
the socket.

**Violation.** Per-socket objects that accumulate user context or
partial results; state recovered from the socket handler instead of
from storage after a reconnect.

**Severity.** medium

## NET-06 The gateway is the only public surface

**Principle.** The gateway is the only public surface: every endpoint
passes its dependencies, which mint the request stage and run the
tenancy manager's transitions once at the edge (who builds a context
is CTX-05), and services never parse raw headers or tokens. It accepts
cross-origin requests only from the browser apps' origins, read from
settings.

**Source.** The Network Layer, The Gateway.

**Look for.** Where bearer tokens and headers are parsed; whether any
router, service impl, or manager reads `Authorization` or an app
header itself; whether a second path to the internet bypasses the
gateway; the allowed-origins setting.

**Violation.** A router that inspects headers to decide who is calling;
a wildcard or hard-coded allowed origin; an endpoint reachable without
passing the gateway's dependencies.

**Severity.** high

## NET-07 One error handler, one envelope

**Principle.** One handler translates `PlatformException` and
`InfraException` into `{"error": {"code", "message", "request_id"}}`
with the status the exception carries, one catch-all turns anything
else into a 500 in the same shape, and routers never set error status
codes. An infra root of its own is DEL-29.

**Source.** The Network Layer, The Gateway (Error envelope).

**Look for.** The registered exception handlers; `try`/`except` blocks
in routers that map exceptions to responses; ad hoc error bodies;
whether the request id is present in every error response.

**Violation.** A router raising an HTTP exception with a hand-picked
status; error bodies with different shapes on different routes; an
error response that lacks the request id; more than one mapping from
domain exceptions to statuses.

**Severity.** medium

## NET-08 Rate limits are a per-route dependency on the shared counter

**Principle.** A rate limit is a per-route dependency counting in the
shared cache so replicas share one budget; the subject is the
credential id, the client address for unauthenticated routes, or a
digest of the path token for inbound webhooks; a rejection is 429 with
`Retry-After` and the error envelope; limits fail open.

**Source.** The Network Layer, The Gateway (Rate limits).

**Look for.** How limits are declared per route; which key identifies
the subject; what happens when the cache is unreachable; the response
shape on rejection.

**Violation.** Per-process counters in a multi-replica deployment; a
limit keyed on a raw secret; a rejection without `Retry-After` or
outside the envelope; a limit that rejects every request when the
cache is down; a rate limit relied on as a security boundary.

**Severity.** medium

## NET-09 Creating requests accept an idempotency key

**Principle.** A creating `POST` accepts an `Idempotency-Key` header;
the first response is stored per tenant and principal under the key
and replayed on a retry, using the same storage primitive the queue
handlers use. Only an outcome a retry cannot change is stored: a
refusal is replayed, a failure releases the marker.

**Source.** The Network Layer, The Gateway (Edge idempotency).

**Look for.** Creating endpoints and whether they read the header;
where the stored response is keyed; whether the key is scoped to the
tenant.

**Violation.** A creating endpoint that produces a second record on a
retried request; a key stored without the tenant in its scope; a
`5xx` stored and replayed, so a transient failure is the answer for
good and the client's only exit is a new key and a second row; a
bespoke replay mechanism for one route that differs from the shared
primitive.

**Severity.** high

## NET-10 Health, readiness, and metrics live outside the versioned API

**Principle.** `/healthz` answers liveness with the version and no I/O,
`/readyz` awaits the storage healthcheck, `/metrics` exposes counters
and histograms and is answered with a 404 at the load balancer, and
the API prefix is applied once where routers are
mounted.

**Source.** The Network Layer, The Gateway (Health, Versioning).

**Look for.** The three operational endpoints and what each does; the
load balancer's listener rules;
whether liveness touches a dependency; where the version prefix is
declared.

**Violation.** A liveness check that queries the database; a load
balancer rule that forwards `/metrics`; readiness
that returns ok without checking storage; operational endpoints under
the versioned prefix; routers that repeat the version prefix in their
own paths.

**Severity.** medium

## NET-11 The gateway verifies; the tenancy domain owns identity

**Principle.** The gateway verifies credentials and asks the tenancy
manager for the principal; tenancy is an ordinary namespace. Nothing
it stores can be presented as a credential: a password is a
memory-hard hash under its own salt; a key, token, or ticket is a
SHA-256 digest shown once and looked up by digest, its entropy the
defense.

**Source.** The Network Layer, Auth: the Gateway Verifies, the Tenancy
Domain Owns.

**Look for.** Where signup, invitation, role management, key rotation,
and session refresh are implemented (organizations, identities, users,
memberships, teams, credentials, sessions, invitations); whether the
gateway holds its own user or token tables; the hashing in the tenancy
rules, what the credential tables hold, and the `Issued...View` that
shows a secret once.

**Violation.** Identity logic inside gateway middleware; credential
tables owned by the gateway rather than the tenancy namespace; a
manager that cannot be tested without the HTTP layer; a password
hashed with a fast digest or no salt; a key, token, or ticket stored
in the clear, or looked up by anything but its digest.

**Severity.** medium

## NET-12 Intra-service traffic needs no TLS, outbound trusts the OS

**Principle.** Service-to-service calls need no TLS on the private
network, and that rests on the network being private: services and
workers in private subnets, security groups that admit only the
platform's own processes, only the gateway with a public address, all
in Terraform. Managed backends that require TLS get a connection
string; outbound TLS verification uses the operating system's trust
store.

**Source.** The Network Layer, Intra-Service Communication.

**Look for.** The subnet and security-group declarations for services
and workers; whether a runtime that offers mutual TLS at no cost has
it on; certificate handling in service impls; how HTTP clients are
constructed and whether a bundled certificate store stands in for the
system's.

**Violation.** A service or worker with a public address, which is an
exposure and not a convention slip, or a security group open past the
platform's own processes; certificate rotation logic inside a service;
an HTTP client pinned to a bundled CA set so a corporate proxy or
private CA fails; TLS configured per component instead of once at
boot.

**Severity.** medium

## NET-13 Wire types are curated, immutable, and hand-written

**Principle.** Wire types are hand-written on two bases, a frozen
`View` and a `RequestBody` that forbids unknown fields; names end in
`View`, `Request`, or `Issued...View`. Lists return a bare list with a
clamped limit; a list that outgrows the clamp pages by an opaque
cursor, a stream by `after_seq`, nothing by an offset.

**Source.** The Network Layer, Public Types.

**Look for.** The `types/` modules and their base classes (`View`
built from attributes); whether entities are returned directly from
routers; naming of request and response classes; list endpoints,
their paging parameters, and the page envelope (`items`,
`next_cursor`) over the list's own order; what changes within a
version is NET-23.

**Violation.** An OM entity serialized straight onto the wire; a
mutable view; a request body that silently ignores unknown keys; a
list endpoint without a clamped limit; a list that can outgrow its
clamp with no cursor; a cursor that is not opaque to the client; offset
paging anywhere.

**Severity.** medium

## NET-14 The OpenAPI document is emitted, committed, and diffed

**Principle.** The running app emits the OpenAPI document; it is
committed, and CI regenerates it and fails on a diff, so a change to
the API surface shows in the document in the same pull request.

**Source.** The Network Layer, From OM to Wire.

**Look for.** The committed document and the make target that
regenerates it; the CI job that compares; whether the document is
authored by hand anywhere.

**Violation.** A view changed without the committed document changing;
a hand-maintained schema file next to the code; no CI check on the
document.

**Severity.** medium

## NET-15 One client per language per service

**Principle.** A service is accessed in exactly one way, so each
language has one client for it, in one place, built from the committed
OpenAPI document: one generated type set for a TypeScript app and one
typed client package for Python consumers.

**Source.** The Network Layer, Clients Live in One Place.

**Look for.** How many places in a language construct calls to a
service; whether a consumer built its own client instead of importing
the shared one; whether the client is derived from the committed
document.

**Violation.** Two clients for the same service in one language; a
consumer hand-rolling HTTP calls to a service that has a client; a
client built from anything other than the committed document.

**Severity.** medium

## NET-16 Pushes travel on the bus and are filtered at the socket

**Principle.** Every process holding sockets subscribes to the topics
its clients care about; a producer publishes once; each socket handler
filters by tenant and by the streams its client subscribed, never by
kind within a stream (NET-30). A routing store
mapping user to instance replaces the broadcast only when the replica
count grows past what broadcast affords.

**Source.** The Network Layer, Realtime at the Edge.

**Look for.** How a push reaches the process with the right socket;
whether producers look anything up before publishing; the filtering in
socket handlers.

**Violation.** A producer that must know which replica holds a user; a
routing store kept in sync by hand with a handful of replicas; a
socket handler that forwards events from other tenants, ignores the
streams its client subscribed, or filters by kind within one.

**Severity.** high

## NET-17 The socket is a hint; storage is the truth

**Principle.** Each socket has one bounded send buffer; when it is
full the oldest frame is dropped and logged. Every push is also a
record, and a reconnecting client asks for everything after the last
contiguous sequence it saw, so a gap is a replay, never a skip. The
first frame and every pong carry the tenant's head `seq`.

**Source.** The Network Layer, Realtime at the Edge.

**Look for.** The send buffer capacity, its drainer task, and its
overflow behavior; whether every pushed event has a durable record;
the reconnect path and its `after_seq` parameter; which sequence the
client keeps as its cursor; what the hello and the pong carry, so a
quiet socket cannot hide a dropped last frame.

**Violation.** A push that exists only as a frame; a send buffer that
grows without bound or blocks the producer; a client that cannot
recover missed events after a reconnect; a client that tracks the last
frame seen instead of the last contiguous one, so a dropped frame is
skipped for good; a pong with no head `seq`, so a dropped last frame
waits for the next event.

**Severity.** high

## NET-18 Inbound socket traffic is subscribe, unsubscribe, and ping

**Principle.** Commands travel over REST, where they get the error
envelope, the rate limit, and the idempotency key; the socket carries
only subscription management and keepalives inbound.

**Source.** The Network Layer, Realtime at the Edge.

**Look for.** The set of inbound message types the socket handler
accepts; whether any mutation is performed from a socket frame.

**Violation.** A create or update issued over the socket; a socket
message type that bypasses authorization or rate limiting a REST route
would apply.

**Severity.** medium

## NET-19 Waiting is the client's choice; the server answers the same way

**Principle.** The choice between waiting and not waiting is made per
operation at the client; a long operation returns an acknowledgement
with an id and completes with a push, and the server produces
responses and notifications the same way in both cases.

**Source.** The Network Layer, Wait-for-Response vs Fire-and-Forget.

**Look for.** Endpoints that start long work and what they return;
whether a request blocks for minutes; how a CLI follows an operation
to completion.

**Violation.** A request that holds the connection for the duration of
a long task; two server code paths for the same operation depending on
the caller's patience; a long operation that returns no id to follow.

**Severity.** medium

## NET-20 One realtime channel per app, typed envelopes routed by type

**Principle.** The moment any corner of an app wants a push, the app
earns one persistent bidirectional channel; one provider component
owns it for the whole app; every push rides it as a typed envelope
parsed by a discriminated union on `type`, and a new kind of push is a
new envelope type, not a new connection.

**Source.** Apps, Push-First Apps; Client App Architecture, Realtime:
One Channel per App.

**Look for.** The number of sockets or streams an app opens and which
component owns them; how envelopes are discriminated; whether a
feature added its own transport for updates; polling that runs while
the channel is up.

**Violation.** A second WebSocket or stream for one feature; a second
component that opens its own socket; a polling endpoint or a poll on a
timer used while the channel is up; envelopes without a type field or
routed by ad hoc inspection.

**Severity.** medium

## NET-21 The channel degrades and its timeouts are pinned

**Principle.** The client reconnects with exponential backoff, and
after more than one failed cycle shows a banner and polls slowly until
the socket returns; the ping interval and the load balancer idle
timeout live in one shared file that both a server test and a client
test assert against.

**Source.** Apps, Push-First Apps.

**Look for.** The reconnect logic and its backoff; the fallback
behavior when the socket stays down; where keepalive and idle timeout
values are defined and which tests read them.

**Violation.** Immediate tight reconnect loops; a client that shows
stale data silently when the socket is gone; a ping interval and an
idle timeout defined in two places that can drift in separate changes.

**Severity.** medium

## NET-22 The event row and its per-tenant seq

**Principle.** The record behind every push is an `Event` in the
`activity` role, appended by one named atomic method that assigns
`seq`, per tenant and gapless, from a cursor row updated and returned
inside the append's transaction, never from `MAX(seq) + 1` with a
retry. A manager records one event per write through the outbox.

**Source.** The Network Layer, Realtime at the Edge.

**Look for.** The `Event` type (`Identifiable` plus `org_id`, `seq`,
`kind`, `target_id`, `actor_id`, a typed payload) and its table's
role; the audit entry, the same shape plus the request id and the app;
the append method, the cursor row it locks, and where the head `seq` the
pong carries is read from; whether the event row is written by the
outbox relay or by a second statement; the `after_seq` read.

**Violation.** `seq` minted in Python, global across tenants, or with
gaps; `MAX(seq) + 1` computed in the append and retried on the
collision; an event table in the `core` role; an event row written in
a second statement after the core write; an event with no `actor_id`,
or an audit entry that is not the event's shape plus the request id
and the app; code that reads `seq` as the order of core writes.

**Severity.** high

## NET-23 Wire and payload changes are additive within a version

**Principle.** Inside `/v1` a view only gains fields and a request only
gains optional ones; a removal or a rename is a new prefix. A reader
ignores a field it does not know, and a payload or an envelope only
gains optional, defaulted fields, so producer and consumer roll out
in either order. A service rolls out before its apps.

**Source.** The Network Layer, Public Types.

**Look for.** The diff of every `types/` module, payload class (topic,
work item), and envelope against the committed OpenAPI document; the
model config of payload and envelope bases; whether a consumer fails
on an unknown field; whether a field added to a payload has a default,
since a new reader in front of an old writer holds only then; the
order in which a service and its apps are deployed, since a request
forbids what it does not know.

**Violation.** A field removed or renamed on a view, or a required
field added to a request, under the same prefix; a payload or envelope
consumer that rejects an unknown field, so producer and consumer must
deploy together; a required field added to a payload or an envelope,
so a row written before the deploy or an old producer's message fails
to parse; an app that sends a new request field before the service
that accepts it is deployed.

**Severity.** medium

## NET-24 The pending marker's attempt token fences finish and release

**Principle.** The pending marker carries the request digest, the id
the create uses, and an attempt token. A marker past the pending lease
is taken over in one conditional write stamping a new token, and rerun
with the marker's id. `finish` and the release are conditional on the
token in the statement; a release clears the attempt and nothing else.

**Source.** The Network Layer, The Gateway (Edge idempotency).

**Look for.** The marker row and what `begin` writes on it; the
take-over statement, what it compares, and which markers it takes
(abandoned by a crash, or held by an attempt still running past its
lease); the `WHERE` of `finish` and of the release, and what the
release clears (the attempt) and keeps (the digest and the id); what
the losing attempt's `finish` returns, since it can neither finish the
marker with its own outcome nor release the one the retry holds.

**Violation.** A marker with no attempt token, so two attempts can
finish it; a take-over that overwrites the marker without a condition;
a `finish` or a release that matches on the key alone; a release that
deletes the marker or clears its digest or id, so the retry after a
failure creates a second row; a rerun that mints a new id instead of
using the marker's; a losing attempt that is not refused like a worker
whose lease has passed.

**Severity.** high

## NET-25 A create that issues a secret re-mints it on the rerun

**Principle.** A create that issues a secret stores it as a digest and
shows it once, so the row as stored is not enough on a rerun: the rerun
finds the row, re-mints the secret on it in the same named atomic
write, and returns a fresh `Issued...View` with the same id, since the
first secret reached no one.

**Source.** The Business Layer, Shape of an Operation; The Network
Layer, The Gateway (Edge idempotency).

**Look for.** The create of every entity that issues a secret (an API
key, a session token, a socket ticket) and what it does when the
insert reports an existing id; the atomic method that re-mints. A
rerun is a retry whose marker holds no outcome; a replay, whose
marker holds one, is NET-31.

**Violation.** A rerun that returns the stored row with no secret, so
the client holds an id and nothing to present; a rerun that inserts a
second row under a new id; a re-mint written in a second statement
after the read; an `Issued...View` whose id differs between the first
run and the rerun.

**Severity.** high

## NET-26 Every outbound call carries a timeout from settings

**Principle.** Every outbound call carries a timeout: the transport
client reads one from settings, one per client, and no call goes out
without one, so a downstream that hangs cannot hold a replica's whole
pool. The gateway bounds a request the same way, with a deadline from
settings; the lease that bounds a work handler is ASY-17.

**Source.** The Network Layer, Clients Live in One Place.

**Look for.** The construction of every transport client, in Python
and in TypeScript; the settings field it reads; any call site that
builds a request outside the client; the request deadline setting and
the middleware or dependency that applies it to every route.

**Violation.** A client constructed with no timeout, or with a library
default nothing in settings names; a timeout hard-coded in the client
instead of read from settings; a per-call override that disables it; a
`fetch` or an `httpx` call outside the client with no deadline; a
request path with no deadline, so a slow handler holds a server slot
for good.

**Severity.** medium

## NET-27 A service-to-service call carries a short-lived internal credential

**Principle.** A service-to-service call carries a short-lived internal
credential minted by the caller: a token naming the principal, the
tenant, the request id, and an expiry minutes out, signed with a key
from the secret store and verified by the callee. The callee's gateway
rebuilds the context from it like any other credential kind, and no
service trusts a bare header.

**Source.** The Network Layer, Intra-Service Communication.

**Look for.** The `internal` credential kind, who mints it, where the
signing key comes from, and how the callee rebuilds a context from it;
the remote impl of every service interface.

**Violation.** A callee that trusts a tenant or user id in a header
from a peer service; an internal token with no expiry, or one signed
with a key held in settings instead of the secret store; a peer call
that reaches a router without the gateway's dependencies.

**Severity.** high

## NET-28 The request id is accepted or minted at the edge

**Principle.** The gateway accepts an inbound `x-request-id` or mints
one, stamps it on the context, echoes it in the response header, and
attaches it to the log context and the trace span.

**Source.** The Network Layer, The Gateway.

**Look for.** The request-id middleware and what it writes to the
context, the response, the log context, and the span; whether the
socket route gets the same treatment.

**Violation.** A response without the `x-request-id` header; a span
without the request id; a request id minted below the gateway or read
from the header by a router; a socket whose context carries none.

**Severity.** medium

## NET-29 The data tier splits by role, never by service

**Principle.** The independence of services is of the process, not of
the data: every service runs the same OM against the same database
roles, and the schema timeline stays with the OM, so the data tier
splits by role, never by service.

**Source.** The Network Layer, Web Services as Scalability Units; The
Storage Layer, Database Roles.

**Look for.** Which database URLs and schemas each service's settings
name; where migration chains live; whether a table's role comes from
the OM's role map or is implied by the service that writes it.

**Violation.** A database or a schema per service; a table owned by a
service rather than a role; a migration chain under a service; two
services that read one role from two databases.

**Severity.** medium

## NET-30 The stream is a stream of hints, whole per tenant

**Principle.** Contiguity is per tenant, so the stream travels whole
and a client filters by kind after ordering, never before. A frame and
a replayed record carry the identity of the change and no field of the
entity; the client reads the entity through the authorized read, so
the hint is metadata every member of the tenant may see.

**Source.** The Network Layer, Realtime at the Edge.

**Look for.** Whether the stream topic filters by kind before the
client; what a frame and a replayed record carry (`seq`, `kind`,
`target_id`, `actor_id`); how a client obtains the entity after a hint;
whether a product where existence itself is restricted keeps one
stream per visibility scope, with a cursor per stream.

**Violation.** A server-side filter by kind on the sequenced stream,
so a legitimate gap reads as a loss; an entity field on a frame or in
the replay, so the stream leaks what the read would have refused; a
consumer that rebuilds a record's state from events.

**Severity.** high

## NET-31 A replay of a create that issued a secret carries no secret

**Principle.** The outcome the marker stores for a create that issued
a secret is the view with the secret absent, so a replay answers with
the row and no secret and says so in its header. The secret exists in
one place, as a digest; a client that lost the first response revokes
the key and issues another.

**Source.** The Business Layer, Shape of an Operation; The Network
Layer, The Gateway (Edge idempotency).

**Look for.** What `finish` stores for a create that issued a secret;
what the replay returns and which header marks the absent secret;
every place a secret in the clear could land (the marker, a cache, a
log line).

**Violation.** A stored outcome that carries the secret in the clear,
so the marker is a second copy; a replay that re-mints the secret, so
a retry of a delivered response mints a credential nobody asked for; a
replay with no header saying the secret is absent.

**Severity.** high
