# ADR 0037: A sign-in is exchanged once, and its retry signs in again

**Status**: accepted (2026-09-25), amended by
[ADR 0068](0068-an-operator-credential-ends-by-itself.md) (2026-09-26):
the second factor ends the sign-in it verified, and an operator's sign-in
is exchanged once for an operator token.

## Context

A sign-in answers with a login credential (`lgn_`) and the person's
places. `POST /v1/auth/sessions` exchanges it for a session in one of
them. The login lives ten minutes, and the exchange left it standing, so
every exchange inside those minutes made another session. Three clicks
on the org picker made three sessions. Any client, or a replayed
request, could do the same. A switch already ends the session it is
given in the write that makes the new one; the exchange of a sign-in
did not.

The guideline says the app "signs in once, picks a membership, and
exchanges it for one session", then "holds one session and drops the
sign-in credential" ("One Tenant at a Time"). It also says, in "The
Gateway": "A creating `POST` accepts an `Idempotency-Key` header, and an
`IdempotencyMarker` [...] owns the retry. Creating is what the request
leaves behind, not what it answers with." The exchange leaves a session
row behind, so the rule reaches it, and the question is what a retry
after a lost answer gets.

## Decision

**The exchange ends the sign-in in the write that makes the session.**
`exchange_sign_in` is one named atomic write: the login row is locked,
refused when it is already ended, marked revoked, and the new session
is inserted, in one transaction. The login lives in the system scope,
so the write runs on the system login and sets the scope to the tenant
before the insert, as a switch does across two tenants. It takes the
tenant first like any tenant method, and the test that holds the system
scope to its named callers names it too. Of two exchanges at once, one
lands; the other finds the sign-in ended. A second exchange answers
`401` ("this sign-in was used already; sign in again"). A refused
exchange, for an org the person is not in, ends nothing.

**The exchange takes no `Idempotency-Key`.** The sign-in is the key.
It is single-use, minted by the server, and ended in the same write as
the effect, which is the idempotent consumer's shape in "Idempotency on
the Consumer Side": "marker and effect are one named atomic write". So
no retry, replay, or second click can make a second session, which is
the whole of what a marker would add here.

The other thing a marker does is replay the answer, and here it cannot.
The answer is the session token. The idempotency records keep no
secret: "A replay answers with the row, the secret missing". A lost
answer is a stored outcome, so a replay would hand back a session
without its token, which leaves the client where the `401` does. The
one way to hand back the same session is to keep its token in the
clear, and the tenancy namespace never keeps a credential it can be
handed. The re-mint the api key uses covers only an attempt that
stored no outcome, which is not the lost answer.

**A retry after a lost answer signs in again.** The code sign-in is
already this way: "A code is exchanged once, so a retry after a lost
answer is refused and the person signs in again." Sign-up in the
guideline is too. The session the lost answer made reached nobody. Its
token was never shown, so it can never be presented, and it ends at its
absolute lifetime like any other session.

## Consequences

Nothing that exchanges once per sign-in changes: the portal's picker
(one exchange per choice), the CLI's `acme login` (device or local),
the traffic run's sign-ins, the demo recorders, and the org chip and
`/orgs/new`, which switch with the session. The portal and the CLI send
no key. A portal pick that meets the `401` says so, and the person
signs in again.

The second factor is unchanged. `POST /v1/auth/second-factor` takes a
sign-in and answers a new one; the one presented is not exchanged and
still stands. The new one is exchanged once like any other.

The operator plane admits a sign-in that verified a code. Once that
sign-in is exchanged into a tenant, the plane refuses it too. An
operator who needs both signs in twice. No client does both today.

The row keeps its columns: an ended sign-in is `revoked_at`, as an
ended session is. There is no schema change.
