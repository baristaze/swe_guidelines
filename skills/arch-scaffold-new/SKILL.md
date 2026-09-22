---
name: arch-scaffold-new
description: "Bootstrap a whole new system in the guideline's shape into an empty folder: the monorepo skeleton, the first API, a worker, a portal, deployment, CI, then the first namespace and entity."
allowed-tools: Read, Grep, Glob, Write, Edit, Agent, Bash(make setup), Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make seed), Bash(make test-integration), Bash(make openapi), Bash(make devx-up), Bash(make test-telemetry), Bash(make traffic PROFILE=light DURATION=30), Bash(uv sync:*), Bash(uv run:*), Bash(pnpm install:*), Bash(pnpm run:*), Bash(pnpm --filter:*), Bash(git init:*), Bash(git status:*), Bash(git rev-parse:*), Bash(python3:*), Bash(git diff:*), Bash(git log:*), Bash(git merge-base:*), Bash(git symbolic-ref:*)
---

# arch-scaffold-new

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Naming
Entities, OpContext (Stages, Scopes, The Operator Context), The Storage
Layer (Namespace Shape, Storage Root, Defining ORM Classes,
Translation, A Storage Impl, Database Roles, The Second Fence,
Migrations), Infrastructure (InfraInterface Root), The Network Layer
(The Gateway; Auth: the Gateway Verifies, the Tenancy Domain Owns;
Realtime at the Edge), Deployment (Cloud: AWS, Infrastructure as Code,
Local: Docker Compose, Twins for External Services, What a Process
Refuses), Operations, Monorepo Folder Structure (Layout Conventions),
Documentation as Code, Telemetry, Cross-Cutting Conventions
(Exceptions, Configuration, Records of Decisions, Tests), Technology
Choices and How to Override Them (Versions, Overriding a Choice).

## Input

`<target-dir> <root-package> [--first <namespace> <Entity> [field:type ...]] [--codeowners <owner,...>] [--no-portal] [--no-worker]`

Example: `./acme acme --first inventory Warehouse address:str`. Both
positional arguments are required; ask for them when missing.
`<target-dir>` must not exist, or must be empty, or be a fresh
repository holding nothing but `.git`, `README.md`, `LICENSE`, and
`.gitignore` (the shape a hosting service creates); refuse otherwise.
In the fresh-repository case `README.md` and `.gitignore` are replaced,
`LICENSE` is kept, and the `git init` of step 1 is skipped. Those two
replacements are the one exception to the collision rule of the
conventions; any other path that exists is a collision. Refuse when a `.git`
directory exists in a parent of `<target-dir>` (`git rev-parse
--show-toplevel` from it names one), because `git init` never runs
inside an existing repository.
`<root-package>` must not shadow a standard-library module. In this
skill `<root>` is `<root-package>`, and `<root-slug>` its kebab-case
slug, as the conventions' Naming derives it (`acme_corp` gives
`acme-corp`). `--codeowners` names the owners `.github/CODEOWNERS`
lists, the repository's owner by default (the account or organization
in the `origin` URL of `.git/config`; ask when there is none).

## Created

Everything the references below list is under `<target-dir>/`. Those
lists are long, so each one lives beside this file and is read by the
step that names it, when that step runs and not before.

| Reference                                          | Holds                                                                                                                                                                                                                   | Read by |
|----------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|
| `${CLAUDE_SKILL_DIR}/references/skeleton.md`       | the workspace and tool config, the `Makefile`, the README, the docs, the two ADRs and the runbooks, the local compose stack, the Terraform modules and roots with the environments' file, the two cloud scripts, the six workflows, and the nine operational skills | step 1  |
| `${CLAUDE_SKILL_DIR}/references/object-model.md`   | the OM distribution under `om/`: the base module, the context stages and scopes, the exceptions, the storage layer with its roles, tables, translation, impls, and migrations, and the `tenancy`, `events`, `audit`, `outbox`, and `idempotency` namespaces with their tests | step 1  |
| `${CLAUDE_SKILL_DIR}/references/infrastructure.md` | the infra distribution under `infra/`: the cache, buckets, topics, queues, and secrets capabilities, observability and the trust store, and the configured and local roots                                                   | step 1  |
| `${CLAUDE_SKILL_DIR}/references/ops-package.md`    | `clients/python/`, the Python client generated from the API's document, and `ops/`, the `<root>-ops` member with the traffic generator, the stress runner, the signals interface, and the telemetry round trip              | step 3  |

