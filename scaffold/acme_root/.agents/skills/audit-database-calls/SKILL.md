---
name: audit-database-calls
description: "Audit how many database calls each endpoint and each worker flow makes, at its least and at its most: round trips, transactions, and roles, counted at the driver while the real API and worker run in one process over a database of their own. Names what grows a flow, every N+1, every read repeated within a flow, and the fixed cost of a transaction, each with its fix and effort. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(uv run:*), Bash(git:*), Bash(mkdir:*)
---

# audit-database-calls

For every route and every worker flow: how many round trips it sends to
the database, in how many transactions, on which roles, at its least and
at its most, and what makes the most grow. The count is taken at the
driver while the real API and worker run in one process, so it is what
the code does, not what it looks like it does.

## Input

`[--only <flow,...>]`

The audit counts the checkout, tools and code alike; to count another
commit, run it from a checkout of that commit that has `ops/audit/`.
Every command runs from the repository root. `--only` runs some of the
built-in flows (`sign_in`, `members`, `invitations`, `api_keys`,
`switch`, `files`, `events`, `org_deleted`, `account_deleted`, `sweep`,
`health`), such as `--only api_keys,sweep`; `seed` always runs first,
and a flows file of the run's own always runs in full.

## Role and credential

None, local only. The skill runs on the local stack (`make
infra-up`, with `make migrate` run once), in a database it makes and
drops, with the identity provider's twin in place of the provider. It
holds no cloud credential and reads no environment.

## Procedure

1. Name the run: `audit_database_calls_<yyyymmdd>`, today's date in UTC.
   When `uv run python ops/audit/auditdb.py list` shows that name taken,
   another run holds it: add a suffix (`_2`), and never drop a database
   this run did not make. The evidence folder is
   `~/Downloads/acme_database_calls_<yyyy-mm-dd>/` and the report
   `~/Downloads/acme_database_calls_<yyyy-mm-dd>.md`; when either
   exists, both take the next free suffix (`_2`), so a run never
   overwrites another's; the database takes the same suffix. Make the folder (`mkdir -p`). Say which commit
   the run counted (`git rev-parse HEAD`).
2. List every route (the routers under
   `services/api/src/acme/services/api/routers/`, the socket in
   `.../api/realtime/socket.py`, and `/healthz`, `/readyz`, and `/metrics`
   in `.../api/app.py`), every kind of work item (`WorkKind` in
   `om/src/acme/om/work/types/work_item.py`) and its handler and every
   consumer (`workers/maintenance/src/acme/workers/maintenance/`), and
   the sweep's steps (that folder's `loop.py`, `_sweep_once`). Compare the list with the calls the built-in flows make
   (`ops/audit/dbcalls_flows.py`): a route or a flow they do not reach is
   written into a flows file of the run's own, as
   `references/flows.md` in this skill's folder shows (read it before
   writing one).
   Add a second call wherever a size changes the count: one id and a
   hundred, an org of one and of many, a list of one row and a full page.
   A route that needs a provider's setup the twins cannot give in a flow
   is listed under "What I could not measure", with why.
3. Make the database and count:

   ```bash
   uv run python ops/audit/auditdb.py create audit_database_calls_<yyyymmdd>
   uv run python ops/audit/dbcalls.py run audit_database_calls_<yyyymmdd> \
     --out ~/Downloads/acme_database_calls_<yyyy-mm-dd>/calls.json \
     [--flows ~/Downloads/acme_database_calls_<yyyy-mm-dd>/more_flows.py] [--only <flows>]
   uv run python ops/audit/dbcalls.py summary ~/Downloads/acme_database_calls_<yyyy-mm-dd>/calls.json
   ```

   A flow that fails is named, the rest run, and `run` exits 1; go on to
   the summary and the drop all the same. Fix a failed flow, move it to a
   flows file of its own (`fixed_flows.py`), and run that file on the
   same database with `--only seed` into a second `--out`
   (`calls_2.json`), or report it as not measured. That is the first
   run plus at most 1 rerun: a flow that fails in `calls_2.json` too is
   reported as not measured. The summary's round trips are warm (every statement already
   prepared); the report uses them and says so, and `calls.json` holds
   each call's `prepares` beside them. Tally `calls.json` with a scratch
   script (`uv run python <script>`), never a file in the repository.
