# Tenant isolation: the cases, and the controls that prove them

Tenancy is a data boundary and lives in storage. The predicate in the
query is the first fence; row-level security is the second
([ADR 0016](../adr/0016-row-level-security-is-the-second-fence.md)).
This runbook says where the cases that hold the fences live, and how to
prove the cases would see a breach.

## Where the cases live

Each storage contract suite under `om/tests/contracts/` has cross-tenant
cases: each presents another tenant's id and asserts that nothing is
found and nothing changes. Each suite names the methods it covers in a
`CROSS_TENANT_CASES` set.
`test_every_tenant_method_is_named_in_a_cross_tenant_case`, in
`om/tests/unit/test_storage_exceptions.py`, holds those sets to the
interfaces, so a storage method added with no case fails the gate. That
test reads names, not queries. The controls below keep the names
honest.

`services/api/tests/test_tenant_isolation_api.py` asks the same of the
API: no list answers another tenant's rows.
`om/tests/integration/test_row_level_security.py` checks the second
fence: every table holds the policy its scope declares, no login is a
superuser or bypasses it, and the policy alone refuses another tenant's
row.

## The negative control, over the memory impls

A suite is worth what it catches, so break the fence on purpose.

1. Pick one query and take the tenant out of it: delete the `org_id`
   comparison, or swap a per-tenant helper for one that reads every row.
2. Run `make test-unit`. It must fail, and the failures must name the
   cross-tenant cases of the methods that query serves.
3. Put the predicate back and run `make test-unit`. It must pass.

Probe several queries of different shapes: a helper the impls share
(`MemoryStorageBase._get`, `_rows`, `_put`), a list with its own filter
(`read_sessions`), a page (`read_api_keys`), a write with its own check.
A predicate that goes missing with the suite still green is the finding
that matters: write the case that catches it, and confirm it fails.

## The two-run control, over Postgres

The Postgres impls carry the same predicates in SQL, and the same cases
run over them under `make test-integration`. With the policy live, a
missing predicate is caught by the second fence, so one run alone proves
nothing about the suite. The control takes two runs on the compose
stack:

1. Take the tenant out of one Postgres query.
2. Run the integration suite with the policy live. It must pass: the
   policy refused every row of another tenant.

   ```bash
   uv run pytest -q -m integration --ignore=om/tests/integration/test_migrations.py
   ```

   The migration round trip is left out: it downgrades and upgrades
   every chain, which puts the policy back under the second run.
3. Disable the policy on that table against the test database, as the
   migration login:

   ```sql
   ALTER TABLE <schema>.<table> DISABLE ROW LEVEL SECURITY;
   ```

4. Run the same suite. It must fail, naming the cross-tenant cases of
   that query's methods and `test_every_table_holds_the_policy_its_scope_declares`
   for the table.
5. Put the predicate and the policy back, and run `make test-integration`
   whole. It must pass.

`test_the_policy_is_what_refuses_the_other_tenant` runs the same two
steps on one table in every integration run.

## What a run records

Each control run is recorded in the pull request that ran it: the query
changed, the command, and what the suite reported (the counts, and the
names of the cases that failed). Read the names, not only the count: a
run must name the cases of the methods the query serves.

## When it fails

- **The suite stays green with a predicate gone (memory).** A case is
  missing. Write it, confirm it fails against the breach, then passes.
- **Run two stays green (Postgres).** The Postgres suite has no case for
  that query, or the policy did not go off: check `pg_class.relrowsecurity`
  for the table.
- **Run one goes red.** The policy does not hold the table. Read
  `test_every_table_holds_the_policy_its_scope_declares` for it.
