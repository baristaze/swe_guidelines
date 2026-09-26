# Object Model

Group id: `om`. Covers The Domain as the Source of Truth, Naming
Entities, and Namespaces as Swimlanes of `architecture.md`.

This group judges what the object model is: which classes exist, what
they promise, how they are shaped, and where they live. It leaves the
interface/impl split and constructor injection to `contracts`, the
context types, authorization, and tenancy to `context`, and table
classes, translation, and database roles to `storage`.

## OM-01 One object model, defined once

**Principle.** The domain has one source of truth: a standalone OM
library that every layer depends on and nothing redefines.

**Source.** The Domain as the Source of Truth.

**Look for.** Where domain entities are declared; whether services,
workers, and apps import them from the OM package or declare their own
copies; whether the OM is packaged as its own library rather than a
module inside a service.

**Violation.** A second class with the same fields as an OM entity
living in a service, a worker, or a script; a domain noun defined only
in a wire type or a table class; the OM importable only by installing a
service.

**Severity.** medium

**Check.** `arch-check` decides that the OM's manifest names a
distribution and that every distribution importing the OM depends on
it; the rest is judged.

## OM-02 Wire and table shapes are projections

**Principle.** The OM is the source of truth for entities; the wire
format and the table layout are projections of it, and neither changes
the OM to suit itself. Tenant entities carry no `org_id`; tenancy is a
storage concern. The one exception is an entity whose readers have no
tenant, which carries `org_id` so the reader knows whose it is.

**Source.** The Domain as the Source of Truth; The Storage Layer,
Defining ORM Classes; The Storage Layer, Namespace Shape.

**Look for.** Fields added to an entity that exist only to satisfy a
response shape or a column (what crosses the wire is NET-13);
storage-only concerns leaking into entity classes; whether `org_id`
appears on an entity and, when it does, whether a reader with no
tenant reads it: an operator across tenants, or the outbox relay. A
cross-tenant sweep may instead get the tenant back beside each row
(CTX-12).

**Violation.** An entity gaining a field because a client wanted it in
JSON; an entity carrying an attribute that exists only for a
column's sake; `org_id` on an entity every reader of which holds a
context, an audit entry among them; an entity a reader without one
takes, such as an `OutboxRow` or an `Event`, declared without it.

**Severity.** medium

**Check.** `arch-check` decides which OM types declare `org_id`; the
rest is judged.

## OM-03 The mixins declare exactly their fields

**Principle.** Small mixins on a fieldless root declare exactly the
fields the guideline lists: `Identifiable` (`id`), `Named` (`name`),
`Created` (`created_at`), `Trackable` (`Created` plus `updated_at`,
`created_by`, `updated_by`), `SoftDeletable` (`deleted_at`,
`deleted_by`). A new trait is a new mixin. `new_id()`, `utcnow()`, and
`PROVENANCE_FIELDS` live in the base module. Each entity declares
`MANAGER_OWNED_FIELDS`, a tuple, even when empty, and the copy on
update excludes `set(PROVENANCE_FIELDS) |
set(<Entity>.MANAGER_OWNED_FIELDS)`. `OutboxRow` and
`IdempotencyMarker` are declared once, each in its namespace.

**Source.** Naming Entities; The Storage Layer, Namespace Shape.

**Look for.** The base module of the OM; the fields each mixin
declares; whether entities redeclare a mixin's fields locally; whether
`new_id()` and `utcnow()` are the helpers used to construct entities;
what `PROVENANCE_FIELDS` names (`created_at`, `created_by`,
`deleted_at`, `deleted_by`); each entity's `MANAGER_OWNED_FIELDS`
and what it names; the fields of `OutboxRow`, which carries
the provenance of the write it announces (`actor_id`, `request_id`,
`traceparent`, `app`) and no `created_by`, since no person stands
behind the row.

**Violation.** A field added to one of the listed mixins instead of a
new mixin for the new trait; an entity declaring its own `created_at`
next to `Trackable`; a root class that holds fields; a local
`datetime.now()` or id factory used in place of the base helpers; a
`PROVENANCE_FIELDS` declared per namespace or naming other fields than
the four; an entity with no `MANAGER_OWNED_FIELDS`, or a field its
manager sets that the tuple leaves out; an `OutboxRow` with no
`actor_id`, `request_id`, or `app`, so the relay has no provenance to
stamp on the event, or with a `created_by`.

**Severity.** medium

