---
name: audit-database-calls
description: "Audit how many database calls each endpoint and each worker flow makes, at its least and at its most: round trips, transactions, and roles, counted at the driver while the real API and worker run in one process over a database of their own. Names what grows a flow, every N+1, every read repeated within a flow, and the fixed cost of a transaction, each with its fix and effort. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Bash(uv run:*), Bash(git:*), Bash(mkdir:*)
---

# audit-database-calls

For every route and every worker flow: how many round trips it sends to
the database, in how many transactions, on which roles, at its least and
at its most, and what makes the most grow. The count is taken at the
driver while the real API and worker run in one process, so it is what
the code does, not what it looks like it does.

## Input

`[--only <flow,...>]`

`--only` runs some of the built-in flows of `ops/audit/dbcalls_flows.py`;
`seed` always runs first, and a flows file of the run's own always runs in
full. The audit reads and runs the checkout, tools and code alike; to
audit another commit, run it from a checkout of that commit that has
`ops/audit/`. Every command runs from the repository root.

## Role and credential

Investigator, local only. The skill runs on the local stack (`make
infra-up`, with `make migrate` run once), in a database it makes and
drops, with the provider twins in place of the providers. It holds no
cloud credential and reads no environment.

## Procedure

1. Name the run `audit_database_calls_<yyyymmdd>`, today's date in UTC.
   When `uv run python ops/audit/auditdb.py list` shows that name taken,
   another run holds it: add a suffix (`_2`), and never drop a database
   this run did not make. Make the evidence folder
   `~/Downloads/acme_database_calls_<yyyy-mm-dd>/` and say which commit
   the run counted.
2. List every route, every worker handler and consumer, and the sweep's
   steps, and compare the list with what the built-in flows call. A route
   or a flow they do not reach goes into a flows file of the run's own in
   the evidence folder, never in the repository: plain Python whose
   `FLOWS` lists `async def` flows that take the world and call
   `w.http(area, name, method, path, ...)` for a request and
   `w.measure(area, name, lambda: <awaitable>)` for a manager or worker
   call, as the built-in flows do. Add a second call wherever a size
   changes the count: one id and a hundred, one org and many, one row and
   a full page, and the sweep at two tenant counts. A route that needs a
   provider's setup the twins cannot give is listed as not measured.
3. Make the database and count:

   ```bash
   uv run python ops/audit/auditdb.py create audit_database_calls_<yyyymmdd>
   uv run python ops/audit/dbcalls.py run audit_database_calls_<yyyymmdd> --out ~/Downloads/acme_database_calls_<yyyy-mm-dd>/calls.json [--flows <file>] [--only <flows>]
   uv run python ops/audit/dbcalls.py summary ~/Downloads/acme_database_calls_<yyyy-mm-dd>/calls.json
   ```

   A flow that fails is named, the rest run, and `run` exits 1; go on to
   the summary and the drop. A fixed flows file runs alone on the same
   database with `--only seed` into a second `--out`. The summary's round
   trips are warm (every statement already prepared); the report says so.
4. Read each call's `detail`: one line per transaction, with its role, its
   scope, the storage method that opened it, and its statements. From
   them: the fixed cost of a transaction (`BEGIN`, the scope, `COMMIT` or
   `ROLLBACK`); the per-request baseline of each credential and each
   route's cost above it (the operator's token among the credentials);
   what grows each count, as a formula with the sizes it was measured at;
   every N+1, every read repeated within one flow, every empty
   transaction, every run of transactions on one role that one would
   serve.

   Rank each fix: remove the call, fold it into another, or defer it off
   the request path, before running reads in parallel. Concurrent reads
   on a bounded pool hold more connections at once, and can make the
   tail worse for every other request.
5. Drop the run's database, whatever happened before:

   ```bash
   uv run python ops/audit/auditdb.py drop audit_database_calls_<yyyymmdd>
   ```

6. Write the report, with the proposed tickets in it. The skill files
   none.

## What it never does

- Never writes to a shared database or to an environment: it logs in
  only to the database it made on the local stack, and drops it.
- Never modifies a tracked file, never commits, never opens a pull
  request: a fix is a proposal with its numbers.
- Never calls a real provider: the twins stand in for every one.
- Never gives a maximum it did not measure as measured: a formula from
  the code is marked as one.

## Output

`~/Downloads/acme_database_calls_<yyyy-mm-dd>.md`:

```markdown
# Acme: database calls per endpoint and flow

<commit>, counted on <database>, with the provider twins.

## Short answer

## Per-request baseline

| Credential | Round trips | Transactions | Roles | What it reads |

## Tables per area

| Flow | Min | Max (or formula) | Transactions | Roles | What drives the range | Measured |

## Findings, by impact

1. Finding. Where. The waste. The fix. Effort S/M/L. Proposed ticket.

## What I could not measure
```
