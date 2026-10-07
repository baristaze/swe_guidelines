# claude.ai

- URL: [redacted]
- Model: Opus 5.5
- Effort: High
- Sent: 2026-10-07T14:53:24Z
- Finished: 2026-10-07T15:01:39Z (the page text read "Claude finished the response" at poll 7; the time was read after that poll's waits)
- Status: ok
- Score: 74/100
- Read: tag v0.52.2, commit d493ebc
- Polls: 7
- Note: the new-chat composer opened holding an unsent draft left from another session, which was cleared with select-all and Delete before the prompt was typed; step 4's check on the new-chat page (find, the page text, and a hover of the composer's "Manual" button, whose tooltip reads "Claude asks before using new tools") found no line saying the product can act on the machine and no device of the person's; Opus 5.5 and High were clicked and the chip read "Opus 5.5 High"; right after the send and at each of the 7 polls, the screenshot and the page text showed no such line; the answer's Method says it read benchmark/runs/browser-judge-swe/README.md and that "The browser-benchmark page shows prior scores from other products; I saw them and did not use them", so it read the earlier scores; the copied answer opens with a line the product wrote before its tool steps, before the score line

## Answer

I'll clone the repository and read through it before scoring.

Score: 74/100

## Strengths
- The core invariants form one consistent system: a frozen domain model, ABC interfaces, tenancy carried in the context, managers that authorize and storage that fences, and calls that only flow downward. Each rule is justified rather than asserted.
- Multi-tenancy is defended in depth: an explicit `org_id` predicate, forced row-level security on transaction-local settings, three non-superuser logins without `BYPASSRLS`, and tests that read `pg_policies` back against the scope map.
- The reliability patterns are correct and actually implemented. Writes use a transactional outbox in one storage call, the work queue claims with `SKIP LOCKED` under a lease and a fencing token, ids come from `derived_id` so a rerun finds its own record, and the gateway's idempotency replays a refusal but releases a 5xx.
- Expand-then-contract is enforced by CI, not just stated: the release before runs its integration suite on the branch's schema.
- The scaffold is a working system, not a sketch: about 83k lines of Python, Terraform with native tests, a compose stack with one Postgres per role, about 99.9% of functions annotated, a test-to-source ratio near 1.15:1, and shared contract suites that run against both the Postgres and memory impls in all seven namespaces.
- The tag scheme (`core`/`default`/`optional`/`style`/untagged plus ADR deviations) gives adopters a precise vocabulary for what may bend and how.
- The checker's suppression design is good: ignores and exceptions must cite an existing ADR, stale ones become findings, and globs that match nothing exit 2 instead of passing silently.
- Supply-chain and CI posture is strong: actions pinned by SHA, read-only default permissions, a human-approval gate on releases, and benchmark secrets isolated in a reviewer-gated environment.
- Review skills are read-only by construction, run the checker with `--no-local`, and require four explicit decision states with file evidence for a high-severity pass.

