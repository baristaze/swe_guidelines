# ADR 0045: Retention purges run once a pass across tenants

**Status**: accepted (2026-09-28)

## Context

The sweep visits every tenant in a ring, under one service context
each, within a budget of 20 seconds a pass. The requeue of expired
leases and the outbox relay run once a pass across tenants.

A purge that asks each tenant in turn costs every idle tenant a
transaction per namespace, mostly in round trips. Measured on a seed of
5,000 tenants, eight such purges cost an idle tenant about 20 ms a
pass. One pass then reached fewer than a thousand tenants, and a
tenant's rows waited minutes past their retention. The cost grows with
every tenant.

A row past its retention needs no tenant to find it. Each is picked by
its own column: `deleted_at`, `expires_at`, `created_at`, `updated_at`,
or `produced_at`.

## Decision

**Each namespace's purge of rows past their retention runs once a pass,
across tenants, in the system scope.** It is one named storage method
per namespace, a batch per statement, rows chosen with `FOR UPDATE SKIP
LOCKED`, called again while its batch comes back whole and the budget
lasts. Each statement reads an index that leads with the retention
column; where a status picks the rows, the status leads and the
retention column follows. The methods take no tenant, so each is an
enumerated exception of the storage rule, with its reason. Each plans
with its values
([ADR 0056](0056-purges-across-tenants-plan-with-their-values.md)).

**The ring keeps only the tenant-shaped work.** The purge of a tenant
deleted longer ago than the retention stays per tenant: it deletes rows
that carry no retention of their own. A living tenant's purge reads
nothing. The mark of a tenant purged stays per tenant too.

Namespace by namespace:

- **Media**: the object of a file deleted past the retention, or of an
  upload never confirmed, is deleted by the tenant and the key the read
  returns, then the rows go in one statement. The batch is a hundred,
  since each row costs a request to the store.
- **Tenancy**: in one transaction, ended users with their
  memberships, ended memberships, revoked and expired keys, expired
  sessions (the system scope's sign-ins among them), expired tickets,
  and closed invitations, then the sign-in delays.
- **Idempotency** and **orchestrations**: a statement per table.
- **Events**: the trim, in one statement. The `limit` events produced
  longest ago, read on `(produced_at)`, name the tenants and each one's
  share of the batch. Each tenant's cursor row is locked as an append
  locks it, but a row an append holds is skipped, not waited on. Each
  tenant's run is the stretch of its window below its first young
  event. The events of each run go, and each floor moves to its run's
  top, in the same statement
  ([ADR 0040](0040-the-event-stream-has-a-floor.md)).
- **Outbox** and **work**: the done outbox rows and the settled work
  items, after every namespace's purge.

## Consequences

A living tenant costs a pass no purge at all. Each purge across tenants
costs a pass a few milliseconds when idle. What a pass spends per
tenant is its visit, and that bounds the tenants one pass reaches.

The indexes follow the reads. Each purge across tenants has an index
that leads with its retention column, and each tenant keeps an index
that `org_id` leads, for the purge of a tenant past its retention.

The trim's batch is picked by age. An old event that sits above a young
one, because the relay numbered it late, holds a share of the batch
without a run, at most for the relay's delay.

A trim does not wait on an append. A stream whose cursor an append
holds is trimmed on the next call.

The system scope's policy makes the planner under-count rows on these
statements. Under a policy, Postgres checks the policy before any
operator that may raise, and `+` may. So the top of the trim's window
is a column, computed with the lock, never a sum in a join: a sum there
would read a whole stream through the filter instead of bounding the
index scan.
