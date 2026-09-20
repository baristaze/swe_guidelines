# Storage

Group id: `storage`. Covers The Storage Layer and the index-related
rules of Identifiers (in Naming Entities) of `architecture.md`.

This group judges how the storage layer persists and returns entities:
what a storage operation may do, how tables are declared, how rows and
entities translate, how tables are grouped into database roles, and how
the schema moves over time. It leaves entity shapes and identifier
minting to `om`, the `org_id`-first rule, user scoping, and
tenancy-on-write to `context`, interface imports and the in-memory
impl to `contracts`, and caches, topics, and the work queue, its table
and statements included, to `async`. On the outbox and retention the
line is this: storage judges the outbox row's life (written with the
core row, relayed at once, marked done, kept for a retention period,
purged after it) and the retention period of every entity; `async`
judges the sweep that relays, purges, requeues, and resumes (ASY-19).

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

**Principle.** The one justification for a named atomic method is an
invariant two rows must hold together: a work-queue claim, a
reservation and its stock level, a unique membership, a core row and
its outbox row, a ledger that moves money. It is a single named
interface method, so the interface stays technology-free and the
exception is visible by name.

**Source.** The Storage Layer, Storage Principles; A Storage Impl.

**Look for.** Storage interface methods that claim, settle, or mutate
under a lock: each is one method whose name says what it does
atomically, and the row lock or compare-and-set lives inside that one
method. Locking primitives (`FOR UPDATE`, `SKIP LOCKED`, a `WHERE`
that compares a stored version) anywhere other than inside one such
method. The `version` field itself belongs on the entity (STO-22).

**Violation.** Locking spread across two interface methods (one to
lock, one to write) so the caller holds the lock between calls. An
atomic operation implemented in a manager by orchestrating several
storage calls. An interface signature that exposes a lock, a session,
or a transaction handle.

**Severity.** medium

## STO-04 Joins stay inside the impl

**Principle.** Joins are avoided but allowed as an implementation
detail. They never leak into the interface.

**Source.** The Storage Layer, Storage Principles; Database Roles.

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
autoincrement; a `write_*` method returns a `UUID` the caller did not
already hold; an insert returns the id and the business layer waits
for it.

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

**Principle.** Storage impls are assembled behind one root that
implements `StorageInterface`: one getter per namespace storage plus
`healthcheck` and `close`. Two roots exist from day one,
`StoragePostgresImpl` and `StorageMemoryImpl`, named like every other
impl; each constructs every namespace impl and injects cross-storage
dependencies through constructors, the interface untouched.

**Source.** The Storage Layer, Storage Root; Cross-Storage Dependencies.

**Look for.** `StorageInterface` with `get_<ns>_storage()` per
namespace storage (one more per aggregate where a namespace has
several), `healthcheck()`, and `close()`. Higher layers receiving a
`StorageInterface` rather than constructing namespace impls
themselves. Storage impl constructors that take sibling storage
interfaces, with the root passing them in the right order, and a
memory root that constructs the same set of impls as the relational
root.

**Violation.** A manager or container constructs a namespace storage
impl directly, or a storage impl reaches a sibling through a global,
the root, or an attribute set after construction. The memory root
lacks a getter the relational root has. A root named for what it is
rather than as an impl (`Storage`, `PostgresStorage`), so the
technology is not last.

**Severity.** medium

## STO-11 Table mixins mirror the OM mixins

**Principle.** Table classes mirror the OM mixins, so a table declares
only what is specific to its entity. The common mixins live in the
shared `tables/` package, with one storage-only addition: `org_id`
rides on `IdentifiableMixin`. A global table composes
`GlobalIdentifiableMixin`, which carries `id` alone. A concrete table
composes the mixins its entity has, in the OM's house-style order.

**Source.** The Storage Layer, Defining ORM Classes.

**Look for.** Table classes composing `IdentifiableMixin` (or
`GlobalIdentifiableMixin` for a global table), `NamedMixin`,
`TrackableMixin`, `SoftDeletableMixin` in the OM order, with the
declarative base last. A table's mixin set matching its entity's mixin
set, so an append-only entity's table has no tracking or soft-delete
columns. `id`, `org_id`, `name`, `created_at`, `updated_at`,
`created_by`, `updated_by`, `deleted_at`, `deleted_by` redeclared on a
concrete table.

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

## STO-14 The first three index rules

**Principle.** A feed gets a compound index on `(org_id, id)`, which
sorts by creation time because ids are v7; a descending index is never
needed. A column that leads a compound index gets no single-column
index of its own, so a feed table composes `FeedIdentifiableMixin`,
whose `org_id` carries none. Index what the SQL filters on, never what
Python filters afterwards.

**Source.** The Storage Layer, Defining ORM Classes; Naming Entities,
Identifiers.

