# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.64.0 (2026-10-22)

A copy's gates take less time: its suites run on every core and side by
side, and the work they repeated runs once. Minor.

### Changed

- A copy's `make test-unit` runs on one worker per core (`-n auto`,
  pytest-xdist in the dev group), names its workers, and ends on its ten
  slowest tests; `PYTEST_XDIST_AUTO_NUM_WORKERS` sets the count. ADR
  0105: a unit test holds nothing another test can reach, and its id is
  fixed at collection. The integration and telemetry suites keep one
  process.
- A copy's CI starts its integration, telemetry, and image jobs beside
  the fast gate rather than after it, so a run takes about its longest
  job. A red fast gate no longer spares their runner minutes.
- This repository's CI runs each copy's fast gate, its integration
  suite, and the release-before step as jobs of their own, side by side.
- An integration test empties only the tables written since the last
  wipe, in one statement a database, the databases side by side, on
  engines that live for the run. A test still starts on empty tables.

### Fixed

- `make openapi` and the documents test cut the app's schema in linear
  time: a schema is matched to its type by name, never by an expression
  compiled for each pair, and one process writes every TypeScript file.
- arch-check walks each module's tree once, and its rules read what that
  walk found; its output is unchanged. A copy takes it when its
  arch-check pin moves.
