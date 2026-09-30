# grok.com

- URL: [redacted]
- Model: Expert
- Effort: none
- Sent: 2026-09-30T14:32:39Z
- Finished: 2026-09-30T14:36:19Z (the first of two polls whose answer length and last line matched, polls 2 and 3; "Enter voice mode" beside the composer confirmed it)
- Status: ok
- Score: 80/100
- Read: `d98c218` (Release 0.45.0 on `main`)
- Polls: 3
- Note: Expert stayed checked and no plan page opened; a "Meet Grok Bot" banner under the composer was left alone, neither "Dismiss" nor "Download" clicked

## Answer

Score: 80/100

## Strengths
- The prescribed model is coherent: one OM as source of truth, frozen entities, context-as-evidence, downward calls, tenant predicates plus RLS as a second fence, outbox-backed writes, workers over in-process jobs.
- Rule taxonomy (`core` / `default` / `optional` / `style`) plus ADR-recorded deviations is a real architectural protocol, not just prose preference.
- Checkability is designed as a ladder: story in `architecture.md`, 259 lenses as restatements, `arch-check` for what AST can decide, contract tests for isolation and atomicity.
- The checker is honestly scoped: parse-only, no import of the target tree, `full` vs `partial` coverage, exceptions require an existing ADR, local rules isolated and optional.
- The scaffold is a running monorepo rather than a sketch, with dual storage impls, four DB roles, expand-then-contract migrations, and isolation suites that include a negative control.
- Named technologies are declared as defaults with an explicit substitution rule; non-goals (multi-region, SLOs, threat models, tenant offboarding) are written down instead of implied.
- The repo’s own design matches its doctrine: generated review skills, TOC and lens format gates, version pinning, and a `make check` surface that treats the guideline as code.

## Weaknesses
- This is a coupled platform, not a portable architecture: layout, mixin inventory, package names, AWS/Postgres/FastAPI/React defaults, and lens text all assume the same monorepo shape.
- The ~80k-line scaffold plus Terraform and 47 ADRs is a second product living inside a guidelines repo; advancing adopters by merging a renamed tree is a fragile evolution model.
- Incremental adoption for an existing codebase is thin: “add the checker and write ADRs” does not get you the architecture, only a linter pointed at a foreign layout.
- Communication of the system is one 2,470-line prose document with no context or layer diagrams, so the dependency graph exists mainly in checker heuristics.
- A large AST rule set can produce cargo-cult compliance: syntactic proxies for tenancy, operation shape, and immutability are not the properties themselves.
- House style leaks into architecture (mixin field lists, 17-character project names, prescribed client stack, “apps are dumb”), which inflates the core invariant set.
- The published contract is still pre-1.0 and moving quickly; pinning via git subdirectory rather than a versioned package makes the checker a distribution afterthought.
- Production architecture that most multi-tenant systems eventually need (DR beyond role restore, multi-region, threat model, tenant export) is deferred, so the “deployment at the edge” story stops before the hard edge.

## What I would change
- Split a small `om-core` / contracts library from the full scaffold so products depend on shapes instead of merging an 80k-line tree.
- Separate `core` lenses from `style` lenses in the default CI gate so substitutions are not punished as architecture breaks.
- Add two diagrams to `architecture.md`: runtime context (apps → gateway → services → workers → roles) and allowed import graph.
- Publish `arch-check` to PyPI with a rule-compatibility policy and a machine-readable catalog of `full`/`partial` coverage.
- Write a strangler path that enforces only import direction, context-first signatures, and tenant-filtered storage before requiring the full monorepo.
- Replace scaffold upgrade-by-merge with versioned core packages plus an explicit adapter layer for product code.
- Record rejected alternatives in this repo’s own ADRs (why uuidv7, why four DB roles, why RLS as fence two) so the guideline is a decision log, not only a present-tense rulebook.
- Cut or demote frontend-stack rules from the architecture core; keep them as an optional app profile.

## Method
- Cloned `https://github.com/baristaze/swe_guidelines` at `d98c218` (Release 0.45.0 on `main`), counted 1,347 files.
- Read `architecture.md` (2,470 lines) end-to-end in sections, plus `README.md`, `docs/adopting.md`, `CONTRIBUTING.md`, `lenses/README.md` and `lenses/om.md`, `checkers/README.md`.
- Inspected checker design (`registry.py`, `imports.py`, start of `om.py`): 107 `@rule` registrations, ~11k LOC under `checkers/`.
- Surveyed `scaffold/acme_root` layout (om/infra/services/workers/apps/ops/deployment), 9 Python packages, 91 `.tf` files, 47 ADRs, ~80k Python LOC in the scaffold.
- Used repo tests and Makefile gates as evidence of how the system checks itself; did not run `make check` or deploy the scaffold.