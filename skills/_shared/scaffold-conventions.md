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
   `${CLAUDE_SKILL_DIR}/../../.claude-plugin/plugin.json`. That is the
   last release in this copy. A copy installed from `main` can carry
   changes made after it, because the version moves only when a
   release is cut. Name the version in the output as "`<version>`, or a
   later snapshot of main", and pin that release.
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

- One naming rule: interfaces and getters are named after the
  namespace in the singular, operations after the entity, handlers
  after the work kind. Manager and storage interfaces read
  `InventoryManagerInterface`, `InventoryStorageInterface`,
  `InventoryStoragePostgresImpl`, `InventoryStorageMemoryImpl`; the
  storage root has one getter per namespace storage,
  `get_inventory_storage()`, `get_order_storage()`. A namespace with
  several aggregates may add one storage interface per aggregate,
  named after the aggregate. The manager impl is `impl/manager.py`.
  The storage roots are `StoragePostgresImpl` and
  `StorageMemoryImpl`, named like every other impl.
- Manager operations read `get_<entities>`, `get_<entity>`,
  `create_<entity>`, `update_<entity>` (only when the entity is
  `Trackable`), `delete_<entity>` (only when it is `SoftDeletable`); an
  append-only entity has neither.
- Storage operations read `read_<entities>`, `read_<entity>`,
  `create_<entity>`, `write_<entity>`, and, on an append-only entity,
  `append_<entity>` in place of the last two.
- Wire types read `<Entity>View`, `Add<Entity>Request`, and, only when
  the manager has `update_<entity>`, `Update<Entity>Request`, on the
  `View` and `RequestBody` bases.
- The one handler interface for background work is
  `WorkHandlerInterface`; impls are `<Kind>HandlerImpl`, `<Kind>` the
  work kind in CamelCase (`NOTIFY_SHIPMENT` gives
  `NotifyShipmentHandlerImpl`).

## While writing

- Every entity is frozen and composes the mixins it needs in
  house-style order (`Identifiable`, `Named`, `Created` or
  `Trackable`, `SoftDeletable`), each an independent opt-in that a
  manager operation exercises; an append-only record is `Identifiable`
  alone; a row the platform writes for itself (the outbox row, the
  marker, the socket ticket) is `Created`, its later stamp a field
  named for what happened. Ids come from `new_id()`, timestamps from
  `utcnow()`. Entity fields are tuples and frozen models, never `list`
  or `dict`; a mapping field is the base module's `FrozenMapping`,
  whose validator descends, wrapping a nested mapping and turning a
  nested list into a tuple, its
  empty default `Field(default_factory=dict, validate_default=True)`.
  A copy that carries a dump, the caller's fields above all, is
  rebuilt from a dict, `model_validate({**current.model_dump(),
  **changes})`; `model_copy(update=...)` is only for values
  constructed of the field's own type, because it does not validate
  and leaves a dumped value object a dict.
- Every manager and service operation takes a context first: `ctx:
  OpContext` for a tenant operation, `rctx: RequestContext` for the
  transitions that produce a stronger stage (sign-in, claim, sweep),
  `ictx: IdentityContext` for the exchange and the operator admission,
  `OperatorContext` for an operation on the operator plane,
  a scope (`ProvenanceScope`, `ActorScope`, `TenantScope`,
  `CredentialScope`) for a helper or an edge concern that needs less.
  Every storage call takes `org_id: UUID` first. The exceptions are the
  ones The Business Layer and The Storage Layer name (the outbox
  handoff that takes `(org_id, row)`, global tables, cross-tenant
  sweeps), each documented in its docstring and listed under
  `[tool.arch-check.options.CTX-12] tenantless` in the root
  `pyproject.toml`, which `arch-check` holds both ways. The manager
  methods that take the request stage are listed in the repository's
  request-stage test. Both read signatures; what says the tenant is
  used is the cross-tenant case beside each method, below.
