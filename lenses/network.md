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

**Source.** The Network Layer, How It Starts and Where It Goes.

**Look for.** The layout of `routers/` and `types/` in the API process:
whether each namespace has exactly one router module and one types
module, and whether an app-specific service in the single-process
start is its own router module composing managers for one app.

**Violation.** A namespace's routes spread across several modules, or
one router module serving several namespaces; wire types for one
namespace scattered across modules; routers grouped by screen rather
than by namespace or by app-specific service.

**Severity.** medium

## NET-02 Services split along namespace lines

**Principle.** A web service is the scalability unit, one per major OM
namespace, running with the whole OM in-process; a service-to-service
call is rare and reserved for a workflow that exceeds one manager's
scope.

**Source.** The Network Layer, Web Services as Scalability Units.

**Look for.** Which `platform.om.<ns>` packages each domain service's
routers import; whether a service composes managers locally; the
number and purpose of over-the-wire calls between services.

**Violation.** A domain service whose routers import managers from more
than one namespace package, or two domain services that both wrap the
same namespace; a service that calls another service for something its
own managers do in-process; a domain service cut along team or client
lines instead of namespace lines.

**Severity.** medium

## NET-03 Domain services and app-specific services stay distinct

**Principle.** Domain services expose one namespace to any caller; an
app-specific service composes domain services for exactly one client
app, stays thin, and holds only logic meaningful to that app, with the
app type explicit on the context. External API users call domain
services directly; an app talks to its own backing service.

**Source.** The Network Layer, Domain Services vs App-Specific Services.

**Look for.** Which callers each service accepts; where per-app
aggregation and session shaping live; whether app-aware branches read
the app type from the context or guess from headers or paths; which
service the app's client is pointed at.

**Violation.** An app-specific service method that raises a domain
exception or writes an entity itself rather than calling a domain
service or manager; a rule meaningful to more than one app held inside
an app-specific service; a domain service with branches keyed on which
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

**Principle.** The gateway authenticates requests, builds the context,
and routes; services never parse raw headers or tokens. A
service-to-service call carries a short-lived internal credential
minted by the caller: a token naming the principal, the tenant, the
request id, and an expiry minutes out, signed with a key from the
secret store and verified by the callee against the same key; the
callee's gateway rebuilds the context from it like any other
credential kind, and no service trusts a bare header. The edge
idempotency marker carries the request digest and the id the create
will use, minted before `begin`; a stale pending marker is taken over
and the request rerun with that id. It accepts
cross-origin requests only from the browser apps' origins, read from
settings. It accepts an inbound `x-request-id` or mints one, stamps it on the context, echoes
it in the response header, and attaches it to the log context and the
trace span. Edge idempotency stores the first response per tenant and
principal under the key.

**Source.** The Network Layer, The Gateway.

**Look for.** Where bearer tokens and headers are parsed; whether any
router, service impl, or manager reads `Authorization` or an app
header itself; whether a second path to the internet bypasses the
gateway; the `internal` credential kind, who mints it, and how the
callee rebuilds a context from it; the allowed-origins setting; the request-id middleware and what it writes to the response
and the span; the key the idempotency store uses.

**Violation.** A router that inspects headers to decide who is calling;
a callee that trusts a tenant or user id in a header from a peer
service; a wildcard or hard-coded allowed origin; an endpoint reachable without passing the gateway's dependencies; a
response without the `x-request-id` header; a span without the request
id; an idempotent response stored per tenant alone, so one principal
replays another's.

**Severity.** high

## NET-07 One error handler, one envelope

**Principle.** One handler translates `PlatformException` into
`{"error": {"code", "message", "request_id"}}` with the status the
exception carries, one catch-all turns anything else into a 500 in the
same shape, and routers never set error status codes.

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
the first response is stored per tenant under the key and replayed on
a retry, using the same storage primitive the queue handlers use.

**Source.** The Network Layer, The Gateway (Edge idempotency).

**Look for.** Creating endpoints and whether they read the header;
where the stored response is keyed; whether the key is scoped to the
tenant.

**Violation.** A creating endpoint that produces a second record on a
retried request; a key stored without the tenant in its scope; a
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
manager for the principal behind them; organizations, identities,
users, memberships, teams, credentials, sessions, and invitations are
a regular namespace with types, a manager, and storage. Nothing it
stores can be presented as a credential: a password is a memory-hard
hash under its own salt; an API key, a session token, and a socket
ticket are stored as their SHA-256 digest, shown once in the
`Issued...View` that minted them, and compared in constant time.

**Source.** The Network Layer, Auth: the Gateway Verifies, the Tenancy
Domain Owns.

**Look for.** Where signup, invitation, role management, key rotation,
and session refresh are implemented; whether the gateway holds its own
user or token tables; the hashing in the tenancy rules and what the
credential tables hold.

**Violation.** Identity logic inside gateway middleware; credential
tables owned by the gateway rather than the tenancy namespace; a
manager that cannot be tested without the HTTP layer; a password
hashed with a fast digest or no salt; a key, token, or ticket stored
in the clear or compared with `==`.

**Severity.** medium

## NET-12 Plain intra-service traffic, operating-system trust for outbound

