# ADR 0045: Retention purges run once a pass across tenants

**Status**: accepted (2026-09-26), amended by
[ADR 0056](0056-purges-across-tenants-plan-with-their-values.md): each
purge plans with its values; and by
[ADR 0070](0070-the-sweep-reads-only-the-tenants-with-a-chore-due.md):
the chores run in the tenants one read across tenants names, not in
every tenant the ring takes. Amends
[ADR 0040](0040-the-event-stream-has-a-floor.md): the trim runs once a
pass for every org, not once per org. The floor, and the transaction
that moves it, stand.

## Context

The sweep visits every tenant in a ring, under one service context
each, within a budget of 20 seconds a pass. The requeue of expired
leases and the outbox relay already run once a pass across tenants.
The purges of rows past their retention did not: each namespace asked
each tenant, one transaction at a time.

A tenant with nothing to purge paid for every one of those asks. On
the query index audit's seed (5,001 tenants, one of them heavy), an
idle tenant cost 21 to 24 ms p50 a pass, most of it the eight purges,
1.5 to 4.8 ms each, of round trips rather than database time. A pass reached
740 to 930 tenants, so a full cycle took six or seven passes, over
five minutes, and it grows with every tenant. A tenant's rows waited
that long past their retention.

The rows a retention purge deletes need no tenant to find them. Each
is picked by its own column: `deleted_at`, `expires_at`, `created_at`,
`updated_at`, or `produced_at`. The tenant only picks where a query
starts.

## Decision

**Each namespace's purge of rows past their retention runs once a pass,
across tenants, in the system scope.** It is one named storage method
per namespace, a batch per statement, rows chosen with `FOR UPDATE
SKIP LOCKED`, and called again while its batch comes back whole and
the budget lasts. Each statement reads an index that leads with the
retention column; where a status picks the rows, the status leads and
the retention column follows. The methods take no tenant, so each is an
enumerated exception of the storage rule, with its reason.

**The ring keeps only the tenant-shaped work.** The purge of a tenant
deleted longer ago than the retention stays per tenant. It deletes rows
that carry no retention of their own, such as open tasks, the billing
account, and the event cursor. The pass already knows which tenants
expired, so a living tenant's purge reads nothing. The day's cleanup
opening stays per tenant too, and so does the mark of a tenant purged.

Namespace by namespace:

- **Tasks** go across tenants, with one tenant-shaped part. The read of
  deleted tasks is one statement a pass, on `(deleted_at)`. A deleted
  task's attachments are still detached first, under its tenant's
  service context, because that write lands an outbox row with its
  actor. Only a tenant with such a task pays for it. The context is the
  one the pass minted, deleted tenants included, from
  `sweep_context`. A tenant marked purged has none; its files went with
  it, so its tasks go as they are.
- **Media** goes across tenants. The object is deleted by the tenant and
  the key the read returns, then the rows go in one statement. The
  batch stays a hundred, since each row costs a request to the store.
  The loop holds a media purge's count against that batch.
- **Tenancy** goes across tenants in one transaction: removed users with
  their memberships, ended memberships, revoked and expired keys,
  expired sessions (the system scope's sign-ins among them), expired
  tickets, and closed invitations, then the sign-in delays.
- **Idempotency**, **billing**, **Slack**, and **orchestrations** go
  across tenants, each a statement per table. Slack's three tables share
  one transaction.
- **Events** go across tenants in one statement. The `limit` events
  produced longest ago, read on `(produced_at)`, name the tenants and
  each one's share of the batch. Each tenant's cursor row is locked as
  an append locks it, but a row an append holds is skipped, not waited
  on. Each tenant's window is its lowest events above its floor, as
  many as its share. The run is the window below its first young event.
  The events of each run go, and each floor moves to its run's top, in
  the same statement.

## Consequences

A living tenant costs a pass its chores and nothing else: about 3 ms,
the cleanup's probe and the respace's. On the same seed a pass takes
14 to 16.5 seconds and reaches all 5,001 tenants, where it took 20
seconds for 740 to 930. The purges across tenants cost about 20 ms a
pass when idle. The chores are what a pass spends per tenant now, so
they are what bounds the tenants one pass reaches.

Twenty indexes lead with a retention column, and the three indexes
that only the per-tenant purges read go. The idempotency markers' index
by attempt serves the re-mint's fence too, so the fence's index that
led with `org_id` goes. Each tenant keeps an index that
`org_id` leads, for the purge of a tenant past its retention.

The trim's batch is picked by age. An old event that sits above a young
one, because the relay numbered it late, holds a share of the batch
without a run. It holds it only until the young one passes the
retention too, at most the relay's delay.

A trim does not wait on an append. A stream whose cursor an append
holds is trimmed on the next call.

A tenant marked purged is no longer visited, and nothing of it remains
to purge. The purges across tenants would reach its rows anyway, since
they visit no tenant.

The system scope's policy still makes the planner under-count rows on
these statements. Each plan was read on the seeded database, and each
walks its index. Under a policy, Postgres checks the policy before any
operator that may raise, and `+` may. So the top of the trim's window
is a column, computed with the lock, and not a sum in a join: a sum
there would read a whole stream through the filter instead of bounding
the index scan. The trim of a thousand events takes about 3 ms.