**Check.** `arch-check` decides the fields of the root, the mixins, and
`OutboxRow`, a redeclared mixin field, where the base helpers and
`PROVENANCE_FIELDS` live, and a local clock; the rest is judged.

## OM-04 Declaration order reads as a description

**Principle.** Mixins are composed in a fixed order: identity first,
human-facing label next, lifecycle, then cross-cutting traits. The
class signature reads as what the entity promises to be.

**Source.** Naming Entities.

**Look for.** The base list of every concrete entity; the order of
`Identifiable`, `Named`, `Trackable`, `SoftDeletable` in it.

**Violation.** `class Warehouse(Trackable, Identifiable, ...)` or any
ordering that departs from identity, label, lifecycle, cross-cutting.

**Severity.** low

**Check.** `arch-check` decides the order of the guideline's mixins;
the rest is judged.

## OM-05 A mixin is a promise, composed only where an operation exercises it

**Principle.** Each mixin is a promise, composed only where a manager
operation exercises it: `Trackable` where an update exists,
`SoftDeletable` where a delete does, `Created` and never `Trackable`
on a row the platform writes for itself (the outbox row, the marker, the socket
ticket), whose later stamp is a field named for what happened.
Inheritance in the OM never shares code.

**Source.** Naming Entities.

**Look for.** Base classes in the OM that carry methods or behavior
rather than a trait; entity-to-entity inheritance; for every entity
composing `SoftDeletable`, a manager method that sets `deleted_at`, and
for every entity composing `Trackable`, a manager method that sets
`updated_at` and `updated_by`.

**Violation.** A `BaseOrder` with helper methods that `Order` and
`ReturnOrder` extend; an entity composing `SoftDeletable` while no
manager method sets `deleted_at`, or composing `Trackable` while no
method sets `updated_at` and `updated_by`; a mixin introduced to
avoid repeating two fields that mean different things in different
entities. (How an update stamps `updated_at` and `updated_by` is
CON-17.)

**Severity.** medium

**Check.** `arch-check` decides methods on the root and the mixins and
entity-to-entity inheritance; the rest is judged.

## OM-06 Append-only records carry identity only

**Principle.** A record that is never updated and never hidden (an
audit entry, a ledger line, an event) is `Identifiable` and nothing
else: no `updated_at`, no `deleted_at`.

**Source.** Naming Entities.

**Look for.** Entities whose managers only ever create them; the mixins
those entities compose.

**Violation.** An audit or event entity composed with `Trackable` or
`SoftDeletable`; an `updated_at` on a record no code path updates.

**Severity.** low

## OM-07 The root forbids unknown fields

**Principle.** `extra="forbid"` on the root makes a misspelled field a
construction error instead of a silently ignored key, and every class
on the OM base chain inherits it.

**Source.** Naming Entities.

**Look for.** The root's model configuration; any class on the chain
that overrides it to allow or ignore extras.

**Violation.** A root configured to ignore unknown keys; a value object
or context type that relaxes the setting so a caller's typo passes.

**Severity.** medium

**Check.** `arch-check` decides the model config spelled in the class;
the rest is judged.

## OM-08 Entities, value objects, and read models are distinct

**Principle.** An entity has an identity and is stored. A value object
is a typed piece of an entity with no identity, stored inline with its
owner. A read model is a shape a manager returns that is never written
back as truth: a persisted copy of one is derived and rebuildable. The
mixins tell them apart.

**Source.** Naming Entities, Entities, Value Objects, and Read Models.

**Look for.** Classes on the OM base chain that carry `Identifiable`;
classes returned by managers that are not entities; whether read models
compose mixins or get persisted (a cache entry, a reporting mirror, a
projection table).

**Violation.** A value object with an `id` and its own table; a read
model composed with `Identifiable`, persisted as truth, or read as
one; an aggregate answered by inventing a table for it instead of a
read model.

**Severity.** medium

## OM-09 Filters and groupings are typed value objects

**Principle.** Typed filter and grouping objects travel through manager
and storage interfaces unchanged, so an aggregation runs in SQL on one
storage impl and in memory on another while the caller writes the same
code.

**Source.** Naming Entities, Entities, Value Objects, and Read Models.

**Look for.** Parameters of list and aggregate methods on manager and
storage interfaces; whether filtering criteria are typed objects or
loose keyword arguments and strings.