A reference file is detail. These are the lines a run must never miss,
so they stay here:

- The guideline release is pinned once. `specs/architecture.md` names
  the tag the conventions found before writing, and the `Makefile`'s
  `arch-check` runs that same tag.
- Three logins reach the database, as the conventions state. The local
  compose file's init script creates them under the names every
  environment uses, `<root>_migration`, `<root>_runtime`, and
  `<root>_system`, since a policy spells the system login's name.
- Every table declares its role and its tenancy scope, and the
  migration that creates the table creates its policy, as the
  conventions state. A chain's first migration also creates its role's
  schema and grants the runtime and system logins on it.
- No secret value in Terraform state or a plan. Every password comes
  from an ephemeral generator through a write-only attribute, and no
  URL secret is computed as an output.
- Every health check, the target group's and each task definition's
  own, is on `/healthz`, never `/readyz`.
- A durable effect never rides a topic alone. What must happen after a
  write rides an outbox row or a work item, and a topic, at most once,
  carries hints only (`ENTITY_CHANGED`, `WORK_AVAILABLE`), so a lost
  message delays an effect and never drops it.
- An event, an audit entry's payload of ids, an outbox row, and a
  socket frame carry ids only, never a personal field's value.
- Every settings knob is documented. A unit test holds every field of
  `StorageSettings`, of `InfraSettings`, and of each process's own
  settings to `.env.example` under its prefix.

## Changed

| File | Change |
|------|--------|
| (none) | The tree is new; every later step appends to the files the references name. |

## Procedure

1. `git init` in `<target-dir>`, nothing staged (skipped when the
   target was a fresh repository). It comes first so that every step
   after it, and every skill this one follows, lists its files from
   `git status`. Then write the skeleton, reading
   `${CLAUDE_SKILL_DIR}/references/skeleton.md` before it (leaving
   `clients/python/` and `ops/` with its `README.md` to step 3); then
   the OM distribution, reading
   `${CLAUDE_SKILL_DIR}/references/object-model.md` before it; then the
   infra distribution, reading
   `${CLAUDE_SKILL_DIR}/references/infrastructure.md` before it; then
   run `make setup`. The fast gate runs from
   step 2 on. The nine operational skills are part of the skeleton:
   copy each template under `${CLAUDE_SKILL_DIR}/../_shared/ops-skills/` to
   `.claude/skills/<name>/SKILL.md` with `acme` substituted, as the
   skeleton reference states, and change nothing else in them.
2. Read `${CLAUDE_SKILL_DIR}/../arch-scaffold-service/SKILL.md` and
   follow its Created, Changed, and Procedure with these arguments:
   `api --realtime --container` (omit `--realtime` with `--no-portal`).
   The operator plane's routes arrive with it.
3. Read `${CLAUDE_SKILL_DIR}/references/ops-package.md`, then
   `make openapi` and write `clients/python/` generated from the
   document it emitted, whether or not `--no-portal`; then write
   `ops/` and `ops/README.md` over that client, add both members to
   the workspace, and run `uv sync`. The ops package rides the client
   and the operator plane, so it is written after both exist.
