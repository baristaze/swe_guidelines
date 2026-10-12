# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.60.0 (2026-10-12)

A person cancels a running or a parked record, and it settles
`cancelled`, keeping what it made. Long-Running Orchestrations states
that every state short of a record's end has a way out. Minor.

### Added

- Long-Running Orchestrations: a person cancels a running or a parked
  record, which settles `cancelled` and keeps its cursor and results;
  no wake, batch wake, or resume revives it. Whoever may resume a record
  may cancel it, and a kind a person starts says what a second start
  does while one is open. The section states the guarantee: a record is
  never in a state it cannot name, every state short of its end has a way
  out, a step run again does once, and a creating request is marked begun
  first. ASY-39 checks it.
- The scaffold's orchestrations manager has `cancel(ctx, record_id,
  reason)`. `CANCELLED` joins `SETTLED`, so the purge takes a cancelled
  record and its lease request stops waiting. The cancel writes by the
  version compare-and-set, takes the permission `resume` takes, and
  lands the record's `updated` hint so a client learns of it. A
  product's cancel route calls the lease line's `leave`. ADR 0099 records
  it. No migration: the status column has no constraint.

### Changed

- The CI pins move to uv 0.12.23, ruff 0.16.10, and mypy 2.4.0.
