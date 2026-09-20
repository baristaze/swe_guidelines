---
name: arch-scaffold-entity
description: "Add one entity to an existing object-model namespace the way the Software Design and Architecture Guidelines prescribe: the frozen Pydantic type, the table class and migration, storage interface methods with Postgres and memory impls, manager operations, wire types and router, and tests. Stack: Python (FastAPI, Pydantic, SQLAlchemy)."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make openapi), Bash(uv run:*), Bash(git status:*)
---

# arch-scaffold-entity

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Naming
Entities (Identifiers), The Business Layer (Shape of an Operation), The
Storage Layer (Namespace Shape, Defining ORM Classes, Translation, A
Storage Impl, Database Roles, Migrations), The Network Layer (Service
Interfaces and Impls, Public Types, Realtime at the Edge),
Cross-Cutting Conventions (Exceptions).
A section is read with its own introduction.

## Input

`<namespace> <EntityName> [field:type ...] [--role core|activity|queue|admin] [--no-api]`

Example: `inventory Warehouse address:str timezone:str`. The role
defaults to `core`. Ask in one message for the fields not given and
for the mixins: `Named`? `Trackable`? `SoftDeletable`? The answer
"append-only" means `Identifiable` alone and, unless `--role` says
otherwise, the `activity` role. A mixin is composed only when a
manager operation exercises it: `Trackable` needs an update,
`SoftDeletable` a delete.

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
| `om/src/<root>/om/<ns>/types/<entity>.py`                     | the frozen entity, mixins in house-style order                          |
| `om/src/<root>/om/<ns>/storage/tables/<entities>.py`          | the table class composing the matching mixins; an `activity`-role entity is a feed and composes `FeedIdentifiableMixin` (no single-column `org_id` index) and declares the `(org_id, id)` index; no concrete table redeclares a mixin column |
| `om/migrations/sql/<role>/<stamp>_<entities>.up.sql`          | `CREATE TABLE <role>.<entities>` with the mixin header block first    |
| `om/migrations/sql/<role>/<stamp>_<entities>.down.sql`        | the matching `DROP TABLE`                                              |
| `om/migrations/versions/<role>/<stamp>_<entities>.py`         | the wrapper: `revision = "<stamp>"`, `down_revision` = the role's current head, `run_sql(<role>, ...)` |
| `om/tests/contracts/<entity>_storage.py`                      | the storage contract cases, with a cross-tenant negative, parameterised by a storage fixture; one case per unique key the table declares (a second row under the same key is refused by both impls, so the memory impl refuses what the engine refuses, as Interfaces (Multiple impls per interface) states; on a `SoftDeletable` entity the key is a partial unique index `WHERE deleted_at IS NULL` in the migration, the memory impl refuses only among the living, and the case creates, deletes, and creates again); a named atomic method the entity adds is raced as well as called: two callers at once, exactly one wins, over memory and over Postgres |
| `om/tests/unit/test_<entity>_storage.py`                      | the contract cases over the memory impl                                |
| `om/tests/integration/test_<entity>_storage_postgres.py`      | the same cases over Postgres, marked `integration`                    |
| `om/tests/unit/test_<entity>_manager.py`                      | every operation the manager has, over the memory storage, including an update sent with another `created_by` or a cleared `deleted_at` that sees both stay as stored |
| `<api>/tests/test_<ns>_<entity>_api.py` (unless `--no-api`)   | the routes over the in-process app and memory container                |

`<api>` is the service whose `--namespaces` includes `<ns>`, else
`services/api`, else the API rows are skipped with a note.

## Changed

