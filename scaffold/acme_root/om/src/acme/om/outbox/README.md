# Outbox rows

The note written beside every change, from which the event and the
push come. This is one of the seven kinds of thing
[Acme is made of](../../../../README.md).

## The nouns

- **Outbox row**: the org, the kind of change, the id of the record
  that changed, and the provenance of the write: the
  actor, the request, the request's trace context, and the app. It
  records when it was relayed, how many attempts the sweep has spent
  on it, when the next is due, its last error, and whether it failed
  for good.
- **Kind** is the row's destination. `<namespace>.<entity>.<action>`
  announces a change and becomes an event. `work.<kind>` asks for a
  job and becomes a work item.

## What can happen

- **Written with the change.** The rows that announce a write are
  handed over with the write itself, and both land in one commit.
  There is no second step anyone could forget.
- **Relayed at once.** A change appends its event and publishes a
  push; a request for work enqueues the item under the row's id and
  wakes the workers. Then every row the write landed is marked done at
  once, in one statement.
- **Kept when the bus drops the push.** A change is done once the bus
  took its push. When the bus refuses it, or its breaker is open, the
  row stays pending and the rows beside it are marked. The drop is
  counted, and the sweep sends the push again. A request for work is
  done once it is queued: the workers poll, so its wake-up is a hint.
- **After the answer, in the API.** A request's rows are relayed once
  its answer is sent, still under its request id and its trace. The
  answer never waits for the relay and carries nothing from it. The
  rows of one org are relayed together, however many writes the
  request made. A worker relays at once, in its own loop.
- **Relayed by the sweep.** Whatever the request path left behind is
  claimed by the maintenance sweep, oldest first, a hundred rows at a
  time and again while a batch comes back whole, one attempt a row,
  with a delay that doubles per attempt. Each org's rows are relayed
  together; when that fails, each is relayed alone, so only the row
  that fails keeps an error. A row whose push was dropped keeps one
  too, `the bus dropped the publish`. A row whose attempts
  are spent is failed for good, counted, and named by an event in the
  org's diary.
- **Purged.** Done and failed rows are erased after the retention,
  eight days by default.
- **Watched.** Each sweep reads how long ago the oldest row neither
  relayed nor failed landed, across every org, and an alarm fires past
  five minutes: the relay is stuck. A bus that drops every push trips
  it as a failing event store does. A row failed for good is no longer
  pending, so the sweep also counts the rows failed in the last fifteen
  minutes, and a second alarm fires on one.

## The rules

- **Same commit or nothing.** The row lands with the change it
  announces, or neither lands.
- **Ids, never values.** A row names the record that changed and
  carries no field of it, so the relay and the stream hold nothing a
  person's erasure has to find. A client that hears of a change reads
  the record.
- **Relaying twice is harmless.** The relay is idempotent on the row's
  id: the event is appended once and the work item is enqueued once.
- **A failed relay never fails the request.** It is logged and
  counted; the row is durable and the sweep relays it again. So is a
  relay the process never ran because it stopped after the answer.
- **Done means someone was told.** A change's row is done once its
  push left, not once its event landed. A revocation is how a socket
  learns its authority ended, and no client replays that for the
  server, so every change is kept until the bus takes it
  ([ADR 0062](../../../../../docs/adr/0062-a-dropped-publish-leaves-its-outbox-row-pending.md)).
- **A stuck row blocks nothing behind it.** Each attempt sets the next
  one later, so a row that will not relay waits on its own.
- **A young row is the request path's.** The sweep leaves rows younger
  than a grace period alone, so two relays do not race for one row.
- **The retention outlives the backup.** Done rows are kept longer
  than the database backups are, so a restore to an earlier point is
  reconciled by relaying the outbox again.
- **The row carries its own org.** The relay and the sweep run with no
  principal and read the org off the row.
