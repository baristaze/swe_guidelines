# The statement file, and the cases each statement is measured in

## The format

`ops/audit/explain.py plans` reads statements, each under its headers, and
runs `EXPLAIN (ANALYZE, BUFFERS)` of each under the login and the scope
the storage funnel would use, in a transaction it rolls back (a write is
measured and never kept).

```sql
-- name: tenancy: a member's own API keys, a member with none
-- scope: 01900000-0000-7000-8000-00000000b16b
-- user: <the member's user id>
SELECT * FROM core.api_keys
WHERE org_id = '01900000-0000-7000-8000-00000000b16b' AND deleted_at IS NULL
  AND user_id = '<user id>'
ORDER BY id DESC LIMIT 51;

-- name: tenancy: the org's users, first page, generic plan
-- scope: 01900000-0000-7000-8000-00000000b16b
-- params: '01900000-0000-7000-8000-00000000b16b', 51
SELECT * FROM core.users WHERE org_id = $1 AND deleted_at IS NULL
ORDER BY id LIMIT $2;

-- name: work: claim the next ready item
-- scope: system
SELECT id FROM queue.work_items
WHERE lane = 'default' AND status = 'queued' AND available_at <= now()
ORDER BY available_at, id LIMIT 1 FOR UPDATE SKIP LOCKED;
```

- `-- scope:` is the tenant, or `system` for a statement across tenants;
  `system` runs on the system login, a tenant on the runtime login, as
  the funnel does.
- `-- user:` sets `app.user_id` for a table whose policy reads it.
- `-- params:` measures the generic plan, the one asyncpg's prepared
  statement settles on after five runs: write `$1`, `$2` where the driver
  binds a value, and give the values in order as SQL literals or
  expressions (`now() - interval '30 days'`).
- `-- user:` sets `app.user_id`; only a table whose policy reads it
  needs one (`\d <table>` in the inventory's database shows the policy).
- A statement ends with `;` at the end of a line.

Write the statement the storage impl sends: the same `WHERE`, `ORDER BY`,
`LIMIT`, and locking clause. A purge's batch is `delete_batch` in
`om/src/acme/om/storage/impl/pg_base.py` (a `MATERIALIZED` CTE that picks
and locks the batch, then the `DELETE`); its batch size and every
retention window are settings in
`workers/maintenance/src/acme/workers/maintenance/settings.py`, and a
claim's lanes, kinds, and lease are in that folder's `loop.py` and
`main.py`. When the ORM makes that hard to read, run
the flow through the counter (`uv run python ops/audit/dbcalls.py run
<db> --only <flow> --out <file>`) and read the statement from the
`detail` lines of its row.

## The ids a statement names

The seed's heavy team org is `01900000-0000-7000-8000-00000000b16b`. Read
the other ids a statement needs from the seeded data with `rows`, which
prints what one `SELECT` returns:

```bash
# the heavy org's member with the most live API keys
uv run python ops/audit/explain.py rows <db> "SELECT user_id, count(*) FROM core.api_keys WHERE org_id = '01900000-0000-7000-8000-00000000b16b' AND deleted_at IS NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
# a user of the heavy org who holds no API key (a departed member has none)
uv run python ops/audit/explain.py rows <db> "SELECT u.id FROM core.users u WHERE u.org_id = '01900000-0000-7000-8000-00000000b16b' AND NOT EXISTS (SELECT 1 FROM core.api_keys k WHERE k.org_id = u.org_id AND k.user_id = u.id) LIMIT 1"
# a small tenant
uv run python ops/audit/explain.py rows <db> "SELECT id FROM core.orgs WHERE kind = 'personal' LIMIT 1"
```

## When an index cannot help

Row-level security runs the policy before any filter whose function is
not leakproof, so such a filter stays out of the index condition and is
applied row by row after it, whatever index exists. A `numeric` or row
comparison (`(created_at, id) > (...)`) is one: a cursor on it cannot seek. The
plan shows it as a `Filter` beside `Rows Removed by Filter`; `SELECT
proname, proleakproof FROM pg_proc WHERE proname = '<function>'` (through
`explain.py rows`) says which. The finding is the statement's shape, not
a missing index.

## The cases

Each statement is measured where its cost differs, not once:

- a small tenant and the heavy one;
- a list's first page and a deep page (a cursor far in);
- a filter that matches much (the member with the most keys) and one
  that matches nothing (a user with none): the second is the one that
  walks the whole range;
- the tenant scope and, for a statement across tenants, the system scope,
  where the policy's estimate is at its worst;
- the generic plan (`-- params:`) for every statement that runs on every
  request or every write, since that is the plan it runs under.

## Contention, when `--contention` asks for it

A lock held across round trips (the event cursor on every append, a
claim) is measured by running the storage call from several coroutines
at once, through the real storage impl on the run's database. Write the
script into the evidence folder, never into the repository:

```python
import asyncio, statistics, time
from auditdb import urls
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import RolePool

values = urls("audit_query_indexes_<yyyymmdd>")
pool = RolePool(size=40, checkout_timeout_seconds=30, statement_timeout_seconds=30)
storage = StoragePostgresImpl(
    {r: values["ACME_DATABASE_URL"] for r in DatabaseRole},
    {r: pool for r in DatabaseRole},
    system_urls={r: values["ACME_DATABASE_SYSTEM_URL"] for r in DatabaseRole},
)
# Call one storage method from 1, 4, 16, and 32 coroutines for a few
# seconds each; report calls per second, p50, and p99 per level.
```

Run it with `uv run python <script>` from the repository root with
`PYTHONPATH=ops/audit`. One process caps at about a thousand simple calls
a second; say so when a level reaches it.
