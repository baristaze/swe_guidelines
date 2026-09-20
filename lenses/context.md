# Context

Group id: `context`. Covers OpContext (with Stages, Scopes, and The
Operator Context), the authorization and tenancy split of Separation
of Layers, the
authorization step and parameter order of The Business Layer and its
"Operations Without a Principal", the tenancy rules of The Storage
Layer, the tenant keying of Infrastructure, the credential and
operator concerns of The Gateway in The Network Layer, and the worker
context provenance of Worker Roles.

This group judges one question: does every operation know who is
acting, for which tenant, with what authority, and is that knowledge
carried and enforced in the right layer. It owns the operator gate,
including whether an operator route is gated by a flag or a tenant
role, and leaves the console's stack, origin, and bundle to
`delivery`. It leaves interface shape and injection to `contracts`,
table and translation mechanics to `storage`, queue and worker loop
mechanics to `async`, and the error envelope, rate limits, and public
types to `network`.

## CTX-01 Context is the first argument of every operation

**Principle.** Every manager, service, and worker-handler operation
takes a context as its first argument: `OpContext`, the identity or
the operator stage on the operator plane (CTX-20), or the request
stage for the enumerated transitions (CTX-16). A helper below the
managers that needs less takes a scope (CTX-22); no manager operation
takes one.

**Source.** OpContext; The Business Layer.

**Look for.** Manager interfaces, service interfaces, worker handlers:
the first parameter of every async method, and the docstring of any
method that has none.

**Violation.** A manager or service method that takes a user id, an
org id, or a token instead of a context; a method that takes the
context in any position other than first; a handler that fetches
identity from a request object; a manager operation that takes a
scope. A method carrying no context at all, the relay's three
handoffs included, is CTX-16's to judge.

**Severity.** medium

## CTX-02 The context carries ids and facts, never entities

**Principle.** The security context holds `user_id`, `org_id`, the
role, the permissions, the teams, the credential kind, and
`credential_id`: ids and facts, never entities. The app context holds
the app type and version. The request stage holds the request id, the
app, and an optional trace id, which every stage above inherits and
every log line, audit row, and error envelope reads.

**Source.** OpContext; OpContext, Stages.

**Look for.** The context type definitions and the context module's
imports, which reach nothing above the base module; the identity
stage's identity id, email, and credential; every place that reads
identity, tenant, or app information; the audit record type.

**Violation.** A `User` or `Org` entity embedded in the context, so a
role change waits for a new session and the context module imports
the tenancy types; a context with no credential id, so rate limits
and socket tickets key on something else; identity or tenant data
passed beside the context as extra parameters; a second ad-hoc
"current user" object; app type derived from headers below the
gateway; a request id threaded by hand; an audit row written without
the context's request id.

**Severity.** medium

## CTX-03 Permissions derive from role; a credential never outranks its issuer

**Principle.** Permissions derive from role through one table in the
tenancy namespace. A credential never carries a role above its
issuer's, where above means the permission set: a role is at most
another when its permissions are a subset of the other's, and a rank
used for comparison is derived from the table or held to it by a unit
test.

**Source.** OpContext.

**Look for.** The role-to-permission table, credential issuing paths
(API keys, invitations, session tokens), the comparison each issuing
path makes and the test that holds a rank to the table, and any code
that assigns permissions.

**Violation.** Permissions stored per user or per credential
independently of role; a second mapping elsewhere; an issuing path that
does not cap the new credential's role at the issuer's; a rank declared
apart from the permission table with no test tying the two.

**Severity.** high

## CTX-04 Teams are the second authorization axis

**Principle.** Inside a tenant, an entity may be owned by a team, and
visibility rules consult the context's team membership.

**Source.** OpContext.

**Look for.** Entities with a team owner; visibility and listing rules
in managers; the `in_team` check.

**Violation.** A team-owned entity listed or read without a team check;
team membership resolved by a manager from storage on every call
instead of read from the context; team checks in a router or a storage
impl.

**Severity.** high

## CTX-05 The request stage is minted at the edge, the rest by transitions

**Principle.** The request stage is minted once, at the edge: by the
gateway per request and socket, by the worker loop per claim and sweep
pass, by the bootstrap command per command. Every stage above it comes
only from a transition: an operation of the tenancy manager, or one
that asks it, as the claim of a worker does.

**Source.** OpContext, Stages; The Business Layer, Operations Without a
Principal; The Network Layer, The Gateway.

