# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.41.0 (2026-09-30)

A copy of the scaffold keeps the scaffold as its base and takes a later
release by merging it, and the scaffold's docs say what its code does.
Minor: a skill is added and a lens is sharpened, and one reversal is
named below.

### Added

- A copy keeps its base in git. `scaffold/new.py`, from a clean
  checkout, commits the copy as its base, on the branch `scaffold` and
  the main branch. `scaffold/base.py` commits the scaffold at a later
  ref onto that branch, fetched as one tarball and renamed by that
  commit's own `new.py`.
- `arch-upgrade-scaffold` moves a copy one release forward: it grafts a
  copy with no base at the release it pins, merges the target's render
  three ways, resolves what the merge leaves, keeping the copy's ADRs
  and migrations its own, and runs the gates. The move merges with a
  merge commit. The adopting page gains "Upgrade a copy of the
  scaffold".
- The browser benchmark has a page of its runs,
  `benchmark/runs/browser-judge-swe/`, text only and redacted, and
  `arch-benchmark-browser` checks each run in with its row. `make runs`
  holds a browser run.
- The scaffold's API page lists its routes by area, what the gateway
  guarantees, and its subcommands. Its operations page says what an
  operator never does, and its integrations page what a provider that
  hangs costs a call.
- The scaffold's cloud page itemizes the fixed monthly cost, and gives
  the unit prices behind the sizes, `max_connections` at L and XL, and
  four savings that need a Terraform change.
- The scaffold's tests pin each loop count its ops skills state, the
  paths only one agent resolves, and Codex's switch beside
  `disable-model-invocation`.

### Changed

- Reversed: ASY-25 no longer holds that the manager's copy keeps an
  enqueue's timestamps. The copy keeps the id as constructed and stamps
  the times, as CON-17 says of every create since 0.39.0.
- `ops-watch` step 5 says how a batch reads whole minutes in plain
  sentences, names both places a query holds its period, and gives the
  local p95 query. `ops-investigate` bounds its polls for each query.
- The scaffold's workflows set up uv with `astral-sh/setup-uv` v10.2.0.

### Fixed

- A copy's `pnpm-lock.yaml` lists each importer's dependencies in
  pnpm's order under the copy's name: `scaffold/new.py` orders them
  after the rename.
- The scaffold's prose puts no article before its name, which the
  rename left wrong for most names, and its local TOTP key holds no
  name, which the rename could not reach inside base64.
- Three of the scaffold's ADRs name paths that exist, and ADRs 0041,
  0045, and 0056 say what its deletion and its purges do. Its README
  lists `make migrate-check` and says an exported variable wins over
  `.env`; its runbooks say the repository issues GitHub's immutable
  OIDC subject and that the database's storage grows on its own.
- Three docstrings: `Created` is the base of a row the platform writes
  for itself, a manager operation takes `TenantContext`, and no
  heartbeat thread runs beside the event loop.
