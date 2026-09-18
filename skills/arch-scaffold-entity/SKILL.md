---
name: arch-scaffold-entity
description: "Add one entity to an existing object-model namespace the way the Software Design and Architecture Guidelines prescribe: the frozen Pydantic type, the table class and migration, storage interface methods with Postgres and memory impls, manager operations, wire types and router, and tests. Stack: Python (FastAPI, Pydantic, SQLAlchemy)."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make test-unit), Bash(make openapi), Bash(uv run:*), Bash(git status:*), Bash(git diff:*)
---

# arch-scaffold-entity

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Naming
Entities (Identifiers), The Business Layer (Shape of an Operation), The
Storage Layer (Namespace Shape, Defining ORM Classes, A Storage Impl,
Database Roles, Migrations), The Network Layer (Public Types).

## Input

`<namespace> <EntityName> [field:type ...] [--role core|activity|queue|admin] [--no-api]`

Example: `inventory Warehouse address:str timezone:str`. The role
defaults to `core`. Ask in one message for the fields not given and
for the mixins: `Named`? `Trackable`? `SoftDeletable`? The answer
"append-only" means `Identifiable` alone. A mixin is composed only
when a manager operation exercises it: `Trackable` needs an update,
`SoftDeletable` a delete.

`<entity>` is the snake-case name, `<entities>` its plural, `<ns>` the
namespace, `<role>` the role, `<stamp>` the minute stamp
`YYYYMMDDHHMM` of the moment the migration is written.

## Created

| File                                                         | Holds                                                                  |
|--------------------------------------------------------------|------------------------------------------------------------------------|
| `om/src/<root>/om/<ns>/types/<entity>.py`                     | the frozen entity, mixins in house-style order                          |
| `om/src/<root>/om/<ns>/storage/tables/<entities>.py`          | the table class composing the matching mixins; a feed composes the feed variant of the identifiable mixin (no single-column `org_id` index) and declares the `(org_id, id)` index; no concrete table redeclares a mixin column |
| `om/migrations/sql/<role>/<stamp>_<entities>.up.sql`          | `CREATE TABLE <role>.<entities>` with the mixin header block first    |
| `om/migrations/sql/<role>/<stamp>_<entities>.down.sql`        | the matching `DROP TABLE`                                              |
| `om/migrations/versions/<role>/<stamp>_<entities>.py`         | the wrapper: `revision = "<stamp>"`, `down_revision` = the role's current head, `run_sql(<role>, ...)` |
| `om/tests/contracts/<entity>_storage.py`                      | the storage contract cases, with a cross-tenant negative, parameterised by a storage fixture |
| `om/tests/unit/test_<entity>_storage.py`                      | the contract cases over the memory impl                                |
| `om/tests/integration/test_<entity>_storage_postgres.py`      | the same cases over Postgres, marked `integration`                    |
| `om/tests/unit/test_<entity>_manager.py`                      | every operation the manager has, over the memory storage               |
| `<api>/tests/test_<ns>_<entity>_api.py` (unless `--no-api`)   | the routes over the in-process app and memory container                |

`<api>` is the service whose `--namespaces` includes `<ns>`, else
`services/api`, else the API rows are skipped with a note.

## Changed

| File                                                   | Change                                                                         |
|--------------------------------------------------------|--------------------------------------------------------------------------------|
| `om/src/<root>/om/<ns>/storage/__init__.py`             | `read_<entities>(org_id, limit)`, `read_<entity>(org_id, <entity>_id)`, `write_<entity>(org_id, <entity>, outbox_row)` on the interface, the write landing the core row and its outbox row in one named atomic method |
| `om/src/<root>/om/<ns>/storage/impl/postgres.py`        | the three methods over `_upsert` (plus the outbox insert in the same statement) and `select`, ordered by `id` |
| `om/src/<root>/om/<ns>/storage/impl/memory.py`          | the same three methods over the in-memory table                                |
| `om/src/<root>/om/storage/roles.py`                     | `"<entities>": DatabaseRole.<ROLE>` in the table-to-role map                   |
| `om/src/<root>/om/<ns>/manager.py`                      | `get_<entities>(ctx, limit)`, `get_<entity>`, `create_<entity>`, plus `update_<entity>` when the entity is `Trackable` and `delete_<entity>` when it is `SoftDeletable`; an append-only entity gets neither |
| `om/src/<root>/om/<ns>/impl/manager.py`                 | the operations: authorize, verify, copy (`updated_at` and `updated_by`), write the core row and its outbox row through the storage method, return the copy; the outbox relay records the event and publishes |
| `om/src/<root>/om/exceptions.py` (when a leaf is needed) | `class <Ns>Exception(PlatformException): ...` once, then leaves that multiply-inherit a shape |
| `<api>/.../types/<ns>.py` (unless `--no-api`)           | `<Entity>View`, `Add<Entity>Request`, and `Update<Entity>Request` only when the manager has `update_<entity>` |
| `<api>/.../routers/<ns>.py` (unless `--no-api`)         | list (with `limit`), get, post, and, only when the manager has them, put and delete routes that translate and call the manager |
| `<api>/.../routers/__init__.py` (when `<ns>` is new to it) | the router added to `all_routers()`                                       |
| `apps/<portal>/src/api/types.ts`, `apps/<portal>/src/queries/<ns>.ts`, `apps/<portal>/src/features/<entities>/` (when a portal exists) | the facade type, the query hooks, and the screen, in the shapes `arch-scaffold-app` defines |

## Procedure

1. Write the type, then the table, then storage, then manager, then
   wire types and router, in that order, so each step has its
   dependency in place.
2. Lists filter `deleted_at IS NULL` only when the entity is
   `SoftDeletable`, in both impls.
3. The router builds the entity for `create_<entity>` from the request
   with `new_id()`, `utcnow()`, and `ctx.user_id`; for
   `update_<entity>` it reads the current entity through
   `get_<entity>` and copies the request's fields onto it (the request
   carries no `created_at` or `created_by`), and the manager copies
   `updated_at` and `updated_by`; `delete_<entity>` exists only for a
   `SoftDeletable` entity and copies `deleted_at` and `deleted_by`;
   the hard delete is the sweep's purge, never a route's. An
   append-only entity has no update, no delete, and no
   `Update<Entity>Request`.
4. Run the migration check for `<role>` after the fast gate; it needs
   Postgres, so it is the integration check against the compose stack
   (`make test-integration`, or the migration CLI's `check`).

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
