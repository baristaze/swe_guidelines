# ADR 0040: The event stream has a floor, and a read below it is gone

**Status**: accepted (2026-09-26). Both steps of the rollout are done:
the worker keeps 90 days of events by default. Where the sweep runs
the trim is amended (2026-09-26) by
[ADR 0045](0045-retention-purges-run-once-a-pass-across-tenants.md):
once a pass for every org, in one statement that moves each org's
floor with its own events. A portal session lasts up to 30 days since
[ADR 0063](0063-sessions-last-weeks.md), not 12 hours; a tab still
cannot live 90 days, so the argument for the rollout holds.

## Context

`activity.events` gains one row for every entity write. Its only delete
is a whole stream, 30 days after its org is deleted. So a living org's
stream grows for ever, and so does the operator's count of the last
day's events, which reads the whole table.

A plain trim is not safe. The guideline makes the stream gapless: "a
client treats a gap as a loss and replays" ("Realtime at the Edge").
Both clients do. The portal keeps the last contiguous seq, and a push
past it replays after the cursor. The replay stops when a page moves the
cursor nowhere. Take a tab whose cursor sits below a trimmed stretch:
every page starts above the gap, so every push and every pong replays
the same page, and the cursor never moves. The Python client pages
through the whole stream on every pong and yields nothing, so
`acme listen` goes quiet for good.

The guideline gives the shape of the answer twice. "The stream is a
stream of hints", and "Replay from storage is the durability
mechanism", so a client that cannot replay reads the records instead.
And a topic payload carries `truncated`, "set by a bus that trims; the
consumer re-reads the record". It names no retention for the stream.

## Decision

**Each org's stream has a floor.** The cursor row gains `floor`: the
highest seq the trim removed, 0 while it removed none. Every event
above the floor, up to the head, is stored. The stream is gapless from
the floor, not from 1.

**The trim moves the floor in the transaction that deletes.** It takes
the cursor row's lock, as the append does. It reads the lowest batch
of events above the floor, by the `(org_id, seq)` index. It deletes the
run from the bottom that is past the retention, and it sets the floor
to the last seq of that run. The run stops at the first younger event,
even when older ones follow: `seq` is assigned by the relay, not at the
write, so time and seq can disagree. A second trim waits on the lock
and finds the floor already moved. The sweep runs the trim once per
org per pass, a batch of at most 1000, beside the purge of an expired
org.

**A read below the floor is `410 Gone`, and names the head.**
`GET /v1/events?after_seq=N` with `N` below the floor answers
`stream_truncated`, with `{"floor", "head"}` under `stream` in the
error envelope, as `plan_limit` rides a `402`. The floor is read after
the page, so a trim that commits in between is seen, and a page with a
hole never leaves. 410, not 409: asking again never succeeds, and the
client must not retry the same read. The guideline's exception shapes
have no 410, as they have no 402; `StreamTruncated` carries its own
status.

**A client resyncs on it, once.** It reads afresh what it shows, then
moves its cursor to the head the refusal named. The portal refreshes
every query. `acme listen` says on stderr that some changes are gone,
and reads every task again. The next push or pong replays from the
head, so the client never asks below the floor again. The hello and
the pong carry no floor: the pong's head is already past a stale
cursor, so the replay it starts meets the 410.

**The retention is 90 days, and it is a setting, off in this release.**
`ACME_EVENT_RETENTION_DAYS` is read by the worker. 0 keeps every event
and never moves a floor. Ninety days is past the 7-day backups and the
8-day outbox, so a restore never needs an event the trim removed. The
two audit kinds in the stream go with it after 90 days.

**The trim turns on one release after the 410.** This is expand and
contract across the client boundary. "A migration is compatible with
the release before it, because a rollout runs both at once", and "a
service rolls out before its apps" (NET-23). Clients of the release
before do not handle the 410: a portal tab still running the old
bundle keeps its cursor and asks again on every pong, and an old
`acme listen` exits on it. So:

1. This release adds the floor column, the 410, and the resync in both
   clients. The trim ships off, so no floor moves. The migration is
   expand-only: the release before names `org_id` and `head` in the one
   insert it makes, and the default fills the floor.
2. The next release sets the retention to 90 in code. Every API task
   serving then answers the 410, and every portal bundle and CLI of
   this release resyncs on it. Done: `ACME_EVENT_RETENTION_DAYS`
   defaults to 90.

A tab or a CLI older than this release can still meet the trim if it
lives through two deploys with its cursor 90 days behind. A portal
session ends after 12 hours, so a tab cannot. An API key lives up to
90 days, so a long-running Python caller could. It gets the 410 and
stops with the error, rather than hanging, and a restart starts it at
the head.

**`produced_at` gets a b-tree index.** The operator's count of the last
day filters on it across every org. A BRIN index would suit an
append-only heap, but the trim frees pages at the bottom and new rows
land there, so the heap stops following time. The trim itself reads
`produced_at` too; ADR 0015's "nothing orders or filters on the
column" no longer holds, and its decision stands.

## Consequences

The next release is one line of code: the retention's default becomes
90, and `.env.example` says so. From then on the stream keeps 90 days,
and an org's floor moves up by at most 1000 events per sweep pass.

A client that sleeps past the retention loses the individual changes
of the stretch it missed. It never loses the state: it reads the state
again.

An append waits on the cursor row for as long as one trim batch takes,
a few milliseconds, once per org per pass.

A retention longer than 90 days for the audit kinds would need a table
of their own.
