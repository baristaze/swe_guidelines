---
name: audit-retention
description: "Audit which stores grow without bound and what trims them: every table of every role, with what writes it, when a row is stale, the purge that deletes it, its retention, and whether that purge is batched and indexed; then the stores outside the database (log groups, buckets, queues, the cache). Measures the purges on a database of its own, reads a cloud's retention settings read-only, and writes a report with verdicts and proposed tickets. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Bash(uv run:*), Bash(git:*), Bash(aws:*), Bash(mkdir:*)
---

# audit-retention

Which tables grow for ever, which purge trims each one, and whether that
purge still works when the table is large. The answer comes from the code,
checked against plans on a seeded database of the run's own, and against
the retention settings the environment runs with.

## Input

`[--env local|staging|production] [--scale <n>]`

`--env` is where the stores outside the database are read, `staging` by
default; `local` reads none of them and says so. `--scale` is the seed's,
`1` by default; `0.01` is a quick run with the same shape, whose plan
findings say "verify at scale 1". The audit reads and runs the checkout,
tools and code alike; to audit another commit, run it from a checkout of
that commit that has `ops/audit/`. Every command runs from the repository
root.

## Role and credential

Investigator, read-only. The database half runs on the local stack
(`make infra-up`, with `make migrate` run once), in a database the run
makes, seeds, and drops; it holds no cloud credential. The cloud half
runs under the investigate profile, `acme-<env>-investigate`. Before any
other `aws` command, run

```bash
aws sts get-caller-identity --profile acme-<env>-investigate
```

and check that `Account` is the environment's `account_id` in
`deployment/cloud/environments.json` and that `Arn` reads
`assumed-role/acme-investigate-<env>/...`. Refuse any profile wider than
the investigate role. Every `aws` command carries `--profile
acme-<env>-investigate` and `--region` with the value of `.region` in
`deployment/cloud/environments.json`: a shell's own region answers from
another region, and an empty answer there looks like nothing deployed.
The skill reads no env file and no token.

## Procedure

1. Name the run `audit_retention_<yyyymmdd>`, today's date in UTC.
   When `uv run python ops/audit/auditdb.py list` shows that name taken,
   another run holds it: add a suffix (`_2`), and never drop a database
   this run did not make. Make the evidence folder
   `~/Downloads/acme_retention_<yyyy-mm-dd>/` and say which commit the
   report read.
2. List every live table: the migrations are the truth
   (`om/migrations/sql/<role>/`, newest last), the table classes are
   `om/src/acme/om/<namespace>/storage/tables/`. For each, what writes it
   (the storage impl, `om/src/acme/om/<namespace>/storage/impl/postgres.py`),
   what it grows with, and when a row is stale.
3. Find the purge of each table: the sweep in the worker's `loop.py`, the
   manager's purge it calls, the storage statement it sends, and the
   retention setting in the worker's `settings.py`. Note whether each
   statement deletes a batch or everything at once, and which index
   serves its `WHERE`. A retention is a setting, or the manager's
   options default when no setting names it; say which. A table no purge
   reaches is a gap, unless the product keeps its rows as the record, as
   one row per tenant, or as a person's until they are erased, or it is
   live data; say which.
4. Measure the purges on a database of the run's own:

   ```bash
   uv run python ops/audit/auditdb.py create audit_retention_<yyyymmdd>
   uv run python ops/audit/seed.py audit_retention_<yyyymmdd> --scale <scale>
   uv run python ops/audit/explain.py reset audit_retention_<yyyymmdd>
   uv run python ops/audit/explain.py plans audit_retention_<yyyymmdd> ~/Downloads/acme_retention_<yyyy-mm-dd>/purges.sql
   uv run python ops/audit/explain.py inventory audit_retention_<yyyymmdd>
   ```

   `purges.sql` holds each purge's statement as the storage sends it,
   with the cut-off its setting gives, each under `-- name: <purge>` and
   `-- scope: <org id | system>`, ending with `;`. `explain.py rows
   <name> "<SELECT>"` reads the ids the seed made. A plan with a
   sequential scan on a large table, or rows read far beyond the rows
   deleted, is a finding with its numbers; a table the seed leaves empty
   is "not measured". Each plan is rolled back.
5. The stores outside the database: each Terraform module under
   `deployment/terraform/modules/` states a retention (log groups,
   buckets' lifecycle rules, queues' message retention); the cache's
   keys carry a TTL. With a cloud `--env`, read what is live with
   `aws logs describe-log-groups`, `aws sqs get-queue-attributes`, and
   `aws s3api get-bucket-lifecycle-configuration`. A log group with no
   retention is a finding.
6. Drop the run's database, whatever happened before:

   ```bash
   uv run python ops/audit/auditdb.py drop audit_retention_<yyyymmdd>
   ```

7. Write the report, with the proposed tickets in it. The skill files
   none.

## What it never does

- Never writes to a shared database or to an environment: it logs in
  only to the database it made on the local stack, and drops it.
- Never modifies a tracked file, never commits, never opens a pull
  request: it writes a report and proposes tickets.
- No `aws` verb that is not `describe`, `get`, or `list`; no secret read.

## Output

`~/Downloads/acme_retention_<yyyy-mm-dd>.md`:

```markdown
# Acme: which stores grow for ever, and what trims them

Read at <commit>; seeded at scale <n>; stores read in <env> under <profile and Arn>.

## The answer

## Every table

| table | what writes it | grows with | stale when | trimmed by (file:line) | retention | batched / indexed | verdict |

## Findings, by impact

### <gap or risk>: <table>
What. The harm. Evidence. Fix. Effort S/M/L. Proposed ticket.

## Stores outside the database

## What I could not verify
```
