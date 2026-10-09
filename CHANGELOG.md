# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.57.0 (2026-10-09)

A lease on a resource can be kept by the worker that runs its holder's
job: a grant starts the job, and the worker that claims it starts,
renews, and ends the lease, for a holder that may be gone by then. A
renewal names its length, a job gets a window to start in, and labels
and bounds fit real resources. Minor.

### Added

- Leases on a Resource: a grant can start a job, one work item the
  grant writes in its own commit, carrying the lease's id and token.
  The worker that claims the job starts the lease, renews it, and ends
  it, under the lease's token and the item's claim token, so once the
  queue takes the item back, the old claim is refused. The holder keeps
  its own right to release, and no one else gains one. ASY-33 and
  ASY-36 hold it, and ADR 0094 records it.
- A job's window: the grant gives the job a window to start in, and the
  lease runs to the window's end. From the job's start, the lease runs
  its full term. A job that waited in its lane still starts up to the
  skew margin past the window's end; one that misses it lets the lease
  lapse.
- The scaffold's `JobClaim`, and `LeasesManagerInterface.start`: the
  job's worker's, in process, since no route carries a claim token.
  `renew` and `release` take a `JobClaim` too. A grant starts one job
  at most, the one `work.<kind>` row among the kind's `grant_rows`, and
  the lease keeps its id as `job_key`. The lease's `job_key` and
  `started_at`, and the request's `start_seconds`, are nullable columns
  (migration `core` `202609280002`). An ask takes `start_seconds`, and a
  lease shows `started_at`, in the API document and both clients.
- `WorkManagerInterface.holds`: whether a tenant's item is claimed under
  a token now, the fence every write to the item conditions on, read
  for a lease whose job the item is. A copy with a work manager of its
  own implements it.

### Changed

- A renewal names its length: the lease runs the seconds it names from
  now, or its term again, within the resource's bound.
  `POST /v1/leases/{lease_id}/renew` takes an optional `seconds`, in the
  API document and both clients.
- A label is free text of 1 to 200 characters with no control
  character, matched as written, in place of a short lower-case token.
  A resource offers up to 160 (`MAX_LABELS`).
- A resource's bound on one lease, and an ask's term and window, run up
  to seven days (`MAX_TERM_SECONDS`).
- ADR 0086 follows: a grant starts one job at most, and a renewal and a
  release are the holder's, or the worker's whose claim on the lease's
  job still holds.
