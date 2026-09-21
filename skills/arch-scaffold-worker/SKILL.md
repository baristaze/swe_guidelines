---
name: arch-scaffold-worker
description: "Create a worker role the way the Software Design and Architecture Guidelines prescribe: a claim, handle, complete loop over the table-backed work queue, a handler impl calling managers through the container, lease renewal and self-fencing, a liveness heartbeat, drain-first shutdown, the maintenance sweep, the console entry point, the image, and tests. Creates the work namespace when the repository has none. Stack: Python (FastAPI, Pydantic, SQLAlchemy)."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(uv run:*), Bash(uv sync:*), Bash(git status:*)
---

# arch-scaffold-worker

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: The Business
Layer (Operations Without a Principal), The Storage Layer (Database
Roles, The Second Fence), Infrastructure (Cache, Topics, Idempotency), The Network Layer
(Long-Running Orchestrations), Worker Roles (The Work Queue, Shape of a
Worker, Shutdown, Maintenance Without a Scheduler, Implementation
Options), Deployment (Infrastructure as Code), Cross-Cutting
Conventions (The App Container).

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
| `README.md`                           | the namespace one level below `om/README.md`, in the product's language: its noun (the work item, its kinds and statuses), what can happen to it (enqueued, claimed, completed, deferred, released, requeued, failed into a dead letter), and which rules hold (one holder per claim, a stale holder's write refused, a failure at `max_attempts` is final); no developer or operator instruction |
| `manager.py`                          | `WorkManagerInterface`: `enqueue(ctx, item)`, `enqueue_relayed(org_id, row)` (the outbox relay's caller, for a work item that follows a core write: it takes no context, builds the item from the row's payload, stamps `created_by` from the row's `actor_id`, and takes the item's `request_id` and `traceparent` from the row's, so the work names the request that caused it and the trace its own spans link to, declared on the interface as an operation without a principal beside the relay and the event append), `claim(rctx, lane, kinds, worker_id, lease) -> tuple[OpContext, WorkItem] \| None` (the loop mints one `RequestContext` per claim, carrying `AppContext(type=WORKER, version="<worker>@<release>")`, and the claim returns the `OpContext` the work runs under, which it asks the tenancy manager for, since only a transition produces a stage, and which names the item's `request_id` as its `caused_by_request_id`), `complete(ctx, item)`, `fail(ctx, item, error)`, `defer(ctx, item, delay)`, `release(ctx, item)`, `extend_lease(ctx, item, lease)`, `requeue_stale(ctx)`, `maintenance_contexts(rctx)` (one `RequestContext` per sweep pass; delegates to the tenancy manager's service contexts, one per live tenant, each minted for the tenant and not for a member: the tenant, the role reserved for services, and the system user `EMPTY_UUID` as its user id, so a tenant whose members have all left is still swept) |
| `types/__init__.py`                   | empty                                                                                   |
| `types/work_item.py`                  | `WorkKind`, `WorkStatus`, `WorkItem(Identifiable, Trackable)` with the fields The Work Queue declares, `claim_token` and the causing `request_id` and `traceparent` among them, and `WORK_PAYLOADS` fixing the payload shape per kind |
| `types/handler.py`                    | `WorkHandlerInterface.handle(ctx, item)`                                                |
| `impl/__init__.py`                    | empty                                                                                   |
| `impl/manager.py`                     | `WorkManagerImpl`: enqueue is a create, the base's insert that reports an existing id without touching it, so a retried enqueue never resets a claim, and a duplicate `idempotency_key` is reported the same way and never raised as a driver error, so the manager reads the enqueued row back and returns it; the manager's copy stamps the actor, the status, and the attempts, clears every claim field, and leaves the id and the timestamps as constructed, whatever the caller sent, then publishes `WORK_AVAILABLE`; `enqueue_relayed` is that same create under `(org_id, row)`, taking the actor, the causing request id, and the trace context off the row instead of a context and presenting the row's own id as the item's `idempotency_key`, which is the same on every run of the relay, so a relay that runs twice and a caller that retries meet one insert under one key; every write to the row after the enqueue signs `updated_by` with `EMPTY_UUID` and never from the context, the one named exception to the copy of The Business Layer (Shape of an Operation), since the context is the attribution of the work and not of the bookkeeping on its row; claim mints a claim token, stamps it with the lease in the same statement, and asks the tenancy manager to build the `OpContext` the work runs under from the row's `created_by` under the role reserved for services, as OpContext (Stages) requires of every stage above the request stage, then returns that context with the item, the token on it; complete, requeue with a growing delay, or fail at `max_attempts`, each conditional on the claim token in the storage statement itself (never on `claimed_by`, since one worker can hold one item twice across a requeue), so a stale holder's write is refused with `Conflict` and the item is handed back without spending an attempt; a failed item is a dead letter: an `AuditEntry` appended through the `audit` namespace's manager (`AuditManagerInterface`, a constructor dependency; `arch-scaffold-new` creates the namespace) names it and a metric counts it; defer, release, and `extend_lease` carry the token and condition on it the same way, and hand back or renew without spending an attempt; the sweep has the two shapes of The Business Layer (Operations Without a Principal): `requeue_stale(ctx)` per tenant under a service context, one conditional statement in storage (`requeue_stale(ctx.org_id, before, limit)`), and the purge of soft-deleted rows past their retention period the same way, while the outbox relay and the claim read across tenants in one statement and get the tenant back with each row |
| `rules.py`                            | the pure arithmetic: `retry_delay`, `is_exhausted`, attempt and stagger bookkeeping; the manager calls them before or after the statement (`is_exhausted` decides fail or requeue, `retry_delay` the next `available_at`), and a rule a statement must evaluate itself (the stale filter of `requeue_stale`) is spelled once more there, named as such, and held to the function by the contract case |
| `storage/__init__.py`                 | `WorkStorageInterface`: `create_item` (the insert that reports an existing id), `write_item(org_id, item, claim_token)` (conditional on the token in the statement), `claim_next(lane, kinds, worker_id, lease)` as one method that mints and returns the claim token with the row, `requeue_stale(org_id, before, limit)` (the per-tenant sweep: one conditional statement that hands back the tenant's items whose lease passed before `before`, at most `limit`, and returns how many it moved), `read_item(org_id, item_id)` (every read after the claim is under the context's tenant, so an item of another tenant is not found); the tenant-less method `claim_next` carries a docstring saying why it takes no tenant and returns the tenant with its row, and opens its session under the system scope, `EMPTY_UUID` passed explicitly to the funnel, as The Storage Layer (The Second Fence) states |
| `storage/impl/__init__.py`            | empty                                                                                   |
| `storage/impl/postgres.py`            | the claim as `SELECT ... FOR UPDATE SKIP LOCKED` in one method                          |
| `storage/impl/memory.py`              | the same contract over an in-memory table and a lock                                    |
| `storage/tables/__init__.py`          | empty                                                                                   |
| `storage/tables/work_items.py`        | the table in the `queue` role with a `claim_token` column, unique index on `idempotency_key` (with its contract case, so the memory impl reports the duplicate the engine reports, never raising where the other returns), index on `(lane, status, available_at)`, index on `(status, lease_expires_at)` for the sweep |
| `om/migrations/sql/queue/<stamp>_work_items.up.sql` and `.down.sql`, `om/migrations/versions/queue/<stamp>_work_items.py` | the table, its policy on `org_id` with `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` (the table is `org`-scoped), and its wrapper; the down file drops the policy with the table |
| `om/tests/contracts/work_storage.py`, `om/tests/unit/test_work_storage.py`, `om/tests/integration/test_work_storage_postgres.py` | claim exclusivity, lease, requeue, a completion after the lease passed refused, a completion carrying the token of an earlier claim of the same worker refused, a duplicate `idempotency_key` reported by both impls; the cross-tenant cases on the methods that take a tenant: `write_item` presenting another tenant's item with its token and leaving the row as it stood, `read_item` finding nothing, and `requeue_stale` under one tenant leaving another tenant's stale item claimed; the raced claim: two claimers at once against one item, exactly one wins, the same case over memory and over Postgres |
| the tree's tenancy policy test (nothing to write) | it reads the tenancy scope map, so `work_items` is covered the moment its scope is declared, and it fails until the migration carries the policy |
| `om/tests/unit/test_work_manager.py`  | enqueue publishes, a retried enqueue leaves a claimed row untouched, the copy stamps the actor and the status over what the caller sent and keeps the timestamps as constructed, `enqueue_relayed` takes the actor, the request id, and the traceparent from the row and meets the same insert as a retried `enqueue` under one `idempotency_key`, claim returns the enqueuer's context, which names the item's request as its cause, and the token, every write after the enqueue signs `updated_by` with `EMPTY_UUID`, complete and defer |

Worker, under `workers/<worker-name>/`:

| File                                       | Holds                                                                                   |
|--------------------------------------------|-----------------------------------------------------------------------------------------|
| `pyproject.toml`                           | the distribution; dependencies on `<root>-om` and `<root>-infra` through `[tool.uv.sources]`; a console entry point |
| `src/<root>/workers/<worker>/__init__.py`  | empty                                                                                   |
| `src/<root>/workers/<worker>/settings.py`  | one `BaseSettings`: lane, capacity, lease length, heartbeat interval, heartbeat failure limit, sweep interval, metrics port; the capacity is set against the pool this process opens for the roles it touches and never independently of it, since a worker that runs more items at once than its pool serves spends the difference waiting on a checkout, as The Storage Layer (Database Roles) states |
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
| `om/src/<root>/om/work/README.md`, `om/README.md` | the new kind named in the namespace's README when the namespace already existed; a link to that README from `om/README.md` (new namespace only) |
| `om/src/<root>/om/storage/roles.py` (new namespace only) | `"work_items": DatabaseRole.QUEUE` in the role map `TABLE_ROLES`, and `"work_items": TenancyScope.ORG` in the tenancy scope map `TABLE_SCOPES` beside it |
| `om/src/<root>/om/storage/root.py` and both impls (new namespace only) | `get_work_storage()`                    |
| `om/src/<root>/om/root.py` (new namespace only) | `WorkManagerImpl` constructed and added to `Managers`, before the outbox relay impl, which now takes it        |
| `om/src/<root>/om/outbox/impl/` (new namespace only) | the relay's second branch: a row whose `kind` is `work.<kind>` calls `WorkManagerInterface.enqueue_relayed(org_id, row)`, which publishes `WORK_AVAILABLE` as every enqueue does, beside the entity-change branch `arch-scaffold-new` wrote; the relay takes the work manager by interface, so `enqueue_relayed` has its caller from this step on |
| `om/tests/unit/test_outbox_relay.py` (new namespace only) | a `work.<kind>` row relayed twice leaves one work item, and the item carries the row's actor, request id, and traceparent |
| root `pyproject.toml` and `om/tests/unit/` the request-stage test (new namespace only) | `WorkStorageInterface.claim_next` added to `[tool.arch-check.options.CTX-12] tenantless`, and `claim` and `maintenance_contexts` to the request-stage test |
| `infra/src/<root>/infra/topics/__init__.py` (when absent) | `Topics.WORK_AVAILABLE` and its payload            |
| `infra/src/<root>/infra/cache/__init__.py` (when absent) | `WORKER_LIVENESS = "worker_liveness"` on the `CacheScope` enum, the scope the liveness key lives under |
| `pyproject.toml` (root)                     | the member added to `[tool.uv.workspace] members`                |
| `scripts/dev.sh`                            | starts the worker                                                |
| `.env.example`                              | every field of the worker's settings under its prefix, with its local value |
| `deployment/terraform/modules/`, `deployment/terraform/environments/*/` | one instance of the service module per environment for this worker, with no load balancer route: its ECS service with rollout limits so it never exceeds its desired count, its log group with retention, its target-tracking autoscaling behind the root switch `autoscaling_enabled`, its running-tasks-below-desired alarm on the environment's alarm topic, and every setting without a local default passed as a variable or wired as a secret, as Deployment (Infrastructure as Code) states for every process |
| `.github/workflows/deploy-staging.yml`, `deploy-production.yml` | the worker's image: built and pushed under the commit by the staging workflow, which records its digest by commit; promoted by that digest for the release commit in the production workflow's plan, never rebuilt |
| `deployment/local/docker-compose.full.yml` (with `--container`) | the worker as a container, for the case that asks for it; `scripts/dev.sh` starts it on the host either way |

## Procedure

1. When the `work` namespace exists, reuse its interfaces unchanged and
   add only the kind, the handler, and the worker.
2. The loop passes the context the claim returned to `handle`; the
   handler never builds one. An item of a kind the worker does not
   handle is released, not failed.
3. Shutdown: stop claiming, cancel every task, return each item to
   the queue with a note, stop the heartbeat, then mark the worker
   offline.
4. The worker's tenant-less storage method (`claim_next`) gets a
   docstring and an entry in
   `[tool.arch-check.options.CTX-12] tenantless` before the fast gate
   runs; `arch-check` fails otherwise, as it should.
5. Add `workers/<worker-name>` to the root's `[tool.uv.workspace]
   members` and run `uv sync` before the fast gate, so the workspace
   resolves the new distribution.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
