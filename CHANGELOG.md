# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.54.0 (2026-10-09)

A thing that serves one holder at a time gets a lease, under a fencing
token that only grows, and whoever waits for it gets a line: an
optional guideline section, its lenses, and the scaffold's `leases`
namespace. Minor.

### Fixed

- OPS-20's Look for lists the routes the scaffold's traffic session
  calls: a sign-in, one socket, a read of the person, a list of the
  members, a write, a key made under an idempotency key and revoked, a
  read of the stream, its own change seen on the socket, and a
  sign-out. It no longer names a completion or a reopen, which the
  scaffold has no route for.

### Added

- The guideline's Leases on a Resource, `optional`. A resource is one
  row per leasable thing, and its anchor holds the highest token
  granted. A lease is one grant to one principal, under a token one
  above the anchor's; it ends on its release, its revocation, or once
  its expiry and a skew margin have passed. A request waits in one rank
  order per tenant, for one resource or for a kind and the labels it
  needs. A grant goes to the head of the line under the anchor's row
  lock, once the waiter says it still waits and the kind says it may
  still be granted. ADR 0086 records the decision.
- ASY-32 to ASY-37: a grant is decided under the anchor's lock; a token
  only grows, and a lease ends past the skew margin; one rank order
  serves every line, and a request gets one lease; no grant goes to a
  waiter that no longer waits; a resource kind registers its hooks, and
  a lease holds no product fact; a freed resource goes to its line, and
  the sweep catches the rest.
- The scaffold's `leases` namespace: the `resources`, `leases`, and
  `lease_requests` tables on the `core` chain, both storages, the
  manager with its `ResourceKind` and `WaiterKind` hooks, and
  `/v1/leases`: ask for, read, cancel, and reorder a request, read a
  resource's line, and read, renew, release, and revoke a lease. Until
  a kind registers, the code sits unused.
- The Python client's `LeaseClock`, which counts a lease's time on a
  monotonic clock from the send, and its `Fence`, which keeps the
  highest token per resource.

### Changed

- A long-running record parks on the reason `resource` while it waits
  in line, and the grant, or its request's end without one, wakes it.
  `WAKE_PARKED` may name one record (`record_id`); one that names none
  wakes every record parked for its reason. ADR 0039 says so.
- The maintenance worker's pass ends each lease past its expiry and the
  skew margin, expires each request past its wait, and offers each free
  resource to its line.
- `arch-review-async` covers leases.
