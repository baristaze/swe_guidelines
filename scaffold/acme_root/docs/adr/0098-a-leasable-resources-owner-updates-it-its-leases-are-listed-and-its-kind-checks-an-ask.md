# ADR 0098: A leasable resource's owner updates it, its leases are listed, and its kind checks an ask

**Status**: accepted (2026-10-09)

## Context

A leasable resource changes after it is registered. A loading dock gets
a cold room, loses its ramp, goes out of service for a repair, and comes
back. Its owner's row says so first, and the leasable resource must say
the same in the same commit, or a request is granted a dock for what it
no longer offers ([ADR
0086](0086-a-scarce-leasable-resource-is-leased-under-a-fencing-token.md)). A
person also reads what happened: who held the dock, for what, and when
it ended. A lease names only its request, and the request holds what was
asked, so a history that read each request apart would read once per
lease. A wait line that answers each place's wait the same way reads
twice per place. And some kinds take an ask only from certain askers, or
only for what a leasable resource offers: a refusal at the grant comes
after the request waited, sometimes for days. The guideline's "Leases on
a Leasable Resource" states the three, and this records how Acme holds
them.

## Decision

- **The owner updates a leasable resource with its row.**
  `update_statement` (`land_update` in memory) changes each field the
  `LeasableResourceUpdate` names, of the labels, the bound, and the
  availability, in the transaction that writes the owner's row, and
  keeps each it leaves out: a dock out of service for a repair names its
  availability alone, and keeps what it offers and its bound. It takes
  the anchor's lock, and leaves the lease, the token, and the wait line
  as they are. After the commit the owner calls the manager's `offer`,
  and the sweep offers what a crash left. The manager's own `update`
  writes under the anchor's lock and offers at once, as `set_available`
  does. A lease the leasable resource holds keeps its term and its
  expiry; the new bound holds from its next renewal. No route updates a
  leasable resource: the owner's own route does, in its commit.
- **A new leasable resource goes to its wait line at once.** `register`
  offers it, so a selector already waiting is granted it without waiting
  for the sweep. An owner that registers through `register_statement`
  calls `offer` after its commit.
- **A grant fits the leasable resource as its row stands.** Under the
  anchor's lock, `grant` lands only while the request still stands in
  the leasable resource's wait line and the lease's term and window are
  within its bound (`rules.grant_fits`). A grant decided on labels read
  before an update lands nothing, and the update's own offer grants the
  wait line anew.
- **The history is a page at a time, each one read.** `list_leases`
  answers a leasable resource's leases, or the org's, newest first by
  `created_at` and then `id`, ended ones included, each with the request
  it answered, from one outer join. The manager clamps the page to
  `history_limit`, 200, and the route to its own `limit`; it reads one
  entry past the page, so `has_more` is a fact. The next page starts
  after the last lease's `HistoryMark`, its `created_at` and its `id`,
  so two leases granted at one instant both list, and a lease granted
  while a reader pages moves no page. It reads under the tenant's scope,
  so a leasable resource of another tenant has no history here. The
  route is `GET /v1/leases`, with `leasable_resource_id`, `cursor`, and
  `limit`, and answers a `LeasePageView`: the entries and an opaque
  `next_cursor` that carries the mark, null on the last page. A cursor
  from another list is refused with 422. Each entry is the lease's view
  and its request's, without the payload, as the request's view leaves
  it out. The Python client's `lease_history` reads a page.
- **Two indexes on the `core` chain.**
  `202610091856_leases_listed_newest_first` adds
  `ix_leases_org_id_created_at_id` and
  `ix_leases_org_id_leasable_resource_id_created_at_id`, which the two
  reads walk
  backwards to the page's bound.
- **A wait line answers each place and estimate.** `line` reads the
  leasable resource, its kind's leasable resources, and the waiting
  requests, and replays them once (`rules.replay_all`,
  `rules.places_of`), so its `Place` for each request is what that
  request's standing answers. The read count does not grow with the wait
  line. `WaitLineView` carries `places`.
- **A kind may check an ask.** A kind that refuses some asks registers
  an `AskCheckInterface` at the root, beside its hooks
  (`LeasesManagerImpl(asks=...)`); a kind with none accepts every ask.
  The ask calls
  `check_ask` once the payload has its kind's shape and the leasable
  resource it names is live, under the asker's context, with the request
  as it would land: its asker, its payload, and the leasable resource or
  the selector. It refuses by raising `NotAuthorized` or
  `ValidationFailed`, and nothing lands; the route answers 403 or 422,
  and the idempotency record replays the refusal. An ask asked again by
  its key is checked again.

## Consequences

- An owner whose row moves a leasable resource out of service, or
  changes what it offers, lands both in one commit, so no grant falls
  between them.
- A product reads what each lease was for from its request's payload in
  the history, and keeps nothing of it on the lease.
- A request purged before its lease, which only a lease renewed past
  the retention outlives, lists with no request.
- A kind that needs its asker to hold a role, or its payload to fit the
  leasable resource, refuses at the ask; `may_grant` still reads the
  asker's standing live at the grant, since a request may wait for days.
- A refusal by the kind replays under its idempotency key; an asker that
  gains what it lacked asks again under a new key.
- `LeasesManagerInterface` declares 21 operations, past the twenty at
  which arch-check asks for a judgment (CON-01). It stays whole: every
  operation reads or moves one leasable resource's anchor and its wait
  line, and a split would cut the grant's lock from what frees a
  leasable resource.
