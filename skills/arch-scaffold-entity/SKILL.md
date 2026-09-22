---
name: arch-scaffold-entity
description: "Add one entity to an object-model namespace: the frozen type, the table and migration, storage in Postgres and memory, manager operations, wire types, router, and tests. Python."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make openapi), Bash(uv run:*), Bash(git status:*)
---

# arch-scaffold-entity

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Naming
Entities (Identifiers), The Business Layer (Shape of an Operation), The
Storage Layer (Namespace Shape, Defining ORM Classes, Translation, A
Storage Impl, Database Roles, The Second Fence, Migrations), The Network Layer (Service
Interfaces and Impls, Public Types, Realtime at the Edge),
Documentation as Code (A README at Every Level), Cross-Cutting
Conventions (Exceptions).
A section is read with its own introduction.

## Input

`<namespace> <EntityName> [field:type ...] [--role core|activity|queue|admin] [--scope system|org|identity|both] [--person-column <col>] [--no-api]`

Example: `inventory Warehouse address:str timezone:str`. The role
defaults to `core`. Ask in one message for the fields not given and
for the mixins: `Named`? `Trackable`? `SoftDeletable`? For a
`Trackable` entity, ask too whether concurrent edits matter: when they
do, the entity carries a `version`. Ask which fields the manager owns:
the fields it sets and a caller never writes (a `credential_ref`, a
status its transitions own, a position). The answer
"append-only" means `Identifiable` alone and, unless `--role` says
otherwise, the `activity` role. A mixin is composed only when a
manager operation exercises it: `Trackable` needs an update,
`SoftDeletable` a delete.

The tenancy scope defaults to `org`, as The Storage Layer (The Second
Fence) names the four. `identity` rows belong to an identity and no
tenant; the table composes `IdentityScopedMixin`, which carries `id`
and `identity_id` and no `org_id`, and the policy rests on
`identity_id`. `both` rows
belong to a tenant and a person in it; the table carries `org_id` and
the person column, `--person-column`, `user_id` by default, and the
policy narrows by it. `system` rows belong to the platform: the table
composes `GlobalIdentifiableMixin`, its migration enables no
row-level security and creates no policy, and its storage methods take
no `org_id` and are each listed under
`[tool.arch-check.options.CTX-12] tenantless` with a docstring saying
why. `<SCOPE>` is the scope in upper case.

A `core`-role entity has a handoff: every write lands the core row
and its `OutboxRow`s (from `om/outbox/`, as `arch-scaffold-new`
defines it) in one commit, and the manager relays each at once
through `OutboxRelayInterface.relay(org_id, row)`, which dispatches
on the row's `kind`: an entity change appends the `Event` and
publishes `ENTITY_CHANGED`. Work that follows the write rides a
second outbox row of kind `work.<kind>` in the same tuple and the
same commit, never an `enqueue`
the manager makes itself, because the queue is another role. An
`activity`-role entity is itself a
record: it is appended by a named `append_<entity>` method, never
upserted, and carries no outbox row, because nothing crosses a role.

`<entity>` is the snake-case name, `<entities>` its plural, `<ns>` the
namespace, `<role>` the role, `<stamp>` the minute stamp
`YYYYMMDDHHMM` of the moment the migration is written.

## Created