**Principle.** Service-to-service calls stay on the private network
without TLS, and that rests on the network being private: services and
workers in private subnets, security groups that admit only the
platform's own processes, only the gateway with a public address, all
declared in Terraform; a runtime that offers mutual TLS at no cost
turns it on. Managed backends that require TLS get it as a connection
string; outbound TLS verification uses the operating system's trust
store in every process.

**Source.** The Network Layer, Intra-Service Communication.

**Look for.** The subnet and security-group declarations for services
and workers; certificate handling in service impls; how HTTP clients
are constructed; whether a bundled certificate store is used instead
of the system's.

**Violation.** A service or worker with a public address, or a
security group open past the platform's own processes; certificate
rotation logic inside a service; an HTTP client pinned to a bundled CA
set so a corporate proxy or private CA fails; TLS configured per
component instead of once at boot.

**Severity.** low

## NET-13 Wire types are curated, immutable, and hand-written

**Principle.** Wire types are hand-written on two bases, a frozen
`View` built from attributes and a `RequestBody` that forbids unknown
fields; names end in `View`, `Request`, or `Issued...View`; lists
return a bare list with a clamped limit, a list that can outgrow the
clamp pages by `after_id` over the id order, streams page by
`after_seq`, and nothing pages by an offset; the OM never changes to
match the wire. Within a version
a change is additive (NET-23).

**Source.** The Network Layer, Public Types.

**Look for.** The `types/` modules and their base classes; whether
entities are returned directly from routers; naming of request and
response classes; list endpoints and their paging parameters.

**Violation.** An OM entity serialized straight onto the wire; a
mutable view; a request body that silently ignores unknown keys; an OM
field added or renamed to suit a client; a list endpoint without a
clamped limit; a list that can outgrow its clamp with no `after_id`;
offset paging anywhere.

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
filters by tenant and by the client's subscriptions. A routing store
mapping user to instance replaces the broadcast only when the replica
count grows past what broadcast affords.

**Source.** The Network Layer, Realtime at the Edge.

**Look for.** How a push reaches the process with the right socket;
whether producers look anything up before publishing; the filtering in
socket handlers.

**Violation.** A producer that must know which replica holds a user; a
routing store kept in sync by hand with a handful of replicas; a
socket handler that forwards events from other tenants or ignores the
client's subscriptions.

**Severity.** high

## NET-17 The socket is a hint; storage is the truth

**Principle.** Each socket has one bounded in-memory send buffer
drained by a task; when it is full the oldest frame is dropped and
logged; every push is also a record, and a reconnecting client asks
for everything after the last contiguous sequence it saw, so a gap is
a replay, never a skip. Contiguity is per tenant, so the stream
travels whole and a client filters by kind after ordering, never
before; that is safe because a frame and a replayed record carry the
identity of the change (`seq`, `kind`, `target_id`, the actor) and no
field of the entity, and the client reads the entity through the
authorized read. The first frame and every pong carry the tenant's
head `seq`, so a quiet socket cannot hide a dropped last frame. `seq`
orders events, not core writes.

**Source.** The Network Layer, Realtime at the Edge.

**Look for.** The send buffer capacity and overflow behavior; whether
every pushed event has a durable record; the reconnect path and its
`after_seq` parameter; which sequence the client keeps as its cursor;
whether the stream topic filters by kind before the client; what the
hello, the pong, a frame, and a replayed record carry.

**Violation.** A push that exists only as a frame; a send buffer that
grows without bound or blocks the producer; a client that cannot
recover missed events after a reconnect; a client that tracks the last
frame seen instead of the last contiguous one, so a dropped frame is
skipped for good; a server-side filter by kind on the sequenced
stream, so a legitimate gap reads as a loss; a pong with no head
`seq`, so a dropped last frame waits for the next event; an entity
field on a frame or in the replay, so the stream leaks what the read
would have refused; a consumer that rebuilds a record's state from
events.

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

**Source.** Apps, Push-First Apps.

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
`activity` role: `Identifiable` plus `org_id`, `seq`, `kind`,
`target_id`, and a typed payload, appended by one named atomic storage
method that assigns `seq`, a per-tenant, gapless sequence and the one
number storage assigns. A manager records one event per write through
the outbox; an audit entry is the same shape plus the principal and
the app.

**Source.** The Network Layer, Realtime at the Edge.

**Look for.** The `Event` type and its table's role; the append method
and where `seq` comes from; whether the event row is written by the
outbox relay or by a second statement; the `after_seq` read; the audit
entry's shape.

**Violation.** `seq` minted in Python, global across tenants, or with
gaps; an event table in the `core` role; an event row written in a
second statement after the core write; an audit entry with a shape of
its own.

**Severity.** high

## NET-23 Wire and payload changes are additive within a version

**Principle.** Inside `/v1` a view only gains fields and a request only
gains optional ones; a removal or a rename is a new prefix. Topic
payloads and realtime envelopes follow the same rule and are read
tolerantly: a consumer ignores a field it does not know, so producers
and consumers roll out in either order.

**Source.** The Network Layer, Public Types.

**Look for.** The diff of every `types/` module, payload class, and
envelope against the committed OpenAPI document; the model config of
payload and envelope bases; whether a consumer fails on an unknown
field.

**Violation.** A field removed or renamed on a view, or a required
field added to a request, under the same prefix; a payload or envelope
consumer that rejects an unknown field, so producer and consumer must
deploy together.

**Severity.** medium