- The predicate in the query is the fence, and the database policy is
  the second fence, taken by default. Every table declares its tenancy
  scope (`system`, `org`, `identity`, `both`) in one map,
  `TABLE_SCOPES`, beside the role map, `TABLE_ROLES` (the names
  `arch-check` reads by default); the migration that creates the
  table creates its policy, with `ENABLE` and `FORCE ROW LEVEL
  SECURITY`; and the Postgres base
  opens every session through one funnel that takes `org_id` and an
  optional `user_id` and sets `app.org_id`, `app.user_id`, and
  `app.identity_id` with `set_config(..., true)`, so the settings die
  with the transaction. `EMPTY_UUID` as the `org_id` is the system
  scope, passed explicitly and never a default, by the methods the
  `tenantless` list enumerates. Nothing in a manager or an impl assumes
  the policy is there. The login the application connects with is
  never a superuser and never carries `BYPASSRLS`, and an integration
  test asserts that on the live connection, beside the one that reads
  `pg_class` and `pg_policies` for every table in the scope map, as
  The Storage Layer (The Second Fence) states.
- Every interface is an `ABC` whose methods are `@abstractmethod` with
  `...` bodies; every impl subclasses it; every dependency is a
  constructor parameter typed by interface.
- Two storage impls always: relational and memory. The contract cases
  live in one module under `tests/contracts/`; a module under
  `tests/unit/` runs them against the memory impl and one under
  `tests/integration/` runs the same cases against Postgres under the
  `integration` marker. Both impls sort by the `UUID` value, never by
  its string. Every storage method gets a case that passes another
  tenant's identifier and asserts that nothing is found and nothing
  changes: reads and writes, the list and the page, the bulk write,
  and the paths that return early or raise. A method added later
  arrives with its case, as The Storage Layer (Namespace Shape) and
  Cross-Cutting Conventions (Tests) state.
- One manager impl. The memory storage root under it is its twin. A
  namespace that fronts something a caller cannot conjure (a payment
  processor, a carrier, a model provider) reaches it through a
  provider client with a real impl and a deterministic twin under
  `integrations/`, so the one manager impl, wired over the twin, runs
  with no account and no network, and needs no memory impl of its
  own.
- Tests are counted in cases, not files: one contract case per storage
  method (the read after the write, the filter, the tenant that sees
  nothing), one race per named atomic method (two callers at once,
  exactly one wins, the same case over memory and over Postgres), one
  refusal per authorization rule a manager states, one test per
  rate-limited route, one per exit code of a command, one per
  capability of the infra root over its local impl, and one
  build-once test per root. Every site that builds a stage above the
  request stage is listed under `[tool.arch-check.options.CTX-26]
  sites`, and `arch-check` fails on a site the list does not name. A test
  file with one round trip is a placeholder.
- Every write follows authorize, verify, copy (an update starts from
  the stored row: the caller's entity supplies the fields a caller may
  change, `model_dump(exclude=set(PROVENANCE_FIELDS))`, the copy is
  `model_validate` over the two dumps and sets
  `updated_at` and `updated_by`, so no caller rewrites who made a row
  or brings a deleted one back; a create sets what the manager
  decides, the actor from the context and the initial state, and
  leaves the id and the timestamps as constructed), write, and
  returns the copy it wrote. Authorize is
  `ctx.require(<permission>)` as the first line of every mutating
  manager operation, before any read, the ones a worker calls
  included (complete, fail, defer, release, extend the lease), as The
  Business Layer (Shape of an Operation) states. A `core`-role write
  lands the core row and its `OutboxRow`s in one storage method,
  `outbox_rows: tuple[OutboxRow, ...]`, and the manager relays each at
  once; a row comes from `outbox_row(ctx, kind,
  target_id, payload)`, so it carries the actor, the request id, the
  trace context, and the app of the write. The caller constructs the entity whole and hands it to
  `create_<entity>`; the one exception is an entity that carries a
  server-minted secret (an API key), whose `create_` takes the fields
  and returns an `Issued...` shape once, and whose rerun finds the
  row, re-mints the secret on it in the same named atomic write, its
  guard the attempt the idempotency marker holds, and
  returns a fresh `Issued...` with the same id.
- Feeds get a compound index on `(org_id, id)` and no single-column
  index on a column that already leads a compound one.
- A namespace's exception family, when one is needed, is
  `class <Ns>Exception(PlatformException): ...` with no attributes;
  leaves multiply-inherit a shape (`NotFound`, `Conflict`, ...) so the
  status and code come from the shape.
