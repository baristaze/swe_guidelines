# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.31.1 (2026-09-22)

`arch-check` finishes on a large import graph, and a rule that runs too
long is reported instead of freezing the run. Patch: no rule changes.

### Fixed

- `arch-check`, lens `NET-09`: the rule followed every imported name
  into its module and walked each path through the import graph anew,
  so its time grew exponentially with the depth of the imports. On a
  service tree of 228 files the network group took 204 seconds. It now
  finds the names that carry the idempotency key in one pass over the
  project, repeated until nothing is added, and takes under a second.
  The re-export walk behind `ASY-04`, `ASY-08`, and `ASY-09`, and the
  mixin walk behind the storage rules, had the same shape and now
  remember what they read.
- `arch-check` gives each rule 60 seconds. A rule past it is an `ERROR`
  finding that names the rule, the other rules still run, and the run
  exits 2.
