# grok.com

- URL: [redacted]
- Model: Fast
- Effort: none
- Sent: 2026-09-29T21:31:56Z
- Finished: 2026-09-29T21:48:19Z (the first of two polls whose answer length and last line matched, polls 9 and 10; "Enter voice mode" beside the composer confirmed it)
- Status: smaller-mode
- Score: 88/100
- Read: not stated
- Polls: 10
- Note: Expert needs a SuperGrok plan: clicking it opened the plan page (URL ending #subscribe), which was closed with its ×, and the session ran on Fast, the next smaller size's mode; the answer revealed a few words per poll while its tab was in front

## Answer

Score: 88/100

## Strengths
- Clear, layered architecture with an explicit core of invariants (OM as single source of truth, frozen entities, uuid_v7, TenantContext stages, storage roles, gateway as sole edge, work-queue workers) that scale by deployment rather than rewrite.
- Exceptional separation of concerns: narrative guideline stays readable; 258 checkable lenses, a pure-stdlib static checker, and generated agent skills carry the enforceable detail without polluting the story.
- Runnable, domain-agnostic scaffold that a new project copies, plus adoption path, deviation ADRs, and technology-substitution mechanism that keeps the shapes intact.
- Strong multi-tenancy, authorization, and isolation model (context stages as evidence, database roles as fences, every write authorize-verify-copy-write in one storage call).
- Agent-first design that still works for humans: skills follow a public standard, reviews never edit, scaffolds never commit, and the checker runs in any CI without an LLM.
- Explicit limits, scalability/resilience-by-design sections, and a reference implementation pointer that records real deviations.

## Weaknesses
- Heavy opinionation and volume (architecture.md alone is large; 258 lenses) raise the cognitive and process cost for teams that do not fully buy the multi-tenant Python monorepo shape or the agent-driven workflow.
- Named technology defaults (Postgres, FastAPI, SQLAlchemy, AWS/ECS, React/Vite, etc.) plus monorepo layout make adoption outside that stack a non-trivial substitution exercise even though the mechanism exists.
- Assumes agents write most code; several rules (second impl of every interface, pure memory roots for every contract) become pure tax without that assumption.
- Static checker covers only the mechanical subset; judgment-heavy lenses still rely on LLM review quality and cost.
- Pre-1.0 versioning and the tight coupling of guideline ↔ lenses ↔ skills ↔ scaffold mean a rule change can cascade across many generated artifacts.

## What I would change
- Add a short “when not to use this” decision tree and a minimal viable subset of the core invariants so smaller or non-multi-tenant systems can adopt without the full scaffold.
- Extract a machine-readable core-invariants list (JSON/YAML) so tools and external linters can depend on a stable surface instead of parsing markdown.
- Provide one more concrete reference implementation in a different stack (or at least a documented partial port) to prove the shapes travel beyond the named defaults.
- Tighten the lens severity distribution and publish a “must-pass-on-CI” subset so projects can start with a lighter gate.
- Move the technology defaults into a clearly versioned “profile” so a project can declare “aws-postgres-fastapi-v1” once and keep the rest of the guideline technology-agnostic.

## Method
- Read full README.md, architecture.md (entire ~2.4k-line document including Core, layers, deployment, limits), lenses/README.md + sample om.md, checkers/README.md, CONTRIBUTING.md, pyproject.toml, scaffold layout, and GitHub repo root/API contents.
- Examined structure of skills, agents, benchmark, docs/adopting, and release/versioning conventions; sampled lens format and checker design.
- Depth: full narrative + tooling design; did not execute the scaffold or run the checker against a live project.