- Wire types are hand-written; a router declares its route and its
  dependencies, calls one operation of the service impl, and returns
  what it returns; the impl translates and never decides; list routes
  take a server-clamped `limit`. A partial update is the impl's
  translation: it reads the current entity through the manager's
  `get_<entity>`, copies the request's set fields onto it, an absent
  field unchanged and an explicit null cleared where the field is
  optional, and hands the whole entity to the manager; the request
  type states that policy, and no manager sees a partial.
- Every creating route declares the gateway's `Idempotency-Key`
  dependency, in every namespace, so a retried create returns the
  stored response, as The Network Layer (The Gateway) states for a
  creating `POST`. Creating is what the request leaves behind, not
  what it answers with: a `POST` that writes a durable row declares
  the dependency whether it answers 201 with the row or 202 with the
  id of work now running.
- A workspace member that depends on another declares it under
  `[tool.uv.sources] <root>-om = { workspace = true }` and is listed in
  the root's `[tool.uv.workspace] members`.
- A runtime, tool, image, or library a scaffold adds is at its latest
  stable release, the current active LTS line where one exists, as
  Technology Choices and How to Override Them (Versions) states. That
  release is one a patch release already sits behind, never one
  published today, so a scaffold run on a `.0` day pins what
  `arch-upgrade-deps` would keep and not what it would roll back. A
  version the scaffold cannot confirm is named in its output.
- No placeholder files (a module exists when it has content), no
  `TODO` left behind, no dead imports.
- Every folder that is an abstraction level carries a README at that
  level, in that level's language: `om/README.md` names the nouns and
  how they relate, for a reader with no code, and carries no
  developer instruction and no operator instruction;
  `om/src/<root>/om/<ns>/README.md` one level down, one per namespace,
  saying what its nouns are, what can happen to them, and which rules
  hold, which the namespace scaffold writes and the entity scaffold
  extends; `deployment/README.md` says how it runs;
  `ops/README.md` says how it is operated. `llms.txt` at the root
  lists what each audience is served, one section per audience, one
  link per document; a document is exposed by being listed, never by
  its folder.
- The ops package is a workspace member like any other, `<root>-ops`,
  package `<root>.ops`, binary `<root>-ops`. It rides
  `clients/python/` and the operator plane and drives the edge, never
  a manager, so it is written after the client exists. The signals it reads back go through one interface with
  a local impl over the `devx` twins and a cloud impl over the
  cloud's own APIs, so a run against `local` proves the same path
  the cloud runs. The nine project-local skills under
  `.claude/skills/` are copied from
  `skills/_shared/ops-skills/` with `acme` replaced by `<root>`.
  Every one takes `--env staging|production`, and every one but
  create and nuke also takes `local`. Each holds the
  credential of the role Operations (Operational Skills) gives it.
  The investigator and supporter skills hold the read-only
  investigate profile of their environment and read the owner-only
  env file `~/.config/<root>/ops/<env>.env`, except
  `ops-infra-as-code`, which plans against the cloud and reads no env
  file. `make seed` writes `local.env`. Create and nuke hold the
  administrator profile alone. The traffic and stress skills have no
  role: `ops-simulate-traffic` and `stress-test-run` read the env
  file for the provisioner identity, and hold the investigate profile
  only to read the signals back from a cloud environment;
  `stress-test-create-or-update` writes a file and holds no profile
  and no env file.

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

1. Run the repository's fast gate (`make check` or its equivalent).
   When a table was added, bring the stack up and migrate first
   (`make infra-up`, then `make migrate`), then run
   `make migrate-check`, which compares the ORM metadata with the
   migrated schema per role and needs Postgres. When a route was added, run
   `make openapi`, so the committed contract and the consuming apps'
   generated types carry it. A single tool runs through the workspace
   (`uv run`, `pnpm run`), never through a global install. Fix
   failures the scaffold introduced. Report pre-existing failures and
   stop; do not edit unrelated files.
2. Print the guideline version the skill ran from (the release, or a
   later snapshot of main), then the list of files created and
   changed, one per line, followed by the commands that were run and
   their outcome.
   Nothing else. The list comes from
   `git status --porcelain --untracked-files=all`, which names every
   new file rather than the folder that holds it. Every scaffold runs
   in a repository: the others in a project that is one already, and
   `arch-scaffold-new` initializes one before it writes anything.

Never commit. Scaffolding produces a working tree for a person to
review.
