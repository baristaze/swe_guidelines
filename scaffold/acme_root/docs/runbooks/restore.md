# Restore: bring the database back, and reconcile the outbox

The database is one RDS instance per environment, and its four roles,
`core`, `queue`, `activity`, and `admin`, are four schemas in it. A
backup and a restore are per instance, so a point-in-time restore brings
every role back to the same point. This runbook covers the restore, its
rehearsal, and the one extra step when one role comes back earlier than
the others.

## What is kept

- **Automated backups** with point-in-time recovery, for
  `backup_retention_days` (7 by default,
  `deployment/terraform/modules/database/variables.tf`). Production keeps
  them when its instance is destroyed, with the final snapshot
  `acme-production-final`.
- **Done outbox rows**, for eight days (`outbox_retention` in
  `workers/maintenance/src/acme/workers/maintenance/loop.py`). That
  outlives the backups on purpose, and
  `test_outbox_retention_outlives_the_database_backup_retention` holds
  it: any point a restore can reach still has every outbox row written
  after it.

## A restore is break-glass

Nothing a person or an agent holds day to day may restore a database.
A person is granted the environment's administrator for the incident,
for a bounded time, with the reason recorded.

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

3. Check the copy before anything points at it: the four schemas, each
   version table at the revision the release expects, and the counts the
   incident is about.
4. Point the environment at it through a pull request, never by hand:
   the database module's identifier or the URL secrets. The deploy rolls
   the services onto it.
5. Relay the outbox again (below) when a role came back earlier than
   `core`.
6. Take the administrator back, and record the restore.

## One role earlier than `core`: relay the outbox again

When one role comes back earlier than `core` (its schema copied from a
restored instance into the running one), the outbox rows `core` relayed
to it since that point are marked done, and what they relayed is gone.
Clearing `done_at` on those rows makes the sweep relay them again. The
relay is idempotent on the row's id, so a row whose event or work item
survived changes nothing.

A row's kind names its destination: `work.<kind>` feeds `queue`, and
every other kind feeds `activity`.

```sql
-- activity came back to :point. For queue, use kind LIKE 'work.%'.
BEGIN;
ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
UPDATE core.outbox_rows
   SET done_at = NULL, next_attempt_at = NULL
 WHERE created_at > :point
   AND done_at IS NOT NULL
   AND kind NOT LIKE 'work.%';
-- Compare the count with the rows created after the point before COMMIT.
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;
COMMIT;
```

It runs as `acme_migration`, the one login that owns the table and may
lift `FORCE` inside a transaction. The migrate task definition holds that
login's URL, so the statement runs as a one-off task on it, started by
the person who holds the administrator.

When `core` itself comes back earlier than the others, nothing is
relayed. A work item whose record is gone fails as not found, and an
event names a record a read cannot find. Rows that failed for good
(`failed_at` set) are not relayed by this step; each is named in its
org's stream, and a person decides them one by one.

## The rehearsal

A restore is rehearsed, not assumed: on staging, once a quarter and
after any change to the database module.

1. Restore staging to a point an hour back, to a new instance, as the break-glass steps say.
2. Check the copy as a restore does, and time the whole restore.
3. Run the relay statement against the copy, with `activity` as the
   role, and read the count it answers.
4. Delete the copy (`aws rds delete-db-instance --skip-final-snapshot`
   on the restored identifier).
5. Record it: the date, the environment, the point, the minutes to
   available, and the rows the statement would relay again.

## When it fails

- **The restore time is refused.** It is outside the backup window: pick
  a later point.
- **The copy's version tables differ from the release.** The point is
  before a migration the release needs. Pick a later point, or release
  the matching commit first.
- **The relay count is far from the rows created after the point.** Stop
  before `COMMIT` and read which kinds differ.
