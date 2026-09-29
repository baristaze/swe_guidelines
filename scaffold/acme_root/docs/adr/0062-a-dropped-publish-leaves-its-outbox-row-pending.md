# ADR 0062: A dropped publish leaves its outbox row pending

**Status**: accepted (2026-09-26). Follows the guideline since v0.36.0
for an entity change: Topics lets `publish` answer whether the bus took
the event (ASY-09), and Database Roles marks a row done only once the
bus took its publish (STO-20). The exception for ASY-09 leaves
`pyproject.toml`, and `arch-check` passes without it. Follows it since
v0.37.0 for a work row too: STO-20 marks a work row done once its item
is queued, as this record decides
([ADR 0079](0079-what-0-37-0-asks.md)). At v0.36.0 it was a deviation,
which [ADR 0067](0067-what-0-36-0-asks-and-what-stays-a-choice.md)
records.

## Context

The relay turns an entity change's outbox row into an event in the
tenant's stream and an `entity_changed` publish on the bus, then marks
the row done. A topic is best effort: the Valkey impl logs and counts a
publish the bus refuses, and the breaker in front of Valkey drops one
while it is open. A relay that cannot tell marks the row done anyway,
and the sweep never sees it again.

For most kinds that costs a client a moment of staleness. The event is
in the stream, and a client that misses a frame catches up at the next
frame or pong, which shows it a gap.

For four kinds it costs more. `tenancy.session.revoked`,
`tenancy.api_key.deleted`, `tenancy.user.deleted`, and
`tenancy.org.deleted` are how the realtime service hears that a socket's
authority ended (`REVOCATIONS`). No client replays those for the server.
A dropped one leaves the socket open until its credential expires.

The guideline, Topics, fixes the interface's shape, and `ASY-09` checks
it: "`publish` is annotated `-> None`". Its reason: "That is why
`publish()` returns `None`. A broker-assigned id carries no durable
meaning across retries and replays, and surfacing it would leak
technology through the interface." The guideline also says: "An effect
a handler must bring about is never sent only on a topic. It rides an
outbox row or a work item, and a topic carries hints only." Closing a
socket is such an effect, and it rides the outbox row. The row keeps
that promise only if the relay learns that the message was dropped.

## Decision

**`publish` says whether the bus took the message.** It returns True
once Valkey accepted it and False when it was dropped: a refusal, which
the impl still logs and counts as `topics` / `publish_failed`, or an
open breaker. An open breaker is never a publish that landed. It still
raises nothing for the bus, and a payload of the wrong type still
raises. A caller that only wakes someone ignores the answer.

The answer is a yes or no, not an id, so no broker's id reaches the
interface, which is the rule's reason. Raising for a dropped message
would also keep `-> None`, but then every caller that only wakes
someone must catch it, and one that forgets fails a request after its
write has landed. A bool is safe to ignore. `ASY-09` accepts it since
guideline v0.36.0.

**An entity change is done once the bus took its message.** A row whose
publish was dropped is not marked. It stays pending, and the sweep
publishes it again, with the delay that doubles per attempt and the dead
letter past `max_attempts` that any failed row gets. Its event is not
appended twice: the append is idempotent on the row's id.

**One rule for every entity change.** A hint and a revocation are
treated alike. The hint could be left done, since the stream replays it.
But a list of the kinds that must not be lost would have to follow
`REVOCATIONS` by hand, and a kind added there and not here would be lost
again. A hint sent again costs one publish. A client drops it as seen,
since its `seq` is at or below the client's cursor, or fills a gap with
it. A revocation sent again ends a socket that is already ended, which
does nothing.

**A work row is done once it is enqueued.** The queue is its truth. Its
`work_available` wake-up is a hint, and a worker polls every five seconds
without it. An enqueue sent again finds the item by its key and
publishes nothing, so keeping the row would not bring the wake-up back.

**A batch marks the rows it published.** The relay appends a batch's
events in one call, publishes each, and marks the rows the bus took in
one statement. A row the bus dropped is left out of the mark. In the
request path the relay then answers False and the row waits for the
sweep past its grace. In the sweep the row spends its attempt with the
error `the bus dropped the publish`. The rows beside it are not relayed
again one by one, because nothing about them failed.

**The count is the relay's.** Each dropped row counts once under
`outbox` / `publish_failed`, whether Valkey refused it or the breaker
did. That differs from `topics` / `publish_failed`, which goes quiet
while the breaker is open.

**No new alarm.** A row that stays pending is what `outbox-lag` reads:
the oldest row neither relayed nor failed, across tenants, past five
minutes. A bus that drops every publish trips it as a failing event
store does. A dead letter still logs `failed for good` and leaves an
`outbox.row.failed` event in the org's diary.

## Consequences

A bus that is down keeps rows pending. The outbox lag climbs, the
sweep publishes each row every pass its delay allows, and each attempt
is a warning in the worker's log. Once Valkey answers, the next pass
publishes the backlog and marks it done. A row whose publish is dropped
on all ten attempts, about an hour and a quarter with the default
delays, becomes a dead letter. Its event is in the stream; only the
frame was never sent.

A revocation the bus drops reaches the sockets on the first pass its
delay allows once the bus is back. The credential's expiry still closes
the socket if the row becomes a dead letter first.

In the API, the relay runs after the answer is sent. A dropped publish
there changes nothing the caller sees: the release logs the drop and the
row waits for the sweep, as a row does whose relay failed.

A frame can reach a client twice. When the mark after a publish fails,
the sweep publishes that row again. That was already true, and every
consumer treats a frame as a hint keyed by its `seq`.
