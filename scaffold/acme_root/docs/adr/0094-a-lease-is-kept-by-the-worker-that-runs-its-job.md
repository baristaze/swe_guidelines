# ADR 0094: A lease is kept by the worker that runs its job

**Status**: accepted (2026-10-09)

## Context

A holder often runs its work through a worker. It asks for a resource,
such as a loading dock, and what the grant starts is a job on the work
queue: unload the truck at the dock. By the time the job runs, the
holder may be gone, and only the holder could renew the lease or end
it ([ADR 0086](0086-a-scarce-resource-is-leased-under-a-fencing-token.md)).
The job may wait in its lane past the lease's term, so the lease could
lapse before the job started. A renewal ran the term again, so a job
could not ask for the time it needs. A label was a short lower-case
token, 32 to a resource, so a resource that says what it offers in
words could not be registered. The guideline's "Leases on a Resource"
lets the worker that claims the job act for the holder, and this
records how Acme holds it.

## Decision

- **A grant's job is the one work row it starts.** Among the rows
  `grant_rows` returns, a row whose kind asks for work
  (`asks_for_work`) is the job, and a grant starts one at most: a kind
  whose grant returns two raises, and nothing lands. The lease keeps the
  row's id as `job_key`, which the relay gives the item as its key.
- **The job's worker acts under a `JobClaim`.** `renew`, `start`, and
  `release` take one: the lease's token and the item's claim token. The
  manager reads the claim live through the work manager's `holds`,
  which answers whether the item under the key is claimed under that
  token now. That is the fence every write to the item conditions on,
  so once the queue takes the item back, the old claim is refused. A
  lease with no job, a token that is not the lease's, or a claim that
  no longer holds is refused (`NotAuthorized`). The principal the worker
  runs as gains nothing by it. Without a claim, the holder alone renews
  and releases, as before. `LeasesManagerImpl` takes the work manager,
  and the root passes it.
- **The job's acts are the manager's alone.** A worker reaches the
  manager in process, so no route carries a claim token, and the routes
  keep the holder's rights alone.
- **The grant gives the job a window, and its start gives the term.** A
  request carries `start_seconds`, the window its job has to start in;
  with none, the window is the term. A grant runs the lease to the
  window's end. `start` lands under the anchor's lock while the lease is
  active, not started, and its expiry is after the clock less the skew
  margin. It sets `started_at` and runs the lease its full term from
  then. So a job that waited in its lane to the end of its window still
  starts: the sweep ends a lease only past its expiry and the margin,
  and nothing has acted under the lease yet. A lease already started is
  answered as it is, so a job run again starts once. Past the margin,
  or once the lease ended, `start` is refused (`LeaseEnded`), and the
  sweep ends the lease as any lapsed one.
- **A renewal names its length.** `renew` takes `seconds`, and its route
  the body `{"seconds": n}`; with none, the lease runs its term again.
  Either is held within the resource's bound (`rules.renewal_of`), and a
  renewal still needs an expiry after the clock.
- **Labels and bounds fit real resources.** A `Label` is free text of 1
  to 200 characters with no control character, matched as written, and
  a resource offers up to `MAX_LABELS`, 160. A resource's bound on one
  lease, a request's term, its window, and a renewal run up to
  `MAX_TERM_SECONDS`, seven days. No column checks them, so only the
  types change.
- **One migration on the `core` chain.**
  `202609280002_a_lease_kept_by_its_jobs_worker` adds `job_key` and
  `started_at` to `leases`, and `start_seconds` to `lease_requests`, all
  nullable, so a lease its holder keeps, and every row before it, reads
  as it did.
- **The client gains the lengths, and its clock is unchanged.**
  `ask_lease` takes `start_seconds`, and `renew_lease` takes `seconds`.
  `LeaseClock` counts the seconds the answer gives from the send, so a
  named length needs nothing new of it.

## Consequences

- A kind whose grant starts a job lets the job's handler keep the lease
  while the job runs: it starts the lease, renews it for the time the
  job needs, and releases it when the job ends. The holder can still
  release it, to cancel.
- A worker that crashes leaves the lease running until its expiry and
  the margin, as a crashed holder does. The queue gives the item a new
  claim, and the next attempt's worker finds the lease started and keeps
  it under that claim.
- A job that waits in its lane holds its resource idle until it starts
  or its window and the margin pass. A window as long as the lane's
  longest wait buys the job its start with that idle time.
- A label is matched as written, so `cold` and `Cold` are two labels.
- A worker that renews or ends a lease reads the queue on each call: one
  read by the item's key, under the tenant fence.
