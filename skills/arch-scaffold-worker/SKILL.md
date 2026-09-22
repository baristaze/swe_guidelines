---
name: arch-scaffold-worker
description: "Create a worker role: the claim, handle, complete loop over the work queue, lease renewal and self-fencing, liveness, drain-first shutdown, the sweep, the image, and tests. Python."
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
first, in step 1, then the worker.

## Created

The file-by-file lists are long, so each one lives beside this file and
is read by the step that names it, when that step runs and not before.

| Reference                                          | Holds                                                                                                                                                | Read by                              |
|----------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------|
| `${CLAUDE_SKILL_DIR}/references/work-namespace.md` | the `work` namespace under `om/src/<root>/om/work/`: the manager and its impl, the work item and the handler interface, the pure rules, storage and both impls, the table, the `queue` chain's first migration, and the contract, unit, and integration cases | step 1, only when the namespace is absent |
| `${CLAUDE_SKILL_DIR}/references/worker.md`         | the worker under `workers/<worker-name>/`: the distribution, its settings, the handler, the container, the loop, the entry point, the image, and the tests | step 2                               |

A reference file is detail. These are the lines a run must never miss,
so they stay here:

- One holder per claim. Complete, fail, defer, release, and
  `extend_lease` are each conditional on the claim token in the storage
  statement itself, never on `claimed_by`, since one worker can hold
  one item twice across a requeue; a stale holder's write is refused
  with `Conflict` and the item is handed back without spending an
  attempt.
- The work manager constructs no stage. The claim asks the tenancy
  manager's `claim_context(...)` for the `OpContext` the work runs
  under, as OpContext (Stages) requires of every stage above the
  request stage.
- A failure at `max_attempts` is final: the item is a dead letter, an
  `AuditEntry` through the `audit` namespace names it, and a metric
  counts it.
- An effect the handler must make happen rides an outbox row or a work
  item, never a topic alone, since a topic delivers at most once and
  carries hints only.
- The worker's liveness is its own. `/healthz` answers from the loop's
  in-memory beat, with no I/O of its own, and a failed liveness publish
  is logged and never pauses claiming.
- The capacity is set against the pool this process opens for the roles
  it touches and never independently of it.

## Changed

