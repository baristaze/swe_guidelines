---
name: arch-scaffold-entity
description: "Add one entity to an object-model namespace: the frozen type, the table and its migration with its policy, storage in Postgres and memory, manager operations, wire types, routes, a portal screen, and tests, in the shape of the scaffold's own. Python and TypeScript."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make openapi), Bash(uv run:*), Bash(git status:*)
---

# arch-scaffold-entity

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Naming
Entities (Identifiers), The Business Layer (Shape of an Operation), The
Storage Layer (Namespace Shape, Defining ORM Classes, Translation, A
Storage Impl, Database Roles, The Second Fence, Migrations), The Network
Layer (Service Interfaces and Impls, Public Types, Realtime at the
Edge), Documentation as Code (A README at Every Level), Cross-Cutting
Conventions (Exceptions). A section is read with its own introduction.

## Input

`<namespace> <EntityName> [field:type ...] [--role core|activity|queue|admin] [--scope system|org|identity|both] [--person-column <col>] [--no-api]`

Example: `inventory Warehouse address:str timezone:str`. The role
defaults to `core`, the scope to `org`. Ask in one message for the
fields not given, and for:

- the mixins: `Named`? `Trackable`? `SoftDeletable`? A mixin is
  composed only when an operation exercises it: `Trackable` needs an
  update, `SoftDeletable` a delete. "Append-only" means neither, the
  mixins Naming Entities gives such a record, and, unless `--role` says
  otherwise, the `activity` role;
- whether concurrent edits of a `Trackable` entity matter. When they
  do, the entity carries a `version`;
- the fields the manager owns and a caller never writes (a status its
  transitions own, a position, a derived key);
- any unique key besides the id.

## Created

The shape of each file is its sibling for the `File` entity of the
`media` namespace, unless the row names another.

| File | Holds |
|------|-------|
| `om/src/<name>/om/<ns>/types/<entity>.py` | the frozen entity, mixins in house-style order, `MANAGER_OWNED_FIELDS` (with `version` in it on a versioned entity) |
| `om/src/<name>/om/<ns>/storage/tables/<entities>.py` | the table over the matching mixins; the sibling by scope is below |
| `om/migrations/sql/<role>/<stamp>_<entities>.up.sql`, `.down.sql` | the table with its mixin header first, then its policy with `ENABLE` and `FORCE ROW LEVEL SECURITY`; the down drops the policy and the table |
| `om/migrations/versions/<role>/<stamp>_<entities>.py` | `revision = "<stamp>"`, `down_revision` the role's current head, `run_sql(DatabaseRole.<ROLE>, ...)` |
| `om/tests/contracts/<entity>_storage.py` | the storage contract cases, shape `om/tests/contracts/media_storage.py` |
| `om/tests/unit/test_<entity>_storage.py`, `om/tests/integration/test_<entity>_storage_postgres.py` | those cases over memory, and over Postgres under the `integration` marker |
| `om/tests/unit/test_<entity>_manager.py` | every operation over the memory storage |
| `services/api/tests/test_<ns>_<entity>_api.py` (unless `--no-api`) | the routes over the in-process app, shape `services/api/tests/test_media_api.py` |
| `apps/portal/src/features/<entities>/` (when a portal exists and unless `--no-api`) | `<Entities>Page.tsx`, `use<Entities>Vm.ts`, `<entities>Model.ts`, and its test, shape `apps/portal/src/features/settings/` |

`<stamp>` is the minute the migration is written, `YYYYMMDDHHMM`. The
table's sibling by scope, with its policy in its role's first migration:

| Scope | Sibling | The policy |
|-------|---------|------------|
| `org` | `media/storage/tables/files.py` | on `org_id`, with the system login's clause |
| `both` | `tenancy/storage/tables/api_keys.py` | on `org_id`, narrowed by the person column, `user_id` unless `--person-column` names another |
| `system` | `tenancy/storage/tables/identities.py` | none: no row-level security, and every storage method is on the tenantless list |
| `identity` | none; `ScopeKind.IDENTITY` in `storage/scopes.py` | on the identity column against `app.identity_id`; every storage method takes `identity_id` in place of `org_id` and is on the tenantless list |

## Changed

