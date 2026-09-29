# Events

The org's diary: the append-only stream behind every live push. This
is one of the kinds of thing [Acme is made of](../../../../README.md).

## What it holds

- **Event**: one line of an org's stream: its sequence number, its
  kind, the record it is about, a payload fixed per kind, when it was
  produced, and who produced it (the actor, the request, and the app).
- **Kind**: `<namespace>.<entity>.<action>` for a change, or an audit
  kind for what the platform records about itself: `work.item.failed`,
  `work.item.requeued`, `outbox.row.failed`, and
  `identity.event.received`.
- **Cursor**: per org, the **head** (the last number given) and the
  **floor** (the last number the trim removed).

## What can happen

- **Append.** The outbox relay appends a change's event. A producer
  with a context appends an audit entry built by `audit_event`.
- **Read after a number**, oldest first, a page at a time. A number
  below the floor is refused as gone (`410 stream_truncated`), with the
  floor and the head.
- **Read the head**, where a screen that opens the live channel starts.
- **Trim.** Once a pass, across every org, the sweep deletes the events
  older than the retention (90 days by default) from the bottom of each
  stream and moves each floor with them.
- **Drop a purged org's stream** once the org is past its retention.

## The rules

- **Gapless above the floor.** An append takes the next numbers under
  the cursor's lock, in the statement that writes them, and holds the
  lock to its commit. A reader never sees a number before the ones
  below it.
- **Appending twice appends once.** An append is idempotent on the
  event's id.
- **Provenance is stamped, never claimed.** The actor, the request, and
  the app come from the context or the outbox row, never from the
  caller's event.
- **A push is a hint; the stream is the truth.** A screen keeps the
  last number it applied and replays the stream when a push runs ahead
  of it. A screen behind the floor reads afresh and goes on from the
  head ([ADR 0040](../../../../../docs/adr/0040-the-event-stream-has-a-floor.md)).
- **Ids, never values.** A payload names records and kinds, never a
  person's email or name.

## How another namespace composes it

A change is announced by an outbox row of the write itself; the relay
turns it into an event, so a namespace never calls `append_event` for
its own changes. A namespace that records something about the platform
builds an audit entry with `audit_event(ctx, id, kind, target_id,
facts)` and appends it through `EventsManagerInterface.append_event`
([ADR 0011](../../../../../docs/adr/0011-audit-entries-are-events.md)).
A client reads the stream through `GET /v1/events`.
