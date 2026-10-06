# ADR 0084: The release before runs its own suite on a branch's schema

**Status**: accepted (2026-10-06)

## Context

A migration runs before the services roll, so the release before serves
the new schema for the minutes of the roll. The fast rollback returns to
it and runs no migration, so it serves that schema again after a
rollback (ADR 0008, ADR 0024). Every migration is meant to be compatible
with the release before it, by expand and contract. A contract is where
that is easiest to get wrong: a drop the release before still writes
fails its inserts, and a drop cannot be undone.

ADR 0038 names the proof: the release before runs its integration tests
against the new schema. Made by hand, it is a step an author has to
remember.

Two releases come before a branch. Staging runs `main`, so the merge
base is the one beside it on staging. Production runs the tip of
`release`, which is also the fast rollback's target.

## Decision

- **CI's `release-before` job runs the proof on every pull request.**
  `scripts/release_before.sh` migrates the compose stack, each role on
  its own instance (ADR 0083), to the branch's head. Then it runs the
  integration suite of the merge base, and of the tip of
  `origin/release` when that branch exists and differs, against that
  schema. `make release-before` runs the same locally.
- **It always reports.** The main ruleset requires it, and a path filter
  never reports on a pull request it filters out. So the job always
  runs, and a branch that changes nothing under `om/migrations` ends
  green before it touches a database.
- **The release before's records are stamped, not migrated.** Its suite
  migrates to its own head, and its chains hold no revision of the
  branch. So `migrate stamp --all --heads-of <checkout>` writes each
  role's version record as the head that checkout's chain holds, and
  applies nothing. On the way out the script stamps the records back to
  the branch's heads. A stamp refuses any database that is not local.
- **Every chain `migrate upgrade --all` runs is stamped the same way.**
  The script names no role and no runner. A chain the migrate command
  adds is stamped by that command, and the job does not change.
- **What the release before cannot pass by design is named, and
  reviewed.** Its own `om/tests/integration/test_migrations.py` holds
  its chain to its ORM, so it fails on any newer schema, and it is left
  out. Any other test goes in `scripts/release_before_deselect.txt`,
  one node id per line under a comment that says why. It comes with the
  branch whose migration causes it.

## Consequences

- A drop the release before still maps fails the pull request that
  makes it, and its output names the statement that failed.
- A pull request that changes a migration pays one or two runs of the
  integration suite. One that changes none pays a checkout.
- A test the release before cannot pass stops the merge until the
  branch names it in the deselect file, where a reviewer reads it. A
  line goes once both releases before are past that branch.
- The job empties every table of the stack it runs on, as
  `make test-integration` does.
