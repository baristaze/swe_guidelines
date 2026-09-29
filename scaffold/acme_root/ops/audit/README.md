# The audit tools

What the audit skills run. An audit is a read-only analysis that repeats:
after a big change, before a release, when the product grows. It builds
what it measures on its own, writes a report, and proposes tickets; it
never fixes. These tools are the parts of an audit that are the same
every time, so a run is one command and its numbers compare with the
last run's.

They live beside the application, never in it: no process of the
platform imports them. They run from the repository root with `uv run
python ops/audit/<tool>.py`, on the local stack (`make infra-up`, and
`make migrate` once).

| Tool | Does | Used by |
|------|------|---------|
| `auditdb.py create\|drop\|env <name>`, `auditdb.py list` | Makes a database named `audit_<slug>` on the local Postgres, gives it the logins' grants, and migrates every role; drops it with its connections; lists the audit databases there are. Refuses any other name and any host that is not local. | every database audit |
| `seed.py <name> --scale <n>` | Fills an audit database, as the superuser, with a heavy synthetic shape: at scale 1, 5,000 people with a personal org each, a team org of 200 members holding 100,000 tasks (open, done, archived, deleted) and a million events, and work items, outbox rows, sessions, idempotency records, files, invitations, API keys, Slack installations, and cleanup records beside them. Every count scales linearly; `0.01` is a quick run of the same shape. The team org's last member holds no task, so an audit always has an idle member. | `audit-retention`, `audit-query-indexes` |
| `explain.py plans <name> <file>` | `EXPLAIN (ANALYZE, BUFFERS)` of each statement in a file, under the login and scope the storage funnel uses (row-level security in force), each on a connection of its own in a transaction it rolls back; the generic plan for a statement given `-- params:`. | `audit-retention`, `audit-query-indexes` |
| `explain.py inventory\|reset\|index\|rows <name>` | Every table's rows and size and every index's size and scans; zeroes the scan counters; creates or drops one candidate index; prints what one `SELECT` returns. | the same |
| `dbcalls.py run\|summary\|providers` | The real API and worker in one process over an audit database, with the provider twins, driven through the flows of `dbcalls_flows.py` (and a run's own `--flows` file); counts every round trip, transaction, and role at the asyncpg adapter, and labels each transaction with the storage method that opened it. Each call also records the calls it made to the identity, payments, and Slack twins, in order; `providers` lists them. | `audit-database-calls`, `audit-credential-lifetimes`, `audit-provider-calls` |
| `deploy_timeline.py steps\|events` | A deploy run's jobs and its long steps from `gh run view --json`, and a window's ECS service events from `describe-services`, as tables with offsets. | `audit-deploy-time` |

The tests are `ops/tests/test_audit_tools.py` (no database) and
`ops/tests/test_audit_database.py` (`make test-integration`), which
makes, seeds, reads, counts, and drops a database of its own; a schema
change the seed no longer fits fails there.
