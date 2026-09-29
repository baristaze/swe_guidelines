# ADR 0061: A push names the version its change wrote

**Status**: accepted (2026-09-28)

## Context

The guideline says what a push carries (Realtime at the Edge):

> A frame and a replayed record carry the identity of the change
> (`seq`, `kind`, `target_id`, the actor) and no field of the entity.
> A client reads the entity through the authorized read, which applies
> the visibility rules of OpContext.

NET-30 lets one field through: the record's compare-and-set `version`,
which the write's outbox row carries and the relay copies onto the event
and the publish.

A client that follows the stream reads a record each time a push names
it. That includes the tab that made the write. It already holds the
write's answer, the record as the server wrote it, so its read brings
back what it holds. The actor on the push cannot tell the writer's tab
apart: the same person's second tab has the same actor, and it needs
the read. The version tells them apart. Every write sets it, the
writer's tab holds the one its answer carried, and no other tab holds it
until it reads.

## Decision

**A record that carries a version names it in the outbox row of every
change.** The row's payload is `{"version": n}`
(`acme.om.outbox.types.row.versioned_row`). The relay reads it into
`EntityChangedPayload.version`, and the frame's `EntityChangedView`
carries `version`. A row that names none leaves the field out of the
frame.

The orchestration record is the one record here that carries a version:
its hint is a `versioned_row` on every write
(`om/src/acme/om/orchestrations/steps.py`), so a push about an
orchestration names the version it wrote. A product's record with a
version does the same.

**A client that holds the record at that version need not read it.** A
push that names no version is read as before.

**A query a push keeps fresh is not read again on focus, or by the
clock, while the socket is open.** A push reaches it, so its answer is
as fresh as the last push. In the portal its stale time is five minutes
(`PUSHED_STALE_MS` in `apps/portal/src/app/queryClient.ts`), a backstop,
and the focus refetch is off. A query no push names keeps ten seconds
(`STALE_MS`) and the focus refetch. When the socket is not open
(connecting, degraded, paused, closed), every query goes back to both at
once.

## Consequences

The version is a counter, never a field's value. The stream already
tells every member that a record changed, when, and by whom, so the
number of changes is no new fact. The payload holds nothing a person's
erasure has to find.

A record's event keeps its version too, since the event's payload is
the row's. The replay (`GET /v1/events`) does not serve the payload, so
the wire changes only on the socket.

The frame's field is optional. A client that does not know it ignores
it and reads.
