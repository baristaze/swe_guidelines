# ADR 0074: The platform's size is a tally the sweep keeps

**Status**: accepted (2026-09-26).

## Context

STO-21: "Analytics across tenants never runs in the request path of any
role. When reporting is needed it reads a mirror fed by change data capture
or a periodic copy, never a role the application writes to." Its violation
is "a cross-tenant analytical query runs against the `core` role in a
request handler".

`GET /v1/admin/size` was that case. The operator plane's `size` counted in
the handler, in three statements across every tenant: the live orgs and the
live users (`count_orgs_and_users`, on `core`), the tasks created in the
last day (`count_created_since`, on `core`), and the events produced in it
(`count_since`, on `activity`). The first responder reads the size before
it escalates an alarm (`acme-ops size`), so the reads ran when something
was already wrong.

Measured on the audit seed at scale 1 (5,006 tenants, 5,206 users, 125,000
tasks, a million events), with `ops/audit/explain.py` and
`ops/audit/dbcalls.py`:

| Statement | Plan | Time |
|---|---|---|
| live orgs and users | two sequential scans, 5,001 and 5,350 rows | 7.2 ms |
| tasks created in the day | a sequential scan of all 125,000 tasks | 6.3 ms |
| events produced in the day | a range of `ix_events_produced_at`, 9,991 rows | 4.7 ms |

The request was 12 round trips in 4 transactions, 19 to 27 ms. Each count
grows with the platform, and the tasks' with every task ever made.

## Decision

**The sweep counts, the handler reads.** The operator manager gains
`tally_size()`, the sweep's count: the same three statements, then one
write of the result. It takes no context, since it counts for no tenant and
no principal, and it is listed with the other principal-less methods. `size`
reads the result and counts nothing.

**The tally lives in the `admin` role.** The guideline gives `admin` "the
operator plane's own state, global rows", and STO-21 keeps a report off the
roles the application writes to. The size is the operator plane's own, and
it is one global row. So `admin.platform_sizes` is the role's first table
and its chain starts here. The row is keyed by `EMPTY_UUID`, the
platform's reference (OM-13). It is `system`-scoped: no tenant, no policy.
The runtime and the system logins reach it through the default privileges
`migrate ensure-logins` sets on every role schema, which runs before every
migrate. The row is a persisted read model: derived, and rebuilt by the
next count.

**Once every five minutes, not every pass.** A count costs 12 round trips
in 4 transactions and 17 to 26 ms at 5,000 tenants. A pass that counts is
159 round trips where an idle pass is 147. Counting on every pass, every
thirty seconds, would add 8% to every idle pass. Counting every five
minutes adds under 1%. The counts cover a day, so a count five minutes old
is off by at most about a three-hundredth of its window, and the tenants and the
users move slower than that. The interval is a setting,
`ACME_WORKER_TALLY_SECONDS` (300).

Each worker keeps its own clock, in memory, and counts on its first pass.
Two workers count twice an interval. Reading the row's age on every pass
would coordinate them, but it costs a read on every pass of every worker
to save one count every five minutes on the second. The write takes the
newer count: a count older than the row's does not replace it, so two
workers that count at once leave the later one.

**Whatever the budget.** The count runs after the purges and before the
gauges, whether or not the budget is spent, as the gauges do. It is due
once an interval and costs about 20 ms, and a backlog that spends every
budget is exactly when the first responder reads the size.

**The answer says how old it is.** The read model and `PlatformSizeView`
carry `counted_at`, and the window is the day before it (`since`). `acme-ops
size` prints how long ago the worker counted. Before the first count the
route is `404`: the size does not exist yet. It is not `503`, which the
gateway logs as a failure with a stack and presents as "internal error"; a
fresh environment is not failing.

## Alternatives

- **Keep the live count and record it.** The ticket named this: an ADR
  with a trigger, such as the route's p95 above 1 s. The count is cheap
  today, but STO-21 names this exact case, and a handler that scans the
  roles the application writes to is what the rule is for.
- **A cache entry.** The guideline allows a persisted read model to be a
  cache entry. The cache is best effort, and a flush would lose the size
  until the next count. The size is the operator plane's state, which the
  `admin` role holds.
- **A table in `core`.** A report that reads a role the application writes
  to is STO-21's second violation.
- **A row per count.** A history needs a purge, and nothing reads one.

## Consequences

The same flows on the same seed, at `main` and on this change:

| Call | Before | After |
|---|---|---|
| `GET /v1/admin/size` | 12 round trips, 4 transactions (`core`, `activity`), 19 to 27 ms | 6 round trips, 2 transactions (`core` for the credential, `admin`), 2.7 to 4.2 ms |
| `tally_size`, the sweep's count | none | 12 round trips, 4 transactions, 17 to 26 ms, once every five minutes |
| an idle pass | 147 round trips, 44 transactions | the same; 159 and 48 on a pass that counts |

The size is up to five minutes and one pass old. A worker that stops
counting shows as a `counted_at` that keeps aging, which the ops skills
read as a finding.

The count still grows with the platform. The tasks' statement reads every
task, about 50 ns each. It runs off every request path, once an interval,
on the system login's pool. Reading only the day's tasks waits for its
trigger: the worker's line `sweep: counted the platform's size in <s>`
above 2 s, about 40 million tasks
([ACME-159](https://tracker.example/issue/ACME-159)).
