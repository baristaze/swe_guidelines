# ADR 0061: A task's push names the version it wrote

**Status**: accepted (2026-09-26). Follows the guideline since v0.36.0:
Realtime at the Edge lets a push carry one field of the entity, its
compare-and-set `version`, which the write's outbox row carries and the
relay copies onto the event and the publish, and NET-30 names it. It is
not a deviation.

## Context

The guideline says what a push carries (Realtime at the Edge):

> A frame and a replayed record carry the identity of the change
> (`seq`, `kind`, `target_id`, the actor) and no field of the entity.
> A client reads the entity through the authorized read, which applies
> the visibility rules of OpContext.

The portal follows it. A push about a task names the task, and every
open tab reads that one task (`GET /v1/tasks/{id}`). That includes the
tab that made the write. It already placed the write's answer, the task
as the server wrote it, so its read brings back what it holds.

The read costs the same as any authenticated request. With one person
editing six times a minute, the writer's tab makes twelve requests a
minute where six are its writes. The actor on the push cannot tell the
writer's tab apart: the same person's second tab has the same actor,
and it needs the read.

What tells them apart is the version. Every write sets it, the writer's
tab holds the one its answer carried, and no other tab holds it until
it reads.

## Decision

**A task's outbox row names the version its change wrote, and the push
carries it.** The row's payload is `{"version": n}`
(`outbox.types.row.versioned_row`). The relay reads it into
`EntityChangedPayload.version`, and the frame's `EntityChangedView`
carries `version`. The create, the edit, the move, the delete, the
restore, a bulk change, an imported task, and the sweep's respace name
it. The reminder and the daily archive write the version in storage,
so the manager does not know it, and their pushes name none. Every other
entity's push names none, and its frame leaves the field out.

**A tab reads a task only when it does not hold the version the push
names.** It holds a task when it placed a write's answer or a read's at
that version or a newer one. Placing a task writes it into every cached
list, so every list the tab holds shows it. An optimistic edit is not
held, a task a 404 took out is not held, and a task seen only in a list
read is not held, since another scope's list may be older. The check
runs when the hint's window closes, so the push of the tab's own write
counts even when it arrives before the write's answer. A push that names
no version is read as before.

**A query a push keeps fresh is not read again on focus, or by the
clock, while the socket is open.** A push reaches it, so its answer is
as fresh as the last push. Its stale time is five minutes, a backstop,
and the focus refetch is off. A query no push names (the person's
identity, a file's signed preview) keeps the ten seconds and the focus
refetch. When the socket is not open (connecting, degraded, closed),
every query goes back to both at once.

## Consequences

Measured in a local run, three tabs on one team list: the writer's, the
same person's second tab, and another member's. The writer ticks a task
done and back six times a minute, then every tab is hidden and shown
four times in a minute.

| Tab | Edit session, before | After | Tab switches, before | After |
|---|---|---|---|---|
| Writer | 12 a minute | 6 (the writes) | 32 a minute | 4 |
| Same person, second tab | 6 | 6 | 32 | 4 |
| Another member | 6 | 6 | 32 | 4 |

The four left on a tab switch are the identity read, which no push
names.

The version is a counter, never a field's value. The stream already
tells every member that a task changed, when, and by whom, so the
number of edits is no new fact. The payload holds nothing a person's
erasure has to find.

A task's event records its version too, since the event's payload is
the row's. The replay (`GET /v1/events`) does not serve the payload, so
the wire changes only on the socket.

The frame gains an optional field. A client that does not know it
ignores it and reads, as it did.
