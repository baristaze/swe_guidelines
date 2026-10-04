# ADR 0056: Purges across tenants plan with their values

**Status**: accepted (2026-09-28)

## Context

Each namespace purges its rows past their retention once a pass, across
tenants, and each purge reads an index that leads with its retention
column ([ADR 0045](0045-retention-purges-run-once-a-pass-across-tenants.md)).

The driver prepares every statement once per connection and keeps it.
After five runs of a prepared statement, Postgres compares the generic
plan, which knows no value, with the average of the custom plans so
far. When the generic plan looks no dearer, Postgres keeps it and makes
no more custom plans.

A purge deletes a batch chosen by `WHERE col < $1 LIMIT $2`, with no
order. The generic plan guesses that a third of the table is past the
cut, so a scan of the whole table that stops at the limit looks cheap.
When the first five runs on a connection meet a backlog, their custom
plans cost more, and the generic scan wins and stays. The connection
then reads the whole table on every pass, with nothing to purge.

Measured through the driver on a seed of 5,000 tenants, each purge run
eight times with a backlog and then idle on the same connection:

| Purge | Idle plan the connection keeps | Idle execution |
|---|---|---|
| outbox, done rows (100,000) | Seq Scan | 4.7 ms |
| work items (50,000) | Seq Scan | 4.8 ms |
| invitations (20,000) | Seq Scan | 0.99 ms |
| sessions (20,000) | Seq Scan | 0.79 ms |
| api keys, tickets, orchestrations | generic, still on their indexes | under 0.07 ms |
| files, idempotency records, the trim | generic, still on their indexes | under 0.1 ms |

Which purge flips depends on its statistics, so a purge on its index
today can flip tomorrow.

## Decision

**Each purge that deletes across tenants plans its statements with their
values.** The first statement of its transaction is
`SET LOCAL plan_cache_mode = force_custom_plan` (`PLAN_WITH_VALUES` in
`acme.om.storage.impl.pg_base`). The outbox's two statements, the work
items, the tenancy transaction, and the settled orchestrations do this.
The setting is in the purge's own method, never in the storage funnel.
`SET LOCAL` ends with the transaction, so no per-request read runs
under it.

**The files purge names the pending status as a literal.** Its index
holds the pending uploads alone: `ix_files_created_at_pending ON
core.files (created_at) WHERE deleted_at IS NULL AND status =
'pending'`. The read spells the same predicate, so the generic plan
proves it and the index serves any plan. A confirm writes the index no
entry.

**The idempotency purge and the trim keep the generic plan,** which
reads their indexes.

**The plan takes its values, never an order by the retention column.**
An order keeps the generic plan on the index, since an index walk gives
that order and stops at the limit. It does not keep a backlog's custom
plan there. The system scope's policy makes the planner count about 22
rows where 95,000 are past the cut. With 22 rows to sort, a scan and a
sort look cheap, so the planner sorts the whole backlog to take one
batch. On the seed's outbox that batch takes 51 ms, with a sort spilled
to disk, where the index walk takes 2.2 ms.

## Consequences

A purge sends one more statement a transaction, and plans each statement
when it runs. A purge runs a few times a pass, so neither shows: after a
backlog, an idle pass of the outbox purge takes 4.0 ms where a kept scan
takes 8.5, and the work items' 2.0 ms where a kept scan takes 7.0.

The integration suite settles the plan cache as production does. It
fills each table with 20,000 rows over 5,000 tenants, runs each purge
eight times with a backlog on one connection, and then reads the plan
that connection keeps for an idle pass. The plan must read the purge's
index, with sequential scans allowed.

A new purge across tenants takes `PLAN_WITH_VALUES` as its first
statement, or names its predicate as a literal on a partial index.

The purge of sign-in delays has no index on its column, so every plan
of it reads the table. That is a question of an index, not of the plan
cache.
