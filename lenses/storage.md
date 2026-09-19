# Storage

Group id: `storage`. Covers The Storage Layer and the index-related
rules of Identifiers (in Naming Entities) of `architecture.md`.

This group judges how the storage layer persists and returns entities:
what a storage operation may do, how tables are declared, how rows and
entities translate, how tables are grouped into database roles, and how
the schema moves over time. It leaves entity shapes and identifier
minting to `om`, the `org_id`-first rule, user scoping, and
tenancy-on-write to `context`, interface imports and the in-memory
impl to `contracts`, and caches, topics, and queues to `async`.

## STO-01 Code never relies on a database relationship

**Principle.** The code never relies on a relationship the database
knows about. A foreign key may exist for integrity or as an
optimization, but no manager assumes a cascade, a rejected orphan, or a
join the schema happens to permit. Relationships the business layer
needs are plain id columns it reads and writes itself.

**Source.** The Storage Layer, Storage Principles.

**Look for.** Delete paths in managers and storage impls: the code
deletes or detaches every dependent record itself rather than stopping
after the parent. Migration SQL or table classes that declare `ON
DELETE CASCADE` or `SET NULL` and code that counts on it, and manager
code that catches an integrity error as its existence check.

**Violation.** A manager deletes a parent and comments or assumes that
children go with it. A referenced-entity existence check is implemented
as "the insert will fail if it does not exist". A manager receives a
related entity it never asked for by id, loaded for it by the schema.

**Severity.** medium

## STO-02 No transaction outlives a storage call; one operation commits itself

**Principle.** A storage operation is one statement or one short,
self-contained unit that the impl commits itself; nothing spans two
storage calls. Each operation opens its own short session and commits
it; there is no session that outlives the call.

**Source.** The Storage Layer, Storage Principles; A Storage Impl.

**Look for.** Session or connection objects opened in one storage
method and used in another, passed through a manager, held on `self`,
or created in the constructor. A `begin`, `commit`, or `rollback`
issued anywhere above the storage impl. Manager methods that call two
storage writes and expect both or neither to land, or any unit-of-work,
request-scoped session, or "outer scope commits" pattern.

**Violation.** A manager wraps several storage calls in a transaction
context. A storage impl exposes `commit()` or accepts an externally
opened session per call. A session is created in the constructor and
shared by every method, or otherwise outlives the storage method that
opened it.

**Severity.** medium

## STO-03 Atomicity is a single named interface method

**Principle.** Where atomicity is genuinely unavoidable (a work-queue
claim, a ledger in a system that moves money), it is a single named
interface method, so the interface stays technology-free and the
exception is visible by name.

**Source.** The Storage Layer, Storage Principles; A Storage Impl.

**Look for.** Storage interface methods that claim, settle, or mutate
under a lock: each is one method whose name says what it does
atomically, and the row lock or compare-and-set lives inside that one
method. Locking primitives (`FOR UPDATE`, `SKIP LOCKED`, version
columns) anywhere other than inside one such method.

**Violation.** Locking spread across two interface methods (one to
lock, one to write) so the caller holds the lock between calls. An
atomic operation implemented in a manager by orchestrating several
storage calls. An interface signature that exposes a lock, a session,
or a transaction handle.

**Severity.** medium

## STO-04 Joins stay inside the impl

**Principle.** Joins are avoided but allowed as an implementation
detail. They never leak into the interface.

**Source.** The Storage Layer, Storage Principles.

**Look for.** Storage interface return types: entities, read models,
and tuples of ids and entities, never row tuples or join projections
named after tables. Read models whose shape is defined by a join rather
than by the business question, and impls that join where a second read
would do.

**Violation.** An interface method returns a shape whose fields mirror
a `JOIN` result or a table alias. A join spans two database roles.

**Severity.** medium

## STO-05 No triggers, no database functions

**Principle.** No trigger functions and no hidden magic. No user-defined
functions in the DB. If something happens, it happens in our code.
Every query is written explicitly in its storage class.

**Source.** The Storage Layer, Storage Principles.

**Look for.** Migration SQL containing `CREATE TRIGGER`, `CREATE
FUNCTION`, `CREATE PROCEDURE`, `CREATE RULE`, or generated columns that
compute business values. Storage impls calling stored procedures or
relying on a database-side computed timestamp for `updated_at`, and
column defaults that encode business logic.

