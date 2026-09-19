# Context

Group id: `context`. Covers OpContext (with The Operator Context),
the authorization and tenancy split of Separation of Layers, the
authorization step and parameter order of The Business Layer and its
"Operations Without a Principal", the tenancy rules of The Storage
Layer, the tenant keying of Infrastructure, the credential and
operator concerns of The Gateway in The Network Layer, and the worker
context provenance of Worker Roles.

This group judges one question: does every operation know who is
acting, for which tenant, with what authority, and is that knowledge
carried and enforced in the right layer. It leaves interface shape and
injection to `contracts`, table and translation mechanics to `storage`,
queue and worker loop mechanics to `async`, and the error envelope,
rate limits, and public types to `network`.

## CTX-01 Context is the first argument of every operation

**Principle.** Every manager, service, and worker-handler operation
takes a context as its first argument, except the enumerated
principal-less operations (CTX-16). By the time a manager runs, the
context is fully built.

**Source.** OpContext; The Business Layer.

**Look for.** Manager interfaces, service interfaces, worker handlers:
the first parameter of every async method, and the docstring of any
method that has none.

**Violation.** A manager or service method that takes a user id, an
org id, or a token instead of a context; a method that takes the
context in any position other than first; a handler that fetches
identity from a request object; a context-less method that is not one
of the documented principal-less operations.

**Severity.** medium

## CTX-02 The context carries identity, tenant, role, permissions, app, and request

**Principle.** The security context holds `user_id`, `org_id`, the
role, the permissions, the teams, the credential kind, and
`credential_id`: ids and facts, never entities, so a manager that
needs the user loads it and a role change is seen on the next request.
The app context holds the app type and version; the context holds the
request id and an optional trace id. The request id reaches every log
line, every audit row, and the error envelope from the context, never
by hand.

**Source.** OpContext.

**Look for.** The context type definitions, every place that reads
identity, tenant, or app information, and the audit record type.

**Violation.** A `User` or `Org` entity embedded in the context, so a
role change waits for a new session and the context module imports
the tenancy types; a context with no credential id, so rate limits
and socket tickets key on something else; identity or tenant data
passed beside the context as extra parameters; a second ad-hoc
"current user" object; app type derived from headers below the
gateway; a request id threaded by hand; an audit row written without
the context's request id.

**Severity.** medium

## CTX-03 Permissions are a pure function of role, and a credential never outranks its issuer

**Principle.** Permissions derive from role through one table in the
tenancy namespace. A credential issued by a principal carries a role no
higher than the issuer's.

**Source.** OpContext.

**Look for.** The role-to-permission table, credential issuing paths
(API keys, invitations, session tokens), and any code that assigns
permissions.

**Violation.** Permissions stored per user or per credential
independently of role; a second mapping elsewhere; an issuing path that
does not cap the new credential's role at the issuer's role.

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

## CTX-05 The context is built only at the four entry points

**Principle.** A context is constructed by the gateway on request
arrival, by the claim operation a worker calls to take a unit of work,
by the tenancy manager's service context per live tenant that a sweep
asks for, and by the bootstrap that seeds an environment. Nothing else
constructs one.

**Source.** OpContext; The Business Layer, Operations Without a
Principal; The Network Layer, The Gateway.

**Look for.** Every construction site of the context type and its
sub-objects.

**Violation.** A manager, storage impl, or test helper used in
production code that builds a context; a router that assembles a
context from headers or tokens; a service that constructs one for an
internal call.

**Severity.** high

## CTX-06 The context is immutable and narrowing is an explicit argument

**Principle.** Once built, the context flows through every downstream
call unchanged. A narrower view (an override, a reduced permission set)
is passed as an explicit argument, never by mutating or copying the
context mid-request.

**Source.** OpContext.

**Look for.** Copies or mutations of the context after the gateway;
methods that accept a context and hand a different one downstream.

**Violation.** A context copied with altered fields inside a manager or
service; a permission set widened or narrowed on the context; a "with
override" helper that replaces the context instead of adding a
parameter.

**Severity.** medium

## CTX-07 No ambient state outside the context

**Principle.** Operations never reach for ambient state through globals,
thread locals, or hidden lookups. Everything ambient flows through the
context.

**Source.** OpContext; Cross-Cutting Conventions, Logs.

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
Routers translate and storage persists; neither decides authorization.

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

**Source.** The Business Layer, Parameters; The Storage Layer, Namespace
Shape.

**Look for.** Parameter order on storage, manager, and service
interfaces; user-scoped storages.

**Violation.** A storage method taking the entity id before the tenant
id; a user-scoped read that takes only the tenant and filters by user
inside the impl, or takes no user at all; a manager or service method
that takes an org id or user id the context already carries.

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

**Principle.** Global tables and cross-tenant sweeps are the documented
exceptions to the tenant-first rule. Global methods take no tenant and
say why in their docstring; sweeps return the tenant with each row as
`tuple[UUID, Entity]`; a test enumerates the exceptions.

**Source.** The Storage Layer, Namespace Shape.

**Look for.** Storage methods without a tenant parameter; the test that
lists them.

**Violation.** A tenant-less storage method with no docstring
justifying it; a sweep that returns entities without their tenant; a
new tenant-less method that the enumerating test does not know about;
no such test at all.

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

## CTX-16 Principal-less operations produce a context

**Principle.** The few operations that exist before a principal does
(claiming work, sweeping every tenant, resolving an inbound webhook
token) are declared without a context, documented as platform-internal,
and produce the context under which the work then runs. The outbox
relay and the event append it performs take `(org_id, row)` instead:
the row carries its tenant, actor, and request id from the write that
made it, and the relay runs again from the sweep.

**Source.** The Business Layer, Operations Without a Principal; Worker
Roles, The Work Queue.

**Look for.** Manager methods without a context parameter; what they
return; their docstrings.

**Violation.** A context-less method that performs tenant work
directly instead of returning a context; a context-less manager method
whose docstring does not say platform-internal, or whose return type is
neither a context nor a list of `tuple[UUID, Entity]`.

**Severity.** high

## CTX-17 A worker runs under the enqueuer's identity with a service role

**Principle.** When a worker claims a work item, it rebuilds the
enqueuer's principal from the item's `created_by` under a service role.
Cross-tenant sweeps obtain one service context per live tenant from the
tenancy manager.

**Source.** Worker Roles, The Work Queue; The Business Layer, Operations
Without a Principal.

**Look for.** The worker's claim path and how it obtains a context;
sweep code.

**Violation.** A worker running every item under one shared machine
principal so attribution is lost; a worker inventing a context with a
made-up user; a sweep acting across tenants under one tenant's context
or with no tenant at all.

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

**Principle.** Operator routes resolve the bearer to an identity, admit
it only when the identity is on the operator allowlist and the
credential is the person's own sign-in, and produce an `AdminContext`
that has no tenant. Operator managers take `AdminContext` and nothing
else; tenant managers take `OpContext` and nothing else.

**Source.** OpContext, The Operator Context; The Network Layer, The
Gateway.

**Look for.** The operator gate, the `AdminContext` type, every manager
signature on the operator plane and the tenant plane.

**Violation.** An operator route gated by a tenant role or a feature
flag; an `AdminContext` with a tenant field; a manager method that
accepts either context type; an operator route that reaches a tenant
manager; an API key or an invitation-minted session admitted to the
operator plane.

**Severity.** high
