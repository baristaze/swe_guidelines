# ADR 0058: A socket asks again, and its pong answers from the bus

**Status**: accepted (2026-09-26). The session lifetimes it names are
14 days idle and 30 days absolute since
[ADR 0063](0063-sessions-last-weeks.md); the recheck and its bound do
not change. The last paragraph of the consequences is amended
(2026-09-26): the recheck of a socket an API key opened asks the plan
too, and a change of the org's billing account wakes that recheck at
once. See the end of this record.

## Context

A socket checks its credential once, when its ticket is redeemed. After
that it trusts the context it got until one of two things happens. The
bus carries a revocation that names it, or the credential expires.

The bus is at most once. A publish that fails, or a subscriber whose
connection dropped, loses the message for good. Then a revoked session's
socket stays open until the session's absolute expiry, up to 12 hours,
and an API key's until the key's, up to 90 days. A change of role is not
on the bus as a close at all, so the socket keeps the old role for its
whole life.

The guideline says the expiry covers a missed frame. With a 90-day key,
that bound holds only on paper.

The ping has a cost of its own. Each client pings every 25 seconds, and
each pong reads the tenant's head: one transaction, three round trips,
432 round trips an hour for every open tab and every `acme listen`. The
head already arrives on the bus, as the `seq` of every hint.

## Decision

**A socket re-checks its credential on an interval.** Every
`ACME_REALTIME_RECHECK_SECONDS` (300 by default) the handler asks the
realtime service again, and the service calls the same `resume` the
redemption called. A refusal closes the socket with 4401, as a refused
ticket does. The first check waits a phase drawn at random within one
interval, so sockets opened together (a deploy's reconnects) do not
check together. No two checks are more than one interval apart. So a
revocation the bus lost keeps its socket open for one interval at most.

**The recheck asks and does not use.** `resume` takes
`record_use=False` for it, and records no use of the session. An open
socket never keeps an idle session alive. A tab left open is signed out
at the idle lifetime, as a tab left closed is, and as its next request
would be.

**A change of rights closes the socket to reconnect.** The bus message
`tenancy.membership.updated` names the membership, so the redemption
now yields the membership's id beside the context. Every socket built
from that membership closes with 1012 and the reason `rights_changed`.
The recheck is the backstop: it compares the role and teams it finds
with the ones the socket holds. 4401 would sign the person out, and
nothing about their sign-in is wrong. Both clients read any other close
as "reconnect", and the new ticket carries the new role.

**The bus stays the fast path.** A revocation it carries closes the
socket at once. The recheck bounds only what it loses.

**A recheck that cannot be made closes the socket with 1011.** A
database out of reach is no proof that the credential still holds. The
client reconnects, and its ticket is minted once the database answers.

**A pong answers from the bus, within a bound.** The realtime service
keeps, for each tenant it holds a socket for, the highest `seq` it
heard on the bus or read, and when it learned it. A ping within
`ACME_REALTIME_HEAD_MAX_AGE_SECONDS` (60 by default) of that answers
with it and reads nothing. Past the bound, or with nothing known (a
process that just started), the ping reads the head, as the hello
always does. The entry goes with the tenant's last socket in the
process, so memory grows with the sockets and not with the tenants.

## Why the pong stays exact enough

The client treats a head above its cursor as a gap and replays. So the
head must never hide a gap for long.

- It is never above the truth. A `seq` is published after its event
  committed.
- It is never below what this client got from this process. The
  socket's hints and the head come off the same subscription. A hint
  the send buffer dropped is still heard by the service, so the next
  pong shows the gap, as before.
- A late hint below the head known changes nothing, and neither does a
  read that returns less than a hint heard meanwhile.
- It is below the truth only when the bus lost a hint after the last
  one this process heard: a failed publish, or its subscription down.
  The next hint shows the gap at once, since its `seq` is past it. With
  no next hint, the head is read once the bound passes.

So a lost hint hides from a quiet socket for the bound plus one ping,
at most 85 seconds by default, where the read on every ping hid it for
one ping. Nothing a process sees can do better: a lost last message
looks exactly like no message, and only a read tells them apart. A
bound of zero reads on every ping, which is the guideline's shape at
its old cost.

## Alternatives

- **Cap a socket's life at an hour.** The client reconnects with a
  fresh ticket, which checks everything. It bounds a lost revocation by
  an hour, not minutes, and each reconnect costs a ticket, a handshake,
  and a replay, about 40 round trips.
- **Keep the verdict in a cache.** A cached "still valid" is one more
  thing a revocation must reach, and the bus that lost the message is
  the same Valkey. The recheck reads the rows.
- **Count the recheck as a use.** Then an open socket keeps its
  session alive until the absolute expiry, and the idle lifetime no
  longer measures what a person did.
- **Share one head read per tenant per ping.** It stays exact, but it
  saves nothing for a tenant with one socket in a process, which is the
  common case.

## Consequences

Measured with `ops/audit/dbcalls.py` over a database of the branch's
own:

| Per open socket, per hour | Before | After |
|---|---|---|
| Recheck (2 transactions, 6 round trips, every 5 minutes) | 0 | 72 |
| Pong, idle tenant (a read at most every 60 s, else none) | 432 | 144 |
| Pong, tenant with a hint in every 60 s | 432 | 0 |
| Total, idle tenant | 432 | 216 |

Several sockets of one tenant in one process share the read, so the
pong's cost falls further with them.

A tab left open with no request past the idle lifetime, four hours by
default, is signed out when its socket rechecks. So is `acme listen`
in an org where nothing changed for four hours; it exits and asks for a
sign-in, as any command would.

A socket opened under an API key is checked as the key's every request
is: the key, the membership, and the plan's lever on keys. The plan
comes from the billing account read with the principal
(`read_key_principal`), so the check costs no read of its own: the
recheck stays at 2 transactions and 6 round trips. After a downgrade to
a plan without keys, the recheck refuses the key with
`plan_limit_reached`, and the socket closes with 4401, as a revoked
key's does. The key is kept, and works again on a plan with keys.

The bus is the fast path here too. A plan change writes
`billing.account.updated`, which every process hears. It carries no
plan, so it closes nothing itself: it wakes, at once, the recheck of
every socket an API key of that org opened, and the recheck reads the
plan. A session's socket is not woken. When the bus loses the message,
or a paid plan runs out at its period's end with no message at all,
the recheck interval bounds the socket as it bounds a lost revocation.

| Per API key socket | Before | After |
|---|---|---|
| Recheck reads | `read_api_key`, `read_principal` | `read_api_key`, `read_key_principal` |
| Recheck cost | 2 transactions, 6 round trips | 2 transactions, 6 round trips |
| Worst case after a downgrade | until the key expires, up to 90 days | at once on the bus; one interval (5 minutes) without it |
