# ADR 0102: A migration chain is ordered by its parents, and a copy folds only its own

**Status**: accepted (2026-10-14)

## Context

Each role has one migration chain, and every tree built on the scaffold
starts from it. A copy takes the scaffold's migrations as its first
render holds them, and puts its own on their head. From then on its
chain is the one its databases applied: a scaffold migration a move
brings stays out of it, and the copy writes its own migration for that
change. A layer, a tree whose own scaffold builds on this one, applies
no database. It takes the scaffold's migrations as rendered on every
move and puts its own on their head, and the copies built on it take
both. A migration's minute stamp is its revision id (STO-23).

A stamp says when a migration was written, not where it sits. A base's
migration written after a layer's own sorts above them, yet it comes
before them on the layer's chain, since the layer's own stand on the
base's head. A gate that held the head to the highest stamp would turn
each such move into a rename of every own migration above the new head,
and each rename reaches every tree built on the layer as a conflict on
the renamed file.

A fold has the same reach. A copy or a layer that folds its whole chain
rewrites the files its base rendered, and its next move meets every
change the base makes to them as a conflict.

## Decision

- **The chain is ordered by its parents.** `chain` in
  `acme.om.storage.migrate` reads a role's order from each wrapper's
  `down_revision`, never from its stamp. It refuses a chain with more
  than one head or more than one first revision, and one that names a
  parent it does not hold; `head` is its last revision. The unit gate
  (`om/tests/unit/test_migrations_sql.py`) holds every role to it, and
  arch-check replays the `up` files in the same order.
- **No migration is renamed to move it.** A move of a layer's base
  re-points the layer's first own migration's parent onto the base's
  new head, and that parent is all it changes. Every migration keeps its
  name, the base's or the layer's own, released or not. A copy's move
  changes no migration of its chain: a scaffold migration the chain
  does not hold stays out.
- **A copy or a layer folds only what it owns.** `docs-compact
  --migrations` folds the revisions that the render on the tree's
  `scaffold` branch does not hold into one revision per role, under the
  head's revision id. Its parent is the last rendered revision the chain
  holds. Every rendered file stays as the render gave it. A tree with no
  `scaffold` branch owns every revision, and its fold holds the whole
  chain, with no parent.

## Consequences

- A migration's stamp may sort below its parent's. The order is in the
  wrappers, and `chain` prints it; a listing of the folder does not
  show it.
- A layer's next move meets no conflict on its migrations: the base's
  files arrive as rendered, and only the parent of the layer's first own
  migration moves. A tree built on the layer meets no renamed file.
- A copy or a layer tells what it owns by its `scaffold` branch. One
  without that branch folds its whole chain, and its next move meets
  every rendered file the fold removed.
- Two migrations of one role in the same minute still collide on the
  stamp, and the later one takes a suffix before it merges (STO-23).
