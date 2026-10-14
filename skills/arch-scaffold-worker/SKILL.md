---
name: arch-scaffold-worker
description: "Add a kind of background work: the kind, its payload and permission, its handler in the maintenance worker or in a worker of its own, and the tests, in the shape of the scaffold's work queue and worker. Python."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(docker info:*), Bash(uv run:*), Bash(uv sync:*), Bash(git status:*)
---

# arch-scaffold-worker

A path that starts with `../` is read from this skill's folder
as `realpath` resolves it.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../architecture.md`: The Business
Layer (Operations Without a Principal), The Storage Layer (Database
Roles), Infrastructure (Topics, Idempotency), The Network Layer
(Long-Running Orchestrations), Worker Roles (The Work Queue, Shape of a
Worker, Shutdown, Maintenance Without a Scheduler), Deployment
(Infrastructure as Code).

## Input

`<worker-name> <WORK_KIND> [--lane <name>] [--container]`

Example: `maintenance NOTIFY_SHIPMENT`. Both are required; ask when
missing. `<Kind>` is the kind in CamelCase. When `<worker-name>` is a
worker the tree has (`maintenance` in a copy), the kind joins it.
Otherwise the skill creates the worker first. The kind belongs to
`<namespace>`, the namespace whose write starts it; ask when that is
not plain. `<Namespace>` is its name in CamelCase, `<NAMESPACE>` in
upper snake case.

A kind that needs its own capacity (`--lane <name>`) is first the lane
on its spec, which the relay lands its items on, and a replica of the
maintenance worker on that lane, which takes it from its deployment
spelled as the spec spells it (`<NAME>_WORKER_LANE`, or
`serve --lane`): one argument of code and a process in each place the
worker runs. Create a worker when the kind needs code or a dependency
the maintenance worker should not carry.

## Created

| File | Holds |
|------|-------|
| `om/src/<name>/om/<namespace>/kinds.py` (the namespace's first kind) | `<Namespace>Kind`, the `StrEnum` of the namespace's kind names, and `<NAMESPACE>_KINDS`, its tuple of `WorkKindSpec`, shape `om/work/kinds.py` and `WorkKind` |
| `workers/<worker-name>/src/<name>/workers/<worker>/<kind>.py` | `<Kind>HandlerImpl(WorkHandlerInterface)` with its `REQUIRES`, shape `handler.py` and `accounts.py` of `workers/maintenance/` |
| `workers/<worker-name>/tests/test_<kind>.py` | the handler over the memory container, run twice with the same item |
| `workers/<worker-name>/` (a new worker) | the distribution, shape `workers/maintenance/`: settings, container, loop, entry, main, health, and their tests, without the sweep and the delivery consumer, which stay in the maintenance worker |
| `deployment/docker/<worker-name>.Dockerfile` (a new worker) | the image, shape `maintenance.Dockerfile` |

## Changed

| File | Change |
|------|--------|
| `om/src/<name>/om/<namespace>/kinds.py` | the kind in `<Namespace>Kind`, and its spec in `<NAMESPACE>_KINDS`: its payload, its permission, and with `--lane` its lane |
| `om/src/<name>/om/<namespace>/types/work.py` | the kind's payload, on the `OmBase` base |
| `om/src/<name>/om/root.py` (the namespace's first kind) | `*<NAMESPACE>_KINDS,` in `KINDS`, below the marker line |
| `om/src/<name>/om/<namespace>/README.md` | the kind, in the product's language |
| the worker's `main.py` | the handler in `handlers` of `build_loop`, keyed by `<Namespace>Kind.<KIND>`, below the marker line |
| the producing manager's impl | the write that starts the work lands a row of kind `work_row_kind(<Namespace>Kind.<KIND>)` beside its own |
| `pyproject.toml` (root), `scripts/dev.sh`, `.env.example` (a new worker) | the member, the process started, and every field of its settings |
| `deployment/local/docker-compose.full.yml` (a new worker, with `--container`) | the worker as a container |
| `deployment/terraform/modules/environment/main.tf`, `modules/account/variables.tf`, both deploy workflows (a new worker) | an instance of the service module beside `module "maintenance"`, with no load balancer route; its image in `images`; its image built once by staging and promoted by digest |
| `scripts/dev.sh`, `deployment/local/docker-compose.full.yml`, `deployment/terraform/modules/environment/main.tf` (`--lane` on a worker the tree has) | a second process of the worker on the lane: in `dev.sh`, `serve --lane <lane>` with a metrics port of its own (`<NAME>_METRICS_PORT`); a second service of its image, and a second instance of its service module beside its own, each with `<NAME>_WORKER_LANE` set to the lane |

## Procedure

1. Name the kind in `<Namespace>Kind`, give its payload a shape on the
   `OmBase` base, and declare its spec in `<NAMESPACE>_KINDS` with the
   permission a producer needs to ask for it. `WorkKind`, `WORK_KINDS`,
   and the work namespace's files are the scaffold's, and a kind never
   joins them: the root's registry takes the namespace's tuple below
   its marker, and refuses a name the scaffold already registers. The
   handler's `REQUIRES` names the permissions its calls take, and the
   test that holds the two to each other fails until they agree: nobody
   reaches through the queue what they could not do directly. The
   worker refuses a handler for a kind the root does not register, and
   its test fails while a registered kind has no handler.
2. The handler runs under the context the claim returned, and never
   builds one. Handling an item twice changes nothing. A write to the
   record the item advances is a compare-and-set on the record's
   version, since the fences guard the queue row and not the record. An
   effect the handler must make happen rides an outbox row or a work
   item, never a topic alone.
3. A long-running kind is an `OrchestrationKindSpec` in its namespace's
   `kinds.py`, registered in `ORCHESTRATION_KINDS` below the marker, whose
   step is mapped by its name in `build_loop`, not a handler that loops; it parks or fails as Long-Running
   Orchestrations states. A lane is a work kind's, and every orchestration
   kind's steps ride `WorkKind.ORCHESTRATION`, so `--lane` gives no one
   orchestration kind a lane of its own. A long-running kind that needs
   one is a long-held work kind whose lease its worker renews; one that
   must stay an orchestration runs on the orchestration lane, and the
   output says so.
4. A producer never enqueues from a manager. Its write carries the
   work row in the same storage call, and the relay enqueues the item
   under the row's id.
5. With `--lane`, the kind's lane is `lane=` on its spec, never an
   edit of the relay. A replica of a worker the tree has takes the lane
   from its deployment, spelled as the spec spells it. A worker of the
   kind's own reads its lane off the spec in the root's registry
   (`KINDS.get(<Namespace>Kind.<KIND>)`, refused at boot when it answers
   None), never a copy of the name, so the relay and the worker cannot
   disagree. Its lane setting is a field of the worker's own name
   (`<worker>_lane`, so `<NAME>_<WORKER>_LANE`), never `worker_lane`:
   `.env.example` sets `<NAME>_WORKER_LANE=default`, and `scripts/dev.sh`
   exports it to every process it starts. The field defaults to `None`,
   its line in `.env.example` stays commented, its settings test lists
   it as one the cloud leaves at its default, and the loop takes the
   spec's lane while it is unset.
6. A new worker sets its capacity against the pool it opens for the
   roles it touches (`<NAME>_DATABASE_POOL_SIZE`), never apart from it.
   Add its member and run `uv sync` before the fast gate.

## Output

As `../_shared/scaffold-conventions.md` states.