**Look for.** Where the request stage is built (the gateway
dependency, the worker loop, the bootstrap command); the transitions on
the tenancy manager (a sign-in into the identity stage, a credential
into `OpContext`, the operator admission into `OperatorContext`, and
one service context per live tenant for a sweep), what each takes (the
stage below and the evidence) and returns (the stage above, or a
refusal); the operations that ask a transition, the worker's claim
among them; every other place a stage object is constructed.

**Violation.** A manager, storage impl, or test helper used in
production code that builds a stage; a router that assembles a context
from headers or tokens; a service that constructs one for an internal
call; a second construction site for a stage the tenancy manager
already produces; a transition that takes raw parameters where the
stage below exists.

**Severity.** high

## CTX-06 The context is immutable and narrowing is an explicit argument

**Principle.** Once built, a context flows through every downstream
call unchanged. A narrower view (an override, a reduced permission set)
is passed as an explicit argument, never by mutating or copying the
context mid-request. A transition builds the stage above as a new
object from the stage below; it is not a copy with changed fields.

**Source.** OpContext; Stages.

**Look for.** Copies or mutations of the context after the gateway;
methods that accept a context and hand a different one downstream;
how each transition constructs its result.

**Violation.** A context copied with altered fields inside a manager or
service; a permission set widened or narrowed on the context; a "with
override" helper that replaces the context instead of adding a
parameter; a stage produced by copying the stage below with fields
changed.

**Severity.** medium

## CTX-07 No ambient state outside the context

**Principle.** Operations never reach for ambient state through globals,
thread locals, or hidden lookups. Everything ambient flows through the
context.

**Source.** OpContext; Telemetry, Logs.

**Look for.** Module-level globals holding a current user, tenant, or
request; thread-local or context-variable reads of identity, tenant,
or request in managers, storage, or workers.

**Violation.** A manager reading the current tenant from a context
variable; a storage impl reading a global "current org"; a request id
read from a context variable where the context is in hand. The one
allowed context variable carries the request id for log enrichment
only, and the authoritative value stays on the context.

**Severity.** medium

## CTX-08 Authorization lives in managers

**Principle.** Permissions and visibility are business decisions.
Every mutating manager operation starts by requiring the permission it
needs; visibility rules sit next to the operation they guard.
Routers bind and service impls translate, storage persists; none of
them decides authorization.

**Source.** Separation of Layers; The Business Layer, Shape of an
Operation.

**Look for.** The first lines of manager write methods; permission
checks in routers, service impls, and storage impls.

**Violation.** A manager write path with no permission requirement; a
permission check performed in a router or a request dependency and
absent from the manager; a storage impl that inspects role or
permissions.

**Severity.** high

## CTX-09 Tenancy is enforced in storage on read and checked on write

**Principle.** Tenancy is a data boundary. Every storage query filters
by the tenant, and every write refuses to overwrite a row that belongs
to another tenant. That statement-level fence, with the test that
enumerates every exception to it, is the fence; row-level security is
not a second one here, and a project that wants it records the
decision.

**Source.** Separation of Layers; The Storage Layer, Storage Principles;
A Storage Impl.

**Look for.** Every query in every storage impl, including the
in-memory one; the shared upsert primitive.

**Violation.** A query without the tenant in its filter; a write that
updates by id alone; an upsert that silently moves a row from one
tenant to another; a memory impl that skips the tenant check the
relational impl performs.

**Severity.** high

## CTX-10 Tenant first, then user, then the narrowing ids

**Principle.** Storage signatures start with `org_id`, add `user_id`
when the scope is personal to a user, and then peel scope from broad to
narrow. Manager and service signatures take the context first and
start at the level below it, since tenant and user are already in it.
Optional filters follow the required scoping ids as keyword parameters
with defaults.

**Source.** The Business Layer, Parameters; The Storage Layer, Namespace
Shape.

**Look for.** Parameter order on storage, manager, and service
interfaces; user-scoped storages; the position and the default of
every filter parameter on a list or an aggregate method.

**Violation.** A storage method taking the entity id before the tenant
id; a user-scoped read whose signature takes only the tenant, so the
personal scope is left to the impl (what the impl's `WHERE` then does
is CTX-11); a manager or service method that takes an org id or user
id the context already carries; a filter passed positionally before a
scoping id, or a filter with no default that every caller must spell.

**Severity.** medium

## CTX-11 Both keys appear in every user-scoped query

**Principle.** When a scope is personal to a user within a tenant, the
interface takes both `org_id` and `user_id`, and both appear in the
`WHERE` clause of every query in the impl.

**Source.** The Storage Layer, Namespace Shape.

**Look for.** Impls behind user-scoped storage interfaces.

**Violation.** A user-scoped query that filters by user but not tenant,
or by tenant but not user; a personal view readable by another user in
the same tenant.

**Severity.** high

