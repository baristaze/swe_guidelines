# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.44.0 (2026-09-30)

A manager that outgrows one interface delegates its duties, and the
scaffold's tenancy manager keeps 17 of its 52 operations. Minor: a rule
is added, with its bound in `arch-check`. Nothing is reversed.

An interface of 52 operations is read whole by every agent that touches
one of them, and a caller that needs API keys depends on sign-in and
deletion too. So a manager keeps its core, and its other duties are
delegates reached through it.

### Added

- The Business Layer says when a manager delegates and how. A delegate
  is an interface and an impl of the same namespace, named after the
  namespace and the duty, built by the root. A caller outside the
  namespace reaches it through its manager
  (`managers.tenancy.credentials.create_api_key(...)`). Inside the
  namespace, the root may hand a delegate a sibling typed by its
  interface, or a narrow callable for one operation of the manager.
  CON-01, CON-09, OM-14, and CTX-21 say the same.
- `arch-check` holds a manager interface, a delegate's included, to a
  bound on its operations under CON-01: twenty, or the project's own
  `max_operations` under `[tool.arch-check.options.CON-01]`.

### Changed

- The scaffold's `TenancyManagerInterface` keeps seeding, the stage
  transitions with the socket tickets, the operator grant job, and the
  sweep. Its other 35 operations move, bodies unchanged, to four
  delegates: `sign_in` (9), `org` (9), `members` (12), and `credentials`
  (5), each in `tenancy/<duty>.py` with its impl in `impl/<duty>.py`.
  Helpers two duties share are module functions in `impl/shared.py`,
  and `build_tenancy` in `om/root.py` builds the five. No route, wire
  type, or storage call changes.
- The scaffold conventions and `arch-scaffold-entity` say where an
  operation is written: a scaffold's new operations go to a new delegate
  when the manager would pass the bound, and `purge_tenant` is always on
  the manager itself.

### What a copy does

- A copy that takes this release merges the move into its own tenancy
  code: `tenancy/manager.py`, `tenancy/impl/manager.py`, and the tenancy
  tests conflict where the copy changed a moved operation. A caller of a
  moved operation names its delegate. An operation the copy added stays
  on the manager when the gateway, a peer, or a worker calls it, and
  goes to the delegate of its duty otherwise.
- A copy whose own manager interface holds more than twenty operations
  fails `arch-check` at this release: it delegates, or sets
  `max_operations`.
