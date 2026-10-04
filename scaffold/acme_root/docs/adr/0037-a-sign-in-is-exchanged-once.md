# ADR 0037: A sign-in is exchanged once, and its retry signs in again

**Status**: accepted (2026-09-28)

## Context

A sign-in answers a login credential (`lgn_`) and the person's places.
`POST /v1/auth/sessions` exchanges it for a session in one of them. The
login lives ten minutes (`ACME_LOGIN_LIFETIME_SECONDS`). An exchange
that leaves the login standing makes another session on every click of
the org picker, and on every replayed request.

In the guideline, the app "holds one session and drops the sign-in
credential" ("One Tenant at a Time"), and a creating `POST` accepts an
`Idempotency-Key` whose marker owns the retry ("The Gateway"). The
exchange leaves a session row behind, so the question is what a retry
after a lost answer gets.

## Decision

**The exchange ends the sign-in in the write that makes the session.**
`exchange_sign_in` is one named atomic write. In one transaction, the
login row is locked, refused when it is already ended, and marked
revoked, and the new session is inserted. The login lives in the system
scope, so the write runs on the system login and sets the scope to the
tenant before the insert, as a switch does. Of two exchanges at once,
one lands. A second exchange answers `401` ("this sign-in was used
already; sign in again"). A refused exchange, for an org the person is
not in, ends nothing.

**The exchange takes no `Idempotency-Key`.** The sign-in is the key. It
is single-use, minted by the server, and ended in the same write as the
effect: the idempotent consumer's shape. So no retry, replay, or second
click can make a second session.

A marker's other job, replaying the answer, cannot work here. The
answer is the session token, and the idempotency records keep no
secret: a replay would hand back a session without its token. Handing
back the same session would mean keeping its token in the clear, and
the tenancy namespace never keeps a credential it can be handed.

**A retry after a lost answer signs in again.** The session the lost
answer made reached nobody. Its token was never shown, so it can never
be presented, and it ends at its absolute lifetime like any other.

**The second factor and the operator's mint follow the same rule**
([ADR 0068](0068-an-operator-credential-ends-by-itself.md)).
`POST /v1/auth/second-factor` takes a sign-in and a TOTP code, answers
a new sign-in that records the code, and ends the one presented in the
same write. An operator's sign-in with its code is exchanged once, for
one operator token.

## Consequences

The portal's picker makes one exchange per choice, and the CLI's
`acme login` one per sign-in. Neither sends a key. A pick that meets
the `401` says so, and the person signs in again.

A sign-in exchanged into a tenant is ended, so the operator plane
refuses it too. An operator who needs the plane and a tenant signs in
twice.

An ended sign-in is `revoked_at`, as an ended session is. The row keeps
its columns.