## CTX-12 Exceptions to tenant-first are enumerated and tested

**Principle.** Global tables and cross-tenant sweeps are the exceptions
to the tenant-first rule: a global method takes no tenant and says why
in its docstring; a bookkeeping sweep with no principal gets the
tenant back with each row, as `tuple[UUID, Entity]` or on an entity
carrying `org_id` itself; a test enumerates them.

**Source.** The Storage Layer, Namespace Shape; The Business Layer,
Operations Without a Principal.

**Look for.** Storage methods without a tenant parameter; the test that
lists them; each step of the sweep and whether it is bookkeeping with
no principal (relaying the outbox, expiring a lease) or a tenant
operation (CTX-17); what each cross-tenant read returns.

**Violation.** A tenant-less storage method with no docstring
justifying it; a sweep that returns entities without their tenant; a
new tenant-less method that the enumerating test does not know about,
or no such test at all.

**Severity.** medium

## CTX-13 The system scope is EMPTY_UUID

**Principle.** Cross-tenant reference data uses `EMPTY_UUID` as the
tenant on cache and bucket calls, so system keys and tenant keys live in
disjoint namespaces.

**Source.** Naming Entities, Identifiers; Infrastructure, Infrastructure
Principles.

**Look for.** Cache and bucket calls for platform-owned data.

**Violation.** Platform-owned data cached or stored under a real
tenant's id; a made-up sentinel other than `EMPTY_UUID`; an impl that
treats the system scope like any tenant and lets a tenant caller reach
it.

**Severity.** medium

## CTX-14 Cache and bucket calls take the tenant first

**Principle.** Cache and bucket calls take `org_id` as a first-class
parameter so a mistake cannot cross tenants at the key level. A value
personal to a user carries the user id inside the key. The bucket impl
prefixes every storage key with the tenant, so one tenant's blobs cannot
be read or listed by another.

**Source.** Infrastructure, Infrastructure Principles; Cache; Buckets.

**Look for.** The cache and bucket interfaces, every call site in
managers, and the key layout inside the bucket and cache impls.

**Violation.** A cache or bucket method without a tenant parameter; a
call that passes one tenant's id for another tenant's data; a personal
value cached under a key with no user id; a bucket impl that stores
keys unprefixed so a list by prefix can cross tenants.

**Severity.** high

## CTX-15 Topic payloads carry the tenant

**Principle.** Every topic payload carries `org_id`, so a consumer can
filter by tenant before it acts.

**Source.** Infrastructure, Infrastructure Principles; Topics.

**Look for.** The payload base class, every payload class, and every
subscriber handler.

**Violation.** A payload class that does not extend the base and so
carries no tenant; a socket handler that forwards an event to a client
without comparing the payload's tenant to the connection's tenant.

**Severity.** high

## CTX-16 Principal-less operations take the request stage and return a stage

**Principle.** The operations that exist before a principal does (a
sign-in, a claim, a sweep, a webhook token) take the request stage and
produce the stage the work runs under; a test names each. The outbox
relay and its two handoffs, the event append and the work enqueue,
take `(org_id, row)` instead: the row carries tenant, actor, and
request id.

**Source.** The Business Layer, Operations Without a Principal; OpContext,
Stages; Worker Roles, The Work Queue.

**Look for.** Manager methods whose first parameter is the request
stage; what they return (the identity stage, an `OpContext`, or one
service context per live tenant); their docstrings, which document
them as transitions; the test that enumerates them; any method with no
context at all, and whether the relay runs again from the sweep.

**Violation.** A request-stage method that performs tenant work
directly instead of returning a stage; a request-stage method the
enumerating test does not name; a method with no context at all other
than the relay and its two handoffs; a transition whose return type is
neither a stage nor a list of stages.

**Severity.** high

## CTX-17 A worker runs under the enqueuer's identity with a service role

**Principle.** When a worker claims a work item, it rebuilds the
enqueuer's principal from the item's `created_by` under a service role.
A sweep that performs a tenant operation holds one service context per
live tenant from the tenancy manager, minted for the tenant, not a
member: the tenant, the service role, and the system user `EMPTY_UUID`
as its user id.

**Source.** Worker Roles, The Work Queue; The Business Layer, Operations
Without a Principal.

**Look for.** The worker's claim path and how it obtains a context;
each sweep step that purges or requeues with an audit entry, and the
context it runs under; what the service context carries as its user
id.

**Violation.** A worker running every item under one shared machine
principal so attribution is lost; a worker inventing a context with a
made-up user; a sweep acting across tenants under one tenant's context
or with no tenant at all; a service context minted on a member, so a
tenant whose members have all left is never swept, or costing one read
per member instead of one per page of tenants.

