# ADR 0040: The event stream has a floor, and a read below it is gone

**Status**: accepted (2026-09-28)

## Context

`activity.events` gains one row for every entity write. Without a trim,
a living org's stream grows for ever, and so does the operator's count
of the last day's events.

A plain trim is not safe. The guideline makes the stream gapless: "a
client reads a gap as a loss" ("Realtime at the Edge"). A client whose
cursor sits below a trimmed stretch gets every page starting above the
gap, so every push and every pong replays the same page, and the cursor
never moves. The guideline's answer is a retention and a floor: the
trim moves the floor, and a read below it is `410 stream_truncated`, so
the client reads the records instead. The numbers, the batch, and the
client's resync are the system's to set.

## Decision

**Each org's stream has a floor.** The cursor row carries `floor`: the
highest seq the trim deleted, 0 while it deleted none. Every event
above the floor, up to the head, is stored. The stream is gapless from
the floor, not from 1.

**The trim moves the floor in the transaction that deletes.** It takes
the cursor row's lock, as the append does, deletes the run of events at
the bottom that is past the retention, and sets the floor to the last
seq of that run. The run stops at the first younger event, even when
older ones follow: the relay assigns `seq`, so time and seq can
disagree. The sweep runs the trim once a pass across every tenant, a
batch of at most 1,000 events, and skips a cursor an append holds
([ADR 0045](0045-retention-purges-run-once-a-pass-across-tenants.md)).

**A read below the floor is `410 Gone`, and names the head.**
`GET /v1/events?after_seq=N` with `N` below the floor answers
`stream_truncated`, with `{"floor", "head"}` under `stream` in the
error envelope. The floor is read after the page, so a trim that
commits in between is seen. It is 410, never 409: asking again never
succeeds, and the client must not retry the same read. The guideline's
exception shapes have no 410, so `StreamTruncated` carries its own
status.

**A client resyncs on it, once.** It reads afresh what it shows, then
moves its cursor to the head the refusal named. The portal refreshes
every query. The Python client resyncs the same way, and `acme listen`
says on stderr that some changes are gone. The next push or pong
replays from the head, so the client never asks below the floor again.

**The retention is 90 days, a setting of the worker.**
`ACME_EVENT_RETENTION_DAYS` defaults to 90, and 0 keeps every event and
never moves a floor. Ninety days is past the 7-day backups and the
8-day outbox, so a restore never needs an event the trim deleted. The
audit kinds in the stream go with it.

**`produced_at` has a b-tree index, never BRIN.** The operator's count
of the last day and the trim both read it across every org. BRIN suits
an append-only heap, but the trim frees pages at the bottom and new
rows land there, so the heap stops following time.

## Consequences

A client that sleeps past the retention loses the individual changes of
the stretch it missed, never the state: it reads the state again.

An append waits on the cursor row for as long as one trim batch takes,
a few milliseconds, at most once a pass.

A retention longer than 90 days for the audit kinds needs a table of
their own.
