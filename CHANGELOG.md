# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.32.0 (2026-09-22)

A skill keeps its spine and names its detail. A skill body is loaded in
full every time the skill runs, so it carries the invariants a run must
never miss and the procedure that reaches them, and the long per-step
material moves into a reference file the step that needs it names. The
three scaffolds with the longest bodies are split that way, and
`check_skills.py` holds both ends. Minor: a rule is added.

### Added

- "Operations, Operational Skills": a skill keeps its spine and names
  its detail. Its body carries the input, the procedure as an ordered
  list, the output, and every invariant that must never be missed,
  stated inline and short, because a referenced file is a promise and
  an inlined line is a guarantee. Long reference material moves into a
  file under the skill's own folder, named by the step that reads it,
  and that step says to read it before it runs. A reference file is
  read when its step runs, and not before.
- `scripts/check_skills.py` holds the rule at both ends. Every Markdown
  file under a skill's folder other than its `SKILL.md` is reference
  material, and a numbered step of that skill's Procedure names it as
  `${CLAUDE_SKILL_DIR}/<path>`; a file no step names is an orphan and
  an error, because nothing opens it. A `${CLAUDE_SKILL_DIR}` reference
  inside a reference file resolves from the skill's folder, the way the
  body's does. A skill body stays under 3,000 words, and the failure
  names the fix: move the long per-step material into the file a step
  reads.

### Changed

- The three scaffold skills with the longest bodies keep their spine
  inline and name their detail. `arch-scaffold-new` goes from 10,469
  words to 1,838, `arch-scaffold-service` from 4,931 to 1,504, and
  `arch-scaffold-worker` from 3,678 to 1,488. The file-by-file lists
  live under each skill's `references/`, one file per step that reads
  it. What stays inline is the handful of lines a run must never miss:
  the pinned guideline release, the three database logins, the policy
  every migration creates, no secret value in Terraform state, the
  gateway written once, the idempotency key on every creating route,
  admission failing closed, one holder per claim, the dead letter at
  `max_attempts`, the durable effect that never rides a topic alone,
  liveness from the in-memory beat, and capacity set against the pool.
- A reference file's own cited sections are read at the step that reads
  the file, and the skill lists them too, so the top of a skill is the
  whole of what a run reads. `arch-scaffold-new` names Cache, Buckets,
  Topics, Queues, Secrets, and Idempotency under "Infrastructure", and
  gains "Namespaces as Swimlanes", "The Business Layer", the
  "Operations" subsections, and "Documentation as Code, A README at
  Every Level".
- A scaffold skill's Created section is what the skill creates: the
  reference files that hold the file-by-file lists, or a table when the
  list is short enough to stay inline. Where a table cell repeated an
  invariant the scaffold conventions already state, the repeat is cut
  and the conventions are named instead: the logins that are no
  superuser, the policy shape a migration creates, the data-migration
  shape, the operational skills' credentials, and which routes declare
  the idempotency key.

### Fixed

- `arch-scaffold-new` with `--no-worker` names the module that carries
  the sweep in the API's lifespan and the gauge it publishes,
  `<root>_outbox_lag_seconds`, which the operational skills query by
  that name.
- The Changed table of `arch-scaffold-service` and of
  `arch-scaffold-worker` belongs to a numbered step that applies it,
  and `deployment/realtime-timeouts.json` is written in the step before
  the tests that assert against it.
- `arch-scaffold-service` writes the gateway only when it is the first
  service of the tree; a later service writes none.
- Every operational skill but create and nuke also takes `local`,
  reading the devx stand-ins.
