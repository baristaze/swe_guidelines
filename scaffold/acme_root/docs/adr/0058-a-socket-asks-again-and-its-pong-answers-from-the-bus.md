# ADR 0058: A socket asks again, and its pong answers from the bus

**Status**: accepted (2026-09-28)

## Context

A socket checks its credential when its ticket is redeemed. After that
it trusts the context it got until the bus carries a revocation that
names it, or the credential expires.

The bus is at most once. A publish that fails, or a subscriber whose
connection dropped, loses the message for good. A revoked session's
socket would then stay open until the session's absolute expiry, up to
30 days, and an API key's until the key's, up to 90. A change of role
would reach an open socket not at all.

CTX-27 (TenantContext, Stages) puts a pull behind every trust decision a
push carries: every socket rechecks its session and its membership
every `realtime_recheck_seconds`, a setting of the realtime service,
five minutes by default, and closes when either has ended or the role
changed.

The ping has a cost of its own. Each client pings every 25 seconds, and
a pong that reads the tenant's head is one transaction of three round
trips: 432 round trips an hour for every open tab and every
`acme listen`. The head already arrives on the bus, as the `seq` of
every hint.

## Decision

**A socket rechecks its credential on an interval.** Every
`ACME_REALTIME_RECHECK_SECONDS` (300 by default) the handler asks the
realtime service again, and the service calls the same `resume` the
redemption called: the session, or the API key, and the principal
behind it. It reads the rows, never a cached verdict: a cache is one
more thing a revocation must reach, and the bus that lost the message is
the same Valkey. A refusal closes the socket with 4401, as a refused
ticket does. The first check waits a phase drawn at random within one
interval, so sockets opened together (a deploy's reconnects) do not
check together. No two checks are more than one interval apart, so a
revocation the bus lost keeps its socket open for one interval at most.

**The setting is the realtime service's.** `realtime_recheck_seconds`
sits beside the realtime service's other bounds, as Stages names it.

**The recheck asks and does not use.** `resume` takes
`record_use=False` for it, and records no use of the session. An open
socket never keeps an idle session alive. A tab left open is signed out
at the idle lifetime, as a tab left closed is.

**A change of rights closes the socket to reconnect.** The bus message
`tenancy.membership.updated` names the membership, and every socket
built from it closes with 1012 and the reason `rights_changed`. The
recheck is the backstop: it compares the security it finds with the
one the socket holds. Both clients read any close but 4401 as
"reconnect", and the new ticket carries the new role. 4401 would sign
the person out, and nothing about their sign-in is wrong.

**The bus stays the fast path.** A revocation it carries closes the
socket at once. The recheck bounds only what the bus loses.

**A recheck that cannot be made closes the socket with 1011.** A
database out of reach is no proof that the credential still holds. The
client reconnects, and its ticket is minted once the database answers.

**A pong answers from the bus, within a bound.** The realtime service
keeps, for each tenant it holds a socket for, the highest `seq` it heard
on the bus or read, and when it learned it. A ping within
`ACME_REALTIME_HEAD_MAX_AGE_SECONDS` (60 by default) of that answers with
it and reads nothing. Past the bound, or with nothing known, the ping
reads the head, as the hello always does. The entry goes with the
tenant's last socket in the process, so memory grows with the sockets
and not with the tenants.

## Why the pong stays exact enough

The client treats a head above its cursor as a gap and replays. So the
head must never hide a gap for long.

- It is never above the truth. A `seq` is published after its event
  committed.
- It is never below what this client got from this process. The
  socket's hints and the head come off the same subscription. A hint the
  send buffer dropped is still heard by the service, so the next pong
  shows the gap.
- A late hint below the known head changes nothing, and neither does a
  read that returns less than a hint heard meanwhile.
- It is below the truth only when the bus lost a hint after the last one
  this process heard. The next hint shows the gap at once, since its
  `seq` is past it. With no next hint, the head is read once the bound
  passes.

So a lost hint hides from a quiet socket for the bound plus one ping,
at most 85 seconds by default. Nothing a process sees can do better: a
lost last message looks exactly like no message, and only a read tells
them apart. A bound of zero reads on every ping.

## Consequences

Measured with `ops/audit/dbcalls.py`, per open socket, per hour:

| | Round trips |
|---|---|
| Recheck (2 transactions, 6 round trips, every 5 minutes) | 72 |
| Pong, idle tenant (a read at most every 60 s) | 144 |
| Pong, tenant with a hint in every 60 s | 0 |
| Total, idle tenant | 216, where a read on every pong costs 432 |

Several sockets of one tenant in one process share the read, so the
pong's cost falls further with them.

A tab left open with no request past the idle lifetime is signed out
when its socket rechecks. So is `acme listen` in an org where nothing
changed for that long; it exits and asks for a sign-in, as any command
would.

A socket opened under an API key is checked as the key's every request
is: the key, and the principal behind it.
