# ADR 0087: A tenant's share of a lane is held at the claim

**Status**: accepted (2026-10-08)

## Context

Every tenant's work shares the default lane. A tenant that enqueues a
burst takes every worker of the lane until the burst drains, and its
neighbours wait behind it. A lane of its own fixes a tenant whose bulk
work is steady, but a burst should not need a deployment. The
guideline's "The Work Queue" lets a lane cap the items one tenant holds
claimed on it, held at the claim, and this records how Acme holds it.

A cap checked at enqueue holds nothing: the work waits and runs later,
past the cap. A claim that takes an over-cap item and hands it back
with a delay holds the cap, but a freed slot then stays empty until the
delay passes, so a burst drains at about the cap per delay with the
workers idle, and one claim walks the tenant's whole ready backlog, a
write per item.

## Decision

- **The cap is the lane's, and the worker passes it.** `claim` takes
  `tenant_cap`, the most items one tenant holds claimed on the lane.
  The maintenance worker sets it with its lane, from
  `ACME_WORKER_TENANT_CAP`, and 0, the default, sets none. A tenant's
  own cap on the lane takes the lane's place for that tenant, and a
  claim with no lane cap counts only the tenants that have one
  ([ADR 0093](0093-a-tenants-own-cap-holds-in-place-of-the-lanes.md)).
- **The claim's one statement passes over a tenant at its cap.** The
  candidate the claim locks leaves out every tenant that holds the cap
  on the lane under a live lease (`rules.is_at_cap`). That set is one
  count per tenant, made once per statement over the claim's index,
  which leads with the lane and the status, so no migration is needed.
  The memory impl counts the same rows.
- **A passed-over item is not written.** It keeps its place, its
  `available_at`, and its attempts, and the first claim after one of its
  tenant's items ends takes it.

## Consequences

- A burst on a capped lane drains as fast as its workers run, the cap's
  worth at a time, and a neighbour's item is claimed while the tenant
  is at its cap.
- A worker that lost its lease stops counting once the lease runs out,
  so a slot it held frees at the next claim.
- Two claims that commit together can each miss the other, so a tenant
  can run past its cap by the claims of that moment, until one of its
  items ends. Holding that moment would take a lock on every claim.
- A claim on a capped lane reads past the ready items of the tenants at
  their cap in the index, one comparison each, and writes none. A
  tenant whose ready backlog is large and steady gets a lane of its own.
- A worker that claims from several lanes passes each lane's cap with
  its lane, and a tenant's own lane can have no cap.
