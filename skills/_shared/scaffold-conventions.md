# Scaffold conventions

Every `arch-scaffold-*` skill follows these. A skill's own file says
what it adds: its input, the files it creates and changes, and the
steps that differ.

## The shape is a file

Every file a scaffold writes has a sibling that shows its shape. In a
tree copied from the scaffold, the sibling is the tree's own file at the
path the skill names. When the tree has no such file, it is the
plugin's copy, `../../scaffold/acme_root/<path>` from the skill's
folder, with `acme` read as the tree's name. Read the sibling, its imports, and
its test before writing. Copy its shape, not its domain. A skill states
only what an agent gets wrong from the files alone.

`<name>` is the tree's name, the folder under `om/src/`. Its forms are
snake case for the package, the logins, and the paths (`free_press`),
kebab case for distributions, cloud resources, and domains
(`free-press`), the upper snake prefix for the environment
(`FREE_PRESS_`), and Title case for prose. Never assume `acme` in a
copy.

## Before writing anything

1. Read `version` in `../../.claude-plugin/plugin.json`, from the
   skill's folder as `realpath` resolves it, and name it in the output
   as "`<version>`, or a later snapshot of main".
2. Read the sections of `architecture.md` the skill names, then the
   sibling files its steps name.
3. Check every path the skill creates. A file that exists, or a folder
   that holds anything, is a collision: stop and say so, and never
   overwrite. An empty folder is no collision; the skill writes into
   it. A migration stamp already used in its role's folder takes the
   next minute.
4. Ask for everything the input lacks in one message, then proceed.
   When no one answers, as in an unattended run, decide each from the
   product's spec or description and the sibling files, name each
   choice in the output after the files, and go on.

## Names

- A namespace `<ns>` (plural, snake case) has a singular: `<Ns>` in
  CamelCase and `<ns_singular>` in snake case (`orders` gives `Order`
  and `order`). Ask when the singular is not a plain one. Its
  interfaces are `<Ns>ManagerInterface` and `<Ns>StorageInterface`, its
  impls `<Ns>ManagerImpl`, `<Ns>StoragePostgresImpl`, and
  `<Ns>StorageMemoryImpl`, and its getters `get_<ns_singular>_storage()`
  and `get_<ns_singular>_service()`. The field on `Managers` is `<ns>`.
- An entity `<Entity>` has `<entity>` and `<entities>` in snake case.
  Manager operations are `get_<entities>`, `get_<entity>`,
  `create_<entity>`, `update_<entity>` (only on a `Trackable` entity),
  and `delete_<entity>` (only on a `SoftDeletable` one). Storage
  operations are `read_<entities>`, `read_<entity>`, `create_<entity>`,
  and `write_<entity>`, and an append-only record has `append_<entity>`
  in place of the last two. Wire types are `<Entity>View`,
  `Add<Entity>Request`, and `Update<Entity>Request` (only beside
  `update_<entity>`).
- A work kind is upper snake case, and its handler is
  `<Kind>HandlerImpl` (`NOTIFY_SHIPMENT` gives
  `NotifyShipmentHandlerImpl`).
- A cloud resource takes the kebab form of the name. The production
  environment is `production` in every name a process, a role, a
  profile, or a resource reads; only its Terraform folders are `prod/`.

## What the files do not say

Each of these holds in every file a scaffold writes, and each is where
a copy of the shape still goes wrong.

- **The context comes first.** A manager or service operation takes its
  stage first: `ctx: TenantContext` for a tenant, `rctx` on the request
  stage for a transition or the sweep, `ictx` for an identity before a
  tenant, and `octx` on the operator plane. A storage method takes
  `org_id: UUID` first. A method that takes no tenant says why in its
  docstring and is listed under `[tool.arch-check.options.CTX-12]
  tenantless` in the root `pyproject.toml` and in
  `om/tests/unit/test_storage_exceptions.py`. A site that builds a
  stage above the request stage is listed under
  `[tool.arch-check.options.CTX-26] sites` and in
  `om/tests/unit/test_stage_construction.py`.
- **An operation goes where its duty is.** In a namespace whose manager
  delegates, a new operation goes to the delegate whose duty it is, and
  to the manager itself only when no delegate's duty covers it, as a
  transition or the sweep. `arch-check` names a manager interface past
  twenty operations, a delegate's too, for a review, and fails none by
  its count (CON-01). A project sets its own number under
  `[tool.arch-check.options.CON-01]` in the root `pyproject.toml`, as
  `review_threshold` or, by the key's other name, `max_operations`.
  Read it there; with neither key, it is twenty.
- **A delegate takes a duty that is added.** When the operations a
  scaffold adds would take a manager past that number, the scaffold
  decides where they go from two things it has: the request's own words
  for what the operations are for, and the callers this run writes for
  them. They go to a new delegate, as `tenancy` has `credentials`, when
  the request names a duty of their own and the callers written for
  them are none of those that call the manager's operations: another
  app, another worker, another router. They go on the manager, whatever
  its count, when the request extends an entity or a duty the manager
  holds, or when a caller of its operations calls these too. When the
  two disagree, or neither decides, they go on the manager, and a
  review reads the interface the checker names. The operations the
  manager already holds stay where they are. Moving those, with their
  callers and their tests, is a change of its own, made only when a
  person asks for it.
