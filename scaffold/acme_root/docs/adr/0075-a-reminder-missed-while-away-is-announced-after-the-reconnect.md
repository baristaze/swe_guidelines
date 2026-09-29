# ADR 0075: A reminder missed while away is announced after the reconnect

**Status**: accepted (2026-09-26).

## Context

A reminder is the one push a person is told about, not only refreshed
by. A live `tasks.task.reminded` push shows a notice with the task's
title, and the task shows its reminded badge.

A portal that was away misses the push: the network dropped, the API
restarted, or the tab was hidden long enough to pause its socket
([ADR 0064](0064-a-hidden-tab-pauses-its-socket.md)). When it comes
back, the replay reads the stream after its cursor. The guideline makes
that the durability mechanism (Realtime at the Edge): "Replay from
storage is the durability mechanism. The socket is a hint that
something changed."

The replay routes a page one record per entity, the last one. Routing
invalidates every query the entity is read from, so a page of two
hundred task records would otherwise restart the list refetch two
hundred times. The collapse is right for the cache and wrong for a
reminder. A reminder was announced only when it was the last task record
of its page. Any later record of any task (the same task edited, another
completed) hid it. The badge still showed, but nobody was told.

## Decision

**A reminder the portal missed is announced after the reconnect.**

**The collapse keeps a reminder.** The replay still routes one record
per entity. Beside that, it keeps every record a person is told about,
whatever record of its entity follows it. The channel asks the provider
which those are (`isAnnounced`). Today that is `tasks.task.reminded`.
The channel stays generic, and the guideline's rule holds: a client that
cares about some kinds filters after it has ordered, never before.

**A replay announces once, at its end.** The replay gathers the kept
records across all its pages and hands them over in stream order, in one
call, however the replay ends. That includes a page that failed: the
cursor has moved past what the replay applied, so no later replay reads
it again. A channel that stopped meanwhile hands over nothing. A switch
of org and a sign-out stop the channel, so a replay from the old session
announces nothing in the new one (One Tenant at a Time). The first
catch-up keeps and announces reminders the same way, since it reads the
same stream for the same reason.

**Three by name, then a count.** Up to three reminders are each
announced as the live push is, "Reminder: <title>", with one read of the
task for its title, one after another. Past three, one notice counts
them: "You missed 5 reminders while you were away." It reads nothing,
and the badges show which tasks. A task reminded twice in one replay
counts once.

**The window is the replay's own.** Nothing is read to find missed
reminders. What the replay reads after the cursor is what is announced.
A stream trimmed past the cursor is a resync that reads no record
([ADR 0040](0040-the-event-stream-has-a-floor.md)), so it announces
nothing.

## Consequences

A person who comes back to the portal learns what came due while they
were away. That is what ADR 0064 already said a paused tab would do.

A replay with more than three reminders costs no extra read. One with
three or fewer costs a read of each task, the read a live reminder makes
for its title, one after another.

A degraded channel polls by replaying every thirty seconds, so a
reminder that fires while it is degraded is announced by the next poll.

A reload inside the first catch-up's window (fifteen seconds, and the
page's own load) announces a reminder that fired in it again. That was
already so when the reminder was the last task record, and a notice
twice beats a notice never.
