---
name: arch-scaffold-worker
description: "Create a worker role the way the Software Design and Architecture Guidelines prescribe: a claim, handle, complete loop over the table-backed work queue, a handler impl calling managers through the container, lease renewal and self-fencing, a liveness heartbeat, drain-first shutdown, the maintenance sweep, the console entry point, the image, and tests. Creates the work namespace when the repository has none. Stack: Python (FastAPI, Pydantic, SQLAlchemy)."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(uv run:*), Bash(uv sync:*), Bash(git status:*)
---

# arch-scaffold-worker

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: The Business
Layer (Operations Without a Principal), The Storage Layer (Database
Roles), Infrastructure (Cache, Topics, Idempotency), The Network Layer
(Long-Running Orchestrations), Worker Roles (The Work Queue, Shape of a
Worker, Shutdown, Maintenance Without a Scheduler, Implementation
Options), Cross-Cutting Conventions (The App Container).

## Input

`<worker-name> <WorkKind> [--lane <name>] [--container]`

Example: `shipment-notifier NOTIFY_SHIPMENT`. Both positional arguments
are required; ask for them when missing. The lane defaults to
`default`. `<worker>` is the worker name in snake case, `<Kind>` the
work kind in CamelCase (`NOOP` gives `Noop`, `NOTIFY_SHIPMENT` gives
`NotifyShipment`, so its handler is `NotifyShipmentHandlerImpl`).

When the repository has no `work` namespace, this skill creates it
first (the `Created` rows marked "work namespace"), then the worker.

## Created

Work namespace, under `om/src/<root>/om/work/` (only when absent):

