---
name: audit-query-indexes
description: "Audit whether the indexes fit the queries, and what breaks first under load: every statement the Postgres storage sends, mapped to the index that serves it and measured with EXPLAIN on a database of its own seeded at a stated scale, under the login and the tenant scope the application uses. Reports each statement's verdict, the hot paths, the unused indexes, and fixes tested on the same data. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Bash(uv run:*), Bash(git:*), Bash(mkdir:*)
---

# audit-query-indexes

Every query the platform sends, the index that serves it, and what it
costs when the data is large. The costs are measured, not guessed: on a
database the run makes and seeds, under the same login, row-level
security policy, and scope the storage funnel uses.

## Input

`[--scale <n>] [--focus <namespace,...>]`

`--scale` is the seed's, `1` by default; `0.01` is a quick run with the
same shape, whose times prove the wiring and nothing about scale.
`--focus` narrows the audit, its hot paths included, to some namespaces;
every namespace by default. The audit reads and runs the checkout, tools
and code alike; to audit another commit, run it from a checkout of that
commit that has `ops/audit/`. Every command runs from the repository root.

## Role and credential

None, local only. The skill runs on the local stack (`make
infra-up`, with `make migrate` run once), in a database it makes, seeds,
and drops. It holds no cloud credential and reads no environment.

## Procedure

1. Name the run `audit_query_indexes_<yyyymmdd>`, today's date in UTC.
   When `uv run python ops/audit/auditdb.py list` shows that name taken,
   another run holds it: add a suffix (`_2`), and never drop a database
   this run did not make. Make the evidence folder
   `~/Downloads/acme_query_indexes_<yyyy-mm-dd>/` and say which commit
   the report read.
2. List every statement of the storage impls
   (`om/src/acme/om/<namespace>/storage/impl/postgres.py`), the indexes of
   the table classes and the migrations (`om/migrations/sql/<role>/`), and
   for each statement its caller and how often it runs: every request,
   every write, every creating POST, once per sweep pass per tenant, once
   per pass, or rare.
3. Make and seed the run's database:

   ```bash
   uv run python ops/audit/auditdb.py create audit_query_indexes_<yyyymmdd>
   uv run python ops/audit/seed.py audit_query_indexes_<yyyymmdd> --scale <scale>
   uv run python ops/audit/explain.py reset audit_query_indexes_<yyyymmdd>
   ```

4. Write every statement as the storage sends it into
   `~/Downloads/acme_query_indexes_<yyyy-mm-dd>/statements.sql`, each
   under `-- name:`, `-- scope: <org id | system>`, `-- user: <id>` where
   a policy reads it, and `-- params: <literals>` for its generic plan
   (written with `$1`, `$2`), ending with `;`. Measure each where its cost
   differs: a small tenant and the large one, a first page and a deep one,
   a filter that matches much and one that matches nothing, the tenant
   scope and the system scope; `explain.py rows <name> "<SELECT>"` reads
   the ids the seed made. Each statement runs on a connection of its own,
   so a file holds any number of generic plans. Then:

   ```bash
   uv run python ops/audit/explain.py plans audit_query_indexes_<yyyymmdd> ~/Downloads/acme_query_indexes_<yyyy-mm-dd>/statements.sql
   ```

   A verdict per statement: `fine` (an index condition that stops at the
   page or the key), `risk` (linear in something that grows, fine today),
   `gap` (reads far more than it returns where it runs often).
5. The hot paths: the per-request baseline, the main lists, the event
   append, the queue claim, the outbox relay, and the sweep's pass; say
   what each costs and what grows it. A per-tenant cost times the tenants
   is the sweep's; a lock held across round trips caps a tenant's write
   rate.
6. Read the inventory now, before any candidate adds scans: an index
   with no scans after step 4 serves no statement the audit measured.

   ```bash
   uv run python ops/audit/explain.py inventory audit_query_indexes_<yyyymmdd>
   ```

7. Test each fix on the same data before proposing it (the before is step
   4's plan): create the candidate, measure the statements it serves
   again, and drop it before the next one.

   ```bash
   uv run python ops/audit/explain.py index audit_query_indexes_<yyyymmdd> "CREATE INDEX ix_try ON <table> (<columns>)"
   uv run python ops/audit/explain.py index audit_query_indexes_<yyyymmdd> "DROP INDEX <schema>.ix_try"
   ```

   A candidate that does not help is a finding too: row-level security
   applies a filter whose function is not leakproof (a `numeric` or row
   comparison) after the policy, row by row, whatever index exists, so
   such a cursor cannot seek.

8. Drop the run's database, whatever happened before:

   ```bash
   uv run python ops/audit/auditdb.py drop audit_query_indexes_<yyyymmdd>
   ```

9. Write the report, with the proposed tickets in it. The skill files
   none.

## What it never does

- Never writes to a shared database or to an environment: it logs in
  only to the database it made on the local stack, and drops it.
- Never modifies a tracked file, never commits, never opens a pull
  request: a tested index is a proposal with its numbers, not a
  migration.
- Never reports a time at a scale it did not seed as measured.

## Output

`~/Downloads/acme_query_indexes_<yyyy-mm-dd>.md`:

```markdown
# Acme: query patterns, indexes, and load

Scope: <commit>. Seed: scale <n> (<counts>).

## The answer

## How it was measured

## Every query

| Table | Query shape | Caller, frequency | Index | Measured | Verdict |

## Hot paths and contention

## Recommendations, by impact

1. Fix. Evidence (before and after). Change. Effort S/M/L. Needed. Proposed ticket.

## Redundant or unused indexes

## What I could not verify
```
