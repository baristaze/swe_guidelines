# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.64.1 (2026-10-22)

A patch: the unit suite holds on every core.

### Fixed

- The mint test counts the process as booted for its duration, so the
  mint command and the app it builds never bind the root log handler to
  the test's captured stream. That stream closes when the test ends, and
  every later test on the same worker that read its stderr read a
  logging error instead.