| File                                        | Change                                                          |
|---------------------------------------------|-----------------------------------------------------------------|
| `om/src/<root>/om/work/types/work_item.py`   | `<KIND>` added to `WorkKind`                                     |
| `om/src/<root>/om/work/README.md`, `om/README.md` | the new kind named in the namespace's README when the namespace already existed; a link to that README from `om/README.md` (new namespace only) |
| `om/src/<root>/om/storage/roles.py` (new namespace only) | `"work_items": DatabaseRole.QUEUE` in the role map `TABLE_ROLES`, and `"work_items": TenancyScope.ORG` in the tenancy scope map `TABLE_SCOPES` beside it |
| `om/src/<root>/om/storage/root.py` and both impls (new namespace only) | `get_work_storage()`                    |
| `om/src/<root>/om/root.py` (new namespace only) | `WorkManagerImpl` constructed and added to `Managers`, with `item_retention` from the options `build_managers` takes. The outbox relay impl is constructed first and takes the work manager as a callable bound at call time (`lambda: managers.work`), because the work manager needs the tenancy manager, which needs the relay |
| `om/src/<root>/om/outbox/impl/` (new namespace only) | the relay's second branch: a row whose `kind` is `work.<kind>`, `<kind>` the `WorkKind` member's name in lower case, calls `WorkManagerInterface.enqueue_relayed(org_id, row)`, which publishes `WORK_AVAILABLE` as every enqueue does, beside the entity-change branch `arch-scaffold-new` wrote; the relay takes the work manager by interface, so `enqueue_relayed` has its caller from this step on |
| `om/tests/unit/test_outbox_relay.py` (new namespace only) | a `work.<kind>` row relayed twice leaves one work item, whose id is the row's id, and the item carries the row's actor, request id, and traceparent |
| `om/src/<root>/om/tenancy/manager.py`, `impl/manager.py` (new namespace only) | `claim_context(rctx, org_id, user_id, caused_by_request_id) -> OpContext`, the transition the claim asks for: it reads the tenant and the membership of `user_id`, refuses a tenant that is gone with `NotFound`, and builds the `OpContext` under the role reserved for services for `user_id`; when `user_id` is `EMPTY_UUID` or holds no live membership, it builds the tenant's service context instead, the one `service_contexts` builds; `purge_socket_tickets(rctx)` and `purge_sessions(rctx)`, the sweep's two purges, when absent, since `arch-scaffold-new` writes them |
| `om/src/<root>/om/idempotency/manager.py`, `impl/manager.py`, and the tenancy and idempotency storage interfaces and both impls of each (new namespace only) | when absent, since `arch-scaffold-new` writes them: `purge_markers(rctx)` on the idempotency manager; the storage purges, `IdempotencyStorageInterface.purge_markers(before, limit)` (markers past their retention), `TenancyStorageInterface.purge_socket_tickets(now, limit)` (socket tickets redeemed or past their expiry), and `TenancyStorageInterface.purge_sessions(now, idle_before, limit)` (sessions ended or past their idle or absolute lifetime), each one statement across tenants under `_session_for(stmt, org_id=EMPTY_UUID)` with a docstring saying why it takes no tenant |
| root `pyproject.toml` and `om/tests/unit/` the request-stage test (new namespace only) | `WorkStorageInterface.claim_next`, `WorkStorageInterface.fail_orphaned`, `WorkStorageInterface.read_gauges`, `WorkStorageInterface.purge_items`, and, when absent, the three storage purges (`IdempotencyStorageInterface.purge_markers`, `TenancyStorageInterface.purge_socket_tickets`, `TenancyStorageInterface.purge_sessions`) added to `[tool.arch-check.options.CTX-12] tenantless`; `om/src/<root>/om/tenancy/impl/manager.py::TenancyManagerImpl.claim_context` added to `[tool.arch-check.options.CTX-26] sites`, since it constructs the `OpContext` the claim returns; and `claim`, `purge_items`, `read_gauges`, `maintenance_contexts`, `claim_context`, and, when absent, `purge_markers`, `purge_socket_tickets`, and `purge_sessions` to the request-stage test |
| `infra/src/<root>/infra/topics/__init__.py` (when absent) | `Topics.WORK_AVAILABLE` and its payload            |
| `infra/src/<root>/infra/cache/__init__.py` (when absent) | `WORKER_LIVENESS = "worker_liveness"` on the `CacheScope` enum, the scope the liveness key lives under |
| `pyproject.toml` (root)                     | the member added to `[tool.uv.workspace] members`                |
| `scripts/dev.sh`                            | starts the worker                                                |
| `.env.example`                              | every field of the worker's settings under its prefix, with its local value |
| `services/api/src/<root>/services/api/settings.py` (new namespace only) | `item_retention`, the same setting the worker reads, since the API's container builds the work manager too and `build_managers` passes it |
| `deployment/terraform/modules/`, `deployment/terraform/environments/*/` | one instance of the service module per environment for this worker, with no load balancer route: its ECS service with rollout limits so it never exceeds its desired count, its log group with retention, its target-tracking autoscaling behind the root switch `autoscaling_enabled`, its running-tasks-below-desired alarm on the environment's alarm topic, and every setting without a local default passed as a variable or wired as a secret, as Deployment (Infrastructure as Code) states for every process |
| `.github/workflows/deploy-staging.yml`, `deploy-production.yml` | the worker's image: built and pushed under the commit by the staging workflow's push-only build job, which records its digest on the repository host's deployment record; promoted by that digest for the release commit in the production workflow's plan, checked against the record, never rebuilt; a new worker's image repository is a bootstrap root change, applied by a create run in each account |
| `deployment/local/docker-compose.full.yml` (with `--container`) | the worker as a container, for the case that asks for it; `scripts/dev.sh` starts it on the host either way |

## Procedure

1. When the `work` namespace exists, reuse its interfaces unchanged and
   add only the kind, the handler, and the worker. When it is absent,
   read `${CLAUDE_SKILL_DIR}/references/work-namespace.md`, write the
   namespace first, and apply every `Changed` row marked "new
   namespace only": the role and scope maps, the storage root and both
   impls, `build_managers`, the outbox relay's second branch, the
   tenancy and idempotency operations the sweep calls, the API's
   `item_retention`, and their tests.
2. Read `${CLAUDE_SKILL_DIR}/references/worker.md`, then write the
   worker. The loop passes the context the claim returned to
   `handle`; the handler never builds one. An item of a kind the
   worker does not handle is released, not failed.
3. Shutdown: stop claiming, cancel every task, return each item to
   the queue with a note, stop the heartbeat, then mark the worker
   offline.
4. Apply the rest of the `Changed` table: the new kind on `WorkKind`,
   the namespace README and its link from `om/README.md`,
   `scripts/dev.sh`, every settings field in `.env.example`, the topic
   and the cache scope when absent, one instance of the service module
   per environment under `deployment/terraform/` with no load balancer
   route, this worker's image in both deploy workflows, and the compose
   file with `--container`.
5. The worker's tenant-less storage methods (`claim_next`,
   `fail_orphaned`, `read_gauges`, `purge_items`, and, when absent,
   `purge_markers`, `purge_socket_tickets`, and `purge_sessions`) get
   a docstring and an entry in
   `[tool.arch-check.options.CTX-12] tenantless`, and
   `claim_context` its entry in `[tool.arch-check.options.CTX-26]
   sites`, before the fast gate runs; `arch-check` fails otherwise, as
   it should.
6. Add `workers/<worker-name>` to the root's `[tool.uv.workspace]
   members` and run `uv sync` before the fast gate, so the workspace
   resolves the new distribution.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
