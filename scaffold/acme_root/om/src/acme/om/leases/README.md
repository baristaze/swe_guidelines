# Leases

A thing one holder may use at a time, such as a loading dock, leased
under a fencing token, with a line in front of it. This is one of the
kinds of thing [Acme is made of](../../../../README.md).

## What it holds

- **Resource**: one leasable thing of an org. It stands for a row of
  another namespace, by a registered kind and that row's id, and it is
  unique by the three. It carries labels (what it offers), its bound on
  one lease, its availability, and the anchor: the highest token
  granted on it, the lease that holds it, and until when.
- **Kind**: what the resource is, with the shape of what an ask for it
  carries and its hooks: what a grant starts, and whether a request may
  still be granted. The core has one, `noop`, whose grant starts
  nothing. A product replaces it with its own kinds.
- **Lease**: one grant of one resource to one principal, under a token
  one above every earlier grant of that resource. It is active, then
  released, expired, or revoked.
- **Request**: a principal's place in line. It names one resource, or a
  selector: a kind and the labels it needs. It carries its ask's key,
  how long it may wait, a rank, and what waits on it, by a registered
  waiter kind and an id. It is waiting, then granted, cancelled with a
  reason, or expired.
- **Line**: a resource's line is the waiting requests that name it,
  and the waiting selector requests it matches, in the org's one rank
  order.

## What can happen

- **Register** a resource with its owner's row, in the same commit, and
  **retire** it with that row: the requests that name it are cancelled,
  and its lease is never renewed.
- **Ask.** A request joins the end of the line, and every free resource
  it may take is offered at once. A direct ask is granted only when no
  one waits in front of it. An ask asked again by its key answers its
  lease or its place.
- **Grant.** A resource that frees goes to the head of its line: on a
  release, a revocation, an expiry, or its availability back.
- **Renew** and **release**, by the holder. A lease past its expiry is
  never renewed.
- **Cancel** a request, by its asker or a manager; **reorder** one, and
  **revoke** a lease, by a manager.
- **Leave.** A waiter that ends leaves every line.
- **Sweep.** A pass ends each lease past its expiry and the skew
  margin, expires each request past its wait, and offers each free
  resource to its line. An ended lease and a settled request go thirty
  days later.

## The rules

- **One holder, by two fences.** A grant locks the anchor's row and
  lands only while the anchor still holds the token it read and no live
  lease. A unique index over a resource's active leases is the second
  fence.
- **A token only grows.** Whatever acts on the resource for the holder
  presents the token, and that side refuses one lower than the highest
  it has seen. The client's fence does this for a holder.
- **The margin outlasts a slow clock.** A lease ends only once its
  expiry and the margin have passed, so a holder whose clock runs slow
  has stopped before the resource is granted again.
- **One request, one lease.** A selector request stands in many lines
  with one place, and the first resource that frees grants it.
- **No grant to no one.** A grant asks the waiter whether it still
  waits, and the kind whether the request may still be granted. A no
  cancels the request, and the next in line is offered.
- **A waiter is told in the grant's commit.** The rows that wake it and
  the rows the kind starts land with the lease.

## How another namespace composes it

A product adds a kind to `ResourceKind` and the shape of its ask to
`ASK_PAYLOADS`, and registers a `ResourceKindInterface` impl for it at
the root. Its owner writes the resource with its own row through
`register_statement` (or `land_resource` in memory), and retires it
through `retire_statements`. A thing that waits adds a `WaiterKind`
with a `WaiterInterface` impl. An orchestration waits as the core's
waiter: its step asks with `park`, and parks on `resource` until the
grant wakes it. Per-lease facts of a product's own live in its own
table, keyed by the lease's id
([ADR 0086](../../../../../docs/adr/0086-a-scarce-resource-is-leased-under-a-fencing-token.md)).