| File                                                         | Holds                                                                  |
|--------------------------------------------------------------|------------------------------------------------------------------------|
| `om/src/<root>/om/<ns>/types/<entity>.py`                     | the frozen entity, mixins in house-style order; `MANAGER_OWNED_FIELDS`, a tuple naming the fields the manager sets and a caller never writes, empty when there are none; `version: int` when concurrent edits matter |
| `om/src/<root>/om/<ns>/storage/tables/<entities>.py`          | the table class composing the matching mixins; an `activity`-role entity is a feed and composes `FeedIdentifiableMixin` (no single-column `org_id` index) and declares the `(org_id, id)` index; no concrete table redeclares a mixin column |
| `om/migrations/sql/<role>/<stamp>_<entities>.up.sql`          | `CREATE TABLE <role>.<entities>` with the mixin header block first, then the table's policy in the shape its tenancy scope implies, with `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`; a `system`-scoped table gets neither, as The Storage Layer (The Second Fence) states |
| `om/migrations/sql/<role>/<stamp>_<entities>.down.sql`        | the matching `DROP POLICY` and `DROP TABLE`                            |
| `om/migrations/versions/<role>/<stamp>_<entities>.py`         | the wrapper: `revision = "<stamp>"`, `down_revision` = the role's current head, `run_sql(<role>, ...)` |
| `om/tests/contracts/<entity>_storage.py`                      | the storage contract cases, parameterised by a storage fixture; a cross-tenant case per method of the interface, passing another tenant's identifier (another person's for `both`, another identity's for `identity`) and asserting that nothing is found and nothing changes, over the read, the list with its page, every write, and the paths that return early or raise, because the enumerating test reads the signature and only the case reads the query, as The Storage Layer (Namespace Shape) states; one case per unique key the table declares (a second row under the same key is refused by both impls, so the memory impl refuses what the engine refuses, as Interfaces (Multiple impls per interface) states; on a `SoftDeletable` entity the key is a partial unique index `WHERE deleted_at IS NULL` in the migration, the memory impl refuses only among the living, and the case creates, deletes, and creates again); a named atomic method the entity adds is raced as well as called: two callers at once, exactly one wins, over memory and over Postgres |
| `om/tests/unit/test_<entity>_storage.py`                      | the contract cases over the memory impl                                |
| `om/tests/integration/test_<entity>_storage_postgres.py`      | the same cases over Postgres, marked `integration`                    |
| the tree's tenancy policy test (nothing to write)              | it reads the tenancy scope map, so the new table is covered the moment its scope is declared, and it fails until the migration carries the policy |
| `om/tests/unit/test_<entity>_manager.py`                      | every operation the manager has, over the memory storage, including an update sent with another `created_by` or a cleared `deleted_at` that sees both stay as stored, an update sent with another value in each field of `MANAGER_OWNED_FIELDS` that sees each stay as stored, and, on a versioned entity, an update carrying a stale expected version refused with `PreconditionFailed` and the row left as it stood |
| `<api>/tests/test_<ns>_<entity>_api.py` (unless `--no-api`)   | the routes over the in-process app and memory container; on a versioned entity, a `PATCH` whose `If-Match` names a stale version answered `412` and one with no expected version refused as a validation failure |

`<api>` is the service whose `--namespaces` includes `<ns>`, else
`services/api`, else the API rows are skipped with a note. When `<ns>`
is new to `<api>`, its `types/<ns>.py`, `services/<ns>.py`,
`impl/<ns>.py`, and `routers/<ns>.py` do not exist yet, and the
`Changed` rows below create them. `<ns_singular>` is the namespace's
singular in snake case, as `arch-scaffold-namespace` names it.

## Changed