- **A delegate's shape.** It is `<Ns><Duty>ManagerInterface` in
  `om/src/<name>/om/<ns>/<duty>.py`, re-exported from the package root,
  and `<Ns><Duty>ManagerImpl` in `impl/<duty>.py`. The root builds it in
  `om/src/<name>/om/root.py` and hands it to the manager, whose
  interface carries it as an attribute, never a method. It has no field
  on `Managers`, and a caller outside the namespace reaches it as
  `managers.<ns>.<duty>`. A delegate that calls a sibling takes it in
  its constructor, typed by its interface, as `members` takes `org`. One
  that needs an operation of the manager, a transition among them, takes
  a narrow callable for that one operation, which the root binds at call
  time, as `org` takes `service_context`. The manager holds the
  delegate, so the delegate never takes the manager.
- **Authorize first.** A mutating manager operation opens with
  `ctx.require(<permission>)`, or `octx.require(...)`, before any read,
  the ones a worker calls included. Only an operation on a stage with no
  permissions (the request stage, the identity stage) and the outbox
  handoff open without one.
- **An update copies onto the stored row.** It reads the row, takes the
  caller's fields less `PROVENANCE_FIELDS` and the entity's
  `MANAGER_OWNED_FIELDS`, and builds the copy with
  `model_validate({**current.model_dump(), **changes})`, never
  `model_copy(update=...)` over a dump, which skips validation. Every
  entity declares `MANAGER_OWNED_FIELDS`, even when it is empty.
- **The outbox rows ride the write.** A `core`-role write lands its row
  and its `OutboxRow`s in one storage call, and the manager relays them
  at once. A relay that fails is logged and left to the sweep, never
  raised, since the write has committed. Work that follows a write is a
  second row, of kind `work_row_kind(<kind>)`, in the same call, never
  an enqueue the manager makes. A payload, an event, and a socket frame
  carry ids, never a personal field's value.
- **Two fences.** The `org_id` in the query is the fence, and the
  table's row-level security policy is the second. Every table is in
  `TABLE_ROLES` (`om/src/<name>/om/storage/roles.py`) and
  `TABLE_SCOPES` (`storage/scopes.py`), and the migration that creates
  it creates its policy, with `ENABLE` and `FORCE ROW LEVEL SECURITY`.
  Every Postgres statement opens through the base's `_session_for`,
  with the call's scope by keyword. `EMPTY_UUID` is passed only by a
  method on the tenantless list.
- **A backfill lifts the force.** A data migration runs as the
  migration login, which `FORCE ROW LEVEL SECURITY` binds, so its
  `UPDATE` would touch nothing. Its SQL lifts the force for the one
  table and puts it back in the same transaction, and raises when the
  rows it touched are not the rows it meant to touch. Its integration
  test seeds two tenants and sees both touched.
- **A cross-tenant case per storage method.** The contract module under
  `om/tests/contracts/` has, for every storage method, a case that
  passes another tenant's id and finds nothing and changes nothing. The
  unit module runs it over memory, the integration module over
  Postgres. Both impls order by the `UUID`, never its string.
- **Two storage impls, one manager impl.** A namespace that fronts an
  outside provider reaches it through a client under `integrations/`,
  with a real impl and a deterministic twin, and the setting that picks
  one.
- **Creates are idempotent.** A route that writes a durable row takes
  the gateway's `Idem` dependency and runs under it, whether it answers
  201 with the row or 202 with the id of work.
- **Every setting is documented and deployed.** A field of a process's
  settings is in `.env.example` under the prefix, and every environment
  under `deployment/terraform/environments/` sets it or wires it as a
  secret. The settings tests hold both.
- **Versions are current.** A library, image, or tool a scaffold adds is
  at its latest stable release that a patch release already follows,
  as Technology Choices and How to Override Them (Versions) states. A
  version the scaffold cannot confirm is named in the output.
- **No placeholder.** No empty module, no `TODO`, no dead import. A
  namespace's `README.md`, and `om/README.md` above it, speak the
  product's language to a reader with no code.

## Changing existing files

A changed file gains an entry: a getter on a root, a field on
`Managers`, a router in `HOSTED`, a row in a map, a member in the
workspace. Nothing is reordered, and nothing is removed but what a
skill's Changed table names as removed.

## After writing

Before the first gate, format what was written with the tree's
formatter, `uv run ruff check --fix .` and then `uv run ruff format .`,
the two the setup target ends with. A formatter run is no gate run,
and no count holds it.

1. Run these, in order, and stop at the first that fails:
   `make infra-up` and `make migrate` when a table was added,
   `make openapi` when a route was added and no step ran it since,
   `make check`, and
   `make migrate-check` when a table was added. The database targets
   run only against the local compose stack, and only when Docker runs
   (`docker info` exits 0). When it does not, they are skipped, and the
   output names each one skipped. A tool runs through the workspace
   (`uv run`, `pnpm run`), never a global install.

   A gate that fails on what the scaffold wrote is fixed, and the
   commands of its step run again from the first: the first run plus at
   most 3 reruns. When the count runs out, stop, and leave the tree as
   the last run left it. A fix edits only files the scaffold created or
   changed.

   Three failures stop at once, with no fix: one that was there before
   the scaffold ran; one of the machine (no network, Docker stopped, a
   port that cannot be moved); and one whose fix takes an exception to
   a rule the guideline states, or removes, skips, or suppresses a
   conformance test. That fix is the person's decision, recorded as an
   ADR.
2. Print the guideline version, then every file created or changed,
   one per line, from `git status --porcelain --untracked-files=all`
   (in a tree with no commit yet, which it lists whole, as the skill
   wrote them), then each command run, once, with the outcome of its
   last run. A stop closes the output with
   `Stopped: <command>: <what went wrong>; <cause>`, the cause one of:
   the count ran out, pre-existing, the machine, needs an exception.

Never commit. A scaffold leaves a working tree for a person to review.
