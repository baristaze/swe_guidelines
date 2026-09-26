# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.37.0 (2026-09-26)

The templates and the scaffolds say what the text says. Each place
that changed was a template or a scaffold that disagreed with the text
it was made from, and an adopter who copied it contradicted the text
too. Minor, with one reversal: STO-20 no longer holds a work row
pending until its wake-up is taken. A work row is done once its item
is queued.

### Changed

- "The Storage Layer, Database Roles" (STO-20), reversed: a work row is
  done once its item is queued. Its `WORK_AVAILABLE` wake-up is a hint,
  and a worker polls the queue on an interval from its settings, which
  bounds the delay a dropped wake costs. An entity change's row still
  waits for its publish. A tree on 0.36.0 that keeps a work row pending
  on its wake marks it done once the item is queued.
- "Worker Roles, The Work Queue" (ASY-25): an enqueue publishes
  `WORK_AVAILABLE` only when its insert won, and a worker polls besides.
  `arch-scaffold-worker` has a `poll_interval` setting.
- "OpContext" and "Pure Rules" (CTX-03, OM-15): the role-to-permission
  table lives in the tenancy namespace's `rules.py`. A tree that
  declares it beside the types moves it.

### Fixed

- The `audit-query-indexes` and `audit-database-calls` templates state
  the role the Operational Skills table gives them: none. They hold no
  cloud credential, and they read a database they make and drop on the
  local stack. `audit-database-calls` ranks a cache before parallel
  reads (OPS-11). A tree that copied them on 0.36.0 changes the same
  lines.
- `ops-investigate`, `ops-watch`, and `stress-test-run` read the queue's
  gauges only where there is a worker. The outbox's lag is read in
  every tree.

### Added

- `make skills` holds the audit templates' roles and fix order to
  "Operations, Operational Skills", and the work row's rule to the four
  places that state it.
