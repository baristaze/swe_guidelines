# ADR 0071: A migration waits briefly for a lock

**Status**: accepted (2026-09-28)
## Context

The deploy migrates a live database. The migrate task runs inside the
apply, before the rollout, while the old tasks still serve. That is the
guideline's order (Migrating a Deployed Database), and expand and
contract keeps the old tasks correct over the new schema.

A migration's DDL takes a lock on its table. `DROP INDEX` and most
`ALTER TABLE` forms take `ACCESS EXCLUSIVE`, and `CREATE INDEX` takes
`SHARE`. When a transaction of the serving tasks holds the table, the DDL
waits for it. While it waits, it holds its place in the table's lock
queue. Every read and write of the table that arrives after it queues
behind it, even one that does not conflict with the transaction that
holds the table. So one long transaction and one waiting migration stop
the whole table.

Measured on the local stack, with the API serving and a transaction that
read a table held open for 20 seconds: a migration step that drops an
index on that table, with no bound on its wait, waits 19.6 seconds and
then goes through. A request for the table sent while it waits queues
behind it and hits its own 10-second statement deadline: it answers 500
after 10.08 seconds.

The guideline says every statement carries a deadline from settings. It
says nothing about a migration's wait for a lock. So this is a choice.

## Decision

**Every statement of the migrate command waits for a lock 5 seconds at
most.** `ACME_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS`, a field of
`MigrationSettings`, is sent as `lock_timeout` in the startup packet of
the one connection the runner opens. So the bound holds for every
statement the runner sends: each migration of every role, `check`, and
`ensure-logins`, on the master's connection. A statement past it fails
with `55P03`, and the role's chain rolls back whole, as it does on any
failure.

**Five seconds is half the serving statements' deadline.** A request
that queues behind a waiting migration still has half of its 10 seconds
left when the migration gives up. A migration that needs a lock held
longer than that is behind a transaction that is too long for a live
table anyway.

**There is no statement deadline.** A backfill may run long, and a lock
it holds is one it was granted. The bound is on waiting, not on work.

**A run that gave up asks to be run again.** The command prints which
role gave up and exits 75, the temporary failure of sysexits. Nothing of
that role was applied. The roles before it are at their heads, so a
second run resumes where the first stopped. Every other failure exits 1.

**The pre-rollout task runs again on 75, three runs in all.**
`pre_rollout.sh` starts the same task again when the container exits 75.
The commands (`ensure-logins`, then `migrate --all`) are safe to repeat.
The next task's cold start, about a minute, is the wait between runs,
long enough for a serving transaction to end. A third 75 fails the apply
with the old tasks still serving, as any failed migration does, and the
next apply runs it again.

**One place opens a migration connection.** The Alembic environment
(`om/migrations/env.py`) refuses to run without the runner's connection,
as it refuses to run without a role, so no migration runs outside the
bound.

With the bound, the same run on the local stack, three times: the
migration step gives up after 5.4 seconds and exits 75, having applied
nothing. The request sent while it waits answers 200 in 4.94 to 4.97
seconds.

## Alternatives

- **A statement deadline on the migration connection.** It bounds the
  wait and the work together. A long backfill would fail for running,
  not for waiting.
- **Retry inside the command.** It saves the cold start between runs.
  It also keeps the migrate task's connection and a place in the lock
  queue through each wait, and it hides the retries from the apply log.
  A rerun of the task is what the pre-rollout step already knows how to
  do, and the log says each time it happens.
- **A longer bound.** Every second of it is a second the table stops for
  everything behind the migration.
- **Build indexes `CONCURRENTLY`.** A role's chain runs in one
  transaction, where `CONCURRENTLY` is refused, and it bounds nothing
  for `ALTER TABLE` or `DROP INDEX`.

## Consequences

- A migration behind a long transaction costs the table 5 seconds of
  queueing at most, and the apply a minute or two of reruns.
- A migration that needs a lock held for longer than three runs allow
  fails the apply. The fix is to find the long transaction, not to raise
  the bound.
- `acme-api migrate` and `python -m acme.om.storage.migrate` exit 0, 1,
  or 75.
