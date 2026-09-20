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

## OM-02 Wire and table shapes are projections

**Principle.** The OM is the source of truth for entities; the wire
format and the table layout are projections derived from it, and
neither changes the OM to suit itself. Tenant entities carry no
`org_id`; tenancy is a storage concern. The one exception is an entity
whose readers have no tenant, which carries `org_id` as a model field
so the reader knows whose it is.

**Source.** The Domain as the Source of Truth; The Storage Layer,
Defining ORM Classes.

**Look for.** Fields added to an entity that exist only to satisfy a
response shape or a column; entity types imported into the network
layer as the response body itself; storage-only concerns leaking into
entity classes; whether `org_id` appears on an entity and, when it
does, whether that entity is read by an operator across tenants.

**Violation.** An entity gaining a field because a client wanted it in
JSON; an entity carrying a column-oriented attribute such as a raw
foreign key that no manager reads; a route returning an OM entity
directly instead of a view; `org_id` on an entity that only tenant
operations read; an entity read across every tenant without it.

**Severity.** medium

## OM-03 The mixins declare exactly their fields

**Principle.** Orthogonal traits are captured by small mixins on a
fieldless root, each declaring exactly the fields the guideline lists:
`Identifiable` (`id`), `Named` (`name`), `Trackable` (`created_at`,
`updated_at`, `created_by`, `updated_by`), and `SoftDeletable` (`deleted_at`,
`deleted_by`). A new trait is a new mixin, not a field on an existing
one. The `new_id()` and `utcnow()` helpers and `PROVENANCE_FIELDS`, the
constant naming `created_at`, `created_by`, `deleted_at`, and
`deleted_by`, live in the same base module.

**Source.** Naming Entities.

**Look for.** The base module of the OM; the fields each mixin
declares; whether entities redeclare a mixin's fields locally; whether
`new_id()` and `utcnow()` are the helpers used to construct entities;
what `PROVENANCE_FIELDS` names.

**Violation.** A field added to one of the listed mixins instead of a
new mixin for the new trait; an entity declaring its own `created_at`
next to `Trackable`; a root class that holds fields; a local
`datetime.now()` or id factory used in place of the base helpers; a
`PROVENANCE_FIELDS` declared per namespace or naming other fields than
the four.

**Severity.** medium

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

## OM-05 Inheritance expresses abstraction, not reuse

**Principle.** Each mixin is a promise about what the entity is. An
entity opts into a trait by adding the mixin and opts out by leaving it
off. Inheritance in the OM is never a way to share code.

**Source.** Naming Entities.

**Look for.** Base classes in the OM that carry methods or behavior
rather than a trait; entity-to-entity inheritance; for every entity
composing `SoftDeletable`, a manager method that sets `deleted_at`, and
for every entity composing `Trackable`, a manager method that sets
`updated_at`.

**Violation.** A `BaseOrder` with helper methods that `Order` and
`ReturnOrder` extend; an entity composing `SoftDeletable` while no
manager method sets `deleted_at`, or composing `Trackable` while no
method sets `updated_at`; a mixin introduced to avoid repeating two
fields that mean different things in different entities.

**Severity.** medium

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

## OM-08 Entities, value objects, and read models are distinct

**Principle.** An entity has an identity and is stored. A value object
is a typed piece of an entity with no identity, stored inline with its
owner. A read model is a shape a manager returns that is never written
back as truth: a persisted copy of one (a cache entry, a reporting
mirror, a projection table) is derived and rebuildable. The mixins
tell them apart.

**Source.** Naming Entities, Entities, Value Objects, and Read Models.

**Look for.** Classes on the OM base chain that carry `Identifiable`;
classes returned by managers that are not entities; whether read models
compose mixins or get persisted.

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

## OM-10 Entities are immutable; updates copy and write

**Principle.** OM entities are frozen snapshots. An update takes the
entity, produces a modified copy, and passes the copy to a write
method. No layer mutates an entity after construction. Fields are
tuples and frozen models, never `list` or `dict`; a mapping field is
`FrozenMapping`, a `Mapping` whose validator wraps the dict in a
`MappingProxyType`, because a frozen model with a bare `Mapping` still
holds a mutable dict; its empty default is validated too, because
pydantic does not validate a default. A copy that carries caller
input is rebuilt from
a dict (`model_validate({**current.model_dump(), **changes})`), because
`model_copy` does not validate and `model_validate` hands an instance
back untouched.