**Look for.** Feed tables (events, audit, streams, lists ordered by
creation) carrying `Index(org_id, id)` and queries ordering by `id`.
Any `DESC` index on an id column, or a single-column index on `org_id`
next to a compound index that starts with `org_id`. Indexes on columns
no query filters on, or missing on columns every list query filters
on, and a compound index no real query asks for; the fourth rule, the
partial unique index on a soft-deletable table, is STO-26.

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
an impl whose row and entity have matching field names, or a
translation base class that impls inherit from. `apply_row` never
touching `org_id`.

**Violation.** A storage impl reimplements `to_row` or `to_model`
locally for a one-to-one shape. A `TranslatorBase` or mixin carries
per-entity translation that multi-entity storages have to contort
around. An update path rebuilds the row from scratch instead of
applying the entity onto the existing row.

**Severity.** low

## STO-16 Two write primitives: an insert that reports, an upsert

**Principle.** A shared base provides the two write primitives every
namespace uses. The insert, for creates, does nothing on an existing
id and reports it, the outbox row landing only when the insert won.
The upsert, for updates, reads the row by id, applies the entity onto
it, and commits it with the outbox row it was handed.

**Source.** The Storage Layer, A Storage Impl; The Business Layer,
Shape of an Operation.

**Look for.** Create methods that are one call to the shared insert
and update methods that are one call to the shared upsert, and
whether a `core`-role write takes the outbox row as a parameter.
Hand-rolled insert-or-update logic repeated across impls.

**Violation.** A namespace impl performs its own select-then-insert-
or-update sequence instead of calling the base primitive; a create
that goes through the upsert, so a retry overwrites the row and
announces it twice, or a check-then-insert with a window between the
two; a key collision that escapes as a driver error.

**Severity.** medium

## STO-17 Every table has one database role

**Principle.** Every table belongs to one database role, the schema
named after it; a map from table name to role is the single source of
truth, and the ORM base derives the schema from it. No cross-role
foreign keys and no cross-role statements: the base class routes a
statement by the table it names and refuses one that spans roles.

**Source.** The Storage Layer, Database Roles.

**Look for.** The table-to-role map: every table present, `schema`
derived from it rather than declared on the class. Statements that
name tables from two roles and the base class refusing them, and
foreign keys whose target is in another role. Unit tests asserting the
map is complete and that no key or statement crosses a role.

**Violation.** A table declares its own `schema` or is missing from the
map. A join, foreign key, or transaction spans two roles. The role
tests are absent.

**Severity.** high

## STO-18 Migrations are SQL pairs, one chain per role

**Principle.** Migrations live with the OM. A migration is a pair of
hand-written, schema-qualified SQL files with a thin wrapper, one
revision chain and one version table per role, the minute stamp as
sort key and revision id. The runner refuses a file that names another
role's table, and refuses to migrate one role when the caller meant
all.

**Source.** The Storage Layer, Migrations.

**Look for.** `om/migrations/sql/<role>/YYYYMMDDHHMM_<slug>.up.sql`
and `.down.sql` pairs, with a wrapper under `versions/<role>/` that
only calls the SQL runner. Wrappers containing hand-written schema
operations instead of `run_sql`, and SQL in one role's chain naming a
table of another role. The runner's refusal of a single-role run where
every role was meant.

**Violation.** A migration lives in a service instead of with the OM,
or a wrapper carries schema operations of its own. A migration names a
table of another role and the runner accepts it. A runner that
migrates one role and stays silent when the caller meant all.

**Severity.** medium

## STO-19 One URL per role, one engine per URL, and a move that changes no code

**Principle.** Each role has its own connection URL, defaulting to the
shared one, and the storage root opens one engine and pool per
distinct URL. When metrics demand it, a role moves to its own
database: the schema is copied under replication or a dual write until
current, the cut-over is one URL, and the code does not change.

**Source.** The Storage Layer, Database Roles.

**Look for.** Settings exposing one URL per role, each defaulting to
the shared URL, with one engine per distinct URL in the root. The
runbook of a role move: the copy, its window and its rehearsal, and
the cut-over.

**Violation.** One URL for every role with no per-role override; a
root that opens one engine per role even when the URLs agree; a role
move that edits a table class, a statement, or a query; a cut-over
before the copy is current, or with no rehearsal.

**Severity.** medium

## STO-20 A handoff after a core write is a core row plus an outbox row

**Principle.** A handoff after a core write is never a second
statement the manager remembers to make: the core row and an outbox
row land in one named atomic method in the `core` role, relayed at
once or by the sweep on a short interval, the cheaper first step. The
relay is idempotent on the row's key (the transactional outbox).

**Source.** The Storage Layer, Database Roles.

**Look for.** The named atomic method that writes the core row and its
outbox row (the event row or the work item that follows), the relay
after it, and whether the relay dedupes on the row's key. The row left
pending, with `done_at` unset, for the sweep that `async` judges.

**Violation.** A manager writes the core row and then, in a second
statement, the event row or the work item. A relay that is not
idempotent, so relaying a row twice duplicates an event.

**Severity.** high

## STO-21 Analytics reads a mirror; roles are backed up and retained