| File | Change |
|------|--------|
| `om/src/<name>/om/<ns>/storage/__init__.py`, `impl/postgres.py`, `impl/memory.py` | the storage operations of the conventions' Names, each with its tenant first by the scope |
| `om/src/<name>/om/<ns>/manager.py`, `impl/manager.py` | the manager operations, and `purge_tenant(ctx)` when the namespace gains its first table |
| `om/src/<name>/om/storage/roles.py`, `scopes.py` | the table in `TABLE_ROLES` and in `TABLE_SCOPES` |
| `om/src/<name>/om/<ns>/README.md` | the noun, what can happen to it, the rules that hold |
| `om/src/<name>/om/exceptions.py` (when a leaf is needed) | `<Ns>Exception(PlatformException)` once, then leaves that multiply-inherit a shape (`NotFound`, `Conflict`) |
| `workers/maintenance/src/<name>/workers/maintenance/main.py` | the namespace in `purges`, and, for a `SoftDeletable` entity, its purge past retention in `across`, as `media` is |
| `services/api/src/<name>/services/api/types/<ns>.py`, `services/<ns>.py`, `services/impl/<ns>.py`, `routers/<ns>.py` (unless `--no-api`) | the views and requests, the service interface, its impl, and the routes, shape `media` |
| `services/api/src/<name>/services/api/services/__init__.py`, `services/impl/root.py`, `gateway/resolve.py`, `routers/__init__.py` (when `<ns>` is new to the API) | the service getter, its impl built over the managers, its `<Ns>Service` alias, and its router in `HOSTED` |
| `apps/portal/src/api/types.ts`, `queries/keys.ts`, `queries/<ns>.ts`, `app/routes.tsx`, `realtime/router.ts` (with the portal screen) | the facade type, a key whose first element is `<entity>`, the query hooks, the route, and `<entity>` in `PUSHED_ENTITIES` |

## Procedure

1. Write the type, the table and its migration, storage, the manager,
   then the wire types and the routes, so each step has what it needs.
2. The migration grants nothing: the role's first migration grants
   every later table by default privilege. A unique key on a
   `SoftDeletable` entity is a partial unique index
   `WHERE deleted_at IS NULL`, and the memory impl refuses a duplicate
   only among the living rows.
3. A create returns `bool`, `False` when the id is already written. With
   a second unique key it returns `InsertOutcome` instead, as
   `create_item` in `om/src/<name>/om/work/storage/` does, and the
   manager reads the row back by the key that collided.
4. Manager operations follow authorize, verify, copy, write, and return
   the copy it wrote. Reads take `Permission.READ`, writes
   `Permission.WRITE`. A `core`-role write carries
   `outbox_row(ctx, "<ns>.<entity>.<created|updated|deleted>", <entity>.id, {})`,
   and a second row when work follows. An `activity`-role entity is
   appended by `append_<entity>` and carries no outbox row. A list is a
   page, `after` and a clamped `limit`, like `get_files`. A get or a
   list leaves out a soft-deleted row, in both impls.
5. On a versioned entity the write is a compare-and-set against the
   caller's expected version, as the orchestrations storage writes
   against `expected_version`. The copy sets `version` to the expected
   version plus one, and a mismatch raises `PreconditionFailed` (412).
   At the edge, the get answers the version as a strong `ETag`,
   `"<version>"`. The `PATCH` takes the expected version from `If-Match`
   or the body's `expected_version`, and refuses a request with neither,
   or with both and different, as `ValidationFailed`. The service impl
   passes the caller's version through and never fills it from the row
   it read, which would make the compare-and-set last writer wins.
6. The service impl translates and never decides. A partial update reads
   the current entity through `get_<entity>`, copies the request's set
   fields onto it (an absent field unchanged, an explicit null cleared
   where the field is optional), and hands the whole entity to
   `update_<entity>`. The create builds the entity with the id the
   `Idem` dependency minted. `delete_<entity>` is a soft delete; the
   hard delete is the sweep's purge, never a route's.
7. The tests hold, beyond one case per operation: for every storage
   method, the case that passes another tenant's id (another person's
   for `both`, another identity's for `identity`); for each unique key,
   a second row refused by both impls; for a named atomic method, two
   callers at once of which exactly one wins, over memory and over
   Postgres; an update sent with another `created_by`, a cleared
   `deleted_at`, or another value of a manager-owned field, which leaves
   each as stored; on a versioned entity, a stale version refused and the
   row left as it stood, and `412` at the edge; and the other tenant
   naming this tenant's row on every route that takes one, answered as
   an id that never existed. The row-level security test reads the scope
   map, so it holds the new table once its scope is declared.
8. After the routes, `make openapi`, so the portal's types and the
   Python client carry them, then the screen.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