**Severity.** high

## CTX-18 The credential prefix decides who accepts it

**Principle.** Each credential kind has a distinct prefix, and the
prefix decides which gateway dependency accepts it. An agent's
key is membership-scoped, expiring, and role-capped; a person's login
credential carries no tenant and is exchanged for a tenant-scoped
session token.

**Source.** The Network Layer, The Gateway.

**Look for.** Credential formats, the dependencies that parse them,
which routes accept which kind.

**Violation.** One dependency that accepts any bearer on any route; a
login credential usable directly on tenant routes; a key that never
expires or that carries a tenant it was not scoped to.

**Severity.** high

## CTX-19 Sockets open with a single-use ticket

**Principle.** A long-lived connection is opened with a single-use,
short-lived ticket minted by an authenticated request, never with a
long-lived credential in a URL. Redeeming the ticket re-checks the
credential behind it.

**Source.** The Network Layer, The Gateway.

**Look for.** The socket handshake, the ticket endpoint, ticket
storage and expiry.

**Violation.** An API key or session token in a socket URL; a ticket
that can be redeemed twice or long after minting; a redemption that
does not re-validate the underlying credential.

**Severity.** high

## CTX-20 The operator plane has its own gate and its own context

**Principle.** Operator routes authenticate the bearer into the
identity stage, and the operator admission on the tenancy manager
refines it into an `OperatorContext` when the identity is on the
operator allowlist, never on a tenant role or a feature flag.
`OperatorContext` refines `IdentityContext` and has no tenant. An
operation acting for a principal takes exactly one of the two.

**Source.** OpContext, The Operator Context; OpContext, Stages; The
Network Layer, The Gateway; Client App Architecture, The Operator
Console.

**Look for.** The operator gate, the `OperatorContext` type and what it
subclasses, the operator admission, every manager signature on the
operator plane and the tenant plane, and for one that takes a stage
below, whether it is a tenancy transition or an operation with no
principal (CTX-21); how the console and its screens decide that a
person is an operator.

**Violation.** An operator route gated by a tenant role or a feature
flag, or operator pages shown in the portal behind a flag or a role
check; an `OperatorContext` with a tenant field; a manager method that
accepts either context type; an operator route that reaches a tenant
manager; an API key or an invitation-minted session admitted to the
operator plane, since the identity stage admits only the person's own
sign-in.

**Severity.** high

## CTX-21 An operation takes the weakest stage that proves what it needs

**Principle.** Stages are concrete frozen types, each a subclass of
the stage it refines, so a function that asks for the weaker stage
accepts the stronger one and never the reverse. An operation declares
the weakest stage that proves what it needs and relies on that
invariant instead of checking it again; `OpContext` does not refine
`IdentityContext`.

**Source.** OpContext, Stages.

**Look for.** The stage type definitions and their bases; the first
parameter of every transition and every operation on the sign-in,
operator, and tenant paths, since the stage in the signature fences
which operations a holder can call; credential and membership checks
inside operations that already take a stage.

**Violation.** An operation that takes `IdentityContext` and verifies
the credential again, or takes `OpContext` and re-reads the membership
to decide whether it is live, the one exception being a work handler
that reads it by name before a sensitive step as a recorded decision
of that kind of work; a sign-in or exchange route handed an
`OpContext`; a stage declared as a `Protocol` or satisfied by anything
other than its transition; `OpContext` subclassing `IdentityContext`;
a bundle of managers per stage, or a manager reachable from a context.

**Severity.** high

## CTX-22 A consumer declares the narrowest scope, and a scope is a Protocol

**Principle.** A consumer that needs less than a stage carries declares
a scope: a small `Protocol` of read-only properties naming the
capability it needs. A stage satisfies a scope structurally, with no
projection object built per call. A scope exists when a consumer
declares it or another scope builds on it. A manager operation takes
`OpContext`, which is its scope.

**Source.** OpContext, Scopes.

**Look for.** The scope definitions and the consumer each one is
derived from; helpers and edge concerns that read a subset of the
context (provenance stamping, rate limiting, realtime subscription);
what they declare.

**Violation.** A helper that reads only the request id and the actor
and takes `OpContext`; a scope declared as an `ABC` the contexts
subclass; a view object copied out of the context to satisfy a scope;
a scope neither a consumer declares nor another scope builds on; an
authorization scope, since no consumer needs the permissions without
the tenant and the actor.

**Severity.** medium

## CTX-23 A scope combination is named only for a concept of the domain

