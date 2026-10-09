# ADR 0098: A resource's owner updates it, its leases are listed, and its kind checks an ask

**Status**: accepted (2026-10-09)

## Context

A resource changes after it is registered. A loading dock gets a cold
room, loses its ramp, goes out of service for a repair, and comes back.
Its owner's row says so first, and the resource must say the same in
the same commit, or a request is granted a dock for what it no longer
offers ([ADR 0086](0086-a-scarce-resource-is-leased-under-a-fencing-token.md)).
A person also reads what happened: who held the dock, for what, and
when it ended. A lease names only its request, and the request holds
what was asked, so a history that read each request apart would read
once per lease. A line that answers each place's wait the same way
reads twice per place. And some kinds take an ask only from certain
askers, or only for what a resource offers: a refusal at the grant
comes after the request waited, sometimes for days. The guideline's
"Leases on a Resource" states the three, and this records how Acme
holds them.

## Decision

- **The owner updates a resource with its row.** `update_statement`
  (`land_update` in memory) changes the labels, the bound, and, when the
  `ResourceUpdate` names it, the availability, in the transaction that
  writes the owner's row. It takes the anchor's lock, and leaves the
  lease, the token, and the line as they are. After the commit the
  owner calls the manager's `offer`, and the sweep offers what a crash
  left. The manager's own `update` writes under the anchor's lock and
  offers at once, as `set_available` does. A lease the resource holds
  keeps its term and its expiry; the new bound holds from its next
  renewal. No route updates a resource: the owner's own route does,
  in its commit.
- **A new resource goes to its line at once.** `register` offers it, so
  a selector already waiting is granted it without waiting for the
  sweep. An owner that registers through `register_statement` calls
  `offer` after its commit.
- **A grant fits the resource as its row stands.** Under the anchor's
  lock, `grant` lands only while the request still stands in the
  resource's line and the lease's term and window are within its bound
  (`rules.grant_fits`). A grant decided on labels read before an
  update lands nothing, and the update's own offer grants the line
  anew.
- **The history is one read.** `list_leases` answers a resource's
  leases, or the org's, newest first by `created_at` and then `id`,
  ended ones included, each with the request it answered, from one
  outer join. The manager clamps the page to `history_limit`, 200, and
  the route to its own `limit`. It reads under the tenant's scope, so a
  resource of another tenant has no history here. The route is
  `GET /v1/leases`, with `resource_id` and `limit`; each entry is the
  lease's view and its request's, without the payload, as the request's
  view leaves it out. The Python client's `lease_history` reads it.
- **Two indexes on the `core` chain.**
  `202610091856_leases_listed_newest_first` adds
  `ix_leases_org_id_created_at_id` and
  `ix_leases_org_id_resource_id_created_at_id`, which the two reads walk
  backwards to the page's bound. No column changes.
- **A line answers each place and estimate.** `line` reads the
  resource, its kind's resources, and the waiting requests, and replays
  them once (`rules.replay_all`, `rules.places_of`), so its `Place` for
  each request is what that request's standing answers. The read count
  does not grow with the line. `LineView` gains `places`.
- **A kind may check an ask.** A kind that refuses some asks registers
  an `AskCheckInterface` at the root, beside its hooks
  (`LeasesManagerImpl(asks=...)`); a kind with none accepts every ask,
  so a kind registered before this takes asks as it did. The ask calls
  `check_ask` once the payload has its kind's shape and the resource it
  names is live, under the asker's context, with the request as it
  would land: its asker, its payload, and the resource or the
  selector. It refuses by raising
  `NotAuthorized` or `ValidationFailed`, and nothing lands; the route
  answers 403 or 422, and the idempotency record replays the refusal.
  An ask asked again by its key is checked again.

## Consequences

- An owner whose row moves a resource out of service, or changes what
  it offers, lands both in one commit, so no grant falls between them.
- A product reads what each lease was for from its request's payload in
  the history, and keeps nothing of it on the lease.
- A request purged before its lease, which only a lease renewed past
  the retention outlives, lists with no request.
- A kind that needs its asker to hold a role, or its payload to fit the
  resource, refuses at the ask; `may_grant` still reads the asker's
  standing live at the grant, since a request may wait for days.
- A refusal by the kind replays under its idempotency key; an asker that
  gains what it lacked asks again under a new key.
- `LeasesManagerInterface` declares 21 operations, past the twenty
  arch-check names for a review (CON-01). It stays whole: every
  operation reads or moves one resource's anchor and its line, and a
  split would cut the grant's lock from what frees a resource.