4. Unless `--no-worker`, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-worker/SKILL.md` and follow
   it with `maintenance NOOP --container`: a worker whose only work is
   the maintenance sweep, ready for real kinds. With `--no-worker`,
   the sweep moves into the API process's lifespan, so outbox rows a
   crash left behind are still relayed: write
   `services/api/src/<root>/services/api/sweep.py`, the sweep of
   `arch-scaffold-worker`'s loop without the queue (the relay's
   `relay_pending(rctx, limit)` and `purge_done(rctx)`; the purges of
   idempotency markers, socket tickets, and sessions, through the
   idempotency manager's `purge_markers(rctx)` and the tenancy
   manager's `purge_socket_tickets(rctx)` and `purge_sessions(rctx)`,
   which step 1 wrote; the purge of soft-deleted rows, once an entity
   composes the mixin, per service context, each context built by
   the tenancy manager's `service_contexts(rctx)`; and the outbox lag
   gauge, which the outbox-lag alarm reads here as it reads the
   worker's), on a timer at
   `sweep_interval`, which this step adds to the API's settings and
   to `.env.example`, started in the lifespan
   after `start()` and cancelled before `close()`, every step
   idempotent and wrapped. Every API replica runs it, which is safe
   because every step is idempotent and each purge is bounded by a
   batch size.
5. Unless `--no-portal`, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-app/SKILL.md` and follow it
   with `portal --kind portal`, including its Terraform and deploy
   rows: the portal's bucket and distribution exist in every
   environment before this step is done. Its Python client row is
   skipped, since step 3 wrote the client.
6. With `--first`, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-namespace/SKILL.md` and follow
   it with `<namespace> <Entity> <field:type ...>`.
7. `make check` and `make openapi`, so the portal's generated types
   and the Python client carry the routes of step 6; then, when Docker
   is available, `make infra-up`, `make migrate`, `make migrate-check`,
   `make seed` twice (the second run changes nothing, and leaves
   `local.env` as the first wrote it), and `make test-integration`,
   only against the
   compose stack of step 1: refuse when any database URL `StorageSettings`
   resolves (the three shared URLs and every per-role URL, from the
   environment, `.env`, or the settings default) is not a local
   address.
8. When Docker is available, run the negative control of
   Cross-Cutting Conventions (Tests) once. Take the tenant predicate
   out of one query of a storage impl over Postgres (the first
   entity's list with `--first`, else a tenancy list). Run
   `make test-integration` with the table's policy in place: it stays
   green, the second fence holding. Turn the policy off for that
   table (`ALTER TABLE ... NO FORCE ROW LEVEL SECURITY` and `DISABLE
   ROW LEVEL SECURITY`, through `uv run` over the local migration
   login's URL, since only the owner alters a table), and run `make test-integration` again: it fails, and the
   failures name the cross-tenant case of that method beside the
   policy check. Put the predicate back, turn the policy on again the
   same way (`ENABLE` and `FORCE ROW LEVEL SECURITY`), and run
   `make test-integration` green. Record both runs in
   `docs/runbooks/tenant-isolation.md`: the query, the table, and
   what the suite reported each time. A run two that stays green is a
   defect of the suite: name it in the output and stop.
9. When Docker is available, `make devx-up`, then
   `make test-telemetry`: the round trip starts the API as a real
   process, drives one session, and reads the counter, the trace, the
   error event, and the log line back by request id through the
   `devx` twins. Then `make traffic PROFILE=light DURATION=30`, the
   thirty-second light run, the same one CI's integration job runs.
   Both are a wiring check of the edge, the
   client, the generator, and the signals, and never a stress test;
   a stress test has a scenario and a target, and is the platform
   developer's to run.
10. Before the review, sweep the tree for the four misses a fresh
    scaffold makes most, and fix each: a setting the Terraform root
    does not pass to the service, a mutating manager operation whose
    first line is not `ctx.require(...)` or `octx.require(...)`,
    leaving out the operations the conventions exempt (the request
    stage, the identity stage, and the outbox handoff), a socket route mounted
    outside the gateway, a route that writes a durable row (201 or 202)
    without the `Idempotency-Key` dependency. Then read
    `${CLAUDE_SKILL_DIR}/../arch-review-full/SKILL.md`
    and run it over the whole tree; the checker run and the git reads
    it takes are in this skill's tools for that step. Close every high
    finding and rerun `make check`; list the rest in the output for the
    person. A high
    finding on a fresh tree is a defect of this skill: name it in the
    output so it can be closed at the source.

Stop at the first step whose gate fails and report where it stopped.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states,
plus one line: the tree is uncommitted, and the first commit is the
user's.
