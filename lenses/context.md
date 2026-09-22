# Context

Group id: `context`. Covers OpContext (with Stages, Scopes, and The
Operator Context), the authorization and tenancy split of Separation
of Layers, the authorization step and parameter order of The Business
Layer and its "Operations Without a Principal", the tenancy rules of
The Storage Layer, the tenant keying of Infrastructure and a cached
read's place below authorization, the credential and operator concerns
of The Gateway and of Auth in The Network Layer, the worker context
provenance and the enqueue permission of Worker Roles, the causing
request a handoff's stage names in Telemetry, and the tenant isolation
suite and its negative control in the Tests of Cross-Cutting
Conventions.

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
takes a context first: `OpContext` for a tenant operation,
`OperatorContext` for an operator one (CTX-20). Only a tenancy
transition, an identity's operations before a tenant, and an operation
with no principal take a weaker stage (CTX-16). A helper that needs
less takes a scope (CTX-22); no manager operation does.

**Source.** OpContext, The Operator Context; The Business Layer.

**Look for.** Manager interfaces, service interfaces, worker handlers:
the first parameter of every async method, and the docstring of any
method that has none.

**Violation.** A manager or service method that takes a user id, an
org id, or a token instead of a context; a method that takes the
context in any position other than first; a handler that fetches
identity from a request object; a manager operation that takes a
scope. A method carrying no context at all, the relay and its two
handoffs included, is CTX-16's to judge.

**Severity.** medium

**Check.** `arch-check` decides the position of a stage or scope and the
scope on a manager; the rest is judged.

## CTX-02 The context carries ids and facts, never entities

**Principle.** The security context holds `user_id`, `org_id`, role,
permissions, teams, credential kind, and `credential_id`: ids and
facts, never entities. The app context holds the app type and version.
The request stage holds the request id, the app, the optional trace,
and the causing request, which every stage above inherits and every log
line, audit row, and error envelope reads.

**Source.** OpContext; OpContext, Stages.

**Look for.** The context type definitions and the context module's
imports, which reach nothing above the base module; the identity
stage's identity id, email, and credential; every place that reads
identity, tenant, or app information; the audit record type.

**Violation.** A `User` or `Org` entity embedded in the context, so a
role change waits for a new session and the context module imports
the tenancy types; a context with no credential id, so rate limits
key on something else; identity or tenant data
passed beside the context as extra parameters; a second ad-hoc
"current user" object; app type derived from headers below the
gateway; a request id threaded by hand; an audit row written without
the context's request id.

**Severity.** medium

**Check.** `arch-check` decides the imports and entity fields of the
stage module and its id fields; the rest is judged.

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

**Look for.** Where the request stage is built (the gateway dependency,
the worker loop, the bootstrap command); the transitions on the tenancy
manager (a sign-in or a live session into the identity stage, a
credential into `OpContext`, the operator admission into
`OperatorContext`, and one service context per live tenant for a sweep),
what each takes (the stage below and the evidence) and returns (the
stage above, or a refusal); the operations that ask a transition, the
worker's claim among them; every other place a stage object is
constructed.

**Violation.** A manager, storage impl, or test helper used in
production code that builds a stage; a router that assembles a context
from headers or tokens; a service that constructs one for an internal
call; a second construction site for a stage the tenancy manager
already produces; a transition that takes raw parameters where the
stage below exists.

**Severity.** high

**Check.** `arch-check` decides the request stage built in the OM,
infra, a router, or a service (a stage above it built there is
CTX-26); the rest is judged.

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

**Check.** `arch-check` decides the copy or assignment of a stage and
the with or override helper; the rest is judged.

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
allowed context variable carries the request id, and the request that
caused it, for log enrichment only, and the authoritative value stays
on the context.

**Severity.** medium

**Check.** `arch-check` decides the context variable outside the log
module and the thread local; the rest is judged.

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

**Check.** `arch-check` decides the permission read in a router or a
storage module; the rest is judged.

## CTX-09 Tenancy is enforced in storage on read and checked on write

**Principle.** Tenancy is a data boundary. Every storage query filters
by the tenant, and every write refuses to overwrite another tenant's
row. The predicate is the fence the business layer relies on, and the
cross-tenant cases are its evidence (CTX-30). The database policy is
the second fence, taken by default, and it catches a predicate that
went missing (STO-28).

**Source.** Separation of Layers; The Storage Layer, Storage Principles;
A Storage Impl; The Second Fence.

