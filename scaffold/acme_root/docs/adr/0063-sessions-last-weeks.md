# ADR 0063: Sessions last weeks

**Status**: accepted (2026-09-28)

## Context

A session ends at whichever comes first: its absolute lifetime, counted
from its exchange, or its idle lifetime, counted from its last use. Both
are settings (CTX-36). The guideline's scaffold defaults them to 24 hours
idle and 7 days absolute.

A person who meets a sign-in every Monday morning, or after every
lunch, pays for a bound that protects little. So does `acme` in a
terminal: the CLI keeps one session in a file, and it lapses with it.

NIST SP 800-63B puts a sign-in with one factor at the lowest assurance
level, AAL1. At that level it asks for re-authentication about once
every 30 days of an extended session, and it sets no idle timeout. A
tenant session here rests on one factor: an email code or link, Google,
GitHub, or the team's own single sign-on.

## Decision

**A session lasts 30 days, and 14 days idle.**
`ACME_SESSION_LIFETIME_SECONDS` defaults to 2592000 and
`ACME_SESSION_IDLE_LIFETIME_SECONDS` to 1209600, in the settings, in
`.env.example`, and in the tenancy manager's own options. A person who
uses Acme signs in once a month. One who stays away for two weeks signs
in when they come back.

**Only a person's requests move the idle clock.** `authenticate` and
`authenticate_login` record a use, at most once a minute. The socket's
recheck calls `resume` with `record_use=False`
([ADR 0058](0058-a-socket-asks-again-and-its-pong-answers-from-the-bus.md)),
so an open tab never keeps a session alive by itself. A tab left open
for 14 days with no request is signed out at its next recheck.

**WorkOS's session mirrors ours.** A sign-in through AuthKit leaves an
AuthKit session in the browser, and while it lives, the next sign-in
there has no prompt. The portal keeps its session in the tab, so a new
tab signs in again, and the AuthKit session decides whether the person
sees a prompt. The Acme App's Sessions tab sets it to the same two
bounds: 30 days maximum, 14 days of inactivity. That tab has no API, so
`acme-ops workos-bootstrap` prints both as checks, and the WorkOS
runbook says where they are. WorkOS's settings never end an Acme
session early or keep it alive: Acme reads the access token once, for
its session id, and never refreshes it.

**Nothing else moves.**

- A revoked session is refused at its next request, since every request
  reads it. An open socket closes at once when the bus carries the
  revocation, and within one recheck, five minutes, when it does not.
- An operator token lives an hour at most. The operator plane needs a
  second factor and keeps its own bound.
- An API key keeps its own expiry and its cap of 90 days.
- A sign-in is exchanged within ten minutes
  (`ACME_LOGIN_LIFETIME_SECONDS`).
- The token stays in the tab's session storage, so a closed tab forgets
  it whatever its lifetime.

## Alternatives

- **The guideline's defaults, 24 hours idle and 7 days absolute.** A
  person who takes a weekend off signs in on Monday, for no assurance
  AAL1 asks for.
- **No idle lifetime.** AAL1 asks for none. But a session nobody used
  for two weeks is more likely a forgotten laptop than a person, and the
  idle clock costs one write a minute at most.
- **Keep the token across tabs, in local storage.** Then a new tab would
  need no sign-in at all. But every tab and every later visit would read
  it, and a shared computer would keep it after the tab closed. The
  tab's own storage stays; AuthKit's session covers the new tab.

## Consequences

- A person signs in about once a month, and after two weeks away.
- A stolen session token works for longer: up to 30 days, or until the
  person signs out, a member is removed, or the session is revoked. A
  revocation takes effect at the next request.
- A session row goes once its expiry is past the retention, thirty days.
  A revoked session waits for its expiry, and so does one that ended
  idle, so a dead session stays up to 60 days. It is refused at every
  read meanwhile, and it holds only a digest. A purge by idle time would
  need an index on `last_seen_at`, and nothing measured asks for one.
- The Acme App's Sessions tab holds 30 days and 14 days in each WorkOS
  environment. A person sets them; no API writes them.
