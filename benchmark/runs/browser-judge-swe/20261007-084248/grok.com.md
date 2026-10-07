# grok.com

- URL: [redacted]
- Model: Expert
- Effort: none
- Sent: 2026-10-07T08:46:43Z
- Finished: 2026-10-07T08:49:25Z (the first of two polls whose answer length and last line matched, polls 1 and 2; "Enter voice mode" beside the composer confirmed it)
- Status: ok
- Score: 84/100
- Read: main at 54bc842b (release 0.52.1, 2026-10-07)
- Polls: 2
- Note: Expert stayed checked and no plan page opened; a "Grok Bot" link in the header ("Download Grok Bot") names no device and says nothing of acting on the machine, so it was not taken for step 4's line and was left alone

## Answer

Score: 84/100
## Strengths
The invariant set is explicit and ranked: `core` departures are a different architecture, `default` is a recorded substitution, and an untagged departure is an ADR rather than a silent fork.
The domain is the source of truth: frozen Pydantic entities, ids minted above storage as `uuid_v7`, and both the wire and the tables are projections that are not allowed to redefine the model.
Dependency direction is coherent and checked: network to managers to storage, calls only downward, infrastructure behind interfaces, constructors for process-lifetime dependencies, and `TenantContext` for ambient facts with no globals or thread locals.
Tenancy is a data fence, not a convention: managers authorize, every storage call takes `org_id`, and Postgres RLS on transaction-local settings is a second fence that fails closed, with a named system bypass.
Cross-role handoff is the transactional outbox: one atomic write of the row and its outbox rows, then an idempotent relay into `activity` or `queue`, so a role can move by URL without a code change.
Work and realtime are specified at the failure modes that usually get hand-waved: producer-set idempotency keys, lease plus claim token, at-least-once handlers, pushes that are also gapless records, and expand-then-contract migrations proven by running the previous release on the new schema.
The repository holds the architecture instead of describing it: a runnable monorepo scaffold, 260 lenses, and a standard-library AST checker whose rule ids are the lens ids, with import direction enforced and review skills generated from the lenses.
## Weaknesses
Cross-aggregate consistency is not a transaction: nothing crosses a database role, no transaction outlives a storage call, and the commerce path (reserve stock, then place the order) is a service composition plus a saga mention, so the partial-failure case the examples need is not a core pattern.
Enqueue authorization is too wide: the permission that enqueues a work kind covers every later call its handler makes, and the claim rebuilds a service principal, so a handler that grows silently widens that grant.
The packaging contradicts the layer story: business and storage ship as one `acme-om` wheel that depends on SQLAlchemy, asyncpg, and Alembic, so "a new engine changes only `impl/`" is false at the dependency boundary even though import rules hide tables from managers.
The opening promise is oversold: "the next step is a deployment change" fails at the gapless per-tenant `seq`, which the text itself names as the first change that is not a deployment change, and at any invariant that spans roles.
"An operational task is a skill" is listed with the architectural invariants, which ties the running system to an agent procedure and is not a property of the process model.
Last-writer-wins is the default, with `version` and compare-and-set opt-in, so concurrent edits of orders and inventory are safe only when someone remembers to opt in.
The system-scope bypass (`EMPTY_UUID` plus the system login) and the allowlist of tenant-less methods in `pyproject.toml` are a single sharp edge next to an otherwise fail-closed fence.
The ceremony is part of the architecture: a full second storage impl per interface, about 260 lenses (157 of them medium), and scaffold upgrades by merge are cheap only if an agent writes the code, which the document admits and a small team may not have.
## What I would change
Make cross-namespace effects a core pattern: one named saga or process-manager shape, with the irreversible step last and compensation per prior step, and show reserve-then-order on it instead of a service-impl composition.
Narrow worker authority to the operations that kind declares, and fail a handler call outside that set, instead of treating the enqueue permission as a blanket grant.
Split the wheel: a pure `om` package of types, rules, and manager interfaces, and a `storage` package that owns SQLAlchemy, migrations, and both impls.
Make `version` the default on any entity a person can edit, and keep last-writer-wins as an explicit deviation.
Move "an operational task is a skill" out of The Core and into Operations, tagged `default`.
Give the per-tenant event cursor an explicit split trigger in the core realtime section, not only in Scalability by Design, so the hotspot is a designed seam.
Keep the RLS bypass, but require each system-scope method to name the table policy it is allowed to see, rather than one login that passes every policy.
## Method
Read `main` at `54bc842b` (release 0.52.1, 2026-10-07), the README, `AGENTS.md`, `lenses/README.md`, and `docs/adopting.md`.
Read `architecture.md` (2512 lines) through The Core, layers, context, business operations, storage, roles, RLS, migrations, call direction, idempotency, realtime, workers, technology defaults, scalability, and the stated non-goals.
Surveyed the scaffold layout (`om`, `infra`, `services`, `workers`, `clients`, Terraform), the `acme-om` dependencies, lens counts and severities (260 lenses, 81 high), and the import rules in `checkers/src/arch_check/rules/imports.py`.