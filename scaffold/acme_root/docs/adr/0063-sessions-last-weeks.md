# ADR 0063: Sessions last weeks

**Status**: accepted (2026-09-28)

## Context

A session ends at whichever comes first: its absolute lifetime, counted
from its exchange, or its idle lifetime, counted from its last use. Both
are settings (CTX-36), and the guideline names no value for either.

NIST SP 800-63B puts a sign-in with one factor at AAL1, the lowest
assurance level. There it asks for re-authentication about once every 30
days of an extended session, and sets no idle timeout. A tenant session
here rests on one factor: an email code or link, Google, GitHub, or the
team's own single sign-on.

## Decision

**A session lasts 30 days, and 14 days idle.**
`ACME_SESSION_LIFETIME_SECONDS` defaults to 2592000 and
`ACME_SESSION_IDLE_LIFETIME_SECONDS` to 1209600, in the settings, in
`.env.example`, and in the tenancy manager's own options. The bounds are
weeks, never hours or days. With a day idle and a week in all, a person
who takes a weekend off signs in on Monday, and `acme` in a terminal
lapses with the one session it keeps in a file, for no assurance AAL1
asks for. The idle lifetime stays, though AAL1 asks for none: a session
nobody used for two weeks is more likely a forgotten laptop than a
person, and the idle clock costs one write a minute at most.

**Only a person's requests move the idle clock.** `authenticate` and
`authenticate_login` record a use, at most once a minute. The socket's
recheck calls `resume` with `record_use=False`
([ADR 0058](0058-a-socket-asks-again-and-its-pong-answers-from-the-bus.md)),
so an open tab never keeps a session alive by itself. A tab left open
for 14 days with no request is signed out at its next recheck.

**Nothing moves the deadline.** Only the exchange of a sign-in starts
the 30 days. A session presented to the exchange is a switch: it ends,
and the new session keeps its deadline, in another org or in the same
one. So does the session an owner lands on after deleting a team org. A
session never renews itself, and no chain of switches outlives its
sign-in. When the setting gives less than the deadline leaves, the new
session takes the shorter of the two.

**WorkOS's session mirrors ours.** A sign-in through AuthKit leaves an
AuthKit session in the browser, and while it lives, the next sign-in
there has no prompt. The portal keeps its session in the tab, so a new
tab signs in again, and the AuthKit session decides whether the person
sees a prompt. The Acme App's Sessions tab, in each WorkOS environment,
sets it to the same two bounds: 30 days maximum, 14 days of inactivity.
A person sets them: the tab has no API, so `acme-ops workos-bootstrap`
prints both as checks, and the WorkOS runbook says where they are.
WorkOS's settings never end Acme's session early or keep it alive: Acme
reads the access token once, for its session id, and never refreshes
it.

**Nothing else moves.**

- A revoked session is refused at its next request, since every request
  reads it. An open socket closes at once when the bus carries the
  revocation, and within one recheck, five minutes, when it does not.
- An operator token keeps its own bound, an hour at most: the operator
  plane needs a second factor.
- An API key keeps its own expiry and its cap of 90 days.
- A sign-in is exchanged within ten minutes
  (`ACME_LOGIN_LIFETIME_SECONDS`).
- The token stays in the tab's session storage, so a closed tab forgets
  it whatever its lifetime.

## Consequences

- A person signs in about once a month, and after two weeks away.
- A stolen session token works for longer: up to 30 days from its
  sign-in, or until the person signs out, a member is removed, or the
  session is revoked. A switch made with it buys no time.
- A session row goes once its expiry is past the retention, thirty days.
  A revoked session waits for its expiry, and so does one that ended
  idle, so a dead session stays up to 60 days. It is refused at every
  read meanwhile, and it holds only a digest. A purge by idle time would
  need an index on `last_seen_at`, and nothing measured asks for one.
