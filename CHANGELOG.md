# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.61.0 (2026-10-14)

The scaffold's own nouns name their mechanism and leave the bare word
to the product. Each namespace owns its OpenAPI document, its types,
and its client. A migration chain is ordered by its parents, a copy and
its scaffold meet on no shared line, and the scaffold's ADRs and
migration chains say what holds at their head. Minor, with reversals:
the renames, with routes renamed inside `/v1`; a stamp no longer orders
a chain; `run_backfill` takes its SQL file; CTX-12's `tenantless`
option is refused; and kinds are registered strings.

### Added

- Naming Entities: a noun the scaffold brings is qualified by what it
  does, and the bare everyday noun is the product's. No name says
  "domain" or names the adopter, and mechanism nouns such as `Org` and
  `Lease` stay bare. OM-19 checks it, and ADR 0100 records the
  scaffold's names.
- Each root and registry of the scaffold holds its own entries, a marker
  line, then a copy's: the composition roots, `HOSTED`, `TABLE_ROLES`,
  the kinds and the hooks, the worker's handlers and purges, the
  portal's keys and routes, CTX-26's construction sites, each `llms.txt`
  audience, the spec's two tables, and the Makefile's buckets and URLs.
  A test merges an entry above every marker against one below it.
  ADR 0103.
- A storage method that takes no tenant, a manager method that takes no
  stage, and a transition are declared beside them (`@tenantless`,
  `@stageless`, `@transition`), and the fence's tests and CTX-12 read
  the declaration.
- Work, orchestration, leasable resource, and waiter kinds are
  registries. A copy names its kinds in its namespace's own module and
  registers them below the root's marker; a registry refuses a name
  registered twice, and a start or an ask of a kind nobody registered is
  refused.
- The ops skills read one signals file per namespace, and a copy adds
  one for each namespace of its own.
- `scaffold/new.py` formats the render with the ruff its `uv.lock`
  names, so a copy's first setup rewrites nothing.

### Changed

- The scaffold is renamed on every surface, a reversal for a copy:
  `Session` becomes `AuthSession`, at `/v1/auth/sessions`; `Event`
  becomes `ChangeEvent`, in `om/change_events` and at
  `/v1/change-events`; `Resource` becomes `LeasableResource` and `Line`
  becomes `WaitLine`, at `/wait-line`; `Step` becomes
  `OrchestrationStep`; `Platform` becomes `OmBase`, with `OmException`
  and `SystemSize`. A rename moves the table, its columns, its
  constraints, the not-null ones included, its indexes, and the stored
  values in place.
- `make openapi` writes one document per namespace, and `common.json`,
  under `clients/openapi/`. A schema is defined in its owner's document
  and referred to by a file `$ref`. Wire types live in
  `types/<namespace>.py`, each namespace serves its own operator routes
  at the same paths, and the generated types, the TypeScript facades,
  and the Python client's calls follow the namespaces. The drift gate
  fails on any new file under `clients/`. NET-36 and ADR 0101.
- A migration chain is ordered by each wrapper's `down_revision`, never
  by its stamp, a reversal: `migrate.chain` refuses two heads, two first
  revisions, or a missing parent, and arch-check replays the chain in
  that order. A layer's base move re-points its first own migration and
  renames none, and a copy keeps a scaffold migration out of its chain.
  ADR 0102.
- A data migration is one `run_backfill(role, "<stem>.up.sql", table)`
  call, a reversal: it takes its SQL file, never strings.
- `docs-compact --migrations` in a copy or a layer folds only the
  revisions its `scaffold` render does not hold, onto the last rendered
  one. Before a fold, it renames each not-null constraint a rename left
  under an old name, in a migration of its own, and it re-points a
  citation of a folded file anywhere in the repository.
- CTX-12 reads the declarations, and arch-check refuses
  `[tool.arch-check.options.CTX-12] tenantless`, a reversal.
- A kind is a snake-case string on every row and on the wire, held to a
  pattern, never a closed enum, a reversal for code that imports one. A
  leasable resource kind carries its ask's shape on its hooks (`ASK`).
- Each make target declares `.PHONY` on the line above it, an index
  document points at what holds a list and repeats none, and a copy
  never edits an ADR its base gave.
- The scaffold's ADRs state their decision under their present nouns,
  and each role's migration chain is one revision at its head: core,
  activity, and admin at `202610142241`, queue at `202610141707`. A
  local database at an older head is made anew (`make reset`).

### Removed

- The `admin` router: each namespace serves its own operator routes.
- The lists of tenantless methods in `pyproject.toml` and in the
  fence's tests.
- `ASK_PAYLOADS`: the ask's shape is on the leasable resource kind's
  hooks.
