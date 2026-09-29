# Events

The org's diary: the append-only stream behind every live push. This
is one of the seven kinds of thing [Acme is made of](../../../../README.md).

## The nouns

- **Event**: one line in the diary. It carries the org, its sequence
  number, its kind, the record that changed, a snapshot of that record,
  when it was produced, and who produced it: the actor, the request,
  and the app the request came from.
- **Kind**: `<namespace>.<entity>.<action>` for a change, such as
  `tasks.task.updated`, or an audit kind for something the platform
  records about itself, such as a job that failed for good.
- **Head**: the sequence number of the last event, zero before the
  first. A screen that opens the live channel starts here.
- **Floor**: the number of the last event the trim removed, zero before
  the first trim. Every event above it, up to the head, is kept.

## What can happen

- **Append.** A change appends its event through the outbox relay. An
  audit entry is appended by the part of the platform that has
  something to record, as itself.
- **Read after a number.** Everything after a sequence number, oldest
  first, so a screen that was away catches up. A number below the floor
  is refused as gone, with the floor and the head: the events after it
  are not all kept.
- **Read the head.**
- **Trim the oldest events.** With an event retention set, the sweep
  deletes, a batch at a time for every org at once, the events at the
  bottom of each org's stream that are older than it, and moves each
  org's floor to the last of its own. It stops at the first younger
  event. The worker keeps 90 days by default; with no retention set,
  every event is kept.
- **Drop an expired tenant's stream.** Once a deleted org is past the
  retention, the sweep drops its events and its cursor, as every
  namespace drops that tenant's rows.

Nothing else. No update, and no other delete. An event about a user
carries no email and no display name, so erasing a person from the user
row erases them.

## The rules

- **The sequence is gapless per org.** Each org has a cursor holding
  its head. An append takes the next numbers under the cursor's lock,
  one per new event, in the statement that writes them, and holds the
  lock to its commit. Two appends to one org queue on the cursor and
  commit in the order of their numbers, so a reader never sees a number
  before the ones below it. An append that rolls back returns its
  numbers with it. The number is never computed by looking for the
  largest one and hoping.
- **Events appended together are numbered together.** An import's
  hundred tasks are appended in one call and take a hundred numbers in a
  row, in the file's order.
- **Appending twice appends once.** An append is idempotent on the
  event's id. Relaying an outbox row twice consumes one number and
  leaves one line.
- **A number is spent only by the org's own write.** An id another org
  owns is refused before the number is taken.
- **Provenance is stamped, never claimed.** The actor, the request,
  and the app on an audit entry come from the context the caller
  holds, not from the event the caller sends.
- **A push is a hint; the diary is the truth.** A screen keeps the last
  contiguous number it applied. A push that arrives ahead of it is not
  a skip: it is a replay of the diary from that number.
- **The floor moves with the trim.** The trim takes the cursor's lock,
  deletes, and moves the floor in one transaction. So a reader never
  sees events gone above a floor that says they are kept. A cursor an
  append holds is left for the next trim; the trim never waits on it.
- **A screen behind the floor reads afresh.** Its number is below the
  floor, so no replay can close the gap. It reads what it shows again,
  and goes on from the head the refusal named.