**Principle.** Cross-tenant analytics never runs in a request path; it
reads a mirror. Every role is backed up and its restore rehearsed, and
a role restored earlier than its siblings is reconciled from the
outbox. A done outbox row outlives the backup schedule, a soft-deleted
row is purged after its entity's retention period, and personal data
lives in named fields.

**Source.** The Storage Layer, Database Roles.

**Look for.** Reporting queries that scan across tenants inside a
request. The backup schedule per role and the runbook that records the
restore rehearsal and the outbox reconciliation, read as documentation;
the retention period of a done outbox row against the backup schedule
of the roles it feeds. The retention period per entity that the purge
reads, and the fields that hold personal data.

**Violation.** A cross-tenant analytical query runs against the `core`
role in a request handler. A role with no backup schedule, or no
runbook recording a restore rehearsal; a done outbox row deleted on
done, or kept shorter than the backup schedule. An entity with no
retention period, a hard delete outside the purge, or personal data
spread over unnamed fields, so erasing a person is a hunt.

**Severity.** medium

## STO-22 Concurrent edits that matter carry a version

**Principle.** Last writer wins by default. An entity whose concurrent
edits matter carries a `version`, the copy increments it, and the
write is a compare-and-set that raises `Conflict` when the row moved
(optimistic concurrency).

**Source.** The Business Layer, Shape of an Operation; The Storage
Layer, A Storage Impl.

**Look for.** Entities that carry `version`, the copy that increments
it, the write behind each, and the `WHERE` that compares the stored
value; entities that carry none and whether a concurrent edit on them
matters.

**Violation.** An entity with a `version` whose write overwrites
without comparing it, or whose copy never increments it; a
compare-and-set that returns quietly instead of raising `Conflict`; a
`version` added to every table by default.

**Severity.** medium

## STO-23 The minute stamp is the revision id, and a collision waits

**Principle.** The minute stamp is the file's sort key and the revision
id, so two authors never negotiate a counter. Two migrations of one
role in the same minute collide on the stamp, and the later one waits
a minute or takes a suffix; two migrations naming the same parent are
a real conflict the tool reports on purpose.

**Source.** The Storage Layer, Migrations.

**Look for.** The stamps in each role's folder and the `revision` and
`down_revision` of each wrapper; two files of one role sharing a
stamp, and how the later one was renamed; a merge that resolved two
heads.

**Violation.** Two migrations of one role share a revision id; a chain
with two heads merged by editing an existing wrapper's
`down_revision`; a counter or a hand-picked id in place of the stamp.

**Severity.** medium

## STO-24 An applied migration is never edited; expand, then contract

**Principle.** A migration file is never edited once it has been
applied anywhere. A migration is compatible with the release before
it, since a rollout runs both: add and backfill in one release, switch
the code, drop in a later one (expand and contract). A
metadata-vs-schema check per role and a downgrade-then-upgrade of the
head run in CI's integration job.

**Source.** The Storage Layer, Migrations.

**Look for.** A migration file that changed after the commit that
added it, per `git log --follow`. A migration that drops or renames a
column the release before it still reads. The integration job running
the metadata-vs-schema check per role and the downgrade-then-upgrade
of the head.

**Violation.** An applied `.up.sql` is modified rather than followed
by a new migration. A column dropped or renamed in the same release
that stops reading it, so a rollout that runs both versions breaks.
The check step or the roundtrip is missing from the integration job.

**Severity.** medium

## STO-25 A stored value object only gains optional fields

**Principle.** A value object stored as JSON is a stored shape: it
only gains optional, defaulted fields. A rename or a removal is a
migration that rewrites the column, expand and contract, before the
class changes; `extra="forbid"` then makes a row the migration missed
a read error, never a silently ignored key.

**Source.** The Storage Layer, Translation.

**Look for.** Every value object dumped into a JSON column and the
history of its fields; for a field renamed or removed, the migration
that rewrote the stored rows; whether the value object keeps
`extra="forbid"`.

**Violation.** A field of a stored value object renamed or removed
with no migration of the column, so rows written before the change
fail to read; a value object relaxed to `extra="ignore"` to make old
rows load; a new field without a default.

**Severity.** medium

## STO-26 A unique key on a soft-deletable table is unique among the living

**Principle.** A uniqueness constraint on a `SoftDeletable` table is a
partial unique index `WHERE deleted_at IS NULL`, so a deleted row
frees its key and the same value can be created again. The memory
impl refuses only among the living, and a contract case creates,
deletes, and creates again.

**Source.** The Storage Layer, Defining ORM Classes.

**Look for.** Every unique index on a table whose class composes
`SoftDeletableMixin`, and its `WHERE` clause; the memory impl's
uniqueness check and whether it skips deleted rows; a contract case
that re-creates after a delete.

**Violation.** A full unique index on a soft-deletable table, so a
deleted slug, email, or membership can never be reused; a memory impl
that refuses a key a deleted row holds while the engine accepts it;
no contract case for the re-creation.

**Severity.** medium
