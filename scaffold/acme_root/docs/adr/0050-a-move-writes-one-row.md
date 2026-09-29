# ADR 0050: A move writes one row

**Status**: accepted (2026-10-12). Deviates from STO-05 until the
position is dropped: the triggers of migrations 202610120000 and
202610200000 live in the schema until then. The first contract step is
done (2026-10-20). `position` is out of the mapping and out of every
reader. The wire still sends it, optional, as the rank's float, since a
client of the release before requires it. The column stays not null:
the release before reads it as a number, so the trigger of 202610200000
gives every row this release writes its rank's float. The trigger of
202610120000 stays: the release before's own integration tests insert
tasks by the position alone, and fail without it. The position's
index goes now, since nothing reads it. The second step drops the
column, both triggers and their functions, and the field.

## Context

An open task's place in the list was a float, `position`. A move took
the midpoint between the anchor and the task after it. Halving a gap
runs out at float precision after about fifty moves into the same gap.
Then the manager renumbered every open task of the org in one
conditional write, conditioned on every row's version, and moved the
version of every task whose position changed.

That write has two costs. A person editing a task nobody moved gets a
412, because the renumber moved its version. And the renumber itself
fails with 412 when anyone changed any open task of the org since it
read the list. The traffic run shows it. Sessions own their tasks, so
no two sessions write the same task, and a run of `make traffic` with
the regular profile for 60 seconds, against a list grown by earlier
runs, still met 34 conflicts: 16 on delete, 6 on edit, 12 on move. The
move's p95 was 1076 ms. Every one came from a renumber.

The guideline's optimistic concurrency (Shape of an Operation) says the
manager's copy increments the version on every write, and the write
lands only while the stored row carries the version the caller names.
[ADR 0009](0009-tasks-carry-a-version.md) holds tasks to it.

Two shapes fix the cost. One keeps the renumber and leaves the version
alone on rows whose only change is the position. The other gives a
move room that never runs out, so there is nothing to renumber.

## Decision

**A place is an exact decimal rank.** `Task.rank` is a Postgres
`numeric`, a Python `Decimal`, and a string on the wire. Between two
different ranks there is always another. `tasks.rules.spread` picks the
shortest one near the middle, at most one digit longer than the longer
of the two. A create takes the whole number below the top rank. An
import takes whole numbers after the bottom one. A bulk reopen takes
whole numbers above the top, the last one named on top. A move reads
the anchor and the smallest rank past it, the moved task aside, and
takes a rank between them.

**A move writes the moved task alone.** Its one conditional write names
the version the caller read, as every write does. No other task's row
is written, so no other version moves, and no edit of another task
meets a 412 because of a move.

**Two tasks may share a rank.** Two writers that read the same list at
once place two tasks on one rank. The list orders them by id, as it
always did, and the cursor is (rank, id). There is no rank between two
that tie, so a task moved after either of them goes after both.

**A rank that grows long is respaced by the sweep.** Moves into one and
the same gap add a digit every three moves or so. Past 24 digits after
the point (`RANK_SCALE_BOUND`, seventy-odd moves into one gap), the
sweep's respace chore, per tenant, finds the rank through a partial
index that holds only such ranks. It takes the run around it: every
task between the nearest ranks of at most 12 digits, read at most 100
places each way. It gives the run short, evenly spread ranks in the
order it had, in one write conditioned on each task's version, and
each task it writes moves its version on and is announced as an edit.
A run written meanwhile is left for the next pass.

**The respace moves the version.** The version is the one signal every
client has that its copy is stale. The portal keeps a task it holds at
the same or a newer version, and places the next task it hears of by
the ranks it holds. A respace that left the version alone would leave
every open tab ordering against ranks the server no longer has, and a
task placed later would land in the wrong row. So a respace is a write
like any other, and the guideline's rule holds without a deviation.
Its cost is small: a person editing a task of the run in the instant
between their read and their write gets one 412, the answer any
concurrent edit gives. A run is only the tasks around one long rank,
at most 201, and it happens once per seventy-odd moves into one gap.

**Expand and contract.** Migration 202610120000 adds `rank`, fills it
from `position` digit for digit (a float's text is the shortest that
reads back as the same float), indexes it, and sets it not null. This
release writes both: the rank, and the position beside it as the rank's
float. The wire carries both: `rank`, and `position`, deprecated.

The release before inserts a task without a rank and moves one by its
position alone. It runs against this schema during the rollout, and
again after a fast rollback. A trigger gives such a row the rank its
position names: on an insert without a rank, and on an update that
changes the position and leaves the rank. A write of this release sets
the rank itself, so the trigger leaves it alone. The position this
release writes is the rank's float, so the release before orders the
list as this one does, up to the digits a float holds.

**The trigger is a deviation from STO-05, for one release.** STO-05
says no triggers and no database functions: if something happens, it
happens in the code. The release before is code that cannot be changed,
and it cannot write the rank. Without the trigger, every task it
creates after the migration has no rank, and every task it moves keeps
a rank that no longer matches its place. A rollback would leave the
next release reading a list out of order. The trigger does one thing,
copies one column into another, and goes with the position.

## Consequences

The renumber is gone, and with it `update_tasks` over a whole open
list. A move is one read of the anchor, one read of the place after
it, and one write. With this release, the same traffic run met 0
conflicts.

The contract follows
[ADR 0038](0038-a-dead-column-leaves-the-mapping-before-the-table.md),
in two releases. The first takes `position` off the wire and out of the
mapping, and lets the column be null, since its inserts no longer name
it. The second drops the column, its index, the trigger, and the
function, and the exception for STO-05 in `pyproject.toml` goes with
them.

A downgrade of 202610120000 drops the rank and keeps the position,
which this release keeps current, so the release before reads the list
as this one left it.

The CLI's `listen` says "moved a task" when a task's rank changed, so
a respace shows as the platform moving the tasks of the run. The order
it shows does not change.