**Look for.** Every query in every storage impl, including the
in-memory one; the shared upsert primitive; whether a manager or an
impl leans on the policy instead of filtering.

**Violation.** A query without the tenant in its filter; a write that
updates by id alone; an upsert that silently moves a row from one
tenant to another; a memory impl that skips the tenant check the
relational impl performs; a query that drops the predicate because the
policy is there.

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

**Check.** `arch-check` decides the position of org_id in storage and
the org_id beside OpContext; the rest is judged.

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

## CTX-12 Exceptions to tenant-first are enumerated

**Principle.** Global tables, cross-tenant sweeps, and the lookups that
run before an identity is known are the exceptions to the tenant-first
rule. A global method takes no tenant and its docstring says why; a
bookkeeping sweep gets the tenant back with each row; the five lookups
run in the system scope. `arch-check` enumerates them (CTX-30).

**Source.** The Storage Layer, Namespace Shape; The Second Fence; The
Business Layer, Operations Without a Principal.

**Look for.** Storage methods without a tenant parameter; the list the
checker holds; each step of the sweep and whether it is bookkeeping with
no principal (relaying the outbox, expiring a lease, `purge_items`
purging done and failed work items, purging ended sessions and
redeemed or expired socket tickets) or a tenant operation (CTX-17);
what each cross-tenant read returns, as `tuple[UUID, Entity]` or an
entity carrying `org_id`; the five lookups by name,
`read_identity_by_email_digest`, `read_identity_by_issuer_subject`,
`read_api_key_by_digest`, `read_session_by_digest`, and
`redeem_socket_ticket`; every call that
passes `EMPTY_UUID` as the `org_id`, where the operator plane's marker
calls are the one caller that takes `org_id` and is handed
`EMPTY_UUID`, by the operator gate alone.

**Violation.** A tenant-less storage method whose interface docstring
does not justify it; a sweep that returns entities without their
tenant; a new tenant-less method that the enumeration does not know
about, or no enumeration at all; a lookup by digest that the
enumeration does not name, or one that runs outside the system scope;
`EMPTY_UUID` passed as the `org_id` by a method outside the three kinds
the enumeration names.

**Severity.** medium

**Check.** `arch-check` decides the docstring and the enumeration of
every tenant-less method; the rest is judged.

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

**Severity.** high

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

**Check.** `arch-check` decides the org_id first on every cache and
bucket operation; the rest is judged.

## CTX-15 Topic payloads carry the tenant

**Principle.** Every topic payload carries `org_id`, so a consumer can
filter by tenant before it acts.

**Source.** Infrastructure, Infrastructure Principles; Topics.

**Look for.** The payload base class, every payload class, and every
subscriber handler.

**Violation.** A payload class that does not extend the base and so
carries no tenant. (A socket handler that forwards an event without
comparing the payload's tenant to the connection's is NET-16.)

**Severity.** high

**Check.** `arch-check` decides the org_id on the payload base and the
base of every payload; the rest is judged.

## CTX-16 Principal-less operations take the request stage and return a stage

**Principle.** The operations that exist before a principal does (a
sign-up, a sign-in, a claim, a sweep, a webhook token) take the request
stage and produce the stage the work runs under; a test names each.
The outbox relay and its two handoffs, the event append and the work
enqueue, take `(org_id, row)` instead: the row carries tenant, actor,
request id.

**Source.** The Business Layer, Operations Without a Principal; OpContext,
Stages; Worker Roles, The Work Queue.

**Look for.** Manager methods whose first parameter is the request
stage; what they return (the identity stage, an `OpContext`, or one
service context per live tenant); their docstrings, which document
them as transitions; the test that enumerates them; any method with no
context at all, and whether the relay runs again from the sweep.

**Violation.** A request-stage method that performs tenant work
directly instead of returning a stage (a sign-up creating the tenant it
answers with acts inside no tenant); a request-stage method the
enumerating test does not name; a method with no context that performs
a tenant operation, where bookkeeping with no principal (the relay and
its two handoffs, the expiry of a lease) is declared as such on its
interface; a transition whose return type is neither a stage nor a list
of stages.

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

**Look for.** The worker's claim path and how it obtains a context,
and from which tenant;
each sweep step that purges or requeues with an audit entry, and the
context it runs under; what the service context carries as its user
id.

