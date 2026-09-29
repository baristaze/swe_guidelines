# ADR 0016: Row-level security is the second fence, taken by default

**Status**: accepted (2026-09-28)
## Context

Tenancy is a data boundary and lives in storage, and the fence is the
predicate in the query. That fence has one failure mode: a predicate
goes missing. A new list, a new `WHERE` beside an old one, or a helper
reused where it does not fit, and one tenant reads another's rows with
nothing to say so.

Postgres can hold the same boundary a second time, in the database,
where a query cannot forget it. It has two costs. Setting the tenant is
the same discipline in a second place. And a policy with no tenant set
reads as no rows, a silent answer where a refusal would be louder.

## Decision

Row-level security is taken, by default, as the second fence. The
predicate in the query stays the fence the business layer relies on:
nothing in a manager or an impl assumes a policy exists, and the
cross-tenant cases of the contract suites run unchanged over Postgres.

Five pieces answer the costs with shape rather than discipline.

**The scope map.** `om/src/acme/om/storage/scopes.py` declares
`TABLE_SCOPES` beside `TABLE_ROLES`: every table to one of `system`,
`org`, `identity`, or `both`, with the column a `both` table is narrowed
on. `scope_for` refuses an unlisted table, so a table arrives with a
scope or not at all. The fast gate holds the map to the tables and to
the migration chains, so a table added without a policy fails before a
database is involved.

**The funnel.** `PgStorageBase._session_for` is the one place a
Postgres impl opens a session, and it takes the scope of the call:
`org_id` required, `user_id` or `identity_id` when the call names the
person. It writes the settings `app.org_id`, `app.user_id`, and
`app.identity_id` with `set_config(name, value, true)` before the first
statement ([ADR 0053](0053-the-scope-rides-in-the-message-that-begins.md)),
so they die with the transaction. The tenant is named once per
transaction, at a place every statement already passes. `EMPTY_UUID` is
the system scope, never a default, spelled at the call site.
`om/tests/unit/test_session_scope.py` fails on any method that passes
it and is not an enumerated exception.

**Three logins, none above the fence.** A superuser walks past every
policy, and so does a role with `BYPASSRLS`. No login of the platform
is either, and `FORCE ROW LEVEL SECURITY` is on every fenced table, so
the owner is held too.

| Login | Holds | Used by |
|-------|-------|---------|
| `acme_migration` | owns every role's schemas and tables | the migrations |
| `acme_runtime` | DML only | every request's connection |
| `acme_system` | DML only, on a pool of its own | the system scope |

The master, `acme`, runs `migrate ensure-logins` and nothing else.
Locally `deployment/local/postgres/initdb/10-acme-login.sql` makes it
with neither attribute; on RDS the master user has neither.
`test_no_login_is_a_superuser_or_bypasses_rls` asserts it on the live
database, since every other assertion here would pass against a
database that fences nothing.

**The system scope is a login, not a setting.** Every `tenant_fence`
policy admits a row when `org_id` matches the setting, or when the
setting is the empty UUID and `current_user = 'acme_system'`. The
runtime login naming the system scope reads nothing, so SQL injected
past the first fence cannot lift the second with a `SET`. The queue's
table carries one policy per login instead
([ADR 0044](0044-the-queue-is-fenced-by-one-policy-per-login.md)).

**The two-run control.** The tenant-isolation runbook's control takes
two runs: the predicate out of one query with the policy live, which
must stay green, and the same breach with the policy off on that table,
which must go red. Both are recorded.

Two choices sit on top of the guideline's shape.

- **More tenant-less storage methods than the guideline lists
  (CTX-12).** The Second Fence names the sweeps and the lookups before an
  identity is known. The tree also takes no tenant where a method reads
  or writes the two `system` tables (`identities`, `sign_in_delays`),
  reads across tenants for the operator plane, steps from an identity to
  its tenants, keeps the platform's size, or erases a person
  ([ADR 0041](0041-an-account-is-deleted-at-once-and-its-providers-by-the-queue.md)).
  Each says why in its docstring. `[tool.arch-check.options.CTX-12]` in
  `pyproject.toml` and `om/tests/unit/test_storage_exceptions.py` hold
  the same list.
- **Sessions, API keys, and socket tickets are `both` tables.** The
  guideline's scaffold makes them `identity` tables. Here each belongs to
  one person inside one org: a session is in one org at a time, and a
  switch ends it. So their policy is on `org_id`, narrowed by the
  person. An operator token is a session under the system scope, which
  only the system login finds.

## Consequences

A transaction that names no tenant reads nothing and writes nothing.
The silent empty read is the accepted price; the refused write, and the
negative control, say the fence is live and not only enabled.

A policy is invisible to `make migrate-check`, which compares tables,
columns, and indexes. `om/tests/integration/test_row_level_security.py`
reads `pg_class` and `pg_policies` for every table in the scope map and
asserts the migrated database holds the declared scope.

The policies compare against `NULLIF(current_setting(...), '')::uuid`.
A setting written with `set_config(..., true)` goes back to the empty
string, not to NULL, when its transaction ends. Without `NULLIF`, the
next transaction on that pooled connection that named no tenant would
raise a cast error instead of reading nothing.

`_upsert` refuses another tenant's row twice. The read names the tenant
on the row. Under the policy that read returns nothing, so the insert
meets the primary key, and a key the read did not see is a row this
transaction may not see: the same `TenantMismatch`, from the key.

`app.user_id` narrows a `both` table to one person when a call names
one: the idempotency markers, the session list, the key page, the
membership read. Everywhere else only the tenant holds.