**Violation.** A trigger maintains a denormalized column, a counter, or
an audit row. `updated_at` is set by the database rather than by the
manager's copy. A query calls a user-defined function.

**Severity.** medium

## STO-06 IDs are passed top-down, never read back

**Principle.** Every ID is passed top-down. We do not create an object
in the DB and read its ID afterwards. IDs originate above storage, with
`new_id()`.

**Source.** The Storage Layer, Storage Principles; Naming Entities,
Identifiers.

**Look for.** Primary key columns declared with a database default, a
sequence, an identity, or `gen_random_uuid()`. Storage write methods
that return the written entity's id, or that flush and read the row
back to learn it. (How ids are minted at construction is `OM-12`.)

**Violation.** A table's `id` column has `server_default` or
autoincrement. A `write_*` method returns a `UUID` that the caller did
not already hold. An `INSERT ... RETURNING id` whose result the
business layer waits for.

**Severity.** medium

## STO-07 Defaults live in the object model

**Principle.** Defaults are set in the object model. Schema-level
defaults are optional, kept as a convenience for admin and test
operations where an operator writes plain SQL by hand. A default added
to backfill a new column is removed once the backfill is done.

**Source.** The Storage Layer, Storage Principles.

**Look for.** A field whose default exists only on the table column
and not on the entity class. A `server_default` introduced by an `ADD
COLUMN` migration that is still present after the backfill migration.

**Violation.** An entity field is required in the model but the code
depends on the column default to fill it. A backfill default remains on
a column that every write now sets.

**Severity.** low

## STO-08 Storage is swappable through impl/ alone

**Principle.** The storage layer must be swappable. Moving from a
relational DB to a columnar DB on a different technology changes only
`impl/`, never the interfaces or the entities.

**Source.** The Storage Layer, Storage Principles; Namespace Shape.

**Look for.** Interface signatures for technology types: sessions,
engines, connections, ORM row classes, query builders, driver
exceptions. Entities or read models that import from the ORM or the
driver. Storage impl constructors where the session factory or pool is
injected and never surfaced.

**Violation.** A storage interface method takes or returns an ORM row,
a statement, or a driver result. A manager catches a driver exception
by its technology-specific class. Swapping the impl would require
editing the interface or a type in `types/`.

**Severity.** medium

## STO-09 Storage namespace shape

**Principle.** Storage follows the same namespace pattern as the rest
of the object model, scoped under its parent entity namespace: the
interface in `storage/__init__.py`, impls under `storage/impl/`, and
ORM classes under `storage/tables/`, not exposed.

**Source.** The Storage Layer, Namespace Shape.

**Look for.** Each OM namespace with persistence has `storage/`
containing `__init__.py` (the interface), `impl/` (one module per
technology), and `tables/`. Interfaces defined inside `impl/` or table
classes defined next to entity types. (Who may import a table class is
`CON-10`; the in-memory impl is `CON-04`.)

**Violation.** A storage interface lives in `impl/` or in `types/`. A
table class lives outside `storage/tables/`.

**Severity.** medium

## STO-10 One storage root, two impls, every dependency wired there

**Principle.** Storage implementations are assembled behind a single
root that implements `StorageInterface`, with one getter per entity
storage plus `healthcheck` and `close`. Two roots exist from day one,
one over the relational engine and one in memory, and each constructs
every namespace impl and wires cross-storage dependencies between them.
Cross-storage dependencies are injected through the constructor; the
interface is untouched.

**Source.** The Storage Layer, Storage Root; Cross-Storage Dependencies.

**Look for.** `StorageInterface` with `get_<entity>_storage()` per
storage, `healthcheck()`, and `close()`. Higher layers receiving a
`StorageInterface` rather than constructing namespace impls
themselves. Storage impl constructors that take sibling storage
interfaces, with the root passing them in the right order, and a
memory root that constructs the same set of impls as the relational
root.

**Violation.** A manager or container constructs a namespace storage
impl directly. A storage impl reaches a sibling storage through a
global, the root, or an attribute set after construction. The memory
root lacks a getter the relational root has.

**Severity.** medium

## STO-11 Table mixins mirror the OM mixins

