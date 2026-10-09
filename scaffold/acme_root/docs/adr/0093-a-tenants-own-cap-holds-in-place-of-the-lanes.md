# ADR 0093: A tenant's own cap holds in place of the lane's

**Status**: accepted (2026-10-09)

## Context

A lane's cap
([ADR 0087](0087-a-tenants-share-of-a-lane-is-held-at-the-claim.md))
is one number for every tenant on the lane, set with the worker. One
tenant often needs a number of its own: a tenant whose bursts crowd its
neighbours is held lower, and one that is owed more is let higher.
With the lane's cap alone, either change is a deployment: a lane of its
own, or a new cap for every tenant at once. The guideline's "The Work
Queue" lets a tenant have its own cap on a lane, which an operator sets,
and this records how Acme holds it.

## Decision

- **A tenant's own cap is a row in the `queue` role.** `TenantCap`
  holds one lane and one cap, from 1 to 10,000, and a unique index
  keeps one row per tenant and lane. It sits beside the work items, so
  the claim reads it in its own statement.
- **The caps are fenced as the queue is**, one policy per login
  ([ADR 0044](0044-the-queue-is-fenced-by-one-policy-per-login.md)). The
  runtime login reaches the tenant its transaction names, so an
  operator's write reaches only the tenant it names. The system login
  reaches every tenant under the system scope, so the claim reads the
  caps of its lane.
- **An operator sets it, with no deployment.** The work operator
  manager sets a cap or writes it over, and clears it, with the write
  permission, and reads it with the read permission, at
  `/v1/admin/orgs/{org_id}/work/lanes/{lane}/cap`. A deleted tenant
  takes no new cap, and a read or a clear where there is none is `404`.
  Each call leaves the support trail's log line, naming the operator
  and the tenant, as the plane's other operator calls do.
- **The claim's one statement joins the caps.** The count ADR 0087
  makes once per statement is joined to the tenants' caps on the lane.
  With a lane cap, the join is outer, and a tenant is at its cap when
  its count reaches its own cap, or the lane's where it has none
  (`rules.cap_for`). With no lane cap, the join is inner, so only a
  tenant with a cap of its own is counted. Nothing reads the caps
  before the claim, and the memory impl counts the same rows.
- **A tenant past its retention takes its caps with it.** The sweep
  purges them with the tenant's other rows, a batch at a time.

## Consequences

- A cap set or cleared holds from the next claim on, with no deployment
  and no restart of a worker.
- A tenant's own cap can be above the lane's as well as below it, so
  one tenant can hold more workers than its neighbours.
- With no row, the claim holds what it held before: the lane's cap for
  every tenant, or no tenant passed over.
- A claim on a lane with no cap now counts the lane's claimed items
  under a live lease and looks each one's tenant up in the caps' index.
  Those are as many rows as the lane's workers run. Measured with 50,000
  queued items, 40 claimed, and 500 caps on the lane, the claim runs in
  about 0.1 ms, with a lane cap or without one.
- The operator plane does not know how many workers a lane runs, so a
  cap at or above that count holds nothing, and nothing refuses it.
- A cap is the platform's setting for a tenant, not the tenant's own
  act, so it writes no event to the tenant's stream; the log line is
  its trail.