**Violation.** A worker running every item under one shared machine
principal so attribution is lost; a worker inventing a context with a
made-up user; a worker configured with a tenant of its own, or a context
carried from one item to the next; an item whose tenant is gone run
under another context; a sweep acting across tenants under one tenant's
context or with no tenant at all; a service context minted on a member,
so a tenant whose members have all left is never swept, or costing one
read per member instead of one per page of tenants.

**Severity.** high

## CTX-18 The credential prefix decides who accepts it

**Principle.** Each credential kind has a distinct prefix, and the
prefix decides which gateway dependency accepts it. An agent's
key is membership-scoped, expiring, and role-capped; a person's login
credential carries no tenant and is exchanged for a tenant-scoped
session token. A live session also proves its identity, and an
exchange presented with one ends it in the same write.

**Source.** The Network Layer, The Gateway; OpContext, Stages.

**Look for.** Credential formats, the dependencies that parse them,
which routes accept which kind; what the identity dependency accepts,
and what the exchange does with a session it is handed.

**Violation.** One dependency that accepts any bearer on any route; a
login credential usable directly on tenant routes; a key that never
expires or that carries a tenant it was not scoped to; a revoked or
expired session accepted into the identity stage; an exchange that
leaves the presented session live, so one tab holds two.

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
principal (CTX-16); what the allowlist entry grants and where an
operator write requires it; how the console and its screens decide
that a person is an operator.

**Violation.** An operator route gated by a tenant role or a feature
flag (operator screens in the portal's bundle are DEL-16); an
`OperatorContext` with a tenant field; an operator write that a
read-only allowlist entry reaches; a manager method that accepts either
context type; an operator route that reaches a tenant manager; an API
key or a session admitted to the operator plane, since the operator
gate admits only the person's own sign-in. (The second factor that
sign-in needs is OPS-07.)

**Severity.** high

**Check.** `arch-check` decides the base and tenant field of the
operator stage and the union of two stages on an operation; the rest
is judged.

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
a bundle of managers per stage (a manager as a member of a context is
CON-18).

**Severity.** high

**Check.** `arch-check` decides the stage hierarchy; the rest is judged.

## CTX-22 A consumer declares the narrowest scope, and a scope is a Protocol

**Principle.** A consumer that needs less than a stage carries declares
a scope: a small `Protocol` of read-only properties naming the
capability it needs. A stage satisfies a scope structurally, with no
projection object built per call. A scope exists when a consumer
declares it or another scope builds on it. A tenant manager operation
takes `OpContext`, which is its scope.

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

**Check.** `arch-check` decides the Protocol shape and the use of every
scope; the rest is judged.

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

**Source.** OpContext, Scopes.

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

**Check.** `arch-check` decides the outbox_row call and the OpContext
built on the operator plane; the rest is judged.

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

## CTX-26 Every stage construction site is enumerated

**Principle.** A stage is an ordinary class anything can call, so
`arch-check` enumerates every site that constructs a stage above the
request stage and fails when a new one appears, the way it enumerates
the tenant-less storage methods.

**Source.** OpContext, Stages.

**Look for.** The list of construction sites the checker holds; every
construction site of `IdentityContext`, `OpContext`, `OperatorContext`,
and their sub-objects, in production code and in test helpers.

**Violation.** A construction site the list does not name; no
enumeration at all; a list that names the transitions but never scans
the code for a new site.

**Severity.** high

**Check.** `arch-check` decides every construction site in production
code; the rest is judged.

## CTX-27 A socket closes at its session's expiry and on the revocation frame

**Principle.** A stage lives no longer than its request. A socket holds
the `OpContext` its ticket produced, so the session's expiry bounds
the socket and the process closes it at that instant; a revocation or
a membership's end travels on the topic bus as `SESSION_REVOKED`, and
every process holding a socket for that session closes it on the
message.

**Source.** OpContext, Stages.

**Look for.** The socket handler and what bounds its life: the deadline
it sets from the session's expiry when the ticket is redeemed, and the
subscription on the topic bus that every process with sockets holds
for `SESSION_REVOKED`; what a process does with a message naming a
session it holds a socket for; what the
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
cannot run without the network (the twin itself is DEL-05).

**Severity.** high

## CTX-29 A handoff's context names the request that caused it

**Principle.** The stage minted on the far side of a handoff is a new
request with its own `request_id`, and it names the request that caused
the work in `caused_by_request_id`, read off the work item. The two are
separate fields, both inherited by every stage above and both logged,
and neither is written over the other.

**Source.** Telemetry, Correlation Across a Handoff; OpContext, Stages.