**Violation.** A manager method taking a free-form dict or a raw SQL
fragment as a filter; a storage impl interpreting a string that another
impl interprets differently; filter shapes declared separately for the
manager and the storage.

**Severity.** medium

**Check.** `arch-check` decides `**kwargs` and loose filter parameters
on the manager and storage interfaces; the rest is judged.

## OM-10 Entities are immutable; updates copy and write

**Principle.** OM entities are frozen snapshots: an update produces a
modified copy and passes it to a write method; nothing mutates an
entity once constructed. A copy that carries a dump is rebuilt from a
dict (`model_validate({**current.model_dump(), **changes})`);
`model_copy` is for values constructed of the field's own type, since
it does not validate and leaves a dumped value object a dict.

**Source.** Naming Entities, Immutability.

**Look for.** The root's frozen configuration; assignment to entity
attributes anywhere; the update path in managers; a
`model_copy(update=...)` fed a `model_dump()` or a request; a
`model_validate` called on an instance of the class it builds.

**Violation.** `order.status = ...` in a manager or service; a class on
the chain that unfreezes itself; an update that reaches into a nested
value object to change it in place; caller input copied into an entity
with no validation, or passed to `model_validate` as the instance it
already is.

**Severity.** medium

**Check.** `arch-check` decides an unfrozen class, a `model_copy` fed a
dump, a `model_validate` of an entity, and an assignment to an entity;
the rest is judged.

## OM-11 Everything on the base chain is frozen, rows excepted

**Principle.** Immutability applies to every object built on the OM
base chain, including value objects, read models, and context
sub-objects. The one deliberate exception is the ORM row classes,
which never leave the storage impl. The views the network layer
returns are NET-13.

**Source.** Naming Entities, Immutability.

**Look for.** Value objects, read models, and context types that
subclass the root; any class on the chain that overrides the frozen
setting.

**Violation.** A value object, read model, or context type declared
mutable; an entity constructed by wrapping a live row.

**Severity.** medium

**Check.** `arch-check` decides the frozen setting of the root and of
every class on the chain; the rest is judged.

## OM-12 Every id is uuid v7, minted above storage

**Principle.** Every id is a time-ordered `uuid_v7` produced by
`new_id()` by whoever constructs the entity, always above the storage
layer.

**Source.** Naming Entities, Identifiers.

**Look for.** Where entity ids are created and which factory produces
them; entity constructions that leave `id` for a lower layer to fill;
any `uuid4()` or other generator imported by OM or service code.

**Violation.** `uuid4()` used for an entity id; an entity constructed
without an id on the assumption that storage will assign one; an id
minted inside a storage impl or assigned by the database. The id a
creating `POST` mints before its idempotency marker, ahead of the
entity, is that protocol and not a breach (NET-24). (Ids read back out
of the database are STO-06.)

**Severity.** medium

**Check.** `arch-check` decides every id factory but `new_id()` above
storage; the rest is judged.

## OM-13 EMPTY_UUID means the platform, and optional means None

**Principle.** `EMPTY_UUID` is the platform's reference: the system
scope on infra calls and the value of a required reference no tenant and
no person owns (`updated_by` on an item the platform claimed), which
keeps the column `NOT NULL` and the index simple. A reference that is
optional is `None`, never `EMPTY_UUID`. Its use as the system scope is
judged by `context`.

**Source.** Naming Entities, Identifiers; Worker Roles, The Work
Queue.

**Look for.** The constant defined once in the base module; required
reference fields on rows the platform writes; every write to a work
item after its enqueue, the claim, the completion, the requeue, the
failure, the hand-back, and the lease renewal, and what each puts in
`updated_by`; optional references and how they express absence;
ad-hoc sentinel constants elsewhere in the OM.

**Violation.** A second sentinel constant invented for "no warehouse";
a required reference column made nullable for platform-written rows;
a write to a work item row after its enqueue signed from the context,
so the enqueuer appears to have run the work; an optional reference
filled with `EMPTY_UUID` instead of `None`.

**Severity.** medium

**Check.** `arch-check` decides the one `EMPTY_UUID`, a second all-zero
UUID, and an optional reference defaulting to it; the rest is judged.

## OM-14 Namespaces mirror product swimlanes with one shape

**Principle.** The OM is split into namespaces that mirror the
swimlanes of the product, and each has the same internal shape:
`manager.py` re-exported from the package root, `types/`, `impl/`,
`storage/`, and `rules.py` when the namespace has pure rules.