**Source.** Naming Entities, Immutability.

**Look for.** The root's frozen configuration; assignment to entity
attributes anywhere; the update path in managers; `list`, `dict`, or
bare `Mapping` fields on the chain; a `model_copy(update=...)` fed
from a request; a `model_validate` called on an instance.

**Violation.** `order.status = ...` in a manager or service; a class on
the chain that unfreezes itself; an update that reaches into a nested
value object to change it in place; a `list` field appended to through
the snapshot; a bare `Mapping` field holding the dict pydantic built;
a `FrozenMapping` whose default is a plain dict for want of
`validate_default`;
caller input copied into an entity with no validation, or passed to
`model_validate` as the instance it already is.

**Severity.** medium

## OM-11 Everything on the base chain is frozen, rows excepted

**Principle.** Immutability applies to every object built on the OM
base chain, including value objects, read models, and context
sub-objects, and to the views the network layer returns. The one
deliberate exception is the ORM row classes, which never leave the
storage impl.

**Source.** Naming Entities, Immutability; The Network Layer, Public
Types.

**Look for.** Value objects, read models, and context types that
subclass the root; the `View` base in the service's wire types and its
frozen configuration; any class on the chain that overrides the frozen
setting.

**Violation.** A value object, read model, or context type declared
mutable; a `View` base without the frozen configuration; an entity
constructed by wrapping a live row.

**Severity.** medium

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
minted inside a storage impl.

**Severity.** medium

## OM-13 EMPTY_UUID means the platform, and optional means None

**Principle.** `EMPTY_UUID` is the platform's own reference: the
system scope on infra calls and the value of a required reference no
tenant and no person owns (`created_by` on a row the platform itself
wrote), which keeps the column `NOT NULL` and the index simple. Both
readings say the same thing. A reference that is genuinely optional is
`None`, never `EMPTY_UUID`. Its use as the system scope on infra calls
is judged by `context`.

**Source.** Naming Entities, Identifiers.

**Look for.** The constant defined once in the base module; required
reference fields on rows the platform writes; optional references and
how they express absence; ad-hoc sentinel constants elsewhere in the
OM.

**Violation.** A second sentinel constant invented for "no warehouse";
a required reference column made nullable for platform-written rows;
an optional reference filled with `EMPTY_UUID` instead of `None`.

**Severity.** medium

## OM-14 Namespaces mirror product swimlanes with one shape

**Principle.** The OM is split into namespaces that mirror the
swimlanes of the product, and each has the same internal shape:
`manager.py` re-exported from the package root, `types/`, `impl/`,
`storage/`, and `rules.py` when the namespace has pure rules.

**Source.** Namespaces as Swimlanes.

**Look for.** The folder layout of each namespace; where the manager
interface is defined and whether the package root re-exports it; where
entity classes and manager impls live.

**Violation.** A namespace whose interface can only be imported from a
deep path; entity classes next to the manager impl; a `types/` folder
holding an entity that no manager interface in the same namespace
accepts or returns; a product swimlane living as a sub-folder of
another namespace's `types/`.

**Severity.** medium

## OM-15 Business rules are pure functions in one module

**Principle.** The pure part of a namespace's logic (pricing, window
arithmetic, eligibility, aggregation rules) lives in a module of plain
functions that read no storage, consult no clock, and open no
settings. Manager impls and every storage impl call them; nothing
re-implements them. A rule the engine must evaluate inside a
statement, a filter or an ordering, is spelled once more in that
statement, named as such, and the contract case that runs both impls
holds the two spellings together; everything a rule decides before or
after the statement calls the function.

**Source.** Namespaces as Swimlanes, Pure Rules.

**Look for.** Arithmetic and eligibility logic inside manager or
storage impls; the same rule implemented twice for two storage
backends; a rules module that imports storage, settings, or a clock; a
rule spelled inside a statement, whether it names the function it
restates, and the contract case that runs both impls over it.

**Violation.** A relational impl aggregating in SQL and an in-memory
impl aggregating with different arithmetic; a rules function calling
`utcnow()` internally instead of taking the time as an argument; a
pricing rule duplicated in a manager and a report builder; a rule
spelled in a statement with no name pointing at the function, or with
no contract case holding the two spellings together; a rule decided
before or after the statement re-implemented instead of calling the
function.

**Severity.** medium

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