**Principle.** Table classes mirror the OM mixins so their definitions
stay focused on what is specific to the entity. The common mixins live
in the shared `tables/` package, with one storage-only addition:
`org_id` rides on `IdentifiableMixin`. A global table composes
`GlobalIdentifiableMixin`, which carries `id` alone. Concrete table
classes compose the mixins their entity has, in the same house-style
order as the OM.

**Source.** The Storage Layer, Defining ORM Classes.

**Look for.** Table classes composing `IdentifiableMixin` (or
`GlobalIdentifiableMixin` for a global table), `NamedMixin`,
`TrackableMixin`, `SoftDeletableMixin` in the OM order, with the
declarative base last. A table's mixin set matching its entity's mixin
set, so an append-only entity's table has no tracking or soft-delete
columns. `id`, `org_id`, `name`, `created_at`, `updated_at`,
`created_by`, `deleted_at`, `deleted_by` redeclared on a concrete
table.

**Violation.** A table redeclares a mixin column by hand or declares
lifecycle columns its entity does not have. Mixins are listed in a
different order from the entity's bases. `org_id` is declared per
table instead of on the identity mixin, or a global table carries
`org_id` at all.

**Severity.** medium

## STO-12 Rows are mutable and never leave the impl

**Principle.** Table classes are mutable by design, so the session can
track writes. This is the one deliberate exception to the OM
immutability rule, and it is bounded: rows never leave the storage
impl.

**Source.** The Storage Layer, Defining ORM Classes.

**Look for.** Return statements in storage impls: every one returns an
entity, a read model, or a plain value, never a row. Row objects passed
to managers, cached, or published on a topic. Row classes marked frozen
or otherwise prevented from in-place change.

**Violation.** A storage method returns a table instance or a list of
them. A row instance is held on a manager or stored in a cache. A table
class is frozen, so updates rebuild rows instead of applying changes in
place.

**Severity.** medium

## STO-13 Column order is part of the model

**Principle.** Every table opens with the columns of the mixins it
composes, in house-style order, and its own columns follow. A new
column is declared at the end of its class so the physical table and
the class stay in step when the column is appended.

**Source.** The Storage Layer, Defining ORM Classes.

**Look for.** Mixin columns carrying the negative `sort_order` bands
that pin the header block to the front. A table class edited in the
same change as an `ADD COLUMN` migration: the new attribute is the last
one in the class.

**Violation.** A new column is inserted in the middle of a table class
while the migration appends it to the table. Mixin columns render after
the domain columns in the initial schema.

**Severity.** low

## STO-14 The three index rules

**Principle.** A feed wants a compound index on `(org_id, id)`, which
sorts by creation time because ids are v7, so a descending index is
never needed. A column that already leads a compound index gets no
single-column index of its own. Index what the SQL filters on, not what
Python filters afterwards; reach for a compound index when a real query
asks for one.

**Source.** The Storage Layer, Defining ORM Classes; Naming Entities,
Identifiers.

**Look for.** Feed tables (events, audit, streams, lists ordered by
creation) carrying `Index(org_id, id)` and queries ordering by `id`.
Any `DESC` index on an id column. A single-column index on `org_id`
next to a compound index that starts with `org_id`. Indexes on columns
no query filters on, or missing on columns every list query filters
on.

**Violation.** A feed orders by `created_at` with its own index instead
of by `id`. Both `ix_<table>_org_id` and `ix_<table>_org_id_id` exist
on one table. An index was added for a filter that happens in Python
after the read.

**Severity.** low

## STO-15 Translation is module-level and mechanical

**Principle.** Module-level helpers (`to_row`, `to_model`, `apply_row`)
cover the one-to-one case, choosing native or JSON dumping by column
type, so a namespace with plain shapes writes no translation code at
all. Custom translation is written only when the row and the entity
diverge, and module-level helpers are preferred over an inheritance
base.

**Source.** The Storage Layer, Translation.

**Look for.** Storage impls calling the shared helpers for reads,
inserts, and in-place updates. Hand-written field-by-field mapping in
an impl whose row and entity have matching field names. A translation
base class that impls inherit from. `apply_row` never touching
`org_id`.

**Violation.** A storage impl reimplements `to_row` or `to_model`
locally for a one-to-one shape. A `TranslatorBase` or mixin carries
per-entity translation that multi-entity storages have to contort
around. An update path rebuilds the row from scratch instead of
applying the entity onto the existing row.

**Severity.** low

## STO-16 One upsert primitive