**Source.** Namespaces as Swimlanes.

**Look for.** The folder layout of each namespace; where the manager
interface is defined and whether the package root re-exports it; where
entity classes and manager impls live (`impl/manager.py`); the names of
the manager and storage interfaces (the namespace in the singular,
`OrderManagerInterface`, `OrderStorageInterface`) and of the operations
(the entity, `write_warehouse`).

**Violation.** A namespace whose interface can only be imported from a
deep path; entity classes next to the manager impl; a product swimlane
living as a sub-folder of another namespace's `types/`; a manager
interface named after an entity rather than the namespace; a storage
interface named after an aggregate in a namespace that has only one.

**Severity.** medium

**Check.** `arch-check` decides the folder shape of each namespace, the
re-export of its interface, and an entity next to the impl; the rest is
judged.

## OM-15 Business rules are pure functions in one module

**Principle.** The pure part of a namespace's logic (pricing, window
arithmetic, eligibility, aggregation rules, a table a decision reads)
lives in a module of plain functions that read no storage, consult no
clock, and open no settings. Storage impls and manager impls call them;
a rule the engine must evaluate inside a statement is spelled there
once more (OM-18).

**Source.** Namespaces as Swimlanes, Pure Rules.

**Look for.** Arithmetic and eligibility logic inside manager or
storage impls; a table a decision reads declared beside the types; the
same rule implemented twice for two storage backends; a rules module
that imports storage, settings, or a clock.

**Violation.** A relational impl aggregating in SQL and an in-memory
impl aggregating with different arithmetic; a rules function calling
`utcnow()` internally instead of taking the time as an argument; a
pricing rule duplicated in a manager and a report builder.

**Severity.** medium

**Check.** `arch-check` decides the imports, the calls, and the async
functions of a rules module; the rest is judged.

## OM-16 Cross-cutting namespaces are ordinary namespaces

**Principle.** Tenancy (organizations, users, memberships,
credentials) and audit (who did what, when, from which app) are
first-class swimlanes with their own types, managers, and storage, not
utilities hanging off the root.

**Source.** Namespaces as Swimlanes.

**Look for.** Where identity, membership, credential, and audit types
live; whether they have a manager interface and a storage like any other
namespace.

**Violation.** User and organization classes in `base.py` or a `utils`
module; audit rows written by a helper function with no storage
interface; credential handling spread across services with no owning
namespace.

**Severity.** medium

**Check.** `arch-check` decides the tenancy namespace, and identity
and audit classes in the base or a utils module; the rest is judged.

## OM-17 Entity fields are tuples, frozen models, and FrozenMapping

**Principle.** Fields are tuples and frozen models, never `list` or
`dict`. A mapping field is `FrozenMapping`, a `Mapping` whose validator
wraps the dict in a `MappingProxyType` and descends, freezing nested
mappings and turning nested lists into tuples; its empty default is
validated too, because pydantic does not validate a default.

**Source.** Naming Entities, Immutability.

**Look for.** `list`, `dict`, or bare `Mapping` fields on the chain, and
abstract collections (`Sequence`, `Collection`, `Iterable`,
`AbstractSet`) that pydantic stores as a list or a set; the validator
behind `FrozenMapping` and whether it descends into nested mappings and
lists; the default of every mapping field and whether `validate_default`
is set on it.

**Violation.** A `list` field appended to through the snapshot; a bare
`Mapping` field holding the dict pydantic built; a `FrozenMapping`
that wraps only the outer dict, so a nested dict in a dumped payload
is still mutated through the snapshot; a `FrozenMapping`
whose default is a plain dict for want of `validate_default`.

**Severity.** medium

**Check.** `arch-check` decides the field annotations on the chain and
the `FrozenMapping` default; the rest is judged.

## OM-18 A rule spelled in a statement is named and held to the function

**Principle.** A rule the engine must evaluate inside a statement, a
filter or an ordering, is spelled once more in that statement, named
as such, and the contract case that runs both impls holds the two
spellings together; everything a rule decides before or after the
statement calls the function.

**Source.** Namespaces as Swimlanes, Pure Rules.

**Look for.** A rule spelled inside a statement, whether it names the
function it restates, and the contract case that runs both impls over
it.

**Violation.** A rule spelled in a statement with no name pointing at
the function, or with no contract case holding the two spellings
together; a rule decided before or after the statement re-implemented
instead of calling the function.

**Severity.** medium
