# Outbox rows

The note written beside every change, from which the event, the push,
and any follow-up work come. This is one of the kinds of thing
[Acme is made of](../../../../README.md).

## What it holds

- **Outbox row**: the org, the kind, the id of the record it is about,
  a payload of ids, and the provenance of the write (the actor, the
  request, its trace context, and the app). It records when it was
  relayed, the attempts the sweep spent, when the next is due, its last
  error, and whether it failed for good.
- **Kind** is the row's destination. `<namespace>.<entity>.<action>`
  announces a change and becomes an event and a push. `work.<kind>`
  asks for a job and becomes a work item.

## What can happen

- **Written with the change.** A write hands its rows to storage with
  the change, and both land in one commit.
- **Relayed after the answer.** In the API, a request's rows are
  relayed once its answer is sent, under its request id and trace. A
  worker relays at once.
- **Relayed by the sweep.** What the request path left is claimed by
  the sweep once it is older than a grace period, a batch at a time,
  with a delay that doubles per attempt. A row whose attempts are spent
  fails for good, and its org's stream records `outbox.row.failed`.
- **Purged.** Done and failed rows go after eight days.
- **Watched.** Each sweep reads the age of the oldest pending row and
  the count of rows failed in the last fifteen minutes, and an alarm
  fires on either.

## The rules

- **Same commit or nothing.** A row lands with its change, or neither
  does.
- **Ids, never values.** A payload names records and counters, never a
  field a person's erasure has to find.
- **Relaying twice is harmless.** The event is appended once and the
  work item enqueued once, both keyed on the row's id.
- **Done means someone was told.** A change's row is done once the bus
  took its push; a dropped push leaves it pending for the sweep. A
  request for work is done once its item is queued
  ([ADR 0062](../../../../../docs/adr/0062-a-dropped-publish-leaves-its-outbox-row-pending.md)).
- **A failed relay never fails the request.** The row is durable, and
  the sweep relays it again.
- **The retention outlives the backup,** so a restore to an earlier
  point is reconciled by relaying again.
- **The row carries its own org.** The relay runs with no principal.

## How another namespace composes it

A namespace builds its rows with `outbox_row(ctx, kind, target_id,
payload)` (or `versioned_row` for a record that carries a version) and
passes them as `outbox_rows` to its own storage write, which lands them
in the same statement as the change. A write that also starts work adds
a row of kind `work_row_kind(kind)` beside it. The namespace never
publishes or enqueues itself.
