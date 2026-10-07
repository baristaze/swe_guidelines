# grok.com

- URL: [redacted]
- Model: Expert
- Effort: none
- Sent: 2026-10-07T07:35:01Z
- Finished: 2026-10-07T07:38:16Z (the first of two polls whose answer length and last line matched, polls 1 and 2; "Enter voice mode" beside the composer confirmed it)
- Status: ok
- Score: 81/100
- Read: commit 3cd6665fca5698d308fadd8d1f34b690354f5838 on main (2026-10-06)
- Polls: 2
- Note: Expert stayed checked and no plan page opened; find was refused once on grok.com at step 4 and worked on the retry; the answer was already done at the first poll, so the finish time is that poll's time, not the send time plus the page's "Worked for 1m 27s"

## Answer

Score: 81/100
## Strengths
The spec is split on purpose: `architecture.md` states the story, lenses hold checkable detail, `arch-check` decides what an AST can decide, and skills keep the judgment calls.
Rule force is tagged (`core` / `default` / `optional` / `style`), and a departure is an ADR rather than a silent edit.
The dependency rule is clear: one frozen object-model library, layers depend downward through interfaces, infrastructure stays a capability.
Tenancy is two fences: application predicates plus row-level security, with operator context kept off the tenant path.
Database roles (`core`, `activity`, `queue`, `admin`) each have their own URL and migration chain, and cross-role effects go through an outbox instead of a second write.
Every storage and capability interface has a memory impl and a shared contract suite, so the shape runs without its technology.
The checker parses source and does not import the project, and a copy pins the guideline release it follows.
## Weaknesses
The stated audience is one process and one database with no rewrite later, but the copyable system is already a platform: gateway, workers, four local Postgres instances, Terraform, apps, and 80-plus ADRs.
The "object model" is a frozen Pydantic schema of mixins; authorization and rules live in managers, so the claimed center is a data contract, not a domain model.
Two hundred sixty lenses and about eleven thousand lines of checker rules freeze a house stack (FastAPI, SQLAlchemy, React, ECS) while the text says only shapes are law; a substitution stays expensive.
The reference kernel is not stable: the scaffold already records local decisions the guideline does not own, so a copy inherits a product history.
A multi-tenant system that claims to reach deployment still omits threat model, tenant offboarding, and multi-region recovery, by its own "does not cover" list.
Checker modules are large procedural walkers (`storage.py` 1348 lines, `async_.py` 1076), and the guideline itself moved to 0.52.0 within about three weeks of the repo's creation.
`agents-only` comments hide constraints from the human reader the same document says it serves.
## What I would change
Split the scaffold into a minimal kernel (object model, tenancy, roles, outbox, one service, one worker) and an optional platform pack for gateway, apps, and cloud.
Rename the center a schema, or put real invariants on the types, so "source of truth" matches where rules actually live.
Drive checker findings from lens ids with small visitors, and publish which lens ids a FastAPI, SQLAlchemy, or AWS substitution deletes.
Add optional sections, with triggers, for tenant export/offboarding and a threat model, since multi-tenancy is `core`.
Stop shipping benchmark run artifacts in the tree; keep the harness only.
Move agents-only constraints into a human-readable appendix so the spec has one audience.
## Method
Read commit `3cd6665fca5698d308fadd8d1f34b690354f5838` on `main` (2026-10-06), the tree of 1386 files, and the 0.52.0 changelog.
Read `README.md`, `AGENTS.md`, `architecture.md` (2513 lines: how to read, core, layers, interfaces, database roles, technology overrides, exclusions), and `lenses/README.md` plus the OM lens format.
Inspected checker layout (`checkers/src/arch_check`, ~11k lines of rules), scaffold top-level packages, ADR count, and lens/skill inventory; did not run `make check` or read every lens.