**Look for.** The worker loop's mint per claim and per sweep pass and
what it reads off the item; the request stage's fields; the log records
a run emits and which of the two ids each carries.

**Violation.** A claim context that carries the item's request id as
its own `request_id`, so the run and its cause are one id; a mint that
drops the causing id, so a run names no cause. (The field on the item
is ASY-29; log lines carrying both are DEL-39.)

**Severity.** medium

**Check.** `arch-check` decides the two request ids and the worker's
reused request id; the rest is judged.

## CTX-30 A cross-tenant case proves what a signature only offers

**Principle.** The fence is the predicate in the query, so a signature
test says only that the tenant was offered. Every storage method has a
case that passes another tenant's identifier and asserts that nothing
is found and nothing changes: reads and writes, the list and the page,
the bulk write, the failure paths. A new method arrives with its case.

**Source.** The Storage Layer, Namespace Shape; Cross-Cutting
Conventions, Tests.

**Look for.** The contract cases behind each storage interface: which
methods have a case under another tenant's identifier, and whether the
list, the page, the bulk write, and the paths that return early or
raise are among them; the body of each query beside its signature.

**Violation.** A method that takes `org_id` and writes a query without
it, which the enumeration of CTX-12 cannot see; a cross-tenant
case on the single read alone, with the list, the page, or the bulk
write untried; a storage method added with no case of its own.

**Severity.** high

## CTX-31 The isolation suite is verified against a deliberate breach

**Principle.** An isolation suite is worth what it catches, so a tenant
predicate is taken out of one query and the suite is run twice. With
the policy live it stays green, which is the second fence holding; with
the policy off for that table it fails, which proves the suite sees the
breach. Both runs are recorded.

**Source.** Cross-Cutting Conventions, Tests; The Storage Layer, The
Second Fence.

**Look for.** The record of the last pair of runs against the tenant
isolation cases (CTX-30): which query lost its predicate, what the
suite reported with the policy live and with it off, and when.

**Violation.** A control run only with the policy live, which says
nothing about the suite; an isolation suite whose worth rests on its
existence, with no run that removed a predicate; a run made and not
recorded, so the next reader takes it on trust.

**Severity.** high

## CTX-32 The funnel sets the scope on every transaction

**Principle.** A storage impl opens its session in one funnel, which
takes `org_id` with an optional `user_id`, or an `identity_id`, and
sets them as settings that die with the transaction. `EMPTY_UUID` as
the `org_id` is the system scope: never a default, passed explicitly,
and enumerated by `arch-check` (CTX-12).

**Source.** The Storage Layer, The Second Fence; A Storage Impl.

**Look for.** The session helper every impl opens through: what it
takes, which settings it sets, and whether each is local to the
transaction; every call site that passes `EMPTY_UUID`, and the
enumeration that lists them.

**Violation.** A session opened without the scope, or a setting that
outlives its transaction, so a pooled connection hands one caller's
tenant to the next; a funnel with a default `org_id`, so a forgotten
argument reads across tenants; a cross-tenant call the enumeration
does not know about.

**Severity.** high

## CTX-33 A cached read sits below authorization

**Principle.** Within a tenant, what one member may read another may
not, so a cached read sits below authorization, never above it. The
manager caches the tenant's data and applies the caller's visibility to
what it read, from the cache or from storage, on every call. A read
cached another way carries every input its visibility depends on in the
key.

**Source.** Infrastructure, Cache.

**Look for.** Every manager read that consults the cache: whether the
visibility check runs after the read on every call, hit or miss, and
what the key carries when it does not (the role, the team, the user).

**Violation.** A manager that caches a filtered view and returns a hit
without applying the caller's visibility; a cached view keyed on the
tenant alone, or missing the role or the team it was filtered by, so
one member's view is served to another.

**Severity.** high

## CTX-34 The permission to enqueue a kind covers its handler's calls

**Principle.** The person authorizes work once, at enqueue, so that
authorization is as wide as the run. The permission that enqueues a
kind covers every operation its handler composes: a role may enqueue a
kind only if it may call each of those operations itself. A test holds
each kind's enqueue permission to its handler's calls.

**Source.** Worker Roles, The Work Queue.

**Look for.** The permission each work kind's enqueue requires, and the
manager operations its handler calls with the permission each needs;
the test that holds the two together; a handler that reads the live
membership before a sensitive step, and the decision recorded for it.