| File                                                   | Change                                                                         |
|--------------------------------------------------------|--------------------------------------------------------------------------------|
| `om/src/<root>/om/<ns>/storage/__init__.py`             | `read_<entities>(org_id, limit)`, `read_<entity>(org_id, <entity>_id)`, and, for a `core`-role entity, `create_<entity>(org_id, <entity>, outbox_rows: tuple[OutboxRow, ...]) -> bool` (False when the id is already written; nothing changes then), or `-> InsertOutcome` when the table declares a unique key besides the id (`INSERTED`, `ID_EXISTS`, or `KEY_EXISTS`, naming the key that collided; nothing changes on a collision), with a read by that key beside it and `write_<entity>(org_id, <entity>, outbox_rows: tuple[OutboxRow, ...])`, which on a versioned entity takes `expected_version` after the entity and writes only where the stored `version` equals it, reporting whether it wrote; for an `activity`-role one, `append_<entity>(org_id, <entity>)`; the scope decides the tenant parameters: `both` takes `org_id` and `user_id` at the front of every method, as The Storage Layer (Namespace Shape) shows for a user-bound scope; `identity` takes `identity_id` in place of `org_id`, and each such method is listed under `[tool.arch-check.options.CTX-12] tenantless` with a docstring saying why, like `system` |
| `om/src/<root>/om/<ns>/storage/impl/postgres.py`        | the reads over `select`, ordered by `id`; the write over the base's `_upsert(table, entity, outbox_rows, org_id=org_id)`, which inserts the outbox rows in the same commit; the create over the base's `_insert` with the same arguments, which does nothing on an existing id or unique key and reports which, the outbox rows landing only when the insert won; the append over the same `_insert`; every statement opens through the funnel with the scope the method takes, by keyword: `_session_for(stmt, org_id=org_id)`, with `user_id=user_id` beside it for `both`, or `_session_for(stmt, identity_id=identity_id)` for `identity`, the same keywords `_upsert` and `_insert` take; both keys are in every `WHERE` clause for `both` |
| `om/src/<root>/om/<ns>/storage/impl/memory.py`          | the same methods over the in-memory table; the memory base lands the outbox rows in the outbox memory storage the root wired |
| `om/src/<root>/om/storage/roles.py`                     | `"<entities>": DatabaseRole.<ROLE>` in the table-to-role map `TABLE_ROLES`, and `"<entities>": TenancyScope.<SCOPE>` in the tenancy scope map `TABLE_SCOPES` beside it, naming the column the policy rests on (`identity_id` for `identity`, `--person-column` for `both`) |
| `om/src/<root>/om/<ns>/README.md`                       | the noun in the product's language: what it is, what can happen to it (the operations the manager gains below), and which rules hold |
| `om/src/<root>/om/<ns>/manager.py`                      | `get_<entities>(ctx, limit)`, `get_<entity>`, `create_<entity>`, plus `update_<entity>` when the entity is `Trackable` (taking `expected_version` after the entity when the entity is versioned) and `delete_<entity>` when it is `SoftDeletable`; an append-only entity gets neither |
| `om/src/<root>/om/<ns>/impl/manager.py`                 | the operations: authorize (`Permission.READ` for reads, `Permission.WRITE` for writes), verify (`get_<entity>` on update and delete, raising `NotFound`, a soft-deleted row included; on create, the insert reports an existing id, or the unique key that collided, and the operation reads the row back by that key and returns it as stored), copy (on create, the actor from the context, the initial status, and a position when the entity has one, the id and the timestamps left as constructed; on update, `<Entity>.model_validate({**current.model_dump(), **<entity>.model_dump(exclude=set(PROVENANCE_FIELDS) \| set(<Entity>.MANAGER_OWNED_FIELDS)), "updated_at": utcnow(), "updated_by": ctx.user_id})`, starting from the stored row so no caller rewrites who made the row, brings a deleted one back, or sets a field the manager owns, and validated because it carries a dump; on a versioned entity the copy sets `version` to `expected_version + 1`, the write is the compare-and-set against the caller's `expected_version`, never a version re-read inside the update, and a write that finds another version raises `PreconditionFailed` (412); on delete, `deleted_at` and `deleted_by`), write with the tuple holding the row `outbox_row(ctx, "<ns>.<entity>.<created\|updated\|deleted>", <entity>.id, <entity>.model_dump(mode="json"))` builds, carrying the actor, the request id, and the app from the context, and a second row of kind `work.<kind>` when work follows the write, then `relay` per row, a relay that never raises, since the write has committed and a failure is left to the sweep, then return the copy; an `activity`-role entity's create is `append_<entity>` alone |
| `om/src/<root>/om/exceptions.py` (when a leaf is needed) | `class <Ns>Exception(PlatformException): ...` once, then leaves that multiply-inherit a shape |
| `<api>/.../types/<ns>.py` (unless `--no-api`)           | `<Entity>View`, `Add<Entity>Request`, and `Update<Entity>Request` only when the manager has `update_<entity>`; neither request carries a field of `MANAGER_OWNED_FIELDS`; on a versioned entity the view carries `version`, and `Update<Entity>Request` an optional `expected_version` |
| `<api>/.../services/<ns>.py` (unless `--no-api`)        | the operations on `<Ns>ServiceInterface`: list, get, create, and, only when the manager has them, update and delete, each taking `ctx` and the request type and returning the view, the create also taking `<entity>_id`, the id the `Idempotency-Key` dependency minted before the marker, which the router passes, and the update, on a versioned entity, the expected version the router read |
| `<api>/.../impl/<ns>.py` (unless `--no-api`)            | the translation on `<Ns>ServiceImpl`: build the entity from the request, call one manager operation, project the result onto the view; the partial update reads the current entity through the manager's `get_<entity>` and copies the request's set fields onto it before handing the whole entity to `update_<entity>`, with the caller's expected version and never the one it just read |
| `<api>/.../routers/<ns>.py` (unless `--no-api`)         | list (with `limit`), get, post, and, only when the manager has them, patch and delete routes; on a versioned entity the get answers the version as an `ETag`, and the `PATCH` takes the expected version from the `If-Match` header or the body's `expected_version`, refusing one that carries neither with `ValidationFailed`, and answering `412` when the version moved; each declares the route and its dependencies (the context, and on the post the gateway's `Idempotency-Key`, like every route that writes a durable row), calls one operation of the service impl, and returns what it returns |
| `<api>/.../routers/__init__.py` (when `<ns>` is new to it) | the router added to `all_routers()`                                       |
| `<api>/.../services/__init__.py`, `<api>/.../impl/__init__.py` (when `<ns>` is new to it) | `get_<ns_singular>_service()` on `ServicesInterface`, and `<Ns>ServiceImpl` constructed over the managers in `ServicesImpl`, so the container wires the new service at `build` |
| `apps/<portal>/src/api/types.ts`, `apps/<portal>/src/queries/<ns>.ts`, `apps/<portal>/src/features/<entities>/` (when a portal exists) | the facade type, the query hooks, and the screen, in the shapes `arch-scaffold-app` defines |

