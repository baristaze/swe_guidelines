# ADR 0086: A scarce resource is leased under a fencing token, with a line in front of it

**Status**: accepted (2026-10-08)

## Context

Some things serve one holder at a time, such as a loading dock. Durable
work has a lease and a fence, its claim; a thing the work needs and
cannot carry has neither. Two holders at once damage it. A holder whose
clock runs slow acts after its time, and a holder that lost its lease
acts after the next one starts. Whoever waits needs a place in line,
and a waiter that stops waiting must never be granted. The guideline's
"Leases on a Resource" states the mechanism in its own words, and this
records how Acme holds it.

## Decision

- **A namespace of its own, in the `core` role.** `om/src/acme/om/leases`
  holds three tables, `resources`, `leases`, and `lease_requests`, with
  storage in Postgres and in memory under the tenant fence, and its
  manager (Namespaces as Swimlanes; Database Roles). The migration is
  `202609280001_leases_on_a_resource` on the `core` chain.
- **A resource is a reference by a registered kind and an id.** It is
  unique by org, `kind`, and `ref_id`. The namespace that owns the row
  lands the resource in the same commit as its row, through
  `register_statement` (`land_resource` in memory), and retires it with
  the row through `retire_statements` (Shape of an Operation).
- **The anchor's row lock decides the grant.** Every write that moves a
  lease or a line locks the resource's row first and the request's
  second, so two writers queue and never deadlock. A grant lands only
  while the anchor holds the token it read and no live lease, the
  resource is live and available, and the request still waits. A
  partial unique index over a resource's active leases is a second
  fence (Storage Principles).
- **The token only grows, and the margin outlasts a slow clock.** A
  grant takes one above the anchor's token, and an end keeps it. A lease
  ends past its expiry and the skew margin, 30 seconds by default
  (`LeasesOptions.margin`), and a renewal past the expiry is refused.
- **One rank order per org.** A rank is a float: an ask takes one past
  the last, and a reorder takes the midpoint of its new neighbours, so
  no one else moves. A selector request stands in each line it matches
  with one place, and the first grant settles it. The estimate replays
  the lines from each resource's measured hold, which moves a fifth of
  the way to each new hold.
- **An ask is idempotent by its key** (Idempotency). The route runs
  under the idempotency record, whose id is the request's key, and an
  ask asked again answers its lease or its place. Every ask is a
  request that joins the end of the line, so a direct ask never passes
  anyone waiting, and one request gets one lease.
- **A grant is a side effect of a resource freeing.** A release, an
  expiry, a revocation, or the resource's availability back offers it
  to its line. The lease, the anchor, the request's answer, and the
  rows the grant starts land in one commit, as outbox rows the relay
  carries (Database Roles).
- **Kinds and waiters register as work kinds do** (The Work Queue). A
  resource kind is a `ResourceKind` with its ask's shape in
  `ASK_PAYLOADS` and a `ResourceKindInterface`: `may_grant`, read just
  before a grant, and `grant_rows`, what the grant starts. A waiter is a
  `WaiterKind` with a `WaiterInterface`: `still_waits`, `wake_rows`, and
  `revoke_rows`. A no from either hook cancels the request, and the
  next is offered. The core's kind is `noop`, and its waiter is the
  orchestration.
- **A waiting record parks on `resource`** (Long-Running
  Orchestrations). A step asks with its `Step`, which lands under the
  request's row lock only while the request still waits; a grant that
  came first leaves the record running with its lease. The grant's
  wake row is a `WAKE_PARKED` item naming the one record.
- **The sweep is a step of the maintenance pass** (Maintenance Without
  a Scheduler). It visits the orgs with something due, as each org's
  service context: it ends each lease past its expiry and the margin,
  expires each request past its wait, and offers each free resource.
  The purge takes ended leases and settled requests after 30 days.
- **The permissions are the core's.** An ask, a renewal, a release,
  and a cancel of one's own take `WRITE`; the reads take `READ`; a
  revocation, a reorder, and a cancel of another's take
  `MANAGE_MEMBERS`. A renewal and a release are the holder's alone.
- **The client holds the holder's side.** `LeaseClock` counts the
  lease on a monotonic clock from the send and renews at half of what
  is left; `Fence` keeps the highest token per resource and runs the
  holder's stop-and-reset before it admits a higher one.

## Consequences

- A product adds a resource kind and its hooks, and lands its resources
  with its own rows; a lease holds no fact of the product's. Facts a
  product keeps per lease, such as a run, live in its own table, keyed
  by the lease's id.
- A thing that waits on a request registers as a waiter, and leaves
  every line when it ends; nothing is granted to it after that.
- The code ships unused until a kind registers. A copy that never needs
  it drops the namespace, its routes, and its client side, and drops its
  tables with a migration of its own.
- One offer reads at most `line_limit` waiting requests of a kind and
  asks at most `offer_tries` heads; a line longer than that waits for
  the next freeing or the sweep.
- The estimate is a replay, not a promise: a reorder, a cancel, or a
  hold longer than the measure moves it.
