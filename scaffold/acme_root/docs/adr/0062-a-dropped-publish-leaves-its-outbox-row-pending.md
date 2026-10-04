# ADR 0062: A dropped publish leaves its outbox row pending

**Status**: accepted (2026-09-28)

## Context

The relay turns an entity change's outbox row into an event in the
tenant's stream and an `entity_changed` publish on the bus, then marks
the row done. A topic is best effort: the Valkey impl logs and counts a
publish the bus refuses, and the breaker in front of Valkey drops one
while it is open. A relay that marked the row done anyway would leave it
where the sweep never looks.

For most kinds a lost publish costs a moment of staleness. The event is
in the stream, and a client that misses a frame sees the gap at the next
frame or pong. Four kinds cost more: `tenancy.session.revoked`,
`tenancy.api_key.deleted`, `tenancy.user.deleted`, and
`tenancy.org.deleted` (`REVOCATIONS`). They tell the realtime service
that a socket's authority ended, and no client replays them for the
server. A dropped one leaves the socket open until the recheck
([ADR 0058](0058-a-socket-asks-again-and-its-pong-answers-from-the-bus.md))
or the credential's expiry.

## Decision

**`publish` says whether the bus took the message**, as the guideline's
Topics (ASY-09) allows. It returns True
once Valkey accepted it, and False when it was dropped: a refusal, which
the impl logs and counts as `topics` / `publish_failed`, or an open
breaker. It raises nothing for the bus; a payload of the wrong type
still raises. A caller that only wakes someone ignores the answer. The
answer is a yes or no, never an id, so no broker's id reaches the
interface. It is never a raise either: in every caller that forgot to
catch one, a raise would fail a request after its write landed.

**An entity change is done once the bus took its message**, as Database
Roles (STO-20) says. A row whose
publish was dropped is not marked. It stays pending, and the sweep
publishes it again, with the delay that doubles per attempt and the dead
letter past `max_attempts` (10) that any failed row gets. Its event is
not appended twice: the append is idempotent on the row's id.

**One rule for every entity change**, a hint and a revocation alike,
never a list of the kinds that must not be lost. Such a list would
follow `REVOCATIONS` by hand, and a kind added there and not here would
be lost. A hint sent again costs one publish: a client drops it as seen,
its `seq` at or below the client's cursor, or fills a gap with it. A
revocation sent again ends a socket that is already ended.

**A work row is done once it is enqueued** (STO-20). The queue is its
truth. Its
`work_available` wake-up is a hint, and a worker polls every five
seconds without it. An enqueue sent again finds the item by its key and
publishes nothing, so keeping the row would not bring the wake-up back.

**A batch marks the rows it published.** The relay appends a batch's
events in one call, publishes each, and marks the rows the bus took in
one statement, leaving out the rows it dropped. In the request path the
relay then answers False, and the row waits for the sweep past its
grace. In the sweep the row spends its attempt with the error `the bus
dropped the publish`. The rows beside it are not relayed again one by
one, because nothing about them failed.

**The count is the relay's.** Each dropped row counts once under
`outbox` / `publish_failed`, whether Valkey refused it or the breaker
did. `topics` / `publish_failed` goes quiet while the breaker is open.

**No new alarm.** A row that stays pending is what `outbox-lag` reads:
the oldest row neither relayed nor failed, across tenants, past five
minutes. A bus that drops every publish trips it as a failing event
store does. A dead letter still logs `failed for good` and leaves an
`outbox.row.failed` event in the org's stream.

## Consequences

A bus that is down keeps rows pending. The outbox lag climbs, the sweep
publishes each row every pass its delay allows, and each attempt is a
warning in the worker's log. Once Valkey answers, the next pass
publishes the backlog and marks it done. A row dropped on all ten
attempts, about an hour and a quarter with the default delays, becomes a
dead letter: its event is in the stream, and only the frame was never
sent.

A revocation the bus drops reaches the sockets on the first pass its
delay allows once the bus is back. If the row becomes a dead letter
first, the recheck and the credential's expiry still close the socket.

In the API the relay runs after the answer is sent, so a dropped publish
changes nothing the caller sees: the relay logs the drop, and the row
waits for the sweep.

A frame can reach a client twice: when the mark after a publish fails,
the sweep publishes that row again. Every consumer treats a frame as a
hint keyed by its `seq`.
