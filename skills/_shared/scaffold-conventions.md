# Scaffold conventions

Every `arch-scaffold-*` skill follows these conventions. A skill's own
file says only what is specific to what it builds: its input, the files
it creates, the files it changes, and the steps that differ.

## Shape of a scaffold skill

Every scaffold skill has the same sections, in this order: `## Input`
(the `$ARGUMENTS` grammar as `<positional> [--flag value]`, required
flags stated in prose, what to ask when something is missing),
`## Created` (a table, columns `File` and `Holds`), `## Changed` (a
table, columns `File` and `Change`), `## Procedure` (numbered, only the
steps that differ from this file), `## Output` (one line pointing here).
Sections are cited by title, as `Title (Subsection, Subsection)`, in
the order the guideline presents them, never by number.

## Before writing anything

0. Find the guideline version. Read `version` in
   `${CLAUDE_SKILL_DIR}/../../.claude-plugin/plugin.json` and the first
   release heading of `${CLAUDE_SKILL_DIR}/../../CHANGELOG.md`. They
   agree on a release, or the changelog lists changes under
   `Unreleased` and the copy is a snapshot between releases. Name what
   was found in the output, and pin only a release the two agree on;
   a snapshot is pinned by hand by the person, after
   `/plugin marketplace update` and `/plugin update`.
1. Find the root package. Read `om/pyproject.toml`; the import root is
   the folder under `om/src/`. Call it `<root>` below. When
   bootstrapping a system, `<root>` is the argument the user gave.
   Never assume `platform`.
2. Find the house style. Open one existing namespace, one storage impl,
   one router, and one test, and match their naming, import order, and
   docstring habits. The guideline decides the shape; the repository
   decides the spelling. When bootstrapping there is nothing to open;
   the guideline's snippets are the house style.
3. Read the sections of `architecture.md` the skill names, in full.
4. Check every path in the skill's `Created` table. A path that exists
   is a collision: stop and say so; never overwrite. A migration stamp
   that already exists in the role's folder is a collision too.
5. Ask for anything the input lacks in one message, then proceed.

## Naming

- Manager and storage interfaces are named after the namespace:
  `InventoryManagerInterface`, `InventoryStorageInterface`,
  `InventoryStoragePostgresImpl`, `InventoryStorageMemoryImpl`,
  `get_inventory_storage()`. A namespace with several aggregates may
  add one storage interface per aggregate, named after the aggregate.
- Manager operations read `get_<entities>`, `get_<entity>`,
  `create_<entity>`, `update_<entity>` (only when the entity is
  `Trackable`), `delete_<entity>` (only when it is `SoftDeletable`); an
  append-only entity has neither.
- Storage operations read `read_<entities>`, `read_<entity>`,
  `write_<entity>`.
- Wire types read `<Entity>View`, `Add<Entity>Request`, and, only when
  the manager has `update_<entity>`, `Update<Entity>Request`, on the
  `View` and `RequestBody` bases.
- The one handler interface for background work is
  `WorkHandlerInterface`; impls are `<Kind>HandlerImpl`.

## While writing

- Every entity is frozen and composes the mixins it needs in
  house-style order (`Identifiable`, `Named`, `Trackable`,
  `SoftDeletable`), each an independent opt-in that a manager operation
  exercises; an append-only record is `Identifiable` alone. Ids come
  from `new_id()`, timestamps from `utcnow()`. Entity fields are
  tuples, frozen models, and `Mapping`, never `list` or `dict`; a copy that
  carries caller input goes through `model_validate` before it is
  written, because `model_copy` does not validate.
- Every manager and service operation takes `ctx: OpContext` first;
  every storage call takes `org_id: UUID` first. The exceptions are the
  ones The Business Layer and The Storage Layer name (principal-less
  operations that produce a
  context, global tables, cross-tenant sweeps), each documented in its
  docstring and listed in the repository's exceptions test.
- Every interface is an `ABC` whose methods are `@abstractmethod` with
  `...` bodies; every impl subclasses it; every dependency is a
  constructor parameter typed by interface.
- Two storage impls always: relational and memory. One contract test
  module under `tests/unit/` runs the memory impl; `tests/integration/`
  reuses the same cases against Postgres under the `integration`
  marker. Both impls sort by the `UUID` value, never by its string.
- Tests are counted in cases, not files: one contract case per storage
  method (the read after the write, the filter, the tenant that sees
  nothing), one refusal per authorization rule a manager states, one
  test per rate-limited route, one per exit code of a command, one per
  capability of the infra root over its local impl. A test file with
  one round trip is a placeholder.
- Every write follows authorize, verify, copy (an update sets
  `updated_at` and `updated_by` in the copy; a create copies nothing),
  write, and returns the copy it wrote. A `core`-role write lands the
  core row and its `OutboxRow` in one storage method and the manager
  relays the row at once. The caller constructs the entity whole and hands it to
  `create_<entity>`; the one exception is an entity that carries a
  server-minted secret (an API key), whose `create_` takes the fields
  and returns an `Issued...` shape once.
- Feeds get a compound index on `(org_id, id)` and no single-column
  index on a column that already leads a compound one.
- A namespace's exception family, when one is needed, is
  `class <Ns>Exception(PlatformException): ...` with no attributes;
  leaves multiply-inherit a shape (`NotFound`, `Conflict`, ...) so the
  status and code come from the shape.
- Wire types are hand-written; routers translate and never decide;
  list routes take a server-clamped `limit`.
- A workspace member that depends on another declares it under
  `[tool.uv.sources] <root>-om = { workspace = true }` and is listed in
  the root's `[tool.uv.workspace] members`.
- A runtime, tool, image, or library a scaffold adds is at its latest
  stable release, the current active LTS line where one exists, as
  Technology Choices and How to Override Them (Versions) states. A
  version the scaffold cannot confirm is named in its output.
- No em-dashes, no placeholder files (a module exists when it has
  content), no `TODO` left behind, no dead imports.

## Changing existing files

A file in the `Changed` table is edited by appending or by inserting
one entry in an existing list (a getter on a root, a field on the
managers object, a router in `all_routers()`, a row in the role map, a
member in the workspace). Nothing is reordered or removed. Roots that
every scaffold touches: the storage root and both of its impls, the
business root, the routers list, the role map, the workspace members,
`scripts/dev.sh`, and the local compose file when a process needs a
container.

## After writing

1. Run the repository's fast gate (`make check` or its equivalent), and
   the migration check when a table was added. Fix failures the
   scaffold introduced. Report pre-existing failures and stop; do not
   edit unrelated files.
2. Print the guideline version the skill ran from (release, or
   snapshot), then the list of files created and changed, one per
   line, followed by the commands that were run and their outcome.
   Nothing else.

Never commit. Scaffolding produces a working tree for a person to
review.