## Weaknesses
- The weight does not fit the stated audience. The guideline is written "for a small team: one process, one database", yet it mandates four database roles, three logins, row-level security, an outbox relay, twin storage impls, 260 lenses and about 50 ADRs before the first feature.
- The governing material is too large to hold in one head: 2.5k lines of guideline, 6.8k lines of lenses, and about 50k lines of tooling and tests around it. Adoption cost lands mostly on comprehension, not code.
- Release churn is high: 62 tags in 23 days. That undermines "pin a release", and every adopter ends up in a merge-based scaffold upgrade treadmill.
- `arch-check` decides 0 of 260 lenses fully (106 partial). It keys on scaffold naming suffixes and paths, so it is a scaffold-conformance test rather than an architecture linter, and its accuracy outside the scaffold is unmeasured.
- Validation is self-referential. The benchmark uses rubrics, a reference implementation and lens text written by the same author, n≤3 runs, and a same-family judge. The browser "benchmark" records chat products scoring the repo itself, inside the repo they read.
- Decision history is erased by policy. `check_leaks.py` refuses an ADR "Alternatives" section and words like "deprecated" and "previously", so ADRs lose the rejected options that make them useful.
- Vendor choices are written as rules. `Cloud: AWS` and `Infrastructure as Code` (Terraform) are untagged, so departing needs a deviation ADR, while only 5 sections are `default`. The delivery lenses also hard-code React, TanStack and Zustand, and DEL-24 requires this repo's own checker.
- The persistence funnel relies on private APIs. It subclasses `asyncpg.Connection.execute` and calls SQLAlchemy's private `_start_transaction` to save one round trip. A test guards it, but this sits next to a skill that upgrades every dependency to latest.
- The memory twin doubles storage code (tenancy alone has 1,132 + 970 + 2,466 contract lines) and cannot model RLS, `SKIP LOCKED` or row locks. Contract parity is weakest exactly where the guarantees matter most.
- The prose is dense and idiosyncratic ("Every write authorizes, verifies, copies, and writes"), with exceptions stacked inside rules, which slows human readers who are named as a primary audience.
- There is no evidence from real use: each role has one folded initial migration, and the stated reference implementation is a to-do app. API deprecation, SLOs and supply-chain rules are explicitly out of scope despite a contract-first stance.
- The meta-tooling is partly ceremony. `check_runs.py` (626 lines plus 879 of tests) polices hand-written benchmark README rows, and the vocabulary-leak list scrubs the origin domain's words.

## What I would change
- Publish a "minimal core" profile (one DB role, the predicate fence, outbox, the work queue) and move RLS, multi-role, the memory twin and the operator plane to `optional` with stated triggers.
- Retag `Cloud: AWS`, Terraform and the client stack as `default` so a substitution needs no ADR. Drop DEL-24's requirement to run this repo's tool.
- Allow `## Alternatives` and a superseded status in ADRs, and remove "deprecated", "previously" and "superseded" from the refused terms.
- Move to a slower release train (for example, monthly minors with patch-only backports) and publish a compatibility promise for lens ids and checker options.
- Pin adopters to the checker by commit SHA or a published wheel hash, not a mutable git tag.
- Make the checker decide a set of high-severity lenses fully (CTX-12, the storage funnel, the import direction), mark them `decides it`, and test them against at least one non-scaffold codebase with different naming.
- Replace the `ScopedConnection` and `_start_transaction` hack with a plain `SET LOCAL` statement after `BEGIN`, and measure the extra round trip before keeping the optimization.
- Delete the browser self-score benchmark from the repo, and rebuild the scenario benchmark with rubrics and seeded defects authored by someone independent, cross-family judges, and n≥5 runs.
- Add one worked expand-then-contract migration and one versioned (compare-and-set) entity to the scaffold, so the most failure-prone rules have a live example.
- Split `architecture.md` into a 2-page Core plus per-layer chapters, and rewrite rule-dense paragraphs as a rule followed by its rationale and exceptions.
- Add the explicitly omitted API deprecation and versioning section, since the OpenAPI document is declared the contract.

## Method
- Repository at tag v0.52.2, commit d493ebc, cloned read-only; I ran none of its scripts, tests or Makefile targets.
- Read `README.md` and `AGENTS.md` in full.
- Read `architecture.md` lines 1–1150 in full (How to Read This through Migrations), all headings, plus the Stack, Cloud, IaC, Technology Choices, Does Not Cover and Next sections, and counted its section tags.
- Read `scaffold/acme_root/om/src/acme/om/storage/impl/pg_base.py` lines 175–290, the scaffold `Makefile` checker pin, ADR 0008, `scripts/check_leaks.py` refused terms, and `benchmark/runs/browser-judge-swe/README.md`.
- Delegated two read-only deep audits:
  - The scaffold: layout and LOC; the media, work, outbox and storage base read in full; parts of tenancy and the API; Terraform; compose; tests.
  - The checker, scripts, lens samples (STO, ASY, DEL), benchmark harness and runs, four skills, and CI workflows.
- Spot-checked the delegated claims: the checker's 106 partial rules, the tag count, the ARCH_CHECK pin and the private-API use were confirmed. One claim was corrected: `If-Match` preconditions do exist in the gateway.
- The browser-benchmark page shows prior scores from other products; I saw them and did not use them.