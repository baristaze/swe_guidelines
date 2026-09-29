# Restore: bring the database back, and reconcile what the outbox fed

The database is one RDS instance per environment, and the three roles,
`core`, `activity`, and `queue`, are three schemas in it. So a backup
and a restore are per instance, and a point-in-time restore brings all
three back to the same point. This runbook says how a restore runs, how
it is rehearsed, and the one step a restore that brings one role back
earlier than the others needs: relaying the outbox again.

## What is kept

- **Automated backups** with point-in-time recovery, kept
  `backup_retention_days` (7 by default,
  `deployment/terraform/modules/database/variables.tf`). Production's
  are kept when the instance is destroyed (`delete_automated_backups`
  is false there), and its destroy leaves the final snapshot
  `acme-production-final`.
- **Done outbox rows**, kept eight days (`outbox_retention` in
  `workers/maintenance/src/acme/workers/maintenance/loop.py`), then
  purged by the maintenance sweep. The retention outlives the backups on
  purpose, and a test holds it longer
  (`test_outbox_retention_outlives_the_database_backup_retention`): any
  point a restore can reach still has every outbox row written after
  it.

## A restore is break-glass

Nothing a person or an agent holds day to day may restore a database: a
restore is a write, and the deploy role never restores. So a restore is
the break-glass: a person is granted the environment's administrator
for the incident, time-bound, with the reason recorded, and what they
change by hand is reconciled by a pull request afterwards.

1. Pick the point: the last moment the data was right, in UTC, inside
   the backup window.
2. Restore to a **new** instance, never over the running one:

   ```bash
   aws rds restore-db-instance-to-point-in-time --profile acme-<env>-admin \
     --source-db-instance-identifier acme-<env> \
     --target-db-instance-identifier acme-<env>-restored-<yyyymmddhhmm> \
     --restore-time <point, ISO 8601 UTC> \
     --db-subnet-group-name <the instance's subnet group> \
     --vpc-security-group-ids <the database's security group> \
     --no-publicly-accessible
   aws rds wait db-instance-available --profile acme-<env>-admin \
     --db-instance-identifier acme-<env>-restored-<yyyymmddhhmm>
   ```

3. Check the copy before anything points at it: the three schemas, the
   version tables at the revisions the release expects, and the counts
   the incident is about.
4. Point the environment at it through a pull request, never by hand:
   the database module's identifier or the URL secrets, whichever the
   change needs. The deploy rolls the services, and every new task
   reads the new URL.
5. The reconciliation below, when a role came back earlier than its
   siblings.
6. Take the administrator back, and record the restore in Rehearsals
   and restores below.

## One role earlier than the others: relay the outbox again

A point-in-time restore of the instance brings every role back to the
same point, and nothing needs relaying. When one role comes back
earlier than `core` (its schema copied from a restored instance into
the running one), the outbox rows `core` relayed to it since that point
are marked done, and what they relayed is gone. The last step of that
restore clears `done_at` on those rows, and the sweep relays them
again. That is harmless: the relay is idempotent on the row's id, so a
row whose event or work item survived is a no-op.

The row's kind names its destination: `work.<kind>` feeds `queue`, and
every other kind (`<namespace>.<entity>.<action>`) feeds `activity`.

```sql
-- activity came back to :point. For queue, use kind LIKE 'work.%'.
BEGIN;
ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
UPDATE core.outbox_rows
   SET done_at = NULL, next_attempt_at = NULL
 WHERE created_at > :point
   AND done_at IS NOT NULL
   AND kind NOT LIKE 'work.%';
-- The count the UPDATE answers is the number of rows to be relayed again;
-- it is the number of rows created after the point that the relay had
-- marked done. Compare it with that count before the COMMIT.
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;
COMMIT;
```

It runs as the migration login, `acme_migration`, the one login that
owns the table and may lift `FORCE` inside a transaction; the runtime
login owns nothing and cannot. The migrate task definition holds that
login's URL, so the statement runs as a one-off task on it, started by
the person who holds the administrator for the incident.

When `core` itself comes back earlier than the others, nothing is
replayed. The events and work items of the lost writes remain: a work
item whose record is gone fails as not found, and an event names a
record a read no longer finds.

Rows that failed for good (`failed_at` set) are not relayed by this
step. Each is named in its org's diary; decide them one by one.

## The rehearsal

A restore is rehearsed, not assumed. On staging, once per quarter and
after any change to the database module:

1. Restore staging to a point an hour back, to a new instance (step 2
   above).
2. Check the copy (step 3), and time the whole restore.
3. Run the reconciliation statement against the copy, not the running
   instance, with `activity` as the role, and read the count it
   answers.
4. Delete the copy: `aws rds delete-db-instance --skip-final-snapshot`
   on the restored identifier.
5. Record it below.

## Rehearsals and restores

| Date | Environment | Kind | Point | Minutes to available | Rows relayed again | Notes |
|------|-------------|------|-------|----------------------|--------------------|-------|

None has run yet. The first rehearsal is due before production holds a
customer's data.