**Principle.** A shared base provides the one write primitive every
namespace uses: an upsert that reads the existing row by id, applies
the entity onto it or inserts a new one, inserts the outbox row it was
handed beside it, and commits the two together. Last writer wins
by default; an entity whose concurrent edits matter carries a
`version`, and its write is a compare-and-set that raises `Conflict`
when the row moved (optimistic concurrency).

**Source.** The Storage Layer, A Storage Impl; The Business Layer,
Shape of an Operation.

**Look for.** Write methods that are one call to the shared upsert,
and whether a `core`-role write takes the outbox row as a parameter.
Hand-rolled insert-or-update logic repeated across impls. Entities
that carry `version`, and whether their write compares it.

**Violation.** A namespace impl performs its own select-then-insert-
or-update sequence instead of calling the base primitive. An entity
with a `version` whose write overwrites without comparing it, or a
`version` added to every table by default.

**Severity.** medium

## STO-17 Every table has one database role

**Principle.** Every table belongs to exactly one database role and
lives in the schema named after it. A map from table name to role is
the single source of truth: the ORM base derives the schema from it,
each role has its own connection URL defaulting to the shared one, and
the root opens one engine and pool per distinct URL. No cross-role
foreign keys and no cross-role statements. A handoff that follows a
core write (an event row, a work item) is a core row plus an outbox
row in one named atomic method, relayed at once and, after a crash, by
the sweep (the transactional outbox). A database-backed topic bus
connects to the queue role. Analytics never runs in the request path
of any role; it reads a mirror. Every role is backed up on its own
schedule with a rehearsed restore; a soft-deleted row is purged by the
sweep after its retention period; personal data lives in named fields.

**Source.** The Storage Layer, Database Roles.

**Look for.** The table-to-role map: every table present, `schema`
derived from it rather than declared on the class. Statements that name
tables from two roles, and the base class refusing them. Foreign keys
whose target is in another role. Settings exposing one URL per role,
each defaulting to the shared URL, with one engine per distinct URL,
and the database-backed topics impl reading the queue role's URL. The
named atomic method that writes the core row and its outbox row, the
relay after it, and the sweep step that relays what a crash left and
marks the row done. Reporting queries that scan across tenants inside
a request. The backup schedule per role and the restore rehearsal; the
purge step of the sweep and the retention period per entity. Unit
tests asserting the map is complete and that no key or statement
crosses a role.

**Violation.** A table declares its own `schema` or is missing from the
map. A join, foreign key, or transaction spans two roles. A manager
writes the core row and then, in a second statement, the event row or
the work item. The database-backed topic bus connects to a role other
than `queue`. A cross-tenant analytical query runs against the `core`
role in a request handler. A soft-deleted row that is never purged, or
a hard delete outside the purge. The role tests are absent.

**Severity.** high

## STO-18 Migrations are SQL pairs, one chain per role

**Principle.** Migrations live with the OM. A migration is a pair of
hand-written, schema-qualified SQL files with a thin wrapper, one
revision chain and one version table per role, the minute stamp as
sort key and revision id. A migration file is never edited once it has
been applied anywhere. The runner refuses a file that names another
role's table, and refuses to migrate one role when the caller meant
all of them. A migration is compatible with the release before it:
add and backfill in one release, switch the code, drop in a later one
(expand and contract). A metadata-vs-schema check for every role is in
the fast test gate; a downgrade-then-upgrade is in CI.

**Source.** The Storage Layer, Migrations.

**Look for.** `om/migrations/sql/<role>/YYYYMMDDHHMM_<slug>.up.sql`
and `.down.sql` pairs, with a wrapper under `versions/<role>/` that
only calls the SQL runner. Wrappers containing hand-written schema
operations instead of `run_sql`. A diff touching a migration file that
has already been applied in any environment. SQL in one role's chain
naming a table of another role. A migration that drops or renames a
column the release before it still reads. The fast gate running the
metadata-vs-schema check per role, and CI running downgrade then
upgrade of the head.

**Violation.** Two migrations share a revision id, or a chain has two
heads that were merged by editing history. An applied `.up.sql` is
modified rather than followed by a new migration. A column dropped or
renamed in the same release that stops reading it, so a rollout that
runs both versions breaks. A migration lives in a service instead of
with the OM. The check step is missing from the fast gate.

**Severity.** medium
