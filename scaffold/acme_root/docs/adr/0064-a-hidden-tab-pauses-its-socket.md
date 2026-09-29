# ADR 0064: A hidden tab pauses its socket

**Status**: accepted (2026-09-28)

## Context

An open socket has a cost on the server whether or not anyone is
looking. The credential recheck and the pings come to about 216 database
round trips an hour, about 72 transactions
([ADR 0058](0058-a-socket-asks-again-and-its-pong-answers-from-the-bus.md)).
The socket also holds memory, a connection on an API task, and a
subscription on the bus.

A session lasts weeks, and a tab in the background is still open. So
most sockets belong to tabs nobody is looking at.

A reconnect costs about 5 transactions: the ticket, its redemption, the
hello's head, and the replay from the cursor. Five minutes of an open
socket cost about 6. So past five minutes hidden, a reconnect on return
is cheaper than the socket it replaces.

The guideline (Realtime: One Channel per App) lets the provider pause
the socket while the app is hidden, and resume with a fresh ticket and a
replay.

## Decision

**A tab hidden five minutes closes its socket, and the channel says
paused.** The wait is `HIDDEN_PAUSE_MS` in
`apps/portal/src/realtime/timeouts.ts`. A hide shorter than that calls
the wait off and closes nothing.

**A pause is not a failure.** No reconnect is scheduled, no failed cycle
is counted, the degraded polling stops if it ran, and no banner shows.
The degraded path of the guideline ("The channel degrades, it does not
disappear") is about a socket the client wants and cannot have. A paused
socket is one the client does not want yet.

**Any sign of the person's return resumes it.** The page turning
visible, a `pageshow` (a page restored from the back/forward cache among
them), a window `focus`, and `online`. Several at once make one connect:
only a paused channel connects. The resume is a first connect. It asks
for a fresh ticket, and the socket's open replays from the cursor. A
stream trimmed past the cursor is a resync, as on any reconnect
([ADR 0040](0040-the-event-stream-has-a-floor.md)). An `online` that
finds the tab still hidden starts the five minutes over.

**While paused, no query counts as kept fresh by a push.** The query
cache keys that on the status `open`, and `paused` is not `open`. So
the focus that ends a pause also reads every stale query, and nothing
stays frozen if a resume were missed.

**A session that ended while paused signs out on return.** The ticket
request carries the session. The API answers it with 401, and the
transport client signs out, as it does for any request.

## Consequences

The return costs one ticket and one socket, however many signals come
with it. A look away shorter than five minutes keeps its socket and
costs no reconnect. An hour hidden holds the socket for five minutes of
it, not sixty.

A paused tab learns of a change only when it returns, from the replay,
as after any reconnect.

`acme listen` and the Python client do not pause. A terminal has no
hidden state to pause on.
