# Writing the purge statements

`ops/audit/explain.py plans` reads a file of statements, each under its
headers, and runs `EXPLAIN (ANALYZE, BUFFERS)` of it under the login and
the scope the storage funnel would use, in a transaction it rolls back.

```sql
-- name: tenancy: revoked or expired API keys past retention, across tenants
-- scope: system
DELETE FROM core.api_keys WHERE id IN (
  SELECT id FROM core.api_keys
  WHERE deleted_at < now() - interval '30 days' OR expires_at < now() - interval '30 days'
  LIMIT 1000 FOR UPDATE SKIP LOCKED);

-- name: outbox: done past retention, across tenants
-- scope: system
DELETE FROM core.outbox_rows WHERE id IN (
  SELECT id FROM core.outbox_rows WHERE done_at < now() - interval '8 days'
  LIMIT 1000 FOR UPDATE SKIP LOCKED);
```

- `-- name:` is what the report calls the purge.
- `-- scope:` is the org the purge runs for, or `system` for a purge
  across tenants. The seed's heavy org is
  `01900000-0000-7000-8000-00000000b16b`; a personal org is any other
  (`uv run python ops/audit/explain.py rows <db> "SELECT id FROM
  core.orgs WHERE kind = 'personal' LIMIT 1"`).
- Write the statement the storage impl sends, not a simpler one: the
  same `WHERE`, the same `LIMIT`, the same `ORDER BY`. `delete_batch` in
  `om/src/acme/om/storage/impl/pg_base.py` is the shape of a batched
  purge: a `MATERIALIZED` CTE that picks and locks the batch, then a
  `DELETE ... WHERE id IN (SELECT id FROM batch)`.
- The exact SQL is the storage's own statement, compiled. Write a
  scratch script in the evidence folder (never in the repository) that
  builds the statement the storage method builds and prints it, and run
  it with `uv run python <script>`:

  ```python
  from sqlalchemy.dialects import postgresql

  statement = ...  # e.g. delete_batch(ApiKeys, ApiKeys.deleted_at < before, limit=1000)
  print(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
  ```

- A statement that binds ids read earlier in the purge (the files
  `read_purgeable` returns, the users `purge_deleted` deletes before
  their memberships) names real ids of the seed: read them with
  `uv run python ops/audit/explain.py rows <db> "<the read's SELECT>"`.
- A per-tenant purge runs for an org past its retention, which the seed
  does not make; measure it under the heavy org's scope, whose rows are
  the most, and say so.
- Literals are enough for a purge: it runs once a pass, and its generic
  plan (`-- params:`) matters only for a statement that runs on every
  request.
- The cut-off is `now() - interval '<retention>'`, with the retention
  from `workers/maintenance/src/acme/workers/maintenance/settings.py`,
  or the manager's options default when no setting names it.
- A statement ends with `;` at the end of a line.

A plan answers three questions for the report: which index it used (or
`Seq Scan`), how many rows it read against how many it deleted (`Rows
Removed by Filter`, `Buffers`), and its time. Read each against the seed's
scale, and say how it grows: with the tenant's rows, with every tenant's,
or not at all.

## The purges the sweep runs

`build_loop` in `workers/maintenance/src/acme/workers/maintenance/main.py`
wires them, and `_sweep_once` in `loop.py` beside it runs them. Each
deletes a batch at most (`worker_purge_batch`, 1,000 by default; the
media purge's own batch is `MEDIA_PURGE_BATCH`, 100, in `container.py`)
and runs again while a batch comes back full and the pass's budget
lasts. The retentions below are the settings' defaults
(`ACME_<FIELD>`), or the options' when no setting names one; a
deployed environment may set its own, which step 5 of the skill reads.

Across tenants, once a pass:

| Purge | Storage method | Deletes | Kept for |
|---|---|---|---|
| tenancy | `purge_deleted` | removed users, then their memberships; removed memberships; revoked or expired API keys; expired sessions; closed or expired invitations | `tenancy_retention_days`, 30 |
| tenancy | `purge_deleted` | expired socket tickets | `socket_ticket_retention_hours`, 24 |
| tenancy | `purge_sign_in_delays` | the sign-in delays of addresses that stopped failing | `sign_in_delay_retention_hours`, 720 |
| idempotency | `purge_records` | finished or released records; markers an abandoned attempt still holds | `idempotency_retention_hours`, 24; an attempt older than ten pending leases of two minutes |
| events | `trim` | each living org's events older than the retention, moving its floor in the same statement | `event_retention_days`, 90; 0 keeps every event |
| orchestrations | `purge_settled` | settled records | the options' `retention`, 30 days |
| leases | `purge_settled` | ended leases; settled requests; retired resources no lease holds | the options' `retention`, 30 days |
| media | `read_purgeable`, then `purge_files_across_tenants` | a deleted file's object and row; an upload never confirmed | `media_retention_days`, 1; `media_pending_expiry_hours`, 24 |
| outbox | `purge_done` | relayed rows, and rows that failed for good, in two statements | `outbox_retention_days`, 8 |
| work | `purge_items` | done and failed items | `work_retention_days`, 30 |

Per tenant, for an org deleted past `tenancy_retention_days` (a
personal org goes at the next pass): `purge_tenant` of media (every
file, the object first), tenancy (users, memberships, API keys,
sessions, socket tickets, invitations), events (the stream, then its
cursor), orchestrations, and leases (resources, leases, requests). The
org row stays, marked purged, as the record.

No purge reaches `core.orgs`, `core.identities` (erased with the
person's account), `activity.event_cursors` beyond a purged tenant's,
or `admin.platform_sizes` (one row the sweep's tally rewrites).
