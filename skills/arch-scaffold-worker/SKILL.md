---
name: arch-scaffold-worker
description: "Create a worker role the way the Software Design and Architecture Guidelines prescribe: a claim, handle, complete loop over the table-backed work queue, a handler impl calling managers through the container, lease renewal and self-fencing, a liveness heartbeat, drain-first shutdown, the maintenance sweep, the console entry point, the image, and tests. Creates the work namespace when the repository has none. Stack: Python (FastAPI, Pydantic, SQLAlchemy)."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make test-unit), Bash(uv run:*), Bash(uv sync:*), Bash(git status:*), Bash(git diff:*)
---

# arch-scaffold-worker

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: The Business
Layer (Operations Without a Principal), The Storage Layer (Database
Roles), Infrastructure (Topics, Idempotency), The Network Layer
(Long-Running Orchestrations), Worker Roles (The Work Queue, Shape of a
Worker, Shutdown, Maintenance Without a Scheduler, Implementation
Options), Cross-Cutting Conventions (The App Container).

## Input

`<worker-name> <WorkKind> [--lane <name>] [--container]`

Example: `shipment-notifier SHIPMENT_NOTIFY`. Both positional arguments
are required; ask for them when missing. The lane defaults to
`default`. `<worker>` is the worker name in snake case, `<Kind>` the
work kind in CamelCase (`NOOP` gives `Noop`, `SHIPMENT_NOTIFY` gives
`ShipmentNotify`).

When the repository has no `work` namespace, this skill creates it
first (the `Created` rows marked "work namespace"), then the worker.

## Created

Work namespace, under `om/src/<root>/om/work/` (only when absent):

| File                                  | Holds                                                                                   |
|---------------------------------------|-----------------------------------------------------------------------------------------|
| `__init__.py`                         | `from .manager import WorkManagerInterface`                                             |
| `manager.py`                          | `WorkManagerInterface`: `enqueue(ctx, item)`, `claim(rctx, lane, kinds, worker_id, lease) -> tuple[OpContext, WorkItem] \| None` (the loop mints one `RequestContext` per claim, carrying `AppContext(type=WORKER, version="worker@<worker_id>")`, and the claim returns the `OpContext` the work runs under), `complete(ctx, item)`, `fail(ctx, item, error)`, `defer(ctx, item, delay)`, `release(ctx, item)`, `extend_lease(ctx, item, lease)`, `requeue_stale(ctx)`, `maintenance_contexts(rctx)` (one `RequestContext` per sweep pass; delegates to the tenancy manager's service contexts, one per live tenant, naming the org's founding user under the service role) |
| `types/__init__.py`                   | empty                                                                                   |
| `types/work_item.py`                  | `WorkKind`, `WorkStatus`, `WorkItem(Identifiable, Trackable)` with the fields of The Work Queue (`lane`, not `queue`), and `WORK_PAYLOADS` fixing the payload shape per kind |
| `types/handler.py`                    | `WorkHandlerInterface.handle(ctx, item)`                                                |
| `impl/__init__.py`                    | empty                                                                                   |
| `impl/manager.py`                     | `WorkManagerImpl`: enqueue writes the row then publishes `WORK_AVAILABLE`; claim rebuilds the enqueuer's principal from `created_by` under the service role and returns it with the item; complete, requeue with a growing delay, or fail at `max_attempts`, each conditional on the claim (`claimed_by` and `lease_expires_at` on the row, in the storage statement itself), so a stale worker's write is refused with `Conflict` and the item is handed back without spending an attempt; a failed item is a dead letter: an audit entry names it and a metric counts it; defer and release hand back without spending an attempt; the sweep is `requeue_stale(ctx)` per tenant, one conditional statement in storage, plus the outbox relay and the purge of soft-deleted rows past their retention period |
| `rules.py`                            | the pure arithmetic: `retry_delay`, `is_exhausted`, attempt and stagger bookkeeping; the manager and both storage impls call it |
| `storage/__init__.py`                 | `WorkStorageInterface`: `write_item`, `claim_next(lane, kinds, worker_id, lease)` as one statement, `read_stale(before)`, `read_item` |
| `storage/impl/__init__.py`            | empty                                                                                   |
| `storage/impl/postgres.py`            | the claim as `SELECT ... FOR UPDATE SKIP LOCKED` in one method                          |
| `storage/impl/memory.py`              | the same contract over an in-memory table and a lock                                    |
| `storage/tables/__init__.py`          | empty                                                                                   |
| `storage/tables/work_items.py`        | the table in the `queue` role, unique index on `idempotency_key`, index on `(lane, status, available_at)`, index on `(status, lease_expires_at)` for the sweep |
| `om/migrations/sql/queue/<stamp>_work_items.up.sql` and `.down.sql`, `om/migrations/versions/queue/<stamp>_work_items.py` | the table and its wrapper |
| `om/tests/unit/test_work_storage.py`, `om/tests/integration/test_work_storage_postgres.py` | claim exclusivity, lease, requeue, a completion after the lease passed refused |
| `om/tests/unit/test_work_manager.py`  | enqueue publishes, claim returns the enqueuer's context, complete and defer             |

