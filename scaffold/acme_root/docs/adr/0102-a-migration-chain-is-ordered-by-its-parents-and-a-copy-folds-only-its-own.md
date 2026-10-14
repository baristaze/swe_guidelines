# ADR 0102: A migration chain is ordered by its parents, and a copy folds only its own

**Status**: accepted (2026-10-14)

## Context

Each role has one migration chain, and every tree built on the scaffold
shares it. A copy takes the scaffold's migrations as rendered and puts
its own on their head. A tree whose own scaffold builds on this one does
the same, and the copies built on it take both. A migration's minute
stamp is its revision id (STO-23).

A stamp says when a migration was written, not where it sits. A base's
migration written after a copy's own sorts above them, yet it comes
before them on the chain, since the copy's own stand on the base's head.
A gate that held the head to the highest stamp would turn each such
move into a rename of every own migration above the new head. In a tree
others are built on, each rename reaches every one of them as a
conflict on the renamed file.

A fold has the same reach. A copy that folds its whole chain rewrites
the files its base rendered, and its next move meets every change the
base makes to them as a conflict.

## Decision

- **The chain is ordered by its parents.** `chain` in
  `acme.om.storage.migrate` reads a role's order from each wrapper's
  `down_revision`, never from its stamp. It refuses a chain with more
  than one head or more than one first revision, and one that names a
  parent it does not hold; `head` is its last revision. The unit gate
  (`om/tests/unit/test_migrations_sql.py`) holds every role to it, and
  arch-check replays the `up` files in the same order.
- **No migration is renamed to move it.** A move of the base re-points
  the first own migration's parent onto the base's new head, and that
  parent is all it changes. Every migration keeps its name, the base's
  or the tree's own, released or not.
- **A copy folds only what it owns.** `docs-compact --migrations` folds
  the revisions that the render on the tree's `scaffold` branch does not
  hold into one revision per role, under the head's revision id, with
  the base's head as its parent. Every rendered file stays as the render
  gave it. A tree with no `scaffold` branch owns every revision, and its
  fold holds the whole chain, with no parent.

## Consequences

- A migration's stamp may sort below its parent's. The order is in the
  wrappers, and `chain` prints it; a listing of the folder does not
  show it.
- A copy's next move meets no conflict on its migrations: the base's
  files arrive as rendered, and only the parent of the copy's first own
  migration moves.
- A copy tells what it owns by its `scaffold` branch. One without that
  branch folds its whole chain, and its next move meets every rendered
  file the fold removed.
- Two migrations of one role in the same minute still collide on the
  stamp, and the later one takes a suffix before it merges (STO-23).
