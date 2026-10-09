# ADR 0087: A tenant's share of a lane is held at the claim

**Status**: accepted (2026-10-08)

## Context

Every tenant's work shares the default lane. A tenant that enqueues a
burst takes every worker of the lane until the burst drains, and its
neighbours wait behind it. A lane of its own fixes a tenant whose bulk
work is steady, but a burst should not need a deployment. The
guideline's "The Work Queue" lets a lane cap the items one tenant holds
claimed on it, checked at the claim, and this records how Acme holds
it.

A cap checked at enqueue holds nothing: the work waits and runs later,
past the cap. A count of every item the tenant holds claimed fails
too: two items claimed at once each count the other, both go back, and
both come back together.

## Decision

- **The cap is the lane's, and the worker passes it.** `claim` takes
  `tenant_cap`, the most items one tenant holds claimed on the lane.
  The maintenance worker sets it with its lane, from
  `ACME_WORKER_TENANT_CAP`, and 0, the default, sets none. A claim with
  no cap counts nothing, so a lane without one runs as it did.
- **The count is of the items ahead.** After the claim's statement,
  `count_claimed_ahead` counts the tenant's items on the lane that are
  claimed under a live lease and come earlier in the claim's order:
  an earlier `available_at`, or the same one and a lower id. It reads
  the claim's index, which leads with the lane and the status, so no
  migration is needed.
- **An item over the cap goes back with a delay and keeps its
  attempt.** Its claim is written over, conditionally on its token, as
  the platform's write: queued, available after
  `WorkOptions.over_cap_delay` (30 seconds), and the claim's attempt
  refunded. The claim moves on to the next item, which may be another
  tenant's. A counter, `work`/`over_cap`, and a log line record it.
- **The check comes before the context.** An item that goes back costs
  no read of the tenant's membership.

## Consequences

- Of two items of one tenant claimed at once, the earlier runs. Two
  claims that commit together can still each miss the other, so a
  tenant can run one item past its cap until either ends. Holding that
  moment would take a lock on every claim; the excess is one item and
  short.
- A worker that lost its lease stops counting once the lease runs out,
  so a slot it held frees at the next claim.
- A tenant over its cap pays a claim, a count, and a hand-back for each
  ready item, once per delay. A tenant whose ready backlog is large and
  steady gets a lane of its own instead.
- A handed-back item comes back behind every item of its tenant that
  runs, since its new `available_at` is later than theirs.
- A worker that claims from several lanes passes each lane's cap with
  its lane, and a tenant's own lane can have no cap.