Worker, under `workers/<worker-name>/`:

| File                                       | Holds                                                                                   |
|--------------------------------------------|-----------------------------------------------------------------------------------------|
| `pyproject.toml`                           | the distribution; dependencies on `<root>-om` and `<root>-infra` through `[tool.uv.sources]`; a console entry point |
| `src/<root>/workers/<worker>/__init__.py`  | empty                                                                                   |
| `src/<root>/workers/<worker>/settings.py`  | one `BaseSettings`: lane, capacity, lease length, heartbeat interval, heartbeat failure limit, sweep interval, metrics port |
| `src/<root>/workers/<worker>/handler.py`   | `<Kind>HandlerImpl(WorkHandlerInterface)` taking the managers it needs by interface (none for the maintenance worker); a write to the record the item advances is a compare-and-set on the record's `version`, the second fence |
| `src/<root>/workers/<worker>/container.py` | `WorkerContainer.build(settings)` and `for_tests(storage, infra)`, the same shape and order as a service container |
| `src/<root>/workers/<worker>/loop.py`      | the loop: wake on `WORK_AVAILABLE` with a short poll fallback; claim `(lane, [<KIND>])` while a slot is free; run each item as a task that renews its lease (each renewal bounded by `asyncio.wait_for` at the renewal interval, a timeout counting as a failure) and cancels itself when renewal has failed for half the lease, measured as wall-clock time since the last successful renewal, never as a count of failed attempts times the interval; heartbeat liveness as a key in `CacheScope.WORKER_LIVENESS` under the system scope, written and read back on every beat (a miss is a failed beat), and stop claiming after repeated failures; the maintenance sweep on a timer (requeue stale items, resume parked records, relay the outbox, purge soft-deleted rows past retention), every step idempotent and wrapped; drain on stop |
| `src/<root>/workers/<worker>/main.py`      | settings, the same `boot(settings)` as a service (logging, error reporting, trust store, tracing), `/metrics` served alone on the metrics port, container, stop handlers, `serve` and `health` (reads the worker's own liveness key, exits non-zero when it is missing) subcommands |
| `tests/test_handler.py`                    | the handler over the memory container, run twice with the same item                     |
| `tests/test_loop.py`                       | claim within capacity, lease renewal, lease loss cancels the task, heartbeat failure pauses claiming, drain on stop |
| `deployment/docker/<worker-name>.Dockerfile` | same shape as a service image, no exposed port; its `HEALTHCHECK` runs the `health` subcommand |

## Changed

| File                                        | Change                                                          |
|---------------------------------------------|-----------------------------------------------------------------|
| `om/src/<root>/om/work/types/work_item.py`   | `<KIND>` added to `WorkKind`                                     |
| `om/src/<root>/om/storage/roles.py` (new namespace only) | `"work_items": DatabaseRole.QUEUE`                    |
| `om/src/<root>/om/storage/root.py` and both impls (new namespace only) | `get_work_storage()`                    |
| `om/src/<root>/om/root.py` (new namespace only) | `WorkManagerImpl` constructed and added to `Managers`        |
| `infra/src/<root>/infra/topics/__init__.py` (when absent) | `Topics.WORK_AVAILABLE` and its payload            |
| `pyproject.toml` (root)                     | the member added to `[tool.uv.workspace] members`                |
| `scripts/dev.sh`                            | starts the worker                                                |
| `deployment/local/docker-compose.full.yml` (with `--container`) | the worker as a container; without it `scripts/dev.sh` starts it on the host |

## Procedure

1. When the `work` namespace exists, reuse its interfaces unchanged and
   add only the kind, the handler, and the worker.
2. The loop passes the context the claim returned to `handle`; the
   handler never builds one. An item of a kind the worker does not
   handle is released, not failed.
3. Shutdown: cancel every task, return each item to the queue with a
   note, stop the heartbeat, then mark the worker offline.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