## Procedure

1. Write the type, then the table, then storage, then manager, then
   wire types and router, in that order, so each step has its
   dependency in place.
2. Lists filter `deleted_at IS NULL` only when the entity is
   `SoftDeletable`, in both impls.
3. The service impl builds the entity for `create_<entity>` from the
   request with the id the router passed, minted by the
   `Idempotency-Key` dependency before the marker (`new_id()` only
   where no gateway is involved) and, when the
   entity is `Trackable`, `utcnow()` and `ctx.user_id` for both
   timestamps and both principals; for `update_<entity>` it reads the
   current entity through the manager's `get_<entity>` and copies the
   request's set fields onto it, an absent field meaning unchanged and
   an explicit null meaning cleared where the field is optional (the
   request carries no `created_at` or `created_by`, and that policy is
   the request type's contract), then hands the whole entity to the
   manager, whose copy starts from the stored row, leaves
   `PROVENANCE_FIELDS` and `MANAGER_OWNED_FIELDS` as stored, and sets
   `updated_at` and `updated_by`. On a versioned entity the expected
   version is the caller's, from `If-Match` or `expected_version`,
   passed through untouched; the impl never fills it from the row it
   read, since that would turn the compare-and-set into last writer
   wins. The router declares the route and
   its dependencies and calls that one operation; it translates
   nothing. `delete_<entity>` exists only for a `SoftDeletable` entity
   and copies `deleted_at` and `deleted_by`; the hard delete is the
   sweep's purge, never a route's. An append-only entity has no
   update, no delete, and no `Update<Entity>Request`.
4. After the table and its migration: `make infra-up`, `make migrate`,
   then `make migrate-check`, which compares the ORM metadata with the
   migrated schema; it needs Postgres, so it runs only against the
   compose stack, refused when the effective database URL is not a
   local address.
5. After the routes: `make openapi`, so the committed contract and the
   consuming apps' generated types carry the new views and requests.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