| File                                                   | Change                                                                         |
|--------------------------------------------------------|--------------------------------------------------------------------------------|
| `om/src/<root>/om/<ns>/storage/__init__.py`             | `read_<entities>(org_id, limit)`, `read_<entity>(org_id, <entity>_id)`, and, for a `core`-role entity, `create_<entity>(org_id, <entity>, outbox_rows: tuple[OutboxRow, ...]) -> bool` (False when the id is already written; nothing changes then) and `write_<entity>(org_id, <entity>, outbox_rows: tuple[OutboxRow, ...])`; for an `activity`-role one, `append_<entity>(org_id, <entity>)` |
| `om/src/<root>/om/<ns>/storage/impl/postgres.py`        | the reads over `select`, ordered by `id`; the write over the base's `_upsert(table, org_id, entity, outbox_rows)`, which inserts the outbox rows in the same commit; the create over the base's `_insert`, which does nothing on an existing id and reports it, the outbox rows landing only when the insert won; the append over the same `_insert` |
| `om/src/<root>/om/<ns>/storage/impl/memory.py`          | the same methods over the in-memory table; the memory base lands the outbox rows in the outbox memory storage the root wired |
| `om/src/<root>/om/storage/roles.py`                     | `"<entities>": DatabaseRole.<ROLE>` in the table-to-role map                   |
| `om/src/<root>/om/<ns>/manager.py`                      | `get_<entities>(ctx, limit)`, `get_<entity>`, `create_<entity>`, plus `update_<entity>` when the entity is `Trackable` and `delete_<entity>` when it is `SoftDeletable`; an append-only entity gets neither |
| `om/src/<root>/om/<ns>/impl/manager.py`                 | the operations: authorize (`Permission.READ` for reads, `Permission.WRITE` for writes), verify (`get_<entity>` on update and delete, raising `NotFound`, a soft-deleted row included; on create, the insert reports an existing id and the operation reads the row back and returns it as stored), copy (on create, the actor from the context, the initial status, and a position when the entity has one, the id and the timestamps left as constructed; on update, `<Entity>.model_validate({**current.model_dump(), **<entity>.model_dump(exclude=PROVENANCE_FIELDS), "updated_at": utcnow(), "updated_by": ctx.user_id})`, starting from the stored row so no caller rewrites who made the row or brings a deleted one back, and validated because it carries a dump; on delete, `deleted_at` and `deleted_by`), write with the tuple holding the row `outbox_row(ctx, "<ns>.<entity>.<created\|updated\|deleted>", <entity>.id, <entity>.model_dump(mode="json"))` builds, carrying the actor, the request id, and the app from the context, and a second row of kind `work.<kind>` when work follows the write, then `relay` per row, then return the copy; an `activity`-role entity's create is `append_<entity>` alone |
| `om/src/<root>/om/exceptions.py` (when a leaf is needed) | `class <Ns>Exception(PlatformException): ...` once, then leaves that multiply-inherit a shape |
| `<api>/.../types/<ns>.py` (unless `--no-api`)           | `<Entity>View`, `Add<Entity>Request`, and `Update<Entity>Request` only when the manager has `update_<entity>` |
| `<api>/.../services/<ns>.py` (unless `--no-api`)        | the operations on `<Ns>ServiceInterface`: list, get, create, and, only when the manager has them, update and delete, each taking `ctx` and the request type and returning the view |
| `<api>/.../impl/<ns>.py` (unless `--no-api`)            | the translation on `<Ns>ServiceImpl`: build the entity from the request, call one manager operation, project the result onto the view; the partial update reads the current entity through the manager's `get_<entity>` and copies the request's set fields onto it before handing the whole entity to `update_<entity>` |
| `<api>/.../routers/<ns>.py` (unless `--no-api`)         | list (with `limit`), get, post, and, only when the manager has them, put and delete routes; each declares the route and its dependencies (the context, and on the post the gateway's `Idempotency-Key`, like every route that writes a durable row), calls one operation of the service impl, and returns what it returns |
| `<api>/.../routers/__init__.py` (when `<ns>` is new to it) | the router added to `all_routers()`                                       |
| `apps/<portal>/src/api/types.ts`, `apps/<portal>/src/queries/<ns>.ts`, `apps/<portal>/src/features/<entities>/` (when a portal exists) | the facade type, the query hooks, and the screen, in the shapes `arch-scaffold-app` defines |

## Procedure

1. Write the type, then the table, then storage, then manager, then
   wire types and router, in that order, so each step has its
   dependency in place.
2. Lists filter `deleted_at IS NULL` only when the entity is
   `SoftDeletable`, in both impls.
3. The service impl builds the entity for `create_<entity>` from the
   request with the id the gateway minted before the idempotency
   marker (`new_id()` only where no gateway is involved) and, when the
   entity is `Trackable`, `utcnow()` and `ctx.user_id` for both
   timestamps and both principals; for `update_<entity>` it reads the
   current entity through the manager's `get_<entity>` and copies the
   request's set fields onto it, an absent field meaning unchanged and
   an explicit null meaning cleared where the field is optional (the
   request carries no `created_at` or `created_by`, and that policy is
   the request type's contract), then hands the whole entity to the
   manager, whose copy starts from the stored row and sets
   `updated_at` and `updated_by`. The router declares the route and
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
