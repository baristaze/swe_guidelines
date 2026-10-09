# ADR 0096: A notice is handed over once, as the cursor passes it

**Status**: accepted (2026-10-09)

## Context

Most records on the stream only refresh what a person sees. A product
may also tell the person about some of them: a notice, such as an
invitation accepted. The guideline (Realtime: One Channel per App) has
the channel hand each notice over once, as the cursor passes it, keep
notices apart from a replay's collapse, and hand over nothing once it
stops.

The portal's channel routes a page read back from the stream one push
per entity, the last. Routing invalidates every query the entity is
read from, so a page of two hundred records of one entity would
otherwise refetch the same list two hundred times. The collapse is
right for the cache and wrong for a notice: any later record of its
entity hides it.

The section leaves the seam to the channel: which hook tells, what a
push past a gap does, and what the first catch-up and a resync tell.

## Decision

**Two hooks, and the route is neither.** The channel asks `isAnnounced`
whether a record is a notice, and hands notices to `announce`. `route`
tells the person nothing, live or read back. If it told, a notice that
is also the last record of its entity on a page would be told twice:
once routed and once handed over. A product with no notice passes
neither hook, and the scaffold has none.

**A notice is told when the cursor passes it.** The cursor passes each
seq once, so each notice is told once. A push in order is handed over
at once. A duplicate frame sits behind the cursor and tells nothing. A
push past a gap is routed, since its entity did change, and not told:
the replay that closes the gap reads it and tells it. A notice told
late beats one told twice.

**A replay hands over once, at its end.** It keeps every notice it
applies, across all its pages, and hands them over in stream order in
one call, however it ends: at the head, at a page that moves the cursor
nowhere, at a failed read, or at a resync after a trim. The cursor has
passed what it applied, so no later replay reads it again, and a later
page's failure loses nothing an earlier page kept. One call lets the
app show many notices as one.

**The first catch-up hands over the notices it reads.** It reads the
stream's tail for what the page may have missed while it loaded, as a
replay reads what a reconnect missed, and keeps notices the same way.

**A stopped channel hands over nothing.** A switch of tenant and a
sign-out stop the channel. A replay still in flight ends into nothing,
so the old tenant's notice never shows in the new one (One Tenant at a
Time).

## Consequences

A person who comes back after a drop, a restart, or a paused tab
([ADR 0064](0064-a-hidden-tab-pauses-its-socket.md)) is told, once,
what they missed.

A stream trimmed past the cursor resyncs and reads no record
([ADR 0040](0040-the-event-stream-has-a-floor.md)), so a notice in the
trimmed stretch is never told. The retention outlasts the backups, so
only a tab away that long misses one.

A replay that a switch stops tells nothing of what it read. The record
stays in the stream, and the screens that read it show it.

A reload inside the first catch-up's margin tells again a notice the
page before it told: the channel keeps nothing across a load.