| File                                  | Holds                                                                                   |
|---------------------------------------|-----------------------------------------------------------------------------------------|
| `__init__.py`                         | `from .manager import WorkManagerInterface`                                             |
| `manager.py`                          | `WorkManagerInterface`: `enqueue(ctx, item)`, `enqueue_relayed(org_id, row)` (the outbox relay's caller, for a work item that follows a core write: it takes no context, builds the item from the row's payload, stamps `created_by` from the row's `actor_id`, and takes the item's `request_id` and `traceparent` from the row's, so the work names the request that caused it and the trace its own spans link to, declared on the interface as an operation without a principal beside the relay and the event append), `claim(rctx, lane, kinds, worker_id, lease) -> tuple[OpContext, WorkItem] \| None` (the loop mints one `RequestContext` per claim, carrying `AppContext(type=WORKER, version="<worker>@<release>")`, and the claim returns the `OpContext` the work runs under, which it asks the tenancy manager for, since only a transition produces a stage, and which names the item's `request_id` as its `caused_by_request_id`), `complete(ctx, item)`, `fail(ctx, item, error)`, `defer(ctx, item, delay)`, `release(ctx, item)`, `extend_lease(ctx, item, lease)`, `requeue_stale(ctx)`, `maintenance_contexts(rctx)` (one `RequestContext` per sweep pass; delegates to the tenancy manager's service contexts, one per live tenant, each minted for the tenant and not for a member: the tenant, the role reserved for services, and the system user `EMPTY_UUID` as its user id, so a tenant whose members have all left is still swept) |
| `types/__init__.py`                   | empty                                                                                   |
| `types/work_item.py`                  | `WorkKind`, `WorkStatus`, `WorkItem(Identifiable, Trackable)` with the fields The Work Queue declares, `claim_token` and the causing `request_id` and `traceparent` among them, and `WORK_PAYLOADS` fixing the payload shape per kind |
| `types/handler.py`                    | `WorkHandlerInterface.handle(ctx, item)`                                                |
| `impl/__init__.py`                    | empty                                                                                   |
| `impl/manager.py`                     | `WorkManagerImpl`: enqueue is a create, the base's insert that reports an existing id without touching it, so a retried enqueue never resets a claim, and a duplicate `idempotency_key` is reported the same way and never raised as a driver error, so the manager reads the enqueued row back and returns it; the manager's copy stamps the actor, the status, and the attempts, clears every claim field, and leaves the id and the timestamps as constructed, whatever the caller sent, then publishes `WORK_AVAILABLE`; `enqueue_relayed` is that same create under `(org_id, row)`, taking the actor, the causing request id, and the trace context off the row instead of a context and presenting the row's own id as the item's `idempotency_key`, which is the same on every run of the relay, so a relay that runs twice and a caller that retries meet one insert under one key; every write to the row after the enqueue signs `updated_by` with `EMPTY_UUID` and never from the context, the one named exception to the copy of The Business Layer (Shape of an Operation), since the context is the attribution of the work and not of the bookkeeping on its row; claim mints a claim token, stamps it with the lease in the same statement, and asks the tenancy manager to build the `OpContext` the work runs under from the row's `created_by` under the role reserved for services, as OpContext (Stages) requires of every stage above the request stage, then returns that context with the item, the token on it; complete, requeue with a growing delay, or fail at `max_attempts`, each conditional on the claim token in the storage statement itself (never on `claimed_by`, since one worker can hold one item twice across a requeue), so a stale holder's write is refused with `Conflict` and the item is handed back without spending an attempt; a failed item is a dead letter: an `AuditEntry` appended through the `audit` namespace's manager (`AuditManagerInterface`, a constructor dependency; `arch-scaffold-new` creates the namespace) names it and a metric counts it; defer, release, and `extend_lease` carry the token and condition on it the same way, and hand back or renew without spending an attempt; the sweep has the two shapes of The Business Layer (Operations Without a Principal): `requeue_stale(ctx)` per tenant under a service context, one conditional statement in storage, and the purge of soft-deleted rows past their retention period the same way, while the outbox relay and the expiry of a lease read across tenants in one statement and get the tenant back with each row |
| `rules.py`                            | the pure arithmetic: `retry_delay`, `is_exhausted`, attempt and stagger bookkeeping; the manager calls them before or after the statement (`is_exhausted` decides fail or requeue, `retry_delay` the next `available_at`), and a rule a statement must evaluate itself (the stale filter of `read_stale`) is spelled once more there, named as such, and held to the function by the contract case |
| `storage/__init__.py`                 | `WorkStorageInterface`: `create_item` (the insert that reports an existing id), `write_item(org_id, item, claim_token)` (conditional on the token in the statement), `claim_next(lane, kinds, worker_id, lease)` as one method that mints and returns the claim token with the row, `read_stale(before)`, `read_item`; the tenant-less methods (`claim_next`, `read_stale`, `read_item`) each carry a docstring saying why they take no tenant and return the tenant with each row |
| `storage/impl/__init__.py`            | empty                                                                                   |
| `storage/impl/postgres.py`            | the claim as `SELECT ... FOR UPDATE SKIP LOCKED` in one method                          |
| `storage/impl/memory.py`              | the same contract over an in-memory table and a lock                                    |
| `storage/tables/__init__.py`          | empty                                                                                   |
| `storage/tables/work_items.py`        | the table in the `queue` role with a `claim_token` column, unique index on `idempotency_key` (with its contract case, so the memory impl reports the duplicate the engine reports, never raising where the other returns), index on `(lane, status, available_at)`, index on `(status, lease_expires_at)` for the sweep |
| `om/migrations/sql/queue/<stamp>_work_items.up.sql` and `.down.sql`, `om/migrations/versions/queue/<stamp>_work_items.py` | the table and its wrapper |
| `om/tests/contracts/work_storage.py`, `om/tests/unit/test_work_storage.py`, `om/tests/integration/test_work_storage_postgres.py` | claim exclusivity, lease, requeue, a completion after the lease passed refused, a completion carrying the token of an earlier claim of the same worker refused, a duplicate `idempotency_key` reported by both impls; the raced claim: two claimers at once against one item, exactly one wins, the same case over memory and over Postgres |
| `om/tests/unit/test_work_manager.py`  | enqueue publishes, a retried enqueue leaves a claimed row untouched, the copy stamps the actor and the status over what the caller sent and keeps the timestamps as constructed, `enqueue_relayed` takes the actor, the request id, and the traceparent from the row and meets the same insert as a retried `enqueue` under one `idempotency_key`, claim returns the enqueuer's context, which names the item's request as its cause, and the token, every write after the enqueue signs `updated_by` with `EMPTY_UUID`, complete and defer |

Worker, under `workers/<worker-name>/`:

| File                                       | Holds                                                                                   |
|--------------------------------------------|-----------------------------------------------------------------------------------------|
| `pyproject.toml`                           | the distribution; dependencies on `<root>-om` and `<root>-infra` through `[tool.uv.sources]`; a console entry point |
| `src/<root>/workers/<worker>/__init__.py`  | empty                                                                                   |
| `src/<root>/workers/<worker>/settings.py`  | one `BaseSettings`: lane, capacity, lease length, heartbeat interval, heartbeat failure limit, sweep interval, metrics port |
| `src/<root>/workers/<worker>/handler.py`   | `<Kind>HandlerImpl(WorkHandlerInterface)` taking the managers it needs by interface (none for the maintenance worker); a write to the record the item advances is a compare-and-set on the record's `version`, because the two fences guard the queue row and never the record |
| `src/<root>/workers/<worker>/container.py` | `WorkerContainer.build(settings)` and `for_tests(storage, infra)`, the same shape and order as a service container |
| `src/<root>/workers/<worker>/loop.py`      | the loop: wake on `WORK_AVAILABLE` with a short poll fallback; claim `(lane, [<KIND>])` while a slot is free; run each item as a task whose span links to the item's `traceparent` when it carries one, and is never parented to it, since the item outlives the causing request; the task renews its lease with the claim token (each renewal bounded by `asyncio.wait_for` at the renewal interval, a timeout counting as a failure): a renewal refused with `Conflict` cancels the task at once, since another worker holds the item now and that answer is definitive; any other failure is retried, and the task cancels itself when renewal has failed for half the lease, measured as wall-clock time since the last successful renewal, never as a count of failed attempts times the interval; heartbeat liveness as a key in `CacheScope.WORKER_LIVENESS` under the system scope, written and read back on every beat (a miss is a failed beat), and stop claiming after repeated failures; the maintenance sweep on a timer (requeue stale items, resume parked records, relay what a crash left in the outbox, purge done outbox rows and soft-deleted rows past retention), every step idempotent and wrapped; drain on stop |
| `src/<root>/workers/<worker>/main.py`      | settings, the same `boot(settings)` as a service (logging, error reporting, trust store, tracing), `/metrics` and `/healthz` served alone on the metrics port, a small port of the worker's own since it has no API, `/healthz` answering non-2xx once the loop's beats have failed their limit, read from the loop's own state and with no I/O of its own, as The Network Layer (The Gateway) states for every liveness route, container, stop handlers, the `serve` subcommand |
| `tests/test_handler.py`                    | the handler over the memory container, run twice with the same item                     |
| `tests/test_loop.py`                       | claim within capacity, lease renewal, a renewal refused with `Conflict` cancels at once, a renewal that times out is retried, lease loss cancels the task, heartbeat failure pauses claiming, drain on stop |
| `deployment/docker/<worker-name>.Dockerfile` | same shape as a service image, the metrics port its only exposed port, healthcheck on `/healthz` like every image |

## Changed

| File                                        | Change                                                          |
|---------------------------------------------|-----------------------------------------------------------------|
| `om/src/<root>/om/work/types/work_item.py`   | `<KIND>` added to `WorkKind`                                     |
| `om/src/<root>/om/storage/roles.py` (new namespace only) | `"work_items": DatabaseRole.QUEUE`                    |
| `om/src/<root>/om/storage/root.py` and both impls (new namespace only) | `get_work_storage()`                    |
| `om/src/<root>/om/root.py` (new namespace only) | `WorkManagerImpl` constructed and added to `Managers`, before the outbox relay impl, which now takes it        |
| `om/src/<root>/om/outbox/impl/` (new namespace only) | the relay's second branch: a row whose `kind` is `work.<kind>` calls `WorkManagerInterface.enqueue_relayed(org_id, row)` and publishes `WORK_AVAILABLE`, beside the entity-change branch `arch-scaffold-new` wrote; the relay takes the work manager by interface, so `enqueue_relayed` has its caller from this step on |
| `om/tests/unit/test_outbox_relay.py` (new namespace only) | a `work.<kind>` row relayed twice leaves one work item, and the item carries the row's actor, request id, and traceparent |
| `om/tests/unit/` the tenant-first exceptions test (new namespace only) | `claim_next`, `read_stale`, and `read_item` added to the enumerated exceptions, and `claim` and `maintenance_contexts` to the methods that take the request stage |
| `infra/src/<root>/infra/topics/__init__.py` (when absent) | `Topics.WORK_AVAILABLE` and its payload            |
| `infra/src/<root>/infra/cache/__init__.py` (when absent) | `WORKER_LIVENESS = "worker_liveness"` on the `CacheScope` enum, the scope the liveness key lives under |
| `pyproject.toml` (root)                     | the member added to `[tool.uv.workspace] members`                |
| `scripts/dev.sh`                            | starts the worker                                                |
| `deployment/local/docker-compose.full.yml` (with `--container`) | the worker as a container, for the case that asks for it; `scripts/dev.sh` starts it on the host either way |

## Procedure

1. When the `work` namespace exists, reuse its interfaces unchanged and
   add only the kind, the handler, and the worker.
2. The loop passes the context the claim returned to `handle`; the
   handler never builds one. An item of a kind the worker does not
   handle is released, not failed.
3. Shutdown: cancel every task, return each item to the queue with a
   note, stop the heartbeat, then mark the worker offline.
4. The worker's tenant-less storage methods (`claim_next`,
   `read_stale`, `read_item`) get a docstring and a line in the
   repository's tenant-first exceptions test before the fast gate
   runs; the test fails otherwise, as it should.
5. Add `workers/<worker-name>` to the root's `[tool.uv.workspace]
   members` and run `uv sync` before the fast gate, so the workspace
   resolves the new distribution.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
