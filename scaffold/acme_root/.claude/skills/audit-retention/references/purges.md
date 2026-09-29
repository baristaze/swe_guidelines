# Writing the purge statements

`ops/audit/explain.py plans` reads a file of statements, each under its
headers, and runs `EXPLAIN (ANALYZE, BUFFERS)` of it under the login and
the scope the storage funnel would use, in a transaction it rolls back.

```sql
-- name: tasks: deleted past retention, one tenant
-- scope: 01900000-0000-7000-8000-00000000b16b
DELETE FROM core.tasks WHERE id IN (
  SELECT id FROM core.tasks
  WHERE org_id = '01900000-0000-7000-8000-00000000b16b'
    AND deleted_at < now() - interval '30 days'
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

  statement = ...  # e.g. delete_batch(TaskRow, TaskRow.org_id == org, ..., limit=1000)
  print(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
  ```

- A statement that binds ids read earlier in the purge (the tasks
  `read_deleted` returns, the files `read_purgeable` returns) names real
  ids of the seed: read them with
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
