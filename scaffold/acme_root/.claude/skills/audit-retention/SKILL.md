---
name: audit-retention
description: "Audit which stores grow without bound and what trims them: every table of every role, with what writes it, when a row is stale, the purge that deletes it, its retention, and whether that purge is batched and indexed; then the stores outside the database (log groups, buckets, queues, the cache). Measures the purges on a database of its own, reads staging's retention settings read-only, and writes a report with verdicts and proposed tickets. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Bash(uv run:*), Bash(git:*), Bash(aws:*), Bash(mkdir:*)
---

# audit-retention

Which tables grow for ever, which purge trims each one, and whether that
purge still works when the table is large. The answer comes from the code,
checked against plans on a seeded database of the run's own, and against
the retention settings staging actually runs with.

Read `.claude/skills/_shared/ops-preamble.md` before the first step: the
profiles, the account check, and the env file are there.

## Input

`[--env local|staging|production] [--scale <n>]`

`--env` is where the stores outside the database are read: `staging` by
default, `local` to read none and say so. `--scale` is the seed's, `1`
by default (5,000 people, a team org of 200 members and 100,000 tasks, a
million events); `0.01` is a quick run with the same shape, whose plans
prove the wiring: a sequential scan on a small table is the planner's
choice, and a quick run's plan findings say "verify at scale 1". The
audit reads and runs the checkout, tools and code alike; to audit
another commit, run it from a checkout of that commit that has
`ops/audit/`. Every command runs from the repository root.

## Role and credential

Investigator, read-only. The database half runs on the local stack
(`make infra-up`, with `make migrate` run once), in a database the run
makes, seeds, and drops; it holds no cloud credential. The cloud half
runs under `acme-<env>-investigate` (`acme-staging-investigate`,
`acme-production-investigate`), checked with `sts get-caller-identity`
before any other `aws` command, as the preamble states. Refuse any
profile wider than the investigate role. Every `aws` command carries
`--profile acme-<env>-investigate` and `--region` with the value of
`.region` in `deployment/cloud/environments.json`, written out in full on
each command: a shell's own region answers from another region, and an
empty answer there looks like nothing deployed. The skill uses the `aws`
command line, not another AWS tool of the session, so the profile check
holds. It reads no env file and no token: it calls no route.

## Procedure

1. Name the run: `audit_retention_<yyyymmdd>`, today's date in UTC. When
   `uv run python ops/audit/auditdb.py list` shows that name taken,
   another run holds it: add a suffix (`_2`), and never drop a database
   this run did not make. Make the evidence folder
   `~/Downloads/acme_retention_<yyyy-mm-dd>/` (`mkdir -p`). Say which
   commit the report read (`git rev-parse HEAD`).
2. List every live table. `explain.py inventory` on the run's database
   (step 4) is the live list; the migrations say why each exists
   (`om/migrations/sql/<role>/*.up.sql`, newest last, a role with no
   folder has no tables), and the table classes are
   `om/src/acme/om/*/storage/tables/*.py`. For each table: what writes
   it (the namespace and the storage method, from
   `om/src/acme/om/<namespace>/storage/impl/postgres.py`), what it grows
   with, and when a row is stale.
