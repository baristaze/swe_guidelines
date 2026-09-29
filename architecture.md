# Software Design and Architecture Guidelines

This document describes how we design and build a multi-tenant,
service-based system in Python, from the object model at the center to
the apps at the edge. It is opinionated on purpose: every rule below is
one we apply, and every shape is one we use.

It is written for a small team: one process, one database, and no
appetite for a rewrite when the system grows. Most rules exist so that
the next step, when load asks for it, is a deployment change and not a
code change. It assumes that an agent writes most of the code and a
person reads it. Some rules, the second impl of every interface above
all, are cheap on that assumption and a tax without it.

## How to Read This

The document has two readers, a person and an agent. It tells the story:
what each part is, and why. Where code can speak, it links the code. The
code is the scaffold, [`scaffold/acme_root/`](scaffold/acme_root/): the
domain-agnostic core of a system in this shape, which a new project
copies and grows. Where a rule needs a domain noun, the text borrows one
from a commerce platform: a catalog, orders, inventory. The nouns are
illustrative; the shapes are not.

A section that deserves one carries a tag, alone on the line under its
heading. There are four:

- `core`: one of the invariants that make a system this architecture. A
  departure is a different architecture.
- `default`: a named choice, a technology or a starting value. A
  project substitutes it through [Technology Choices and How to
  Override Them](#technology-choices-and-how-to-override-them), and a
  substitution is not a deviation.
- `optional`: adopted when its trigger arrives. Its absence is no gap.
- `style`: a house convention, kept for consistency. A departure is a
  low finding at most.

Untagged text is the rule. A departure from it is recorded as a
deviation, in an ADR of the project's own. A tag covers the text under
its own heading, up to the next heading of any level. So a `##` tag
never reaches the `###` sections under it: each carries its own tag, or
none. A principle takes its section's tag. Nuance that only an agent
needs sits in a short `agents-only` comment inside its section, which a
rendered page does not show.

The detail lives in the tools, and that is a choice. This document
tells the story. The [lenses](lenses/README.md) and the skills hold the
detail, and they are stricter than the story on purpose: a lens names
the exact field, call, and breach. The checker (`arch-check`) and the
gates are stricter still, because a program holds only what it can
decide. That ladder keeps the story clean and lets the tools carry the
weight.

## The Core

These are the invariants. Each links the section that states it.

- [One source of truth](#the-domain-as-the-source-of-truth): the object
  model, a library every layer depends on.
- [Entities are frozen](#immutability); an update is a copy written
  back.
- [Every id is a `uuid_v7`](#identifiers), minted above storage.
- [Namespaces are swimlanes](#namespaces-as-swimlanes), each with the
  same shape.
- [Three layers](#separation-of-layers), each depending on the one below
  through interfaces. Managers authorize; storage enforces tenancy.
- [Every layer is defined by interfaces](#interfaces), and each can run
  without its technology.
- [Dependencies arrive through constructors](#injectability), typed by
  interface.
- [All ambient state rides the context](#tenantcontext), every operation's
  first argument.
- [A context stage is evidence](#stages), and only a transition makes
  one.
- [Tenant and operator operations never mix](#the-operator-context).
- [Every write authorizes, verifies, copies, and
  writes](#shape-of-an-operation), with its outbox rows, in one storage
  call.
- [A storage call is short and whole](#storage-principles); every read
  filters by tenant, and every write checks it.
- [Every table has one database role](#database-roles), and nothing
  crosses a role.
- [A migration is compatible with the release before
  it](#migrations).
- [Infrastructure is capabilities behind
  interfaces](#infrastructure-principles); the object model imports
  them, never the reverse.
- [The gateway is the only edge](#the-gateway), and it mints the
  context.
- [Domain services are stateless](#stateless-vs-stateful-services).
- [Wire types project the object model](#public-types), and the OpenAPI
  document is the contract.
- [Calls flow downward](#direction-of-calls).
- [Every handler is idempotent](#idempotency-on-the-consumer-side) on a
  key its producer set.
- [Every push is also a record](#realtime-at-the-edge); the socket
  carries hints.
- [Background work runs in workers](#workers-not-web-service-side-jobs),
  never in a web service.
- [Durable work is a row](#the-work-queue), claimed under a lease and
  fenced by a token.
- [Apps are dumb](#apps-are-dumb).
- [Every process ships from one commit](#deployment), and what two
  releases share changes expand-then-contract.
- [An operational task is a skill](#operations) a person runs with an
  agent, and its credential is the boundary.

## Contents

<!-- toc -->
- [How to Read This](#how-to-read-this)
- [The Core](#the-core)
- [The Domain as the Source of Truth](#the-domain-as-the-source-of-truth)
- [Naming Entities](#naming-entities)
  - [Entities, Value Objects, and Read Models](#entities-value-objects-and-read-models)
  - [Immutability](#immutability)
  - [Identifiers](#identifiers)
- [Namespaces as Swimlanes](#namespaces-as-swimlanes)
  - [Pure Rules](#pure-rules)
- [Separation of Layers](#separation-of-layers)
- [Interfaces](#interfaces)
  - [Multiple impls per interface](#multiple-impls-per-interface)
  - [Composition by decoration](#composition-by-decoration)
  - [Injectability](#injectability)
- [TenantContext](#tenantcontext)
  - [Stages](#stages)
  - [Scopes](#scopes)
  - [The Operator Context](#the-operator-context)
- [The Business Layer](#the-business-layer)
  - [Shape of an Operation](#shape-of-an-operation)
  - [Parameters](#parameters)
  - [Cross-Manager Dependencies](#cross-manager-dependencies)
  - [Operations Without a Principal](#operations-without-a-principal)
- [The Storage Layer](#the-storage-layer)
  - [Storage Principles](#storage-principles)
  - [Namespace Shape](#namespace-shape)
  - [Storage Root](#storage-root)
  - [Defining ORM Classes](#defining-orm-classes)
  - [Translation](#translation)
  - [A Storage Impl](#a-storage-impl)
  - [Cross-Storage Dependencies](#cross-storage-dependencies)
  - [Database Roles](#database-roles)
  - [The Second Fence](#the-second-fence)
  - [Migrations](#migrations)
- [Infrastructure](#infrastructure)
  - [Infrastructure Principles](#infrastructure-principles)
  - [InfraInterface Root](#infrainterface-root)
  - [Cache](#cache)
  - [Buckets](#buckets)
  - [Topics](#topics)
  - [Queues](#queues)
  - [Secrets](#secrets)
  - [Idempotency](#idempotency)
- [The Network Layer](#the-network-layer)
  - [How It Starts and Where It Goes](#how-it-starts-and-where-it-goes)
  - [Web Services as Scalability Units](#web-services-as-scalability-units)
  - [Domain Services vs App-Specific Services](#domain-services-vs-app-specific-services)
  - [At a Glance](#at-a-glance)
  - [Stateless vs Stateful Services](#stateless-vs-stateful-services)
  - [Service Interfaces and Impls](#service-interfaces-and-impls)
  - [The Gateway](#the-gateway)
  - [Auth: the Gateway Verifies, the Tenancy Domain Owns](#auth-the-gateway-verifies-the-tenancy-domain-owns)
  - [Intra-Service Communication](#intra-service-communication)
  - [Public Types](#public-types)
  - [From OM to Wire](#from-om-to-wire)
  - [Clients Live in One Place](#clients-live-in-one-place)
  - [Direction of Calls](#direction-of-calls)
  - [Idempotency on the Consumer Side](#idempotency-on-the-consumer-side)
  - [Realtime at the Edge](#realtime-at-the-edge)
  - [Wait-for-Response vs Fire-and-Forget](#wait-for-response-vs-fire-and-forget)
  - [Long-Running Orchestrations](#long-running-orchestrations)
- [Worker Roles](#worker-roles)
  - [Workers, Not Web-Service Side Jobs](#workers-not-web-service-side-jobs)
  - [The Work Queue](#the-work-queue)
  - [Shape of a Worker](#shape-of-a-worker)
  - [Shutdown](#shutdown)
  - [Maintenance Without a Scheduler](#maintenance-without-a-scheduler)
  - [Implementation Options](#implementation-options)
- [Apps](#apps)
  - [Apps as Products](#apps-as-products)
  - [Apps Are Dumb](#apps-are-dumb)
  - [Push-First Apps](#push-first-apps)
- [Client App Architecture](#client-app-architecture)
  - [Stack](#stack)
  - [Client Rendering](#client-rendering)
  - [State and Data](#state-and-data)
  - [Views, View-Models, Models](#views-view-models-models)
  - [API Access](#api-access)
  - [One Tenant at a Time](#one-tenant-at-a-time)
  - [Realtime: One Channel per App](#realtime-one-channel-per-app)
  - [The Operator Console](#the-operator-console)
  - [The CLI Is Different](#the-cli-is-different)
- [Deployment](#deployment)
  - [Cloud: AWS](#cloud-aws)
  - [Infrastructure as Code](#infrastructure-as-code)
  - [Migrating a Deployed Database](#migrating-a-deployed-database)
  - [Local: Docker Compose](#local-docker-compose)
  - [Twins for External Services](#twins-for-external-services)
  - [What a Process Refuses](#what-a-process-refuses)
  - [Security Defaults](#security-defaults)
- [Operations](#operations)
  - [Operator Roles](#operator-roles)
  - [Operator Credentials](#operator-credentials)
  - [Operational Skills](#operational-skills)
  - [Dashboards and Alarms as Code](#dashboards-and-alarms-as-code)
  - [Scale-Out as a Lever](#scale-out-as-a-lever)
  - [Cost Boundaries](#cost-boundaries)
  - [Creating and Destroying an Environment](#creating-and-destroying-an-environment)
  - [Traffic and Stress](#traffic-and-stress)
  - [The Telemetry Round Trip](#the-telemetry-round-trip)
- [Monorepo Folder Structure](#monorepo-folder-structure)
  - [Layout Conventions](#layout-conventions)
- [Documentation as Code](#documentation-as-code)
  - [A README at Every Level](#a-readme-at-every-level)
  - [The Knowledge Map](#the-knowledge-map)
- [Telemetry](#telemetry)
  - [Logs](#logs)
  - [Traces and Metrics](#traces-and-metrics)
  - [Error Tracking](#error-tracking)
  - [Correlation Across a Handoff](#correlation-across-a-handoff)
- [Cross-Cutting Conventions](#cross-cutting-conventions)
  - [Exceptions](#exceptions)
  - [Configuration](#configuration)
  - [The App Container](#the-app-container)
  - [Records of Decisions](#records-of-decisions)
  - [Tests](#tests)
- [Technology Choices and How to Override Them](#technology-choices-and-how-to-override-them)
  - [Versions](#versions)
  - [Overriding a Choice](#overriding-a-choice)
- [Scalability by Design](#scalability-by-design)
- [Resilience by Design](#resilience-by-design)
- [What This Document Does Not Cover](#what-this-document-does-not-cover)
- [Next: An End-to-End Reference Implementation](#next-an-end-to-end-reference-implementation)
<!-- /toc -->

## The Domain as the Source of Truth

`core`

Good design starts with a clear domain: the nouns we use to describe the
product, and how they relate. For a commerce platform they are
`Product`, `Order`, `Shipment`, `Warehouse`, and `Invoice`. Every layer
refers back to them, so they are defined in one place.

That place is an object model (OM) of the business domain:
handwritten, pure Python classes on Pydantic, shipped as a standalone
library rather than a service or an app. Every application that
depends on it shares its vocabulary, and it lives in the monorepo with
them, so the domain evolves with the system.

The OM is the source of truth for entities. The wire format is not, and
neither is the table layout. The network layer projects entities onto
the wire, and the storage layer onto rows. Neither projection changes
the OM to suit itself.

> **Principle:** The domain has one source of truth: a standalone OM
> library. Every layer depends on it; nothing redefines it.

## Naming Entities

Every entity in the domain is named by a class in the object model.

First comes a small set of mixins, each one trait. An entity composes
the mixins it needs, in a fixed order, so its signature reads as a
description. The base module holds them, with the root and the two
helpers every constructor needs, `new_id()` and `utcnow()`
([`base.py`](scaffold/acme_root/om/src/acme/om/base.py)):

``` python
def new_id() -> UUID:
    return uuid7()  # time-ordered, from the standard library

def utcnow() -> datetime:
    return datetime.now(UTC)

class Platform(BaseModel):  # the root; holds no fields
    model_config = ConfigDict(frozen=True, extra="forbid")

class Identifiable(Platform):
    id: UUID  # uuid_v7, from new_id()
class Named(Platform):
    name: str
class Created(Platform):
    created_at: datetime
class Trackable(Created):
    updated_at: datetime
    created_by: UUID
    updated_by: UUID
class SoftDeletable(Platform):
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None
```

A warehouse is `Identifiable`, `Named`, `Trackable`, and
`SoftDeletable`: identity first, then the label, the lifecycle, and the
cross-cutting traits. It takes a mixin only where an operation exercises
it: `Trackable` where an update exists, `SoftDeletable` where a delete
does.

| Mixin           | What the entity promises                    |
|-----------------|---------------------------------------------|
| `Identifiable`  | it has an identity                          |
| `Named`         | it carries a human-facing label             |
| `Created`       | it has a birth time, and nothing more       |
| `Trackable`     | its lifecycle is recorded, by whom and when |
| `SoftDeletable` | it can be hidden without being purged       |

An append-only record, an event or a ledger line, is `Identifiable`
and carries its time in a field of its own: `created_at` from `Created`,
or a name of its own, as an event's `produced_at`. It is never updated,
so it has no `updated_at`, and never hidden, so no `deleted_at`. The one
write it takes after its birth is erasure, which redacts a person's
fields in place.

A row the platform writes for its own bookkeeping composes `Created`,
since no person made it. Two are touched by every namespace, and each is
declared once, in its own namespace: the `OutboxRow` that announces a
write and the `IdempotencyRecord` that owns a retry. A work item looks
like bookkeeping and is `Trackable`: the person who enqueued it is its
attribution, and the platform signs its later writes with `EMPTY_UUID`.

The base module also names `PROVENANCE_FIELDS`, which no caller
rewrites, and each entity declares `MANAGER_OWNED_FIELDS`, which its
manager sets ([Shape of an Operation](#shape-of-an-operation)).
`extra="forbid"` on the root makes a misspelled field a construction
error, on every class of the chain.

> **Principle:** Inheritance expresses abstraction, not code reuse.
> Each mixin is a promise about what the entity is.

### Entities, Value Objects, and Read Models

Three kinds of class live on the base chain. An **entity** has an
identity and is stored: `Order`, `Warehouse`. A **value object** is a
typed piece of an entity with no identity of its own, an `Address` or a
`Money` amount, stored inline with its owner. A **read model** is a
shape a manager returns that is not an entity, such as `StockLevel`
across warehouses. It carries no mixins, and a stored copy of one is
derived and rebuildable.

Typed filters and groupings, `OrderFilter` and `StockGroupBy`, are
value objects too. They pass through manager and storage interfaces
unchanged, so an aggregation runs in SQL on one storage impl and in
Python on the other while the caller writes the same code.

### Immutability

`core`

> **Principle:** OM entities are immutable. Updates happen by
> copy-and-write, never by mutation.

Everything on the base chain is frozen, so a `Warehouse` read from
storage is a snapshot, not a live handle. An update takes the entity,
makes a modified copy, and passes the copy to a write. Shared
references stay safe across async tasks. The copy is `model_copy` when
every new value already has its field's type, and `model_validate` from
a dict when it carries dumped data, so the values are validated.

Frozen reaches inside. Fields are tuples and frozen models, never a
`list` or a `dict`, and a mapping field is a `FrozenMapping`, which
wraps what pydantic builds, all the way down, in a `MappingProxyType`.
The one exception is the SQLAlchemy row classes, which the session must
mutate. They never leave the storage impl.

<!-- agents-only
- A bare `Mapping` on a frozen model still holds a mutable dict.
  `FrozenMapping` freezes on the way in, nested lists into tuples, and
  its serializer dumps plain dicts and lists on the way out.
- Pydantic does not validate a default, so an empty `FrozenMapping` is
  `Field(default_factory=dict, validate_default=True)`.
- `model_validate` given an instance returns it unvalidated: pass it a
  dict.
-->

### Identifiers

`core`

Every id is a `uuid_v7`: a millisecond timestamp in front and randomness
behind. It is time-ordered, and that buys index locality: an insert
lands at the tail of its B-tree, and a list in id order pages by id. An
id is not a clock, though. Two processes mint with two clocks, so its
time is near the truth and never the truth. When a record's time
matters, it is a field of its own, such as `created_at`. The one place
an id's time is read, on purpose, is the lease of a pending idempotency
record: it runs from the attempt's `attempt_id`, a `uuid_v7`, and a
lease needs no more than near the truth
([`attempt.py`](scaffold/acme_root/om/src/acme/om/idempotency/types/attempt.py)).

Whoever constructs the entity mints its id, above the storage layer,
with `new_id()`. The database never assigns one, and nothing reads one
back. A record an outside delivery creates is the one exception. Its id
is `derived_id(key, at)`: a v7 whose time is the delivery's and whose
random bits come from the delivery's key
([`base.py`](scaffold/acme_root/om/src/acme/om/base.py)). A copy the
queue hands over again presents the same id, so its create meets the
row already there and creates nothing.

`EMPTY_UUID`, the zero UUID, means the platform: not a tenant and not a
person. It is the `org_id` of cross-tenant reference data, and the value
of a required reference nobody owns, such as `updated_by` on a work
item the platform claimed. A reference that is genuinely optional is
`None`.

> **Principle:** Every id is `uuid_v7`, minted above storage with
> `new_id()`, or with `derived_id()` for a record an outside delivery
> creates. The order is for the index; the time is a field, save for
> the one lease that reads an attempt's id.

## Namespaces as Swimlanes

`core`

The object model is split into namespaces, each a swimlane of the
product. A warehouse is a long-lived thing we maintain. An order is
something we create, fulfil, and report on. The two interact constantly
and stay separate domains, and neither is buried under the other.

Every namespace has the same shape. The scaffold's `media` is the plain
case ([`om/media/`](scaffold/acme_root/om/src/acme/om/media/)):

```text
acme/om/orders/
    __init__.py    # re-exports OrderManagerInterface
    manager.py     # the manager interface
    types/         # entities, value objects, read models
    impl/          # the manager impl
    storage/       # storage interface, impls, tables
    rules.py       # pure functions, when there are any
```

Names follow the namespace: `OrderManagerInterface`,
`OrderStorageInterface`, and an impl with its technology last,
`OrderStoragePostgresImpl`. A storage operation reads or writes
(`read_orders`, `write_order`). A manager operation names what the
caller asks for (`get_orders`), or the domain's own verb
(`place_order`). A work handler is `<Kind>HandlerImpl`, its kind in
CamelCase.

Cross-cutting concerns are namespaces like any other: tenancy, events,
the outbox, idempotency, work. None is a utility off the root. Audit,
who did what and from which app, is a kind of event: an audit entry is
an `Event` with an audit kind, in the events namespace's stream
([Realtime at the Edge](#realtime-at-the-edge)). Audit becomes a
namespace of its own when it gains a reader of its own, such as a
screen that lists who did what, or an export.

### Pure Rules

A namespace with real business logic keeps its pure part in one module
of plain functions, `rules.py`: pricing, eligibility, a table a decision
reads ([`rules.py`](scaffold/acme_root/om/src/acme/om/media/rules.py)).
They take values and return values, and read no storage, clock, or
settings. So they test without infrastructure, and two storage impls
that compute the same aggregate call the same function. A rule the
engine must evaluate inside a statement is spelled there once more,
named, and the contract case that runs both impls holds the two
spellings together.

> **Principle:** A namespace's rules are pure functions in one module,
> called by its storage and its managers alike.

## Separation of Layers

`core`

The system has three layers. **Network** receives requests, shapes
responses, and enforces protocols. **Business** is the object model:
entities, managers, operations. **Storage** is persistence, under the
object model and apart from it. An upper layer depends on the
interfaces of a lower one, never on its internals, and infrastructure is
injected into any of them without leaking a technology across.

Managers authorize; storage enforces tenancy. Permissions are a business
decision, so they sit in the manager beside the operation they guard.
Tenancy is a data boundary, so it sits in storage, where every query
carries the tenant. So a request that reaches storage was authorized,
and a query that reaches the database cannot cross a tenant.

In the scaffold, the network layer is
[`services/api/`](scaffold/acme_root/services/api/), the object model
and its storage are [`om/`](scaffold/acme_root/om/), and the
capabilities are [`infra/`](scaffold/acme_root/infra/):

``` mermaid
flowchart TD
    App[Apps] --> GW[Gateway: mints the context]
    GW --> Svc[Service impls: translate, compose]
    Svc --> Mgr[Managers: authorize, decide]
    Mgr --> Sto[Storage: tenancy, persistence]
    Sto --> DB[(Database roles)]
    Svc -.-> Infra[Infrastructure capabilities]
    Mgr -.-> Infra
```

> **Principle:** Three layers: Network, Business, Storage. Upper
> depends on lower through interfaces only. Infrastructure cross-cuts
> without leaking technology. Managers authorize; storage enforces
> tenancy.

## Interfaces

`core`

Every layer is defined by its interfaces. A manager, a storage, a
service: each exposes a `*Interface`, an `ABC` of async methods whose
signatures are the contract
([`manager.py`](scaffold/acme_root/om/src/acme/om/media/manager.py)):

``` python
class MediaManagerInterface(ABC):
    @abstractmethod
    async def get_file(self, ctx: TenantContext, file_id: UUID) -> File: ...
    @abstractmethod
    async def confirm_file(self, ctx: TenantContext, file_id: UUID) -> File: ...
```

The interface describes a capability; the impl decides how to deliver
it. That split makes impls mockable and injectable, and an impl that
forgets a method will not instantiate.

### Multiple impls per interface

Every interface can be satisfied without its technology. A storage, a
capability, and an external service each get an in-memory impl or a
deterministic twin beside the real one: `InventoryStoragePostgresImpl`
and `InventoryStorageMemoryImpl`. A manager needs no second class: over
the memory roots it already runs without technology. A service interface
pairs its in-process impl with the typed client, written the day a
process stops holding what the callee needs.

The memory impl is a full second implementation, not a stub, and one
suite of contract cases runs both: the named atomic methods, the
compare-and-set, every unique key. Without those cases, a lenient
memory impl passes where the engine refuses, and the pair is two impls
of two contracts. For a person the pair is overhead; for an agent it is
cheap, and it lets a whole application run in-process in a test.

### Composition by decoration

Because an impl depends on an interface, impls compose. A mixed cache
holds a local and a cloud `CacheInterface` and is itself one, and the
caller cannot tell where a hit came from. Retry, metrics, and tracing
wrappers take the same shape. A manager takes a decorated capability;
it never wraps its storage.

A dependency that is down does not fail fast: every call pays the full
timeout, and the timeouts exhaust the pool. A breaker cuts it off. It
counts consecutive failures, refuses at once for a cool-down past a
bound from settings, and then lets one call through
([`breaker.py`](scaffold/acme_root/infra/src/acme/infra/breaker.py)). An
open breaker answers the way the dependency's own failure answers: the
unavailable exception where the interface raises, and a miss over a
cache, whose failure is a miss. It declines to pay the timeout, never to
keep the contract.

### Injectability

`core`

> **Principle:** Dependencies are injected through constructors and
> typed by interface, never by impl.

A root constructs the impls in order and wires them: storage, managers,
services. A constructor takes what is structural and lives as long as
the process: storages, peer managers, capabilities, and a frozen options
object built once from settings. What one operation needs, the actor
and the request, arrives in the context, per call. Managers never read
the environment. When two managers need each other, the cycle is
broken above them: move the shared operation down, or pass a narrow
callable.

## TenantContext

`core`

Every operation takes a context first. It says who is acting, for which
tenant, with what role, from which app, under which request. `TenantContext`
is a tenant operation's context, the one most operations take
([`context.py`](scaffold/acme_root/om/src/acme/om/context.py)):

``` python
class SecurityContext(Platform):
    user_id: UUID
    org_id: UUID
    role: Role
    permissions: tuple[Permission, ...]
    teams: tuple[UUID, ...] = ()
    credential_kind: CredentialKind
    credential_id: UUID = EMPTY_UUID  # the session or key; EMPTY_UUID for internal contexts

class RequestContext(Platform):
    request_id: UUID
    app: AppContext
    trace_id: str | None = None
    traceparent: str | None = None
    caused_by_request_id: UUID | None = None
    deadline: datetime | None = None  # when this request's time runs out

class TenantContext(RequestContext):
    security: SecurityContext  # org_id, user_id as properties; require(), in_team()
```

Permissions are a pure function of role, one table in the tenancy
namespace's `rules.py`. A credential never carries a role above its
issuer's, at every use: an API key acts at the lower of its own role and
its issuer's current one. The role reserved for services is no rung a
person can mint. Teams are a second axis inside a tenant, read with
`ctx.in_team`.

The context carries ids and facts, never entities. `request_id` is
stamped once, at the edge, and reaches every log line, audit row, and
error envelope. `caused_by_request_id` is empty at the edge and names
the request behind a handoff. `ctx.require(Permission.WRITE)`, which
raises `NotAuthorized`, at the top of a manager method keeps
authorization in the business layer. A context is immutable, and
nothing reaches around it through a global or a thread local.

> **Principle:** All ambient state flows through the context. No
> globals, no thread locals, no hidden lookups.

### Stages

`core`

A request proves who is behind it in steps, and each step is a type
([`context.py`](scaffold/acme_root/om/src/acme/om/context.py)):

```text
RequestContext            a request exists; nobody is known yet
  ├─ IdentityContext      a person is verified by their own sign-in
  │    └─ OperatorContext the person is on the operator allowlist
  └─ TenantContext        a membership: one tenant, one user, one role
```

A stage subclasses the stage it refines, so a function asking for the
weaker one accepts the stronger, never the reverse. `TenantContext` does
not refine `IdentityContext`: an API key or a worker has no sign-in
behind it. The chain grows below `TenantContext` only when operations come
to rely on a role instead of requiring a permission.

The request stage is minted at an edge: the gateway, the worker loop,
the bootstrap command. Every stage above it comes from a transition, an
operation of the tenancy manager that consults the evidence and returns
the next stage or refuses: `authenticate` for `TenantContext`,
`admit_operator` for `OperatorContext`. Nothing else constructs one,
and `arch-check` holds the construction sites to that.

The stage is the proof. An operation takes the weakest stage that proves
what it needs and never checks it again, and the type checker refuses a
caller holding less. A sign-in route cannot reach a tenant manager.

A stage lives as long as what minted it. A socket holds its `TenantContext`
and closes when the evidence goes: at the session's expiry, on a change
on the bus that ends it, such as `tenancy.session.revoked`, and on a
recheck every `session_recheck_interval` that finds the session or the
membership ended or the role changed. The recheck is not activity and
never moves `last_seen_at`. The server does not cap a connection's life,
since the recheck bounds its trust, and closing a socket never ends its
session.

> **Principle:** A context stage is evidence. Only a transition
> produces it, its type is the proof, and an operation takes the
> weakest stage that proves what it needs.

### Scopes

Some consumers need less than a stage carries, and should not see the
permissions. Each declares a scope: a `Protocol` naming what it reads,
which a stage satisfies by carrying the members. `TenantScope` is
`org_id`, and `ActorScope` adds `user_id`. `ProvenanceScope` joins the
actor and the request, and `outbox_row` takes it to stamp provenance.
A scope is a view a context already satisfies, not a contract an impl
is written to, so it is a `Protocol` and not an `ABC`.

A combination gets a name only when it is a domain concept, as
provenance is. A scope never proves a stage, since anything with the
right fields satisfies one, so a tenant operation still takes
`TenantContext`. And a manager never arrives on a context, as a request id
never arrives in a constructor.

> **Principle:** Scopes are typed capability boundaries. A consumer
> declares the narrowest scope it needs, a context satisfies it
> structurally, and no scope carries the object graph.

### The Operator Context

`core`

The people who run the platform ask what no tenant context answers:
usage across organizations, health, global configuration. That is a
second plane with its own context, `OperatorContext`: the identity stage
refined by `admit_operator` when the identity is on the operator
allowlist. It has no `org_id`, on purpose.

An operation takes `TenantContext` or `OperatorContext`, never a choice
between them. Only three kinds take a weaker stage: a transition, an
identity's own operations before any tenant, and [Operations Without a
Principal](#operations-without-a-principal). An operator reads a
tenant's rows only by naming the tenant, and every such read is
recorded.

> **Principle:** Tenant operations take `TenantContext`; operator
> operations take `OperatorContext`. The two never mix in one
> signature.

## The Business Layer

The business layer is where the object model comes alive. A manager
exposes operations through its interface, takes a context first, and
returns entities or read models. Its impl holds its storage, the peers
it composes, and its capabilities, all injected, and `build_managers`
wires every manager at boot into one frozen object
([`root.py`](scaffold/acme_root/om/src/acme/om/root.py)).

### Shape of an Operation

`core`

Every write takes four steps: authorize, verify, copy, write. Read it
once and you can read every manager
([`manager.py`](scaffold/acme_root/om/src/acme/om/media/impl/manager.py)):

``` python
async def confirm_file(self, ctx: TenantContext, file_id: UUID) -> File:
    ctx.require(Permission.WRITE)             # authorize
    file = await self.get_file(ctx, file_id)  # verify: it exists, in this tenant
    ...                                       # and its object has arrived
    stored = file.model_copy(                 # copy
        update={"status": FileStatus.STORED, "updated_at": utcnow(), "updated_by": ctx.user_id}
    )
    await self._write(ctx, stored, "updated")  # write
    return stored

async def _write(self, ctx: TenantContext, file: File, action: str) -> None:
    rows = (outbox_row(ctx, f"media.file.{action}", file.id, {}),)
    await self._storage.write_file(ctx.org_id, file, rows)  # the row and its outbox rows, one call
    await self._relay.relay_all(ctx.org_id, rows)
```

The row and the outbox rows that announce it land in one storage call.
Relaying them at once is optional, and never raises: the write has
committed, so a failure is left to the sweep.

A create receives the entity whole, its id from `new_id()`. The id
stays as constructed, and the manager's copy stamps the times, the
actor, and the fields that are its own to decide. A create
whose id is already written returns the row as stored, because the only
way to present an id twice is a retry.

An update starts from the stored row, and two sets stay as stored:
`PROVENANCE_FIELDS` (`created_at`, `created_by`, `deleted_at`,
`deleted_by`) and the entity's `MANAGER_OWNED_FIELDS`, such as a status
its transitions own. The caller's fields come in by `model_validate`.
The manager sets `updated_at` and `updated_by` on every update, and the
`deleted_*` pair on a soft delete, and returns what it wrote. A partial
update is the service impl's: it reads the entity, applies the set
fields, and hands over the whole.

Last writer wins by default. An entity whose concurrent edits matter
carries a `version`, and its update is a compare-and-set against the
version the caller names, the `If-Match` of a `PATCH` or an
`expected_version` field, never one re-read inside the update. A
mismatch is `PreconditionFailed`.

<!-- agents-only
- A create that issues a secret stores its digest and shows the secret
  once. Its rerun under the same idempotency key re-mints the secret on
  the row it finds, guarded by the record's `attempt_id`, and a replay
  returns the row with the secret absent (NET-25, NET-31).
- A `PATCH` on a versioned entity with neither `If-Match` nor
  `expected_version` is `ValidationFailed`.
-->

### Parameters

Parameters run from the broadest scope to the narrowest. A storage
method starts with `org_id`, then `user_id` where the scope is personal.
A manager method takes the context, which holds both, so `(ctx,
customer_id, order_id, line_id)` peels customer, order, line. Optional
filters follow as keywords with defaults.

### Cross-Manager Dependencies

A manager that needs another takes it in its impl's constructor, and
its interface does not change. It calls a peer for a fact its own
decision rests on. A step that joins two namespaces' operations,
reserving stock and then placing the order, belongs to the service
impl, because a split may put one behind a wire.

### Operations Without a Principal

A few operations run before any principal exists, or across every
tenant, and take the request stage, `RequestContext`. Some produce a
stage: signing up, signing in, claiming work, starting a sweep. A
webhook's lookup of the integration that owns its token takes it too,
and produces none. Some are bookkeeping, relaying the outbox or expiring
a lease, and read across tenants, getting each row's tenant back. And a
handoff of a row a tenant's write produced takes the tenant id alone. A
sweep that performs a tenant operation asks for one service context per
live tenant, whose user is `EMPTY_UUID`. Each is declared on its
interface, and a test names every one.

## The Storage Layer

The storage layer persists what the business layer gives it and returns
it on request. It does not orchestrate. It does not decide. It never
surprises.

### Storage Principles

`core`

- The code never relies on a relationship the database knows about: no
  assumed cascade, no join the schema happens to allow.
- No transaction outlives a storage call. An operation is one statement
  or one short unit whose commit the impl owns. An invariant two rows
  hold together, a queue claim or a core row and its outbox rows, is one
  named atomic method.
- Joins stay inside an impl. No trigger, no database function: what
  happens, happens in our code.
- Ids are passed top-down, from `new_id()`, and never read back.
- Defaults live in the object model.
- A new engine changes only `impl/`, and a swap is done when the
  contract suite passes, not when it compiles.
- Every read that returns a list is bounded in its statement, by a bound
  the caller picks.
- Tenancy is enforced on every read and checked on every write. The
  `org_id` predicate is the fence, and a case that presents another
  tenant's id is its evidence.
- Row-level security is a second fence, taken by default. It holds a
  query whose predicate went missing, and nothing assumes it is there.

### Namespace Shape

Storage has the namespace shape, under its namespace: an interface, the
impls under `impl/`, and the table classes under `tables/`, never
exposed
([`storage/`](scaffold/acme_root/om/src/acme/om/media/storage/)). Every
operation takes `org_id`, and a scope that is personal within a tenant
takes `user_id` too; both appear in every `WHERE`. The interface never
names its technology.

A write on a `core`-role entity takes the outbox rows that announce it,
landed in the same transaction, and a write that also starts work adds
a row of kind `work.<kind>`. Create and update are two methods: an
insert that reports an existing id without touching it, and an upsert.
A create with more than one unique key returns which collided, an
`InsertOutcome`. The outbox row carries its own `org_id`, because the
relay runs with no context, and the provenance of the write
([`row.py`](scaffold/acme_root/om/src/acme/om/outbox/types/row.py)):

``` python
class OutboxRow(Identifiable, Created):
    org_id: UUID    # the relay runs with no context
    kind: str       # "<namespace>.<entity>.<action>", or "work.<kind>"
    target_id: UUID
    payload: FrozenMapping = Field(default_factory=dict, validate_default=True)
    actor_id: UUID  # the write's principal; EMPTY_UUID for the platform
    request_id: UUID
    traceparent: str | None = None
    app: str
    done_at: datetime | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    failed_at: datetime | None = None
```

A row the relay cannot carry keeps its error and waits a growing delay,
so it stops nothing behind it, and past its last attempt it is failed, a
dead letter that an audit event names.

A few tables hold no tenant's rows. A global table's methods take no
tenant, an identity table's take `identity_id`, a cross-tenant sweep
returns `(org_id, entity)` pairs, and the lookups before an identity is
known name no one. The project names each of them in one list, which
`arch-check` holds the code to
([`pyproject.toml`](scaffold/acme_root/pyproject.toml)). A signature
only offers the tenant, so every storage method arrives with a case that
presents another tenant's identifier and finds nothing.

### Storage Root

One root, `StorageInterface`, has a getter per namespace storage,
`healthcheck()`, and `close()`. Two impls exist from day one,
`StoragePostgresImpl` and `StorageMemoryImpl`, and each builds every
namespace impl and wires their dependencies
([`root.py`](scaffold/acme_root/om/src/acme/om/storage/root.py)).

### Defining ORM Classes

Table classes mirror the OM mixins
([`base.py`](scaffold/acme_root/om/src/acme/om/storage/tables/base.py)).
`IdentifiableMixin` carries `id` and the storage-only `org_id`, indexed
on its own unless the table sets `__org_id_index__ = False` because
`org_id` already leads one of its compound indexes.
`GlobalIdentifiableMixin` carries `id` alone, for a global table, and a
table whose rows belong to an identity declares its identity column,
which the scope map names. `TrackableMixin` carries `created_at`,
`updated_at`, `created_by`, and `updated_by`, and `SoftDeletableMixin`
carries `deleted_at` and `deleted_by`, in the OM's order. Every datetime
column carries its time zone.

Tables carry `org_id`; tenant entities do not, save one whose reader
has no tenant, such as the outbox row. Row classes are mutable and never
leave the impl. Mixin columns come first, pinned by negative
`sort_order` bands, and a new column goes last. Indexes follow what the
SQL filters on. A unique key a tenant supplies leads with `org_id`, and
one on a `SoftDeletable` table is unique among the living.

### Translation

Three module-level helpers translate
([`translation.py`](scaffold/acme_root/om/src/acme/om/storage/utils/translation.py)):
`to_row` builds a row, `to_model` reads one, and `apply_row` updates one
in place. Value objects go into JSON columns, so a namespace with plain
shapes writes no translation code. A stored value object only gains
optional, defaulted fields, staged across a rollout
([Deployment](#deployment)), and a rename is a migration first.

### A Storage Impl

A Postgres impl extends `PgStorageBase`, which holds the two write
primitives: `_insert`, which reports an existing id or key, and
`_upsert`, which checks the tenant of the row it replaces and refuses
another's as `Conflict`
([`pg_base.py`](scaffold/acme_root/om/src/acme/om/storage/impl/pg_base.py)).
Outbox rows land only when the insert won. An id another tenant holds is
reported the same way, and the manager's read-back under its own tenant
finds nothing and refuses the create as `Conflict`.

Every statement passes one funnel, `_session_for(stmt, *, org_id,
user_id, identity_id)`. It routes the statement to its role's engine,
uses the system login only for the system scope, `EMPTY_UUID`, and sets
the scope on the transaction for [The Second Fence](#the-second-fence).
Each operation opens and commits its own short session, and every
statement carries a deadline from settings.

### Cross-Storage Dependencies

A storage impl that needs another takes it through its constructor: the
order storage reads a pick list's warehouses through
`InventoryStorageInterface`, under the same tenant, rather than joining
another namespace's table.

### Database Roles

`core`

Tenant metadata is small and read on every request. Event streams are
append-only. A work queue is hot and tiny. One schema would give them
one pool and one backup, and a scan would compete with a claim.

A **database role** is a schema with its own connection URL and its own
migration chain; our word, not a Postgres login role. Every table has
exactly one, named in one map
([`roles.py`](scaffold/acme_root/om/src/acme/om/storage/roles.py)):

| Role       | Holds                                                  |
|------------|--------------------------------------------------------|
| `core`     | the system of record: tenancy, catalog, orders, config |
| `activity` | append-only streams: events, audit, ledgers            |
| `queue`    | the work queue and the channels that wake workers      |
| `admin`    | the operator plane's own state, global rows            |

One database holds every role by default. When metrics demand it, a role
moves to its own, and the cut-over is one URL. Each role's pool declares
its size and its wait bound, and the connection budget counts every
process that can run at once.

Nothing crosses a role, so a handoff after a core write is never a
second write a manager remembers. The manager writes the core row and
its outbox rows in one atomic method, and the relay carries each row to
where its `kind` says, at once or from the sweep, and marks it done. An
entity change becomes an `Event` in `activity` and an `ENTITY_CHANGED`
publish; a `work.<kind>` row becomes a work item in `queue` and a
`WORK_AVAILABLE` wake-up. The relay is idempotent on the row's key. This
is the transactional outbox
([`relay.py`](scaffold/acme_root/om/src/acme/om/outbox/impl/relay.py)):

``` mermaid
flowchart LR
    Mgr[Manager write] -->|one transaction| Core[(core: row + outbox rows)]
    Core --> Relay[Outbox relay: now, or the sweep]
    Relay -->|append, seq| Act[(activity: Event)]
    Relay -->|publish| Bus[[Topic bus]]
    Relay -->|enqueue| Q[(queue: work item)]
    Bus --> Sock[Sockets on every replica]
    Q --> Wrk[Worker claims]
    Sock -.->|reconnect: after_seq| Act
```

An entity change is done when its event is appended and the bus took the
publish, so a dropped push is relayed again. A work row is done once its
item is queued: the wake-up is a hint, and workers also poll. Relaying
from the sweep alone, every second or two, is the cheaper first step;
relaying at once buys a push in milliseconds for three round trips.

Analytics across tenants reads a mirror, never a role the application
writes. Every database is backed up and its restore rehearsed, and a
role restored behind its siblings is reconciled from the outbox, whose
done rows outlive the backup window. Purge, after retention, is the one
hard delete, save one. A person who deletes their account is gone at
once: one atomic write deletes their identity and every user,
membership, and credential it holds, outside the sweep, since a soft
delete would keep the very fields they asked to lose. What they made in
a team org stays the org's, under their id. Payloads carry ids, never a
personal value, so erasure redacts audit entries and nothing else.

<!-- agents-only
When `activity` or `queue` comes back to an earlier point than `core`,
the restore runbook's last step clears `done_at` on every outbox row
created after the restored role's point, and the sweep relays them
again, harmlessly, since the relay is idempotent. When `core` comes back
earlier, nothing is replayed: a work item whose record is gone fails as
not found (STO-31).
-->

> **Principle:** Every table has one role. The role is its schema, its
> pool, and its migration chain. Nothing crosses a role.

### The Second Fence

> **Principle:** Every table declares its tenancy scope, and the
> database carries the policy that scope implies. The predicate in the
> query is still the fence the business layer relies on. The policy is
> what catches the predicate that went missing.

A table's **tenancy scope** says whose rows it holds, declared once per
table beside the role map
([`scopes.py`](scaffold/acme_root/om/src/acme/om/storage/scopes.py)):
`system` (no policy), `org`, `identity`, or `both`, which narrows the
`org` policy by a person column, or by an identity column, when the
transaction names one. The map names the column each policy rests on.
The funnel sets the call's scope as transaction-local settings, so a
pooled connection hands nothing on
([`pg_base.py`](scaffold/acme_root/om/src/acme/om/storage/impl/pg_base.py)),
and each table carries the policy on them, forced on its owner too
([`the_core_role.up.sql`](scaffold/acme_root/om/migrations/sql/core/202609280000_the_core_role.up.sql)):

``` sql
SELECT set_config('app.org_id', :org_id, true);

ALTER TABLE core.orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.orgs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.orgs
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    )
    WITH CHECK (...);  -- the same
```

A transaction that names no tenant fails closed. The system scope,
`EMPTY_UUID`, is the one bypass, spelled out so it can be found, and
only the system login passes it.

Three logins reach the database, none a superuser and none with
`BYPASSRLS`. The migration login owns the schema and runs only in the
deploy's migration task. The runtime login serves requests, owns
nothing, and holds only `SELECT`, `INSERT`, `UPDATE`, and `DELETE`, so
nothing it runs can drop a policy. The system login runs the listed
system-scope methods. Tests assert each fact on a live connection, and
read `pg_class` and `pg_policies` to hold the policies to the scope map.

<!-- agents-only
The methods that take no tenant are listed by name in the project's
`pyproject.toml`, under `[tool.arch-check.options.CTX-12]`, and no
others take none ([`pyproject.toml`](scaffold/acme_root/pyproject.toml)).
Among them:

- the sweeps' claims and purges across tenants, such as `claim_next`,
  `purge_items`, `purge_done`, and `purge_records`;
- the five lookups before an identity is known:
  `read_identity_by_email_digest`, `read_identity_by_issuer_subject`,
  `read_api_key_by_digest`, `read_session_by_digest`, and
  `redeem_socket_ticket`;
- the reads and writes of the global tables, the operator's size read
  among them.

The operator plane's idempotency records take `org_id` like any other,
keyed under `EMPTY_UUID` with the operator's identity id as the user. A
new cross-tenant sweep joins the list by name. A table whose
system-scope statement plans badly may carry one policy per login, kept
only where a measurement in its migration shows it (STO-28).
-->

### Migrations

`core`

Migrations live with the OM, which owns the schema timeline: a pair of
hand-written SQL files per change, `YYYYMMDDHHMM_<slug>.up.sql` and
`.down.sql`, under their role, with a thin wrapper Alembic runs
([`om/migrations/`](scaffold/acme_root/om/migrations/)). Each role has
one chain, and the minute stamp is the revision id. An applied file is
never edited.

A migration is compatible with the release before it, because a rollout
runs both ([Deployment](#deployment)): add and backfill, switch the
code, drop in a later release. The release before a drop no longer maps
the column at all, since the ORM writes every mapped column.

A backfill runs under the fence, which binds the owner, so a data
migration lifts `FORCE` for its own statements and restores it in the
same transaction. It counts the rows it means to touch and fails on a
difference, and a test with two tenants' rows catches a missing lift.
`make migrate-check` holds the ORM to a migrated database.

## Infrastructure

Managers need more than rows. A cache skips an expensive read, a bucket
parks a blob, a topic wakes other processes, and a secret store
resolves a credential. These are capabilities, not a layer: a manager
takes one the way it takes a storage, in its constructor.

### Infrastructure Principles

`core`

- Every capability is an interface with swappable impls, and a caller
  does not know which it holds.
- The OM imports infra interfaces. Infra imports nothing from the OM.
- Tenancy is a keying concern. Cache, buckets, and secrets take
  `org_id` first, and topic payloads carry it.
- Cross-tenant reference data uses `EMPTY_UUID` as its `org_id`, which
  infra knows by value, without importing the OM.
- A handle arrives through a constructor, wired by the app container at
  boot, never through a global or a context.
- Observability is used through its vendor API. Anything else a vendor
  provides sits behind an interface with a twin.
- Every impl can `describe()` itself, and the container logs its
  choices once at start.

### InfraInterface Root

One root, under `acme.infra`, has a getter per capability, such as
`get_cache(scope)`, and `start()` and `close()` for the capabilities
that hold connections
([`root.py`](scaffold/acme_root/infra/src/acme/infra/root.py)). The app
container picks each impl from settings, so environments differ without
a manager changing.

### Cache

A cache is scoped by a `CacheScope` so consumers do not collide, and a
manager receives its `CacheInterface` already scoped
([`cache/`](scaffold/acme_root/infra/src/acme/infra/cache/)). Every call
takes `org_id`. `increment` is the one atomic primitive, for rate limits
and generations.

A cached read sits below authorization: the manager caches the tenant's
data and applies the caller's visibility on every call. A read cache is
a projection with a generation: a write bumps the tenant's generation,
and older keys expire, with the TTL as a backstop and the bound on
staleness. A cache fails open, an unreachable backend is a miss, and
nothing that must be correct lives only in a cache. Caching is a
manager's decision, never a storage impl's.

A degraded answer is declared where it is chosen. There are three: this
cache, the rate limit ([The Gateway](#the-gateway)), and the channel
that degrades to polling ([Push-First Apps](#push-first-apps)). A fourth
is named the same way or it does not exist.

### Buckets

Buckets hold large blobs, with an S3-like shape: put, get, exists, a
bounded list in key order that pages after the last key, delete, and
presigns
([`buckets/`](scaffold/acme_root/infra/src/acme/infra/buckets/)). Every
call takes `org_id`, which prefixes the key. A short-lived presigned URL
moves bytes between a browser and the store, so no service holds a large
upload. An upload is `presign_post`, whose signed policy carries the
content type and `max_bytes`, so a form cannot fill the bucket.

<!-- agents-only
A presign returns `None` when its backend cannot sign, and the caller
moves the bytes through `put` and `get` under the same bounds. The
local filesystem impl holds an upload to the same content type and
`max_bytes`, and refuses a key that is absolute or climbs out of its
root with `..`.
-->

### Topics

Topics carry wake-ups and live updates. Names are an enum, each payload
type is fixed by a map, and every payload extends `TopicPayload`, with a
producer-set `idempotency_key`, `produced_at`, `org_id`, and a
`truncated` flag
([`topics/`](scaffold/acme_root/infra/src/acme/infra/topics/)). That key
is the observable id through the whole pipeline, and the producer and
every consumer log it.

A topic is best effort, at most once, and that is what makes it cheap:
`LISTEN/NOTIFY`, a pub/sub channel, or an in-process dispatcher all
satisfy it. So durable work and any effect a handler must bring about
ride an outbox row or a work item, and a topic carries the hint. A
missed publish costs polling latency, never lost work. `publish()`
answers whether the bus took it, and no broker id.

### Queues

A queue takes work from outside producers that cannot be told to wait:
inbound webhooks, partner deliveries, bulk uploads
([`queues/`](scaffold/acme_root/infra/src/acme/infra/queues/)). Internal
work is [The Work Queue](#the-work-queue), not a queue. An inbound
webhook is authenticated by the provider's signature over its body and a
timestamp, never by its URL, and the route keys it with a UUID v5 over
the provider and its delivery id, so a retried delivery carries the same
key. A queue is at-least-once with no deduplication knob, and dead
letters are visible: an audit entry names each, and a metric counts it.

<!-- agents-only
Queue names are a `Queues` enum. `send()` returns `None`, as `publish()`
returns no broker id: the observable id is the key the body carries. A
received message's `receipt` is the handle of one delivery, which
`delete` and `change_visibility` take, and never an id. `depth()`
reports the visible, the in-flight, and the dead-lettered counts.
-->

### Secrets

A secret store holds values, and the object model holds only references.
A secret belongs to a tenant: every call takes `org_id`, under the
tenant's own prefix
([`secrets/`](scaffold/acme_root/infra/src/acme/infra/secrets/)). A
carrier integration carries `credential_ref`, the secret's name, which
its manager sets and lists in `MANAGER_OWNED_FIELDS`, so no caller
writes it. The value is resolved for one operation and never enters an
entity, a log, an audit payload, an error, or a subprocess's
environment. The process's own credentials, injected at start, are
another kind.

### Idempotency

Every topic payload, queued message, and work item carries a
producer-set `idempotency_key` by construction, so every handler has
something to dedupe on ([Idempotency on the Consumer
Side](#idempotency-on-the-consumer-side)).

## The Network Layer

### How It Starts and Where It Goes

A system starts as one API process: behind one gateway, a router module
and a wire-types module per namespace, and the realtime channel, beside
a few workers. The boundary that matters at first is between
interactive traffic and background work, and one process is the
cheapest to deploy and debug. A namespace is a module boundary from day
one, so growth is mechanical: splitting one out moves its router and
types into another container, and no manager changes.

### Web Services as Scalability Units

`optional`

When a namespace's load differs from the rest, it gets its own service:
`catalog` gets `catalog-api`, at first the same image mounting only its
routers, and an image of its own only once its code diverges. A split
lets each service scale and be exposed on its own. It splits processes,
not data: every service runs the same OM on the same roles, so the data
tier splits by role, never by service. A service still calls other
namespaces through their in-process service impls, and a wire hop
between processes that share the OM is a recorded decision.

### Domain Services vs App-Specific Services

A **domain service** wraps one namespace for any caller: `orders-api`.
An **app-specific service** serves one client app, `portal-web-svc`,
composing domain services into the shape that app needs. It is thin, an
app talks only to its own, and in one process it is a router module and
its service impl.

### At a Glance

The target shape, served from
[`services/api/`](scaffold/acme_root/services/api/) until a split:

``` mermaid
flowchart TD
    User[Product users] --> App[Apps: portal, CLI, operator console]
    API[External API users] --> GW
    App --> GW[Gateway: auth and the context]
    GW --> AppSvc[App-specific services]
    GW --> DomSvc
    AppSvc -->|composes| DomSvc
    subgraph Container [Service container]
      DomSvc[Domain services] --> Core[OM and storage library]
    end
```

### Stateless vs Stateful Services

`core`

A service scales out only while it is ephemeral: a process's memory
must rebuild from durable sources. Domain services are always
stateless. An app-specific service that holds a WebSocket is lightly
stateful, narrowly: the connection, the `TenantContext` its ticket produced,
the subscriptions, and a bounded send buffer.

> **Principle:** Domain services are always stateless. App-specific
> services hold only the open socket, the context that opened it, its
> subscriptions, and a bounded buffer. Never session data, never
> accumulated business state.

### Service Interfaces and Impls

Each service declares its operations on a `*ServiceInterface`, and a
`ServicesInterface` root holds a getter per service
([`services/`](scaffold/acme_root/services/api/src/acme/services/api/services/)).
A router declares the route and the dependencies that mint the context
and the idempotency key, then calls one service operation. The service
impl translates: arguments in, one manager call, a view out. Routers
call through the interface from day one, so a split is a wiring change,
and a decision a router starts making moves into a manager.

### The Gateway

`core`

The gateway is the only layer that talks to the public internet. It
mints the request stage, runs the transitions that authenticate it, and
routes. No service builds a context from a raw header. In one process it
is a package inside the API
([`gateway/`](scaffold/acme_root/services/api/src/acme/services/api/gateway/)),
and at the second service it becomes a distribution every service
imports. It owns the edge concerns, each done once.

**Credentials.** Each credential kind has its own prefix, which decides
who accepts it. A person signs in with a credential that names no
tenant, then exchanges it for a tenant session. An agent presents a
membership-scoped, expiring API key. A socket opens with a single-use
ticket, never a long-lived credential in a URL. A request id is
accepted only as a UUID, or minted.

**Error envelope.** One handler turns `PlatformException` and
`InfraException` into `{"error": {"code", "message", "request_id"}}`,
and a catch-all makes anything else a 500 of that shape.

**Rate limits.** A per-route dependency counts in the shared cache,
keyed on the credential, so every replica spends one budget. A refusal
is `429` with `Retry-After`. The limits fail open: fairness, not a
security boundary.

**Admission.** A process bounds the requests it has in flight, one
budget for reads and one for writes, and refuses past either at once.
That is self-defense, and it fails closed.

**Edge idempotency.** A creating `POST` accepts an `Idempotency-Key`,
and an `IdempotencyRecord`, the marker, owns the retry
([`record.py`](scaffold/acme_root/om/src/acme/om/idempotency/types/record.py)).
`begin` writes it pending, per tenant and principal, with the request's
digest, the id the create will use, and an `attempt_id`; `finish` stores
the outcome, which a retry replays, saying so in a header. Another
digest under the key is refused. A `4xx` is stored, while a `5xx` or a
`429` releases the marker, and the retry reruns on the same id. A stale
attempt's lease lets a retry take over, and `finish` conditions on the
`attempt_id`.

**Health.** `/healthz` answers liveness with no I/O. `/readyz` checks
storage under a deadline shorter than its poller's. `/metrics` is for
the collector.

**Versioning.** The prefix, `/v1`, is applied once, where routers are
mounted.

The operator plane has its own gate, for the routes under `/v1/admin/*`
that the same process serves. It admits the person's own sign-in with a
second factor, a TOTP code, or an operator token, and never an API key
or a tenant session; then `admit_operator` checks the allowlist. A
tenant's sign-in needs no second factor, but the operator plane reads
across tenants, so a password alone never admits to it. An agent never
signs in with a password. It presents an operator token: minted by an
operator with a second factor or by the grant job, naming one identity
and one permission its entry grants (`write` implies `read`), expiring
within the hour, and stored as its digest, found by
`read_session_by_digest`. An operator with no confirmed second factor
yet is admitted with `OperatorPermission.ENROL` alone, which reaches the
two enrolment calls and nothing else.

<!-- agents-only
The TOTP secret is stored encrypted, under a key from the secret store,
and enrolled once its first code confirms it. A code already used is
refused, even inside its time step.
-->

### Auth: the Gateway Verifies, the Tenancy Domain Owns

The gateway verifies and owns nothing. The tenancy namespace owns the
identity model, from organizations to sessions, and issues the tokens
([`tenancy/`](scaffold/acme_root/om/src/acme/om/tenancy/)). Sign-up,
invitations, and roles are business logic in the OM.

Sign-up opens a deployed environment: the identity, its first org, and
the owner membership, in one transaction. It is open by default, and a
setting closes it. There is no email verification, a choice with two
named costs: sign-up tells whether an address is taken, and anyone can
claim an address they do not own. With no verified mailbox there is no
recovery by mail either: an operator resets a lost password through the
operator plane, as an operator write audited with the identity it
reset. Verification is the first thing to add when a product mails the
address or trusts it.

An external identity provider is one more credential kind: the gateway
verifies its token, and the tenancy manager finds or creates the
identity by issuer and subject and produces the same `IdentityContext`
a sign-in does. Nothing stored can be presented as a credential: a
password is a memory-hard hash, and a key, a session, or a ticket a
digest, shown once in the `Issued...View` that minted it. Failed
sign-ins are throttled in storage, per email digest.

### Intra-Service Communication

Services run in one private network, and only the gateway has a public
address. Calls between tasks use TLS wherever the runtime offers it. A
service-to-service call carries a short-lived internal credential,
minted by the caller, naming the principal, the tenant, the request, and
its audience. The callee rebuilds `TenantContext` from it and trusts no bare
header.

One key is one trust domain: any process holding it can speak for
anyone. That is a decision, made for a platform whose processes are all
its own and ship together. A process trusted less gets a key of its
own, which attributes, and a declaration of what it may assert, which
contains. Outbound TLS trusts the operating system's store.

<!-- agents-only
The trust store is injected by a small entry module before the process
is imported, never as an import side effect: injecting it replaces
`ssl.SSLContext`, and a library that bound the old class first
(urllib3, and the cloud SDK through it) recurses until the process
dies. Twins open no cloud client, so a test per process replays the
start in a fresh interpreter and opens a cloud client's TLS context
([`trust.py`](scaffold/acme_root/infra/src/acme/infra/trust.py)).
-->

### Public Types

`core`

What a service exposes is a curated projection of the OM, and the OM
never changes to match it. Wire types are hand-written, one module per
namespace, on two bases: `View`, frozen, and `RequestBody`, which
forbids unknown fields
([`types/`](scaffold/acme_root/services/api/src/acme/services/api/types/)).
`...View` is returned, `...Request` accepted, and `Issued...View` is the
one response with a fresh secret. A request carries no id and no
timestamps; the server assigns them. A list is bounded by a
server-clamped `limit`, or paged by `next_cursor` or, for a stream,
`after_seq`, and never by an offset.

Inside `/v1` a view only gains fields and a request only gains optional
ones; a removal is a new prefix. A reader ignores a field it does not
know, so a topic payload or an envelope only gains optional, defaulted
fields. A stored payload and a new request field are staged across a
rollout ([Deployment](#deployment)): the service accepts a field one
release before an app sends it.

### From OM to Wire

`Warehouse` lives in the OM, `WarehouseView` decides the wire, the
service impl translates, and the router binds the route. The OpenAPI
document is emitted by the app and committed, and CI fails on a diff, so
an API change shows in the document every client builds against.

### Clients Live in One Place

> **Principle:** One client per language per service. Every consumer
> imports it. Nobody builds their own.

The clients live under `clients/`, one folder per language. The
TypeScript client generates its types from the document into one file,
re-exports them through a facade, and sends through one transport client
([`clients/typescript/`](scaffold/acme_root/clients/typescript/)).
Every browser app imports it. A Python consumer imports one typed client
([`clients/python/`](scaffold/acme_root/clients/python/)). Every
outbound call carries a timeout from settings, a request a deadline, and
a work handler its lease. Nothing runs unbounded.

### Direction of Calls

`core`

Calls flow downward. A **domain service impl** calls other domain
services and its own managers, never an app-specific service. An
**app-specific service impl** calls domain services, never a sibling. A
**manager** calls managers and storages; `services.*` exists only in the
network layer. A **storage impl** calls other storages.

So orchestration across services lives in the service impl, which
composes and never decides. Placing an order reserves stock through
`InventoryServiceInterface`, then hands the reservation to the order
manager as a plain argument. The id the gateway minted travels into the
reservation as its key, so a retry finds what it made, and the key is in
the service interface's signature before any split, not after.

A retry that arrives is owned at the edge. A retry we send is
classified, only a failure that can differ, bounded, with a growing,
jittered delay, and retries never stack. A chain that must survive a
crash is a durable record advanced by a worker, with the irreversible
step last and compensation for each before it: a saga.

> **Principle:** Calls flow downward: services to services and
> managers; managers to managers and storage; storage to storage.
> Nothing reaches up.

### Idempotency on the Consumer Side

`core`

> **Principle:** Every queue is at-least-once. Every handler must be
> idempotent, keyed on a producer-generated idempotency key.

A client retries after a disconnect, a handler crashes between its work
and its mark, an operator replays a backlog. So every message carries a
key its producer set, a `uuid_v7` or a UUID v5 over an outside
delivery, and every handler dedupes on it before working, through a
unique index or an upsert. The key lives on the row the effect produces,
or marker and effect are one atomic write. A marker marked done before
its effect turns a crash into lost work.

### Realtime at the Edge

`core`

Pushes travel on the topic bus. Every process that holds sockets
subscribes, a producer publishes once, and each socket filters by tenant
and stream, so no process needs to know which replica holds whom
([`realtime/`](scaffold/acme_root/services/api/src/acme/services/api/realtime/)).

Every push is also a record: an `Event` in `activity`, with `org_id`,
`seq`, `kind`, `target_id`, `actor_id`, the request, and the app, and a
payload of ids
([`event.py`](scaffold/acme_root/om/src/acme/om/events/types/event.py)).
One atomic method appends it and takes `seq` from a cursor row per
tenant, so the sequence is gapless and a client reads a gap as a loss.
`seq` orders events, not writes. An audit entry is an event with an
audit kind, so one stream records both.

The stream is a stream of hints: a frame names the change, and its
`version` when the entity has one, and nothing else of it, so a client
reads the entity through the authorized read. By default there is one
stream per tenant, travelling whole. A socket opens with no
subscription: the client subscribes to the topics it reads with a
`subscribe` frame, as the portal does on every open, and the socket
confirms each. A product where a record's existence is itself restricted
keeps a stream per visibility scope, which a client subscribes to by
name.

Replay from storage is the durability. A socket's send buffer is
bounded, and a burst drops the oldest hint, never a control frame: the
hello frame, a pong, a subscription confirmed or ended, an error. The
hello frame and every pong carry the tenant's head `seq`, so a quiet
socket cannot hide a loss, and a reconnect asks for everything after
the last contiguous `seq`. A socket takes only subscribe, unsubscribe,
and ping; commands go over REST.

Replay reaches back as far as the stream is kept. The stream's
retention is a setting of the worker that trims it, longer than the
backups and the outbox keep theirs, so a restore never needs a trimmed
event. The trim deletes the oldest run of events past the retention
and, in the same transaction, moves the tenant's floor to the last
`seq` it deleted. The stream is gapless above the floor. A read below
the floor is `410 stream_truncated`, naming the floor and the head.
Asking again never succeeds, so the client reads afresh what it shows
and goes on from the head.

<!-- agents-only
- A dropped stream frame is logged. The control lane has a small bound
  of its own, and its overflow is logged as such, since it means the
  socket produces state faster than it can be written.
- A change that ends a socket's evidence rides the stream like any
  other: `tenancy.session.revoked` and `tenancy.api_key.deleted` name the
  credential, `tenancy.user.deleted` the user,
  `tenancy.membership.updated` the membership, and `tenancy.org.deleted`
  the org, and each closes every socket it names
  ([`realtime.py`](scaffold/acme_root/services/api/src/acme/services/api/services/impl/realtime.py)).
-->

### Wait-for-Response vs Fire-and-Forget

Waiting is the client's choice, per operation, and the server answers
the same way. A short CLI command waits. Long work answers `202` with an
id at once: the CLI may follow it to an exit code, and the portal
returns to its loop and trusts a push.

### Long-Running Orchestrations

Work that takes minutes or hours is a durable record, with a status and
a cursor, advanced by stateless workers. A worker claims it, does a
step, writes the cursor, and hands off; if it dies, another picks up
where the row says
([`orchestrations/`](scaffold/acme_root/om/src/acme/om/orchestrations/)).
A record succeeds, fails, or **parks**: stops with a reason and, where
known, a time to resume, keeping everything achieved. The event that
clears the reason, a sweep, or a person wakes it.

> **Principle:** A guard parks, a bound fails. A safety check leaves
> the work resumable; only a real limit terminates it.

## Worker Roles

Some work runs on its own schedule, and some drains a queue with nobody
waiting. A worker role is a process that claims a unit of work, does
it, writes the result, and notifies whoever should know. Workers sit
next to web services, never inside them.

### Workers, Not Web-Service Side Jobs

`core`

> **Principle:** Web services do not spawn background jobs or schedule
> recurring tasks. Every such need is an explicit worker role.

A service that fires off a job is no longer stateless: the job outlives
the request, and only the process remembers it. A worker role has its
own container and deployment, so its work is observable, restartable,
and scaled on its own. A subscriber that only forwards to its own
process's sockets is part of the network tier; anything that writes,
retries, or outlives a connection is a worker.

### The Work Queue

`core`

Durable work is a row in the `queue` role
([`work_item.py`](scaffold/acme_root/om/src/acme/om/work/types/work_item.py)):

``` python
class WorkItem(Identifiable, Trackable):
    kind: WorkKind             # its payload's shape is in WORK_PAYLOADS
    target_id: UUID            # the record it advances
    idempotency_key: UUID      # unique per tenant
    request_id: UUID           # the request that caused it
    traceparent: str | None = None
    payload: FrozenMapping = Field(default_factory=dict, validate_default=True)
    lane: str = "default"
    status: WorkStatus = WorkStatus.QUEUED
    available_at: datetime     # not before
    claimed_by: str | None = None       # for an operator; never a fence
    claim_token: UUID | None = None     # the fence
    lease_expires_at: datetime | None = None
    attempts: int = 0
    max_attempts: int = 3
    last_error: str | None = None
```

Enqueue is a create that never resets a claim: a key already held
reports `KEY_EXISTS`, and the enqueue reads that row back by its key. An
enqueue that won publishes `WORK_AVAILABLE`, a wake-up, and workers also
poll, so a dropped wake costs one interval. Work that follows a core
write rides the write's `work.<kind>` outbox row, enqueued by the relay
under the row's id.

The claim takes the oldest available row in a lane, skips rows others
locked, and stamps a claim token and a lease in one statement.
Completion marks it done, requeues it with a growing delay, or fails it
as a dead letter when attempts run out.

Every write after the enqueue is the platform's, signed `EMPTY_UUID`.
`created_by` is the person who asked, so the claim rebuilds their
principal under the role reserved for services, and the run names the
item's request as its `caused_by_request_id`. When the enqueuer holds no
live membership, or is the platform, the claim builds the tenant's
service context instead. The person authorized the work once, at
enqueue, so the permission that enqueues a kind covers every call its
handler makes. The tenant arrives beside each item, and an item whose
tenant is gone is failed.

### Shape of a Worker

A worker is a small loop
([`loop.py`](scaffold/acme_root/workers/maintenance/src/acme/workers/maintenance/loop.py)):
claim when a slot is free, do the work through managers and services,
write the result through a manager, whose outbox rows notify, and
complete the item. A handler implements `WorkHandlerInterface`, one impl
per kind, whose `handle(ctx, item)` takes the run's `TenantContext`, and it
is idempotent on the item's key.

A worker runs items up to a capacity, and two fences hold one completion
per item. Each item renews its lease on a timer, and a renewal refused
with `Conflict` cancels the task at once. Every write to the queue row
conditions on the claim token. Neither fence covers the record, which
lives in another role, so the handler is idempotent and a record whose
edits matter carries a `version`. A worker beats its liveness in memory
for `/healthz`, and publishes it as a key with a TTL under the system
scope and `WORKER_LIVENESS`, best effort: a publish that fails is logged
and counted and never pauses claiming.

### Shutdown

On a stop signal a worker stops claiming, cancels its tasks, and
releases their items; then the heartbeat stops, and then it goes
offline. It is alive until its work is back in the queue. A rollout
stops an old worker before it starts a new one.

### Maintenance Without a Scheduler

Housekeeping is a sweep every worker runs on its own timer: requeue
expired leases, resume parked records, open the next period of a record
kept per period, relay what a crash left, and purge what is past
retention. It is idempotent and serialized by the database, so it needs
no leader, lock, or scheduler. Resumes are staggered.

### Implementation Options

`default`

Worker roles run as always-on containers, a web service's shape without
the public surface. A worker that needs more compute gets a bigger box.

## Apps

### Apps as Products

Apps are products that consume the system: the portal, the CLI, the
operator console. Each sits at the outermost layer and reaches the
platform through the gateway and its own backing service.

### Apps Are Dumb

`core`

> **Principle:** Apps are intentionally dumb. Only UI, input, and
> browser- or terminal-specific behavior live in the app. Business
> logic and orchestration belong on the server.

Logic that means something to one app moves to that app's service;
logic for more than one moves to a domain service or the OM. The app
stays thin and cheap to replace.

### Push-First Apps

> **Principle:** Polling is a workaround for the absence of push. The
> moment any single corner of an app wants a push, the app earns a
> realtime channel.

A realtime channel is one WebSocket, opened at startup and read for the
session. Every push rides it as a typed envelope, routed by its type.
Another envelope costs nothing; another transport is a new operational
surface. The channel degrades and does not disappear: the client
reconnects with backoff, then shows a banner and polls slowly until the
socket is back. Its ping interval and the load balancer's idle timeout
are pinned in one shared file both sides test.

> **Principle:** One realtime channel per app, not per feature. Every
> push rides the same connection as a typed envelope.

## Client App Architecture

### Stack

`default`

Browser apps are React and TypeScript on Vite, built to a static bundle
([`apps/portal/`](scaffold/acme_root/apps/portal/)). The CLI is Python.
Vite builds nothing but the bundle, so there is nowhere in the app to
put backend logic.

> **Principle:** One React + TypeScript stack for every browser app.
> The CLI stays Python.

### Client Rendering

Rendering happens in the client. The bundle talks to its backing
services, the realtime channel, a presigned URL it was handed, and the
error tracker, and nothing else.

### State and Data

`default`

Server state lives in TanStack Query, with one key factory per domain;
client state, what only the UI knows, in Zustand. A push writes into the
query cache: a hint reads the one entity it names and places it, a read
that finds nothing removes it, and a collection is read again only where
placement cannot decide. A late answer never overwrites a newer version.

> **Principle:** Zustand for client state, TanStack Query for server
> state. Realtime writes into the query cache.

### Views, View-Models, Models

`style`

A screen has a **Model**, a pure module of builders, codecs, and
predicates tested without React; a **View-Model**, a hook combining
queries, stores, and the model; and a **View**, a component that renders
what the hook gives it.

### API Access

Feature code never calls `fetch`. The transport client attaches the
bearer, parses the envelope into a typed error with the request id, and
clears the session on a 401. The bearer lives in memory and session
storage, never local storage. The distribution sends a
`Content-Security-Policy` that names only the origins the app uses.

### One Tenant at a Time

A person may hold several memberships, and the portal works in one at a
time. It signs in once, picks a membership, and exchanges it for one
tenant session. The org chip switches it with a second exchange, which
ends the old session, and the app drops every cache of the old tenant
and mounts afresh.

> **Principle:** The app works in one tenant at a time. A switch is a
> second exchange that drops the old tenant's caches. The app never
> holds two sessions.

### Realtime: One Channel per App

One provider component owns the portal's socket
([`realtime/`](scaffold/acme_root/apps/portal/src/realtime/)). Envelopes
are parsed by their `type` and routed into the query cache or the store,
never into components. A hidden app may pause the socket and resume with
a fresh ticket and a replay.

### The Operator Console

The operator console is a separate app. It shares the portal's stack,
design, and client, and never its security context: its own origin and
routes under `/v1/admin/*`, no tenant, and authority from the operator
allowlist alone.

### The CLI Is Different

The CLI is a Python app whose UI is the terminal
([`apps/cli/`](scaffold/acme_root/apps/cli/)). It talks REST with an API
key scoped to one membership, so it never holds two credentials, sends
an idempotency key with every create, and turns a followed operation
into an exit code.

## Deployment

`core`

Every process ships from one commit. A deploy builds every image and
bundle of that commit and rolls them all out, so no two services run
versions that were not built and tested together. A rollout still takes
a window: the runtime replaces tasks in steps, and for that window the
old release and the new one serve side by side. So whatever two releases
share changes expand-then-contract: a table, a stored payload, a topic
payload, a wire type. One release adds the new shape and tolerates its
absence; a later one, after every reader has moved, removes the old.
[Migrations](#migrations), [Translation](#translation), and [Public
Types](#public-types) apply this to the schema, stored shapes, and the
wire. No other rollout order is assumed.

### Cloud: AWS

Every environment has a cloud account of its own, and the account is
the boundary: no credential or root reaches two. Environment roots share
one module graph and differ by variables. The one crossing is named:
staging's replication writes images and bundles into production's
account ([`terraform/`](scaffold/acme_root/deployment/terraform/)).

Staging is `main`: every merge deploys it. Production is `release`,
moved only by the release workflow, which a person dispatches, as a
fast-forward to the last commit staging deployed, and it applies after a
person approves the plan. Production never rebuilds: it promotes
staging's images by digest and bundles by commit, copied into its own
account and checked against a record kept outside staging's. A revert is
the rollback, and a revert never removes an applied migration. Browser
apps ship from a private bucket through a CDN, built once and configured
at start by a `config.json`.

<!-- agents-only
- A rollout that fails is rolled back by the runtime's circuit breaker.
  The apply waits for steady state, so it fails too, and the next apply
  writes the running shape again. The fast rollback swaps images and
  bundle back to the previous release only, running no migration and no
  plan (DEL-50).
- The approver reads the plan's text rendering, which masks sensitive
  values. The `release` ruleset forbids a push, a force push, and a
  deletion, with the repository host's app as its one bypass actor.
- The distribution answers every path that is not a file with
  `index.html`, caches hashed assets as immutable, and never caches
  `index.html` or `config.json`. Every response carries the security
  policy, `Strict-Transport-Security`, `frame-ancestors`, and
  `X-Content-Type-Options: nosniff`. The platform sets no cookie on a
  parent domain. The deployed edge serves no interactive API docs.
- Each registry keeps a bounded number of images and never expires one
  production runs: the production deploy adds a tag under a prefix the
  retention spares, and never moves a tag. Bundles expire after a
  retention that outlasts the last few releases.
-->

> **Principle:** One cloud account per environment, and nothing spans
> two but the replication into production. Production promotes the
> copy of what staging ran. A revert is the rollback.

### Infrastructure as Code

Every cloud resource is declared in Terraform, in the monorepo, so an
environment change is a pull request. Each environment has a **bootstrap
root**, which the administrator applies, holding what the pipeline needs
first, and an **environment root**, which the pipeline applies on every
deploy. One file,
[`environments.json`](scaffold/acme_root/deployment/cloud/environments.json),
names every account, and every provider pins its own. The deployer is
fenced by a permissions boundary it cannot change, and no secret value
lands in state or in a plan.

<!-- agents-only
- A change to what a bootstrap root holds, a new process's image
  repository among them, is a pull request and then a create run in
  each account; the service and worker scaffolds say so.
- The deployer is refused a role or a user created without the
  boundary, and refused an access key outright.
- A generated password reaches the database and the secret store
  through write-only attributes. Its rotation bumps the write-only
  version into the task definition, so services roll and pools are
  replaced. With a password the database service manages, a process
  builds its URL from the host and the secret at start, and a pool
  reconnects on an authentication failure by reading the secret again.
-->

### Migrating a Deployed Database

The deploy migrates, as a one-off task on the new image before the
rollout, inside the apply, and a failure leaves the old tasks serving.
The first operator is granted by the same kind of task, from a pipeline
job a person dispatches, so production's grant waits behind the same
approval as its apply.

<!-- agents-only
The grant job also mints the operator tokens of the provisioner and the
smoke identity into the secret store, as
`<root-slug>-<env>-provisioner-token` and `<root-slug>-<env>-smoke-token`,
and never prints one. Until it has run, the create run writes the
operator's file with its token line empty (CTX-38).
-->

### Local: Docker Compose

Every dependency runs locally in one compose stack, with the cloud's
image or a wire-compatible stand-in, and the application runs on the
host, so a change is a restart
([`local/`](scaffold/acme_root/deployment/local/)). Developer dashboards
sit in an optional `devx` profile. `make up` starts everything,
migrates, seeds, and prints every local URL, and the steps it wraps run
one at a time for a gate.

> **Principle:** Every dependency runs in a local container. The
> application runs on the host.

### Twins for External Services

A hosted service the platform depends on has an interface, a real
client, and a deterministic twin that speaks its wire shapes, signs its
own synthetic deliveries, and runs in-process or on disk
([`twin.py`](scaffold/acme_root/integrations/src/acme/integrations/identity/twin.py)).
Tests, the local stack, and CI run the twin, and the real client is
proven against recorded fixtures. A twin refuses to run outside a local
environment.

### What a Process Refuses

A process refuses at boot, naming the setting, what is safe only
locally: the file secrets backend in a deployed environment, a twin
outside `local`, and the development seed against a remote database.

### Security Defaults

Every environment takes these from its first apply, and a team departs
only by a named choice: private subnets with a named egress, encryption
at rest and TLS to every store, an audit trail per account, a stated
position on the web firewall, a production database that survives a zone
and restores to a point in time, a second factor at every cloud and
operator sign-in, a protected `main`, and a scan of every image.

<!-- agents-only
An environment that reaches the cloud's services through private
endpoints instead of a NAT gateway keeps a narrow egress path for the
error tracker, the identity provider's keys, and every outside
provider, or names each one it goes without (DEL-48).
-->

## Operations

`core`

A deployed system is an operated one. People steer, agents maintain:
every operational task is a skill a person runs with an agent. The
person chooses; the agent does.

The safety boundary is the credential the skill holds, never the prompt.
A credential that only reads cannot break anything, so an agent holding
one may look everywhere. A cloud credential that writes is held by the
pipeline, or by the administrator for creating and destroying an
environment. The administrator is also the break-glass: granted to a
person for an incident, time-bound and recorded, and what they change by
hand is reconciled by a pull request. A stale state lock has a narrower
answer first: a workflow, dispatched by a person, that unlocks a named
lock under the deployer.

> **Principle:** Every operational task is a skill a person runs with
> an agent. The boundary is the credential. A cloud credential an agent
> holds reads and never writes.

### Operator Roles

Four roles operate a platform. The **administrator** creates and
destroys an environment and nothing else, granted per run. The
**deployer** is the pipeline, trusted only for one environment's jobs on
that environment's branch. The **investigator** reads every signal and
the infrastructure's state and writes nothing. The **supporter** is the
investigator plus a read of one named tenant through the operator plane.
People sign in through the identity center with a second factor, so no
cloud user or long-lived key exists, and an agent's profile chains from
a person's session.

<!-- agents-only
- The investigator plans without a refresh and without the lock: a
  refresh reads each secret's version, which only the plan role may,
  and the lock is a write.
- Roles are named `<product>-<verb>-<environment>`. Folders under
  `deployment/terraform/` spell production `prod`; every name a process,
  a role, or the repository host reads spells it `production`.
-->

### Operator Credentials

A skill names its profile, and before it reads anything it checks that
it holds that role, in the expected account, and nothing wider. The rest
an operator holds, the operator tokens among it, lives in one
owner-only file per environment outside the repository. No credential
enters the repository, a skill, a log, or a report. The local stack is
an environment too.

### Operational Skills

Every system ships with an operational skill per task that repeats,
written into the tree by the scaffold. The skills live in
[`.agents/skills/`](scaffold/acme_root/.agents/skills/), the folder
every agent that reads the [Agent Skills](https://agentskills.io/specification)
standard shares, and `.claude/skills` links to it for Claude Code.

| Skill                            | Role          | Answers                                         |
|----------------------------------|---------------|-------------------------------------------------|
| `ops-investigate`                | investigator  | what is happening now, and why                  |
| `ops-watch`                      | investigator  | a live tail of logs and alarms                  |
| `ops-root-cause`                 | supporter     | why one tenant saw what it saw                  |
| `ops-infra-as-code`              | investigator  | a Terraform change, planned                     |
| `ops-cloud-deployment-create`    | administrator | an environment, to its first deploy             |
| `ops-cloud-deployment-nuke`      | administrator | an environment gone, with what remains named    |
| `ops-simulate-traffic`           | provisioner   | realistic traffic at the edge                   |
| `stress-test-create-or-update`   | none          | a stress scenario with its target               |
| `stress-test-run`                | provisioner, investigator | a run, pass or fail against the target |
| `audit-retention`                | investigator  | which stores grow without bound                 |
| `audit-query-indexes`            | none          | whether the indexes fit the queries             |
| `audit-database-calls`           | none          | how many database calls each flow makes         |
| `audit-deploy-time`              | investigator  | where a deploy's minutes go                     |
| `audit-credential-lifetimes`     | none          | optional: how long a credential outlives its revocation |
| `audit-provider-calls`           | none          | optional: which external calls each flow makes  |

The provisioner is the traffic generator's operator identity, with no
cloud role, and `none` holds no credential at all. Every skill but the
administrator's two and the deploy audit runs against `local`. An audit
reads, reports the answer first, and proposes tickets; it never fixes.
An audit of calls ranks its fixes: remove a call, fold it into another,
defer it, cache it, and only then run calls in parallel. The first
responder to an alarm is an agent, which reads whose traffic raised it
before it escalates, and never suppresses one in production.

<!-- agents-only
- An audit's report puts the answer first, then a table with a verdict
  per row, the findings by impact with a fix and an effort each, and
  what it could not verify. A database audit seeds its own database at
  a scale the run states.
- The two optional audits read the code and the settings only. The
  credential audit states, per credential kind and channel, where it is
  checked, how often, what a check costs, the longest a revoked one
  keeps working, how a role change reaches it, its rate limit, and
  whether its check fails open or closed. The provider audit states, per
  flow, the external calls, how often, whether they repeat, depend on
  each other, or sit in the request path, whether the client is reused,
  the timeout times the retries, and the request's own deadline.
- A skill's body carries its input, its procedure as an ordered list,
  its output, and every invariant inline. Long reference material sits
  in a file under the skill's folder, named by the step that reads it
  when that step runs.
-->

### Dashboards and Alarms as Code

Every environment has one operator dashboard, declared as code, with
the same panels in the cloud and in the `devx` profile. A default alarm
set, the edge, the processes, the database, and the queue's age, goes
to one topic per environment. A tenant admin's view of their own org is
a product screen, never a telemetry query.

### Scale-Out as a Lever

Every service and worker declares its autoscaling with its deployment.
One variable per environment turns it on, off by default, because an
unattended scale-out is a bill nobody approved. The apply never sets the
count autoscaling owns, and the database's storage grows on its own up
to a declared maximum.

### Cost Boundaries

Every account has a budget and an anomaly monitor from its first apply,
a retention on every log group, and the environment tag on every
resource.

### Creating and Destroying an Environment

Creating and destroying are the administrator's two runs: scripts the
repository holds, narrated by their skills, one account at a time, and
dry-runnable ([`scripts/`](scaffold/acme_root/scripts/)). A create run
is safe to repeat, which is also how an account is reproduced, and its
one credential from outside the account is the domain host's token,
scoped to the domain's zone and held for the run. Production is
destroyed only behind its typed name and a released change that turned
its deletion protection off, and it leaves a final snapshot. Outside
production a destroy deletes secrets with no recovery window, so a
create that follows finds their names free.

### Traffic and Stress

One traffic generator drives the edge with realistic sessions, in
tenants it creates for the run through the operator plane and removes
after ([`ops/`](scaffold/acme_root/ops/)). The counts an agent reads
before it escalates leave those tenants out, and in production the
provisioner stays disabled until a run needs it. A stress test is the
generator with a scenario and a target stated before the run. The gate
runs it for thirty seconds, which proves the wiring and never the
capacity.

### The Telemetry Round Trip

A signal no test reads is a claim. One integration test drives a
session through the edge, one call failing on purpose, and reads every
signal back by its request id: the log line, the counter, the trace, the
error. The readers have a local and a cloud impl, so the same test is
the smoke test after every deploy.

## Monorepo Folder Structure

The monorepo groups code by role. A system that starts as one API
process has one entry under `services/` and grows the rest. The scaffold
is the whole tree ([`scaffold/acme_root/`](scaffold/acme_root/)):

```text
[root]/
├── om/               # acme-om: the object model, storage, migrations
├── infra/            # acme-infra: cache, buckets, topics, queues, secrets
├── integrations/     # third-party providers: interface, client, twin
├── services/api/     # the API process: gateway, routers, services, types
├── workers/          # one folder per worker role
├── apps/             # portal, operator console, CLI
├── clients/          # a typed client per service, per language: typescript/, python/
├── ops/              # acme-ops: traffic, stress, signal readers
├── deployment/       # terraform/, local/, docker/, cloud/
├── scripts/          # runnable entry points
├── specs/            # architecture.md: the pin and the deviations
├── docs/             # the system as built, adr/, runbooks/
├── .agents/skills/   # the operational skills
├── .claude/skills    # a link to .agents/skills
├── .github/workflows/
├── Makefile          # setup, check, test-*, migrate, seed, up, down, openapi, traffic
└── llms.txt          # the knowledge map
```

### Layout Conventions

Every Python distribution uses the `src/acme/...` layout, with tests in
a sibling `tests/`, so they run against the installed package. Workspace
tooling sits at the root: one uv and one pnpm workspace, `ruff.toml`,
and `pyrightconfig.json`. The OM is one distribution, and its namespaces
are folders. Workers and services share one project shape, and a service
binary is its own operations CLI: `serve`, `migrate`, `bootstrap`,
`openapi`. Dockerfiles live under `deployment/docker/`: two stages,
locked dependencies, a non-root user, a healthcheck.

> **Principle:** The OM is one distribution. Namespaces are folders
> inside it, not separate packages.

`make check` is the fast gate: lint, format, types, unit tests. CI adds
the integration, migration, image, and infrastructure jobs, and runs
them on a pull request's merge with the current `main`, so two changes
green alone cannot land broken together. The later takes the next free
ADR number and re-points its migration's parent.

## Documentation as Code

Documents are code, reviewed with the change they describe and read by
people and agents alike. An agent on its first day reads them first.

### A README at Every Level

Every abstraction level carries a README in its own language: `om/` in
the product's nouns, `deployment/` in processes and environments, `ops/`
in roles and signals. `om/README.md` names the nouns and their relations
for a reader with no code, and carries no developer or operator
instruction.

> **Principle:** Every abstraction level carries a README in its own
> language. `om/README.md` names the nouns and their relations for a
> reader with no code.

### The Knowledge Map

One map at the root, `llms.txt`, names what each audience is served:
the platform's developers, its operators, and the tenant's own users. A
document is served by being listed, never by its folder. Every document
speaks product, technology, or service, and nothing that would matter if
it leaked.

## Telemetry

What a process emits is part of its shape. Every process logs, traces,
counts, and reports errors the same way, and one id joins it all, so a
reader follows a request across every process it touched
([`observability.py`](scaffold/acme_root/infra/src/acme/infra/observability.py)).

### Logs

Logging is Python's standard `logging`, configured once at boot, JSON in
the cloud. A filter stamps every line with the service, the environment,
the request id, and the causing request, so no call site has to
remember.

> **Principle:** Python's `logging` is the platform logger. Every
> module uses it. No module replaces it.

### Traces and Metrics

Traces use OpenTelemetry directly, with a no-op tracer until an endpoint
is set. Metrics are Prometheus counters and histograms on `/metrics`,
served by every process: every request by route and status, every
queue, cache, and limit by outcome. A label is bounded, never an id.

### Error Tracking

Every process reports errors through the Sentry SDK, browser apps
included, tagged with the service, the release, and the request id.
Reporting is off until a DSN is set, and an event carries no secret.

### Correlation Across a Handoff

A handoff carries the request that caused it. One id joins the request,
its outbox row, the item it queued, and the run that followed. The run
is a new request whose `caused_by_request_id` names the cause. The
handoff also carries the cause's trace context as a `traceparent`, never
a `trace_id`, and the run's span links to it rather than becoming its
child, since a queue can hold an item long after its request ended.
When the cause ran with no tracer, the `traceparent` is empty and the
run starts a trace of its own.

> **Principle:** A handoff carries the request that caused it and that
> request's trace context. The far side is a new request that names the
> cause, and its span links to the causing trace.

## Cross-Cutting Conventions

### Exceptions

Every exception inside the platform is rooted at `PlatformException`,
which carries an `http_status` and a stable `code`. Infra imports
nothing from the OM, so it has its own root, `InfraException`, with the
same two fields
([`exceptions.py`](scaffold/acme_root/om/src/acme/om/exceptions.py)):

``` python
class PlatformException(Exception):
    http_status: int = 500
    code: str = "platform_error"

class NotFound(PlatformException):
    http_status = 404
    code = "not_found"
```

The shapes cover almost every case: `NotFound` (404), `Conflict` (409),
`PreconditionFailed` (412), `ValidationFailed` (422), `NotAuthenticated`
(401), `NotAuthorized` (403), and `Unavailable` (503), for what cannot
be reached right now, each with its name in snake case as its code. A
namespace's exception inherits a shape. One that no shape fits sets a
status and a code of its own under the root, as a read of the event
stream below its floor is `410 stream_truncated`. A boundary catches
both roots and translates them in one place; managers never format
HTTP.

### Configuration

Every process reads one settings object at boot, from environment
variables under one prefix, documented in `.env.example`. Backends are
chosen there and nowhere else, and a browser app reads its settings
from the `config.json` beside its bundle. Variation that belongs to the
product is a modelled entity, not a flag.

> **Principle:** One settings object per process, read once at boot.
> Backends are chosen there; nothing below reads the environment.

### The App Container

Every process boots the same way: settings, then logging, error
reporting, and tracing, then storage, infra, and the managers
([`container.py`](scaffold/acme_root/services/api/src/acme/services/api/container.py)).
The container has `start()` and `close()`, and a test builds it over the
memory roots to run the whole application in-process. The roots,
`build_managers` among them, are built whole, once per process. A
constructor opens nothing, so that costs microseconds, and a root that
builds on first use is refused, since it moves a wiring error from boot
to a request. A boot benchmark measures it, and a test holds that the
managers build once for any number of requests.

### Records of Decisions

A decision that constrains future work is an ADR under `docs/adr/`:
context, decision, consequences, numbered and cited by number.
`docs/architecture.md` describes the system as built and links its ADRs;
this document describes how we build, and names each decision it makes
for every system where it makes it, with what would end it. A rule a
program can check is checked: `arch-check` decides what reads the
source, and a test holds what needs the built system. A rule only
written down drifts; a rule that fails the build holds.

### Tests

Unit tests run over the memory roots and the pure rules. The storage
contract cases run over memory in the fast gate and over Postgres in the
integration job
([`contracts/`](scaffold/acme_root/om/tests/contracts/)). The named
atomic methods are raced, and exactly one caller wins. Tenant isolation
is proven by the case that tries the breach, on every storage method.

The isolation suite is itself proven. A tenant predicate is taken out of
one query, and the suite runs twice: green with the policy in place, red
with it off. The migration login turns the policy off, since only the
owner can, and the suite runs as the runtime login both times. Both runs
are recorded. A negative control nobody ran is a claim, not evidence.

## Technology Choices and How to Override Them

`default`

This document names technologies, not only shapes: Python on Pydantic
for the object model; FastAPI on uvicorn, httpx, and Typer; SQLAlchemy
and Alembic over Postgres; Valkey, an S3-like object store, and SQS;
React and TypeScript on Vite, with TanStack Query and Zustand; uv, pnpm,
and Docker Compose; AWS in Terraform, with ECS Fargate and CloudFront;
OpenTelemetry, Prometheus, and the Sentry SDK.

A guideline that says "a relational database" leaves a decision open at
every step. One that says "Postgres" closes it the same way for
everyone, and that makes the shapes concrete enough to check and the
scaffold concrete enough to run.

> **Principle:** Named technologies are defaults. The shapes are the
> guideline; the names make the shapes concrete.

### Versions

`default`

Every dependency runs on its latest stable release once a patch release
sits behind it: the active LTS line where one exists. The version is
stated where each tool reads it, and lock files hold the resolution. A
deployed database changes major version only by a planned upgrade,
rehearsed on a restored copy with a snapshot taken first, in a window of
its own, and never because a newer line appeared; its minor versions
follow the rule.

### Overriding a Choice

A project may substitute an equivalent: another engine, another cloud,
another view library. It keeps every rule that does not name the
technology, and records each substitution in one ADR, written when it
adopts this document, with the rules the substitute must still satisfy.
Reviews then treat the substitute as the named technology. A
substitution keeps a shape; a deviation changes one.

> **Principle:** A substitution is recorded once, in the project's own
> ADR, with the rules the substitute must still satisfy. A change of
> shape is a deviation, not a substitution.

## Scalability by Design

Horizontal scale is what the rules add up to. Stateless services,
tenant-scoped queries, roles that move by URL, workers that compete for
rows, and deliveries that repeat harmlessly make scaling out a matter
of adding processes, with no code change. That holds while tenants are
of comparable size and pools fit the engine. Each assumption names what
ends it. A tenant hot enough to serialize on its gapless `seq` gets an
engine of its own through a routing key under its role: the first
change that is not a deployment change, and one that does not lift the
tenant's single sequence, which would take more than one stream per
tenant. A tenant whose bulk work starves its neighbours gets a lane,
and a role whose pool runs out gets a pooler.

## Resilience by Design

Staying up while something downstream fails is what the bounds add up
to: a deadline on every call, a bound on every pool and on what a
process has in flight, a breaker, classified retries, bounded buffers,
parks instead of failures, and declared degradation. Every bound is a
shape: that it exists, that settings name it, and what happens when it
is reached. Its value belongs to the system.

## What This Document Does Not Cover

Some concerns are real and absent on purpose, because a team commits to
them per system once the shape holds: a threat model and a rotation
schedule; service objectives, alarm thresholds, and on-call; the
tuning of deadlines, retries, and admission bounds; disaster recovery
beyond each role's rehearsed restore, and multi-region; tenant export
and offboarding; stress targets; API deprecation; and supply-chain
rules. When one earns a rule that holds across systems, it
lands beside the rules it touches.

## Next: An End-to-End Reference Implementation

Tadas, a to-do app for teams, applies this document end to end and
records each gap as a deviation: <https://github.com/baristaze/tadas>.
