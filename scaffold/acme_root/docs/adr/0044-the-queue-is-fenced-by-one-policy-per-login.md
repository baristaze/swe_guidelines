# ADR 0044: The queue is fenced by one policy per login

**Status**: accepted (2026-09-25). Follows the guideline since v0.35.0:
The Second Fence lets a table that plans badly under the one policy
carry one policy per login, kept where a measurement shows it, and
STO-28 names the split's shape. It is not a deviation. The measurement
is this record's table; the migration that makes the split names this
record, and an applied migration is never edited.

## Context

The guideline, The Storage Layer, The Second Fence: "Each table gets
one policy, `FOR ALL`, with `USING` and `WITH CHECK` the same
expression." For an `org` table that expression is the tenant
comparison with the system-scope clause beside it:

```sql
org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
  OR (current_setting('app.org_id', true) = '<EMPTY_UUID>'
      AND current_user = '<system_login>')
```

STO-28: "The policies admit the system scope to the system login
alone."

The worker's claim runs in the system scope on `queue.work_items`.
Postgres adds the policy to the claim's `WHERE` and estimates it. The
first arm compares `org_id` with a setting, which it prices at one
tenant's share of the rows. The second arm has no column at all, which
it prices at a default fraction. So a claim over 50,000 ready items is
planned for one or two rows. With that estimate the planner reads every
ready item and sorts them, and the cost of a claim grows with the
backlog. Measured on a copy of the audit's queue:

| Ready items | Claim, one policy | Claim, one policy and the new index, generic plan | Claim, split by login |
|---|---|---|---|
| 5,000 | 3.0 ms (bitmap scan and a sort of 4,971 rows) | 3.0 ms | 0.05 ms |
| 50,000 | 3.4 ms (a walk of the primary key past 44,000 rows); 29 ms with the generic plan's sort | 29.5 ms | 0.06 ms |

The index ordered like the claim does not fix it on its own. The
generic plan, the one a prepared statement settles on, keeps the sort
because the estimate still says two rows.

## Decision

`queue.work_items` carries two policies, each `FOR ALL` with `USING`
and `WITH CHECK` the same, each bound to one login:

```sql
CREATE POLICY tenant_fence ON queue.work_items FOR ALL TO acme_runtime
    USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) ...;
CREATE POLICY system_fence ON queue.work_items FOR ALL TO acme_system
    USING (current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000') ...;
```

Postgres picks the policies by login when it plans, so the system
login's claim carries one predicate with no column in it, and it walks
`ix_work_items_lane_status_available_at_id` in order and stops at the
first free row. The claim orders by `(available_at, id)`: the item
ready longest goes first.

The scope map declares it: `TableScope(ScopeKind.ORG, by_login=True)`.
Every other table keeps one policy. A table takes the split when a
system-scope statement on it is measured to plan badly on the one
policy, and not before. It is permanent for the queue.

Each guarantee STO-28 states still holds, and is tested:

- The runtime login reads the tenant it names and nothing under the
  system scope. `tenant_fence` has no system-scope clause.
- The system scope is explicit: the system login reads every row under
  `EMPTY_UUID`, and nothing under a tenant or under no setting.
- No other login is named, so the migration login and the master read
  no work item under any setting. That is narrower than before.
- The policy check test and the negative control run on the queue
  table and go red when a predicate is taken out of either policy, when
  the runtime login is added to `system_fence`, or when row-level
  security is off.

## Consequences

The bypass is still spelled in the chain, as the empty UUID in
`system_fence`. It is bound by `TO acme_system` and not by a
`current_user` comparison, so a `grep` for the login name finds it in
the role list of the policy.

A policy that names a login needs the login to exist when the
migration runs. `migrate ensure-logins` runs before `migrate --all`
everywhere, as it already must for the grants.

A review reads the queue's two policies against the split STO-28
names, with this record as its measurement.
