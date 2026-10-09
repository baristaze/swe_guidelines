# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.59.0 (2026-10-09)

A resource's owner updates its labels, its bound, and its availability
in the commit of its own row, and its line is offered it again. A
tenant reads its leases back as a history, each with the request it
answered. A kind may refuse an ask before anything of it lands, and a
line answers each place and estimate from one replay. The scaffold's
import-direction test counts the read cache as the cache's interface.
Minor.

### Added

- An owner's update of a resource: `update_statement` with a
  `ResourceUpdate` (`land_update` in memory) changes the labels, the
  bound, or the availability it names, in the commit of the owner's
  row, and keeps the rest. After the commit that registered or updated
  a resource, the owner calls the manager's `offer`; the manager's own
  `register` and `update` offer at once, and the sweep offers what a
  crash left. A request the new labels match may be granted, and one
  they no longer match leaves the line. A held lease keeps its term,
  and a new bound holds from its next renewal. ASY-36 and ASY-37 check
  it.
- A lease history: a resource's leases, and the org's, newest first by
  `created_at` and then `id`, ended ones included, each with the
  request it answered from one join, at most `history_limit` a page.
  The next page starts after the last lease's `HistoryMark`, which an
  opaque cursor carries. A tenant reads only its own. The scaffold
  serves it at `GET /v1/leases`, the Python client reads it with
  `lease_history`, and the migration `core/202610091856` adds the two
  indexes it walks. ASY-36 checks it.
- A kind's check at the ask: a kind that refuses some asks registers an
  `AskCheckInterface` beside its hooks. Its `check_ask` runs under the
  asker's context, with the request as it would land and the resource
  it names, and refuses with `NotAuthorized` or `ValidationFailed`
  before anything lands. A kind with none accepts every ask. ASY-36
  checks it, and ADR 0098 records it with the update and the history.

### Changed

- Leases on a Resource: a grant lands only while the request still fits
  the resource as its row stands under the anchor's lock, in its line
  and with the lease's term and window within its bound (`grant_fits`).
  A grant decided on labels or a bound read before the lock is a
  violation of ASY-32.
- Leases on a Resource: a resource's line answers each request's place
  and estimate (`Place`) from one replay over the reads it makes. A line
  that reads the store again for each place is a violation of ASY-34.

### Fixed

- The scaffold's import-direction test lists `acme.infra.cache.read`
  with the infra interfaces, so a manager that takes a `ReadCache`
  through its constructor, as ADR 0095 asks, passes a copy's unit gate.
  The scan's own test asserts the read cache is no impl.