**Principle.** Scopes compose by Protocol inheritance. A combination
gets a name only when it is a concept of the domain, as provenance is;
a consumer that needs two scopes with no concept between them takes the
stage that carries both. Subclassing is the refinement of a stage,
composition the only combinator of a scope.

**Source.** OpContext, Scopes.

**Look for.** The list of scope names; any name that concatenates two
others; any operation that takes a scope where it relies on an
invariant only a stage proves.

**Violation.** A scope named for the intersection of two others with
no domain meaning; a growing list of composed names; an operation that
authorizes, or relies on a live membership, and takes a scope instead
of `OpContext`, so a scope stands in for a stage.

**Severity.** low

## CTX-24 An operator write is stamped by a helper of the operator plane

**Principle.** Provenance is a tenant concept: `ActorScope` names a
user inside a tenant, and `OperatorContext` cannot satisfy it. An
operator write is stamped by the operator managers from the identity
id and the request id their stage carries, through a helper of the
operator plane, never through `outbox_row`.

**Source.** OpContext, Scopes; The Operator Context.

**Look for.** The write paths of the operator managers and the helper
that stamps them; every call of `outbox_row` and the type of the
context handed to it; the `created_by` and the request id on rows the
operator plane writes.

**Violation.** An operator manager that calls `outbox_row`, or builds
an `OpContext` or a placeholder tenant to satisfy `ProvenanceScope`;
an operator row whose `created_by` is not the identity id its stage
carries, or that carries no request id; an operator write stamped by
hand in a router or a storage impl instead of through the helper.

**Severity.** medium

## CTX-25 The service role is not a rung a person can mint

**Principle.** The role reserved for services is not a rung on the
ladder of roles: no credential a person mints carries it, and every
operation that issues a credential refuses it by name, whatever the
rank says.

**Source.** OpContext.

**Look for.** Every credential issuing path (API keys, invitations,
session tokens) and the check each makes against the service role by
name; the test that tries to mint it and is refused.

**Violation.** An issuing path that lets the service role through
because its rank sits below the issuer's; a key or an invitation
minted with the service role; a refusal that compares ranks only and
never names the role.

**Severity.** high

## CTX-26 Every stage construction site is enumerated by a test

**Principle.** A stage is an ordinary class anything can call, so a
unit test enumerates every site that constructs a stage above the
request stage and fails when a new one appears, the way the exceptions
test enumerates the tenant-less storage methods.

**Source.** OpContext, Stages.

**Look for.** The test that enumerates the construction sites; every
construction site of `IdentityContext`, `OpContext`, `OperatorContext`,
and their sub-objects, in production code and in test helpers.

**Violation.** A construction site the enumerating test does not name;
no such test at all; a test that lists the transitions by name but
never scans the code for a new site.

**Severity.** high

## CTX-27 A socket closes at its session's expiry and on the revocation frame

**Principle.** A stage lives no longer than its request. A socket holds
the `OpContext` its ticket produced, so the session's expiry bounds
the socket and the process closes it at that instant; a revocation or
a membership's end travels on the topic bus, and every process holding
a socket for that session or user closes it on the frame.

**Source.** OpContext, Stages.

**Look for.** The socket handler and what bounds its life: the deadline
it sets from the session's expiry when the ticket is redeemed, and the
subscription on the topic bus that every process with sockets holds
for the revocation and membership-end frames; what a process does with
a frame naming a session or a user it holds a socket for; what the
socket carries meanwhile, hints only.

**Violation.** A socket that outlives its session's expiry because the
client keeps pinging; a revocation that reaches a socket only at its
next reconnect; a process that closes the sockets it revoked itself
and ignores the frame from another; a socket whose context is
refreshed in place instead of closed; a frame missed with no expiry to
cover it.

**Severity.** high

## CTX-28 An external identity provider is one more credential kind

**Principle.** An external identity provider's token is a credential
kind: the gateway verifies it against the provider's published keys
and hands the tenancy manager the issuer and the subject, whose
transition finds or creates the identity keyed on that pair and
produces the `IdentityContext` a sign-in does. The provider is an
integration (DEL-05); nothing below the gateway knows which spoke.

**Source.** The Network Layer, Auth: the Gateway Verifies, the Tenancy
Domain Owns.

**Look for.** Where a provider's token is verified and what it
produces; the key the identity is stored under; whether the exchange
into a tenant session, the memberships, and the sessions are the ones
every person has; the provider's interface, real client, and twin in
the `integrations/` distribution, and the container that wires one of
them at boot.

**Violation.** A provider's token accepted as a session on tenant
routes; an identity keyed on an email the provider may reassign; a
second session or membership model for federated users; a tenant
manager that branches on the provider; no local twin, so the sign-in
cannot run without the network.

**Severity.** high
