# ADR 0056: Purges across tenants plan with their values

**Status**: accepted (2026-09-26). Amends
[ADR 0045](0045-retention-purges-run-once-a-pass-across-tenants.md): each
purge still reads an index that leads with its retention column, and now
the plan cache cannot take that index away.

## Context

The driver prepares every statement once per connection and keeps it.
After five runs of a prepared statement, Postgres compares the generic
plan, which knows no value, with the average of the custom plans it made
so far. When the generic plan looks no dearer, it keeps it and makes no
more custom plans.

A purge is `SELECT id … WHERE col < $1 LIMIT $2` with no order. The
generic plan guesses that a third of the table is past the cut, so a scan
of the whole table that stops at the limit looks cheap. When the first
five runs on a connection meet a backlog, their custom plans cost more,
and the generic scan wins. It stays: the connection reads the whole table
on every pass after, with nothing to purge.

Measured through the driver on the 5,001-tenant seed, with the tables it
leaves empty filled to 20,000 rows, each purge run eight times with a
backlog and then idle on the same connection:

| Purge | Idle plan the connection keeps | Idle execution |
|---|---|---|
| outbox, done rows (100k) | Seq Scan | 4.7 ms |
| work items (50k) | Seq Scan | 4.8 ms |
| invitations (20k) | Seq Scan | 0.99 ms |
| Slack install states (20k) | Seq Scan | 0.83 ms |
| sessions (20k) | Seq Scan | 0.79 ms |
| api keys, tickets, delivery marks, posts, orchestrations | generic, still on their indexes | under 0.07 ms |
| files, idempotency records, the trim | generic, still on their indexes | under 0.1 ms |
| tasks, users, memberships, Slack installations, failed outbox rows | custom | under 0.05 ms |

Which purge flips depends on its statistics, so a purge on its index
today can flip tomorrow.

## Decision

**Each purge that deletes across tenants plans its statements with their
values.** The first statement of its transaction is
`SET LOCAL plan_cache_mode = force_custom_plan` (`PLAN_WITH_VALUES`).
The outbox's two statements, the work items, the tenancy transaction,
Slack's transaction, the delivery marks, and the orchestrations do this.
The setting is in the purge's own method. The storage funnel does not
set it, and no per-request read runs under it, since `SET LOCAL` ends
with the transaction.

**The files purge names the pending status as a literal.** Its index
holds the pending uploads alone:
`(created_at) WHERE deleted_at IS NULL AND status = 'pending'`. The
generic plan proves that predicate, since the read spells it, and so the
index serves it in any plan. The index is 16 kB where the old
`(status, created_at)` was 496 kB, and a confirm writes it no entry.

**The reads that already hold their index stay as they are.** The read
of deleted tasks has an order by its retention column. The idempotency
purge and the trim keep generic plans that read their indexes.

## Alternatives

An order by the retention column keeps the generic plan on the index,
since an index walk gives that order and stops at the limit. It does not
keep a backlog's custom plan there. The system scope's policy makes the
planner count about 22 rows where 95,000 are past the cut. With 22 rows
to sort, a scan and a sort look cheap, so the planner sorts the whole
backlog to take one batch. On the seed's outbox that batch took 51 ms,
with a sort spilled to disk, where the walk takes 2.2 ms and the batch
with no order 9.8 ms. The order is kept only where it was: the read of
deleted tasks, whose partial index holds deleted rows alone.

## Consequences

A purge sends one more statement a transaction, and plans each statement
when it runs. A purge runs a few times a pass, so neither shows: after a
backlog, an idle pass of the tenancy purge takes 5.0 ms where it took
6.6, the outbox's 4.0 where it took 8.5, the work items' 2.0 where it
took 7.0. A backlog's pass takes what it took.

The integration suite settles the plan cache as production does. It
fills each table with 20,000 rows over 5,000 tenants, runs each purge
eight times with a backlog on one connection, and then reads the plan
that connection keeps for an idle pass. The plan must read the purge's
index, with sequential scans allowed.

The purge of sign-in delays has no index on its column at all, so every
plan of it reads the table. That is a question of an index, not of the
plan cache, and it is left out here.
