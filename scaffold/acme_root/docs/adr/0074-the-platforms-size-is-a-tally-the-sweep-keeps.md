# ADR 0074: The platform's size is a tally the sweep keeps

**Status**: accepted (2026-09-28)

## Context

STO-21: "Analytics across tenants never runs in the request path of any
role. When reporting is needed it reads a mirror fed by change data
capture or a periodic copy, never a role the application writes to." Its
violation is "a cross-tenant analytical query runs against the `core`
role in a request handler".

`GET /v1/admin/size` answers the platform's size: the live orgs, the
live users, and the events produced in the last day. The first responder
reads it before escalating an alarm (`acme-ops size`), so it is read
when something is already wrong. Counted in the handler, it is two
statements across every tenant, one on `core` and one on `activity`, and
each count grows with the platform.

## Decision

**The sweep counts, the handler reads.** The operator manager's
`tally_size()` is the sweep's count: the live orgs and users in one
statement (`count_orgs_and_users`, on `core`), the events produced in the
day (`count_since`, on `activity`), then one write of the result. It
takes no context, since it counts for no tenant and no principal, and it
is listed with the other principal-less methods. `size` reads the result
and counts nothing.

**The tally lives in the `admin` role.** The guideline gives `admin`
"the operator plane's own state, global rows", and STO-21 keeps a report
off the roles the application writes to. The size is the operator
plane's own, and it is one global row, `admin.platform_sizes`, keyed by
`EMPTY_UUID`, the platform's reference (OM-13). It is `system`-scoped:
no tenant, no policy. The runtime and the system logins reach it through
the grants the admin role's migration sets. The row is a persisted read
model: derived, and rebuilt by the next count.

**Once every five minutes, not every pass.** The counts cover a day, so
a count five minutes old is off by at most about a three-hundredth of
its window, and the tenants and the users move slower than that. The
interval is a setting, `ACME_WORKER_TALLY_SECONDS` (300). Counting on
every pass, every thirty seconds, would add the count's cost to every
idle pass.

Each worker keeps its own clock, in memory, and counts on its first
pass. Two workers count twice an interval. Reading the row's age on
every pass would coordinate them, but it costs a read on every pass of
every worker to save one count every five minutes. The write takes the
newer count: a count older than the row's does not replace it, so two
workers that count at once leave the later one.

**Whatever the budget.** The count runs after the purges and before the
gauges, whether or not the pass's budget is spent, as the gauges do. It
is due once an interval, and a backlog that spends every budget is
exactly when the first responder reads the size.

**The answer says how old it is.** The read model and `PlatformSizeView`
carry `counted_at`, and the window is the day before it (`since`).
`acme-ops size` prints how long ago the worker counted. Before the first
count the route is `404`: the size does not exist yet. It is not `503`,
which the gateway logs as a failure with a stack and presents as
"internal error"; a fresh environment is not failing.

## Alternatives

- **Keep the live count in the handler.** The count is cheap on a small
  platform, but STO-21 names this exact case, and a handler that scans
  the roles the application writes to is what the rule is for.
- **A cache entry.** The guideline allows a persisted read model to be a
  cache entry. The cache is best effort, and a flush would lose the size
  until the next count. The size is the operator plane's state, which the
  `admin` role holds.
- **A table in `core`.** A report that reads a role the application
  writes to is STO-21's second violation.
- **A row per count.** A history needs a purge, and nothing reads one.

## Consequences

`GET /v1/admin/size` reads two rows in two transactions: the operator's
credential on `core`, and the tally on `admin`. It reads nothing across
tenants.

The size is up to five minutes and one pass old. A worker that stops
counting shows as a `counted_at` that keeps aging, which the ops skills
read as a finding.

The count still grows with the platform. It runs off every request path,
once an interval, on the system login's pool. The worker logs `sweep:
counted the platform's size in <s>`, which is where a count that grows
too slow shows first.