3. Find the purge of each table. The sweep is
   `workers/maintenance/src/acme/workers/maintenance/loop.py`
   (`_sweep_once`, which also purges the outbox and the work items
   itself); `build_loop` in that folder's `main.py` wires the rest: the
   per-tenant `purges`, the `across` and `across_batches` purges across
   tenants, and the `chores`. Each calls a manager
   (`om/src/acme/om/<namespace>/impl/manager.py`; the outbox's is
   `outbox/impl/relay.py`), which calls its storage's delete. A
   retention is a field of `settings.py` in that folder (read as
   `ACME_<FIELD>`), passed into the manager's options in `container.py`,
   or an options default in the manager when no setting names it; say
   which. For each purge note its statement, whether it deletes a batch
   (`delete_batch`, `LIMIT`) or everything at once, and which index serves
   its `WHERE`. A table no purge reaches is a gap, unless the product keeps
   its rows as the record (the org row), as one row per tenant (the
   billing account, the event cursor), or as a person's until they are
   erased (the identity), or it is live data (a living org's tasks);
   say which.
4. Measure the purges. Make and seed the run's database:

   ```bash
   uv run python ops/audit/auditdb.py create audit_retention_<yyyymmdd>
   uv run python ops/audit/seed.py audit_retention_<yyyymmdd> --scale <scale>
   uv run python ops/audit/explain.py reset audit_retention_<yyyymmdd>
   ```

   Write each purge's statement into
   `~/Downloads/acme_retention_<yyyy-mm-dd>/purges.sql`, in the file
   format `${CLAUDE_SKILL_DIR}/references/purges.md` gives (read it before writing
   the file: it says how to get the exact SQL and the ids a statement
   binds), and run:

   ```bash
   uv run python ops/audit/explain.py plans audit_retention_<yyyymmdd> ~/Downloads/acme_retention_<yyyy-mm-dd>/purges.sql
   uv run python ops/audit/explain.py inventory audit_retention_<yyyymmdd>
   ```

   A plan with a `Seq Scan` on a large table, or rows read far beyond the
   rows deleted, is a finding with its numbers. A table the seed leaves
   empty is "not measured", never "fine". Each plan runs in a transaction
   that is rolled back, so nothing is deleted.
5. The stores outside the database. The Terraform modules under
   `deployment/terraform/modules/` state each one's retention: log groups
   (`log_retention_days`), buckets' lifecycle rules, and queues'
   `message_retention_seconds`; every cache write carries a TTL
   (`infra/src/acme/infra/cache/valkey.py`). With a cloud `--env`, check
   what is live, read-only (each command with the profile and the region
   of the Role section):

   ```bash
   aws logs describe-log-groups --profile acme-<env>-investigate --region <region>
   aws sqs list-queues --queue-name-prefix acme-<env> --profile acme-<env>-investigate --region <region>
   aws sqs get-queue-attributes --queue-url <url> --attribute-names MessageRetentionPeriod --profile acme-<env>-investigate --region <region>
   aws s3api list-buckets --profile acme-<env>-investigate --region <region>
   aws s3api get-bucket-lifecycle-configuration --bucket <name> --profile acme-<env>-investigate --region <region>
   aws rds describe-db-instances --profile acme-<env>-investigate --region <region>
   ```

   The buckets are the environment's and the account's own (artifacts,
   audit, state); read all of them. A log group with no
   `retentionInDays`, or a bucket that keeps current objects for ever,
   is a finding. The database's size is its `AllocatedStorage` against
   `FreeStorageSpace` (`aws cloudwatch get-metric-statistics --namespace
   AWS/RDS --metric-name FreeStorageSpace --dimensions
   Name=DBInstanceIdentifier,Value=<id> --start-time <a day ago>
   --end-time <now> --period 3600 --statistics Minimum`), since no role
   logs in to it.
6. Drop the run's database, whatever happened before:

   ```bash
   uv run python ops/audit/auditdb.py drop audit_retention_<yyyymmdd>
   ```

7. Write the report, and the proposed tickets in it. Each ticket names
   the table, the evidence, the fix, and the effort. The skill files
   none.

## What it never does

- Never writes to a shared database or to an environment: it logs in
  only to the database it made on the local stack, and drops it.
- Never modifies a tracked file, never commits, never opens a pull
  request: it writes a report and proposes tickets.
- No `aws` verb that is not `describe`, `get`, or `list`; no secret read.
- No row count from a cloud: no role reads its rows, and the report says
  so rather than guessing.

## Output

`~/Downloads/acme_retention_<yyyy-mm-dd>.md`:

```markdown
# Acme: which stores grow for ever, and what trims them

Read at <commit>; seeded at scale <n>; stores read in <env> under <profile and Arn>, <region>.

## The answer

<two paragraphs: what is trimmed, the gaps, the risks, most important first>

## Every table

| table | what writes it | grows with | stale when | trimmed by (the storage's file:line) | retention (setting or default) | batched / indexed | verdict |
|---|---|---|---|---|---|---|---|

Verdicts: fine, risk, gap, kept on purpose, not measured.

## Findings, by impact

### <gap or risk>: <table>
**What.** **The harm.** **Evidence** (plan, rows read, file:line).
**Fix.** **Effort** S/M/L. **Proposed ticket:** <title>.

## Stores outside the database

| store | retention | verdict |

## What I could not verify
```
