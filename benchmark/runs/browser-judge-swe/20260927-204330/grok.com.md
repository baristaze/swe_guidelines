# grok.com

- URL: [redacted]
- Model label: Fast
- Effort label: none
- Sent: 2026-09-27T20:47:29Z
- Finished: 2026-09-27T21:00:09Z (the first of two polls, 21:00:09Z and 21:02:10Z, whose answer text and last answer line matched)
- Status: smaller-mode
- Score: 89/100
- Note: Expert needs a SuperGrok plan

## Answer

Score: 89/100

## Strengths
- Coherent layered architecture with a single, pure domain OM (Pydantic, immutable, mixin-composed) as the unambiguous source of truth that every other layer projects from or depends on.
- Explicit separation of concerns (OM → business managers → storage → network/gateway → workers/apps) with directional call rules, injectability, and multiple implementations per interface by design.
- Rules are made mechanically checkable: 258 lenses in eight orthogonal groups, each with Principle/Source/Look-for/Violation/Severity, plus a deterministic AST-based static checker that decides the mechanical subset offline.
- Built-in agent tooling (parallel group reviews, scaffolds that emit the prescribed shape, deviation ADRs, dependency upgrades) turns the guideline into an enforceable, evolvable process rather than aspirational prose.
- Scalability and resilience are designed in as shape, not afterthoughts: scale-out by deployment units, explicit bounds/timeouts/breakers/idempotency/park-vs-fail, database roles, and outbox patterns so growth is largely operational.
- Clear audience, technology defaults, substitution path, and deviation recording keep the opinionated stance honest and adoptable without forking the text.
- Self-consistent meta-design: TOC generation, lens format enforcement, skill regeneration, CI gates, and a benchmark harness that measures the material against frontier models.

## Weaknesses
- Extreme volume and density (architecture.md alone is a long, tightly-coupled narrative) raise the cognitive and adoption cost; most teams will need the scaffolds or heavy agent assistance to stay compliant.
- Prescriptive technology and layout choices (specific mixin set, uuid-v7, table-backed work queue, monorepo layout, AWS/Terraform defaults) make non-matching stacks feel like constant deviations even when the underlying principles still apply.
- Heavy reliance on the Claude Code plugin/skills ecosystem for the highest-value enforcement and scaffolding; the static checker is portable but the full review/scaffold loop is not.
- Some low-severity conventions and exact declaration orders risk becoming noise that reviewers or the checker flag without clear payoff on every project.
- Reference implementation is external and deliberately minimal (a to-do app), so the end-to-end proof of the full shape under realistic multi-tenant load lives outside the repository itself.

## What I would change
- Extract a short “core principles + decision tree” front-matter (≤10 pages) that maps common team contexts to the subset of rules that must be adopted first, leaving the full narrative for later.
- Make the static checker the primary, always-on gate and demote agent reviews to advisory or delta-only; surface a machine-readable lens inventory so other tools can consume it.
- Soften or parameterize a handful of low-severity shape rules (exact mixin order, specific helper names) behind configuration so substitutions stay exceptions rather than the default path.
- Ship a minimal, self-contained skeleton (or stronger link + CI badge) of the reference implementation inside or beside the repo so the claimed end-to-end shape can be inspected without leaving the guideline.
- Add an explicit “exit criteria / partial adoption” section that lists which high-severity lenses remain non-negotiable when a team deliberately stays smaller or uses a different cloud/ORM.

## Method
- Read the repository README, architecture.md (full TOC + domain/OM, layers, resilience, non-goals, and closing sections), lenses/README.md + sample group (om.md), checkers/README.md, docs/adopting.md.
- Inspected top-level structure (lenses/, skills/, checkers/, agents/, benchmark/, scripts/, tests/, Makefile, pyproject.toml) and GitHub metadata via API and raw files on main.
- Sampled lens format, checker rule registration, and adoption/pointer mechanics; did not run the checker or scaffolds locally.
