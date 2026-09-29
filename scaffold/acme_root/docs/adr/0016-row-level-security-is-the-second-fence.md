# ADR 0016: Row-level security is the second fence, taken by default

**Status**: accepted (2026-09-20)

## Context

Tenancy is a data boundary and lives in storage, and the fence is the
predicate in the query. That fence has one failure mode, and it is the
one an early team ships: a predicate goes missing. A new list, a new
`WHERE` beside an old one, a helper reused where it does not fit, and
one tenant reads another's rows with nothing to say so. This repository
has met it. `read_done_tasks` carried a `WHERE` of its own, the suite
had a case for the open list and none for the done list, and taking the
tenant out of the done list alone left the suite green;
`docs/runbooks/tenant-isolation.md` records that run.

Postgres can hold the same boundary a second time, in the database,
where a query cannot forget it. The guideline's earlier text named two
costs and left row-level security out because of them. The first is
that setting the tenant is the same discipline in a second place, so a
policy is only as good as the discipline it was meant to replace. The
second is that a policy with no tenant set reads as no rows, which is a
silent answer where a refusal would be louder.

## Decision

Row-level security is taken, by default, as the second fence. The
predicate in the query stays the fence the business layer relies on:
nothing in a manager or an impl assumes a policy exists, and the
cross-tenant cases of the contract suites run unchanged over Postgres
and stay green. That is the test of the sentence, not the sentence
itself.

Four pieces answer the two costs with shape rather than discipline.

**The scope map.** `om/src/acme/om/storage/scopes.py` declares
`TABLE_SCOPES` beside `TABLE_ROLES`: every table to one of `system`,
`org`, `identity`, or `both`, with the person column a `both` table is
narrowed on. `scope_for` refuses an unlisted table the way `role_for`
does, so a table arrives with a scope or not at all. The fast gate
holds the map to the tables (a global table is `system` and nothing
else is; an `org` or `both` table carries `org_id`; the declared person
column exists) and to the migration chain, so a table added without a
policy fails before a database is involved.

**The funnel.** `PgStorageBase._session_for` is the one place a Postgres
impl opens a session, and it now takes the scope of the call:
`org_id` required, `user_id` optional, `identity_id` for an
`identity`-scoped table. It writes the transaction settings
(`app.org_id`, `app.user_id`, `app.identity_id`) with
`set_config(name, value, true)` before anything else, so they are the
first statement of the transaction and die with it. This is the answer
to the first cost: the tenant is named once per transaction at a place
every statement already passes, not once per query in a second place.
`EMPTY_UUID` is the system scope, never a default, spelled at the call
site, and `om/tests/unit/test_session_scope.py` walks the impl modules
and fails on any method that passes it and is not in the enumerated
exceptions of `test_storage_exceptions.py`.

**The login.** A superuser walks past every policy, and so does a role
with `BYPASSRLS`; behind either, a fence is a drawing. Locally the
superuser is `postgres` and an init script under `deployment/local/`
creates `acme` as `LOGIN NOSUPERUSER NOBYPASSRLS`, owning the
database. `FORCE ROW LEVEL SECURITY` is on every fenced table, so the
owner is held too. On RDS the master user is neither, which the
Terraform module now says where the username is set.
`test_the_login_is_no_superuser_and_cannot_bypass_rls` asserts it on
the live connection, because every other assertion here would pass
against a database that fences nothing.

**The two-run control.** A control that only runs with the policy live
proves nothing about the suite. The runbook's procedure now takes two
runs: the predicate out of one query with the policy live, which must
stay green, and the same breach with the policy disabled on that table,
which must go red. Both runs are recorded, with the query, the command,
and what the suite reported.

## Consequences

A transaction that names no tenant reads nothing and writes nothing.
The read going silently empty is the accepted price, and it is what the
second cost names; the write is refused outright, and the negative
control is what says the fence is live rather than merely enabled. The
first thing this caught was a test: an integration case that reached
around the impl to seed a cursor row was refused, and it now names the
tenant the way every other transaction does.

A policy is invisible to `make migrate-check`, which compares tables,
columns, and indexes. `om/tests/integration/test_row_level_security.py`
reads `pg_class` and `pg_policies` for every table in the scope map and
asserts the declared scope is what the migrated database holds, so the
two halves of the schema are both checked, in different places.

The policies compare against `NULLIF(current_setting(...), '')::uuid`
and not the setting alone. A custom setting written with
`set_config(..., true)` does not go back to NULL when the transaction
ends; on that connection it goes back to the empty string, which casts
to no uuid at all. Without `NULLIF`, the next transaction on a pooled
connection that named no tenant would raise a cast error instead of
reading nothing, which is not the answer a fence gives. The system
scope is still spelled as the literal empty UUID in every policy, so
every place the one deliberate bypass holds is found by reading the
chain.

Two storage reads ask a cross-tenant question and now say so. `_upsert`
read another tenant's row to report `TenantMismatch`; under the policy
that read returns nothing, so the insert that follows meets the primary
key and a key the read did not see is a row this transaction may not
see. The refusal is the same, from the key rather than from the column,
and it holds with the policy and without it. `WorkStoragePostgresImpl.create_item`
reads back the same way. `TasksStoragePostgresImpl._why_not`, which
tells the caller which of three conditions the bulk update missed,
cannot: narrowed to the tenant, "another tenant holds it" and "nobody
does" are one empty answer, and they are different refusals. It takes
the system scope in a session of its own, named in
`test_session_scope.py` with that reason. It reads two columns of one
id, which is what it read before the policy existed.

`app.user_id` narrows a `both` table to one person when a call already
names one: the idempotency markers, the session list, the key page, the
membership read. Everywhere else the narrowing is absent and only the
tenant holds, which is what the policy shape says. `users` declares
`identity_id` as its person column, since the person behind a user row
is the identity; no call site narrows that table today.

Local development needs `make reset` once. The init script runs on an
empty data directory, so a stack that was up before this change keeps
the old superuser login and no policy would hold behind it.