**Violation.** A kind whose enqueue permission is narrower than one of
its handler's calls, so a role reaches through the queue what it could
not do directly; no test holding each kind's enqueue permission to its
handler's calls; a handler that asks again without a recorded decision.

**Severity.** high

## CTX-35 A secret is a tenant's, under its own prefix, named by the manager

**Principle.** A secret belongs to a tenant: every call takes the
`org_id` first, and the impl keeps each tenant's secrets under a
prefix of its own, so a name one tenant presents never resolves to
another tenant's secret or the platform's. The manager sets
`credential_ref` when it puts the secret. The entity lists it in
`MANAGER_OWNED_FIELDS`, so every create and update a caller shapes
excludes it.

**Source.** Infrastructure, Secrets; The Business Layer, Shape of an
Operation.

**Look for.** The secrets interface and whether each method takes
`org_id` first; the key each impl builds from the tenant and the name;
where `credential_ref` is set, whether the entity's
`MANAGER_OWNED_FIELDS` names it, and whether any request or create
shape a caller fills carries it.

**Violation.** A secrets method with no tenant, or an impl that stores
names unprefixed, so a name can reach another tenant's secret or the
platform's own; a `credential_ref` a caller can write, so a caller
points an integration at a secret it does not own.

**Severity.** high

## CTX-36 Failed sign-ins are throttled in storage; sessions have two lifetimes

**Principle.** Sign-in has a defense that does not fail open: the
tenancy manager counts failed sign-ins in its own storage, keyed on
the email's digest, and each failure doubles the delay before the next
attempt for that email is checked, up to a cap. An attempt inside the
delay is refused `429` before its password is checked, and a success
resets it. An unknown email is delayed like a known one. Every
session has an idle and an absolute lifetime, both settings, and every
API key an expiry.

**Source.** The Network Layer, Auth: the Gateway Verifies, the Tenancy
Domain Owns.

**Look for.** The sign-in transition and where it records a failure;
what the count is keyed on, the delay it applies, its base and cap,
and whether the refusal comes before the password check; the session's idle and
absolute lifetimes in settings and where each is checked; the expiry
on an API key.

**Violation.** A sign-in whose only defense is the per-address rate
limit on the cache, which fails open; a failure count kept in the
cache instead of the tenancy manager's storage; a count keyed on the
identity, so an unknown email answers faster than a known one; an
attempt inside the delay whose password is still checked; a session
with no idle
or no absolute lifetime; an API key with no expiry.

**Severity.** high

## CTX-37 A key's role is the lower of its own and its issuer's, at every use

**Principle.** A key's effective role is the lower of the role it was
issued with and its issuer's current role. It is checked at every use,
never only at issue, so a demoted issuer's key loses what the issuer
lost. Removing the issuer's membership revokes, in the same write,
every key the issuer minted in that tenant, and a revoked key is refused
`401`.

**Source.** OpContext; The Network Layer, The Gateway.

**Look for.** Where an API key is admitted: whether the admission
reads the issuer's current membership and role beside the key's own,
and which of the two it grants; what removing a membership does to
the keys its holder issued.

**Violation.** A key admitted at the role stored on it while its issuer
now holds a lower one; a key that outlives its issuer's membership,
or one revoked in a later write than the removal; a
role comparison made once, when the key is minted, and never again.
(The cap at issue is CTX-03.)

**Severity.** high

## CTX-38 An agent reaches the operator plane with an operator token

**Principle.** Agents and pipelines never sign in with a password. The
traffic generator, the deployed smoke test, and a supporter agent use
an operator token. An operator signed in with the second factor mints
it, or the grant job does for the provisioner and the smoke identity.
It carries one operator permission, expires within one hour, and is
stored as its digest and shown once. `admit_operator` admits it as the
one named exception to "a password alone never admits". The ops env
file holds `<ROOT>_OPERATOR_TOKEN`, never a password or a TOTP secret.

**Source.** OpContext, The Operator Context; The Network Layer, The
Gateway; Operations, Operator Credentials.

**Look for.** How the traffic generator, the smoke test, and each
agent skill authenticate to the operator plane; where an operator
token is minted, its permission, its expiry, and how it is stored;
what `admit_operator` accepts; the keys in each ops env file.

**Violation.** An agent or a pipeline that signs in with a password or
holds a TOTP secret; an operator token with more than one permission,
an expiry past one hour, or a value stored in the clear; a token
minted by a sign-in with no second factor, or a gate that admits some
other credential without one.

**Severity.** high