4. Read each call's `detail` in `calls.json`: one line per transaction,
   with its role, its scope, the storage method that opened it, and its
   statements. From them, per route and flow:
   - the fixed cost: every transaction pays `BEGIN`, which carries the
     scope's `set_config` in the same message, and `COMMIT` or
     `ROLLBACK`, so a one-statement read is three round trips;
   - the per-request baseline of each credential (session, API key,
     sign-in credential, operator token), and each route's cost above it;
   - what grows it: a count that rises with a list, an id set, the
     orgs of a person, the members of an org; say the formula (`13 + 8M`)
     and the sizes it was measured at;
   - an N+1 (a transaction per row), a read repeated within one flow, a
     transaction opened and rolled back with nothing in it, several
     transactions in a row on the same role that one would serve;
   - the cost of an open socket per hour: every timer the socket's
     handler runs (`services/api/src/acme/services/api/realtime/socket.py`)
     at its interval. The recheck costs its measured round trips every
     `realtime_recheck_seconds`
     (`services/api/src/acme/services/api/settings.py`). A client's ping
     comes every `ping_interval_seconds` of
     `deployment/realtime-timeouts.json`, and its pong costs nothing
     while the head this process heard is younger than
     `realtime_head_max_age_seconds`, and a head read past it; count the
     pings an hour that read, for a quiet tenant (no write for longer
     than that age) and a busy one (a write within every such age). The
     head is kept per tenant per process and shared by its sockets there:
     say it per socket per hour, and at a thousand open sockets with one
     per tenant per process, the worst case;
   - the fan-out of one change: one write publishes one hint, every open
     tab of every member of the tenant receives it, and each tab reads
     what the hint does not carry. Read what a tab does with a hint in
     `apps/portal/src/realtime/`: which tabs skip it (a tab that already
     holds the version, such as the one that wrote it; none when the
     writer is an API key or the worker) and measure the read it makes,
     in a flows file when no built-in flow makes that call. Say the reads
     caused by one change as reads per hint × members × open tabs, minus
     the tabs that skip, at an org of 2 and of 200 members with 2 tabs
     each. Writes in quick succession publish hints in a burst; say what
     a tab does with one;
5. Drop the run's database, whatever happened before, and check it is
   gone:

   ```bash
   uv run python ops/audit/auditdb.py drop audit_database_calls_<yyyymmdd>
   uv run python ops/audit/auditdb.py list
   ```

6. Write the report, and the proposed tickets in it. The skill files
   none. Findings rank by their round trips at the sizes the report
   names. A fix is proposed in this order, the first that applies:
   remove the read, fold it into a read the flow already makes, defer
   it off the request path, cache its answer (with the TTL and what
   invalidates it), and only then run reads in parallel. That
   order is the rule, because concurrent reads on a bounded pool can
   make the tail worse. Each role's pool has a fixed size
   (`database_pool_size` in `om/src/acme/om/storage/settings.py`) and
   no overflow, while admission lets many more requests in at once. A
   request that runs three reads at once holds three connections, so
   under load it takes two from requests that would each have needed
   one, and they wait on a checkout. The median falls and the tail
   grows. Parallel reads are proposed only when they are independent,
   when nothing earlier in the order applies, and with the pool's
   headroom at the load the report names.

## What it never does

- Never writes to a shared database or to an environment: it logs in
  only to the database it made on the local stack, and drops it.
- Never modifies a tracked file, never commits, never opens a pull
  request: a run's own flows live in its evidence folder, and a fix is a
  proposal with its numbers.
- Never calls a real provider: the twin stands in for the identity
  provider.
- Never gives a maximum it did not measure as measured: a formula from the
  code is marked as one, with the sizes that agree with it.

## Output

`~/Downloads/acme_database_calls_<yyyy-mm-dd>.md`:

```markdown
# Acme: database calls per endpoint and flow

<commit>, counted on <database>, with the identity provider's twin. How the count is taken, in two sentences.

## How to read the numbers

## Short answer

- Baseline per authenticated request: <n> round trips, <n> transactions.
- The cheapest and the most expensive routes and flows, with their counts.
- What grows with data.
- Surprises.

## Per-request baseline

| Credential | Round trips | Transactions | Roles | What it reads |

## Tables per area

| Flow | Min | Max (or formula) | Transactions | Roles | What drives the range | Measured |

Measured: yes, formula, or read (code only).

## Realtime

| Measure | Per unit | Formula | At the stated sizes | Measured |

## Findings, by impact

1. **<finding>.** Where (file:line). The waste (numbers). The fix. Effort
   S/M/L. Proposed ticket: <title>.

## What I could not measure
```
