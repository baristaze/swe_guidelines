# ADR 0047: A sign-in over a held session ends it from the client

**Status**: accepted (2026-09-28)

## Context

A switch presents the tab's session to `POST /v1/auth/sessions`, and
the server ends it in the write that makes the new one, so a tab never
holds two live sessions (the guideline, Stages, and One Tenant at a
Time).

A sign-in does not present the session. A tab still signed in can open
`/login` or `/login/dev` and sign in again, as another person or into
another org, and `acme login` can run over a session the file still
keeps. That exchange presents the sign-in credential (`lgn_`). A client
that takes up the new session and drops the old token leaves the old
session live on the server until it expires, with its socket open.

The server cannot end what it is not shown, and showing it the held
session means a second credential on the exchange. The guideline's API
Access gives a request one bearer.

## Decision

**The client ends the session it replaced, after it holds the new one.**
The portal (`takeUpSignIn`) and the CLI (`end_replaced`) read the token
they hold before the sign-in, take up the new session, and then send
`POST /v1/auth/logout` with the old session's own token. That is the
sign-out's route, so no route changes. The CLI sends it to the API that
issued the old session, never to another.

**The order is new first, old after.** A sign-in that fails leaves the
tab with the session it had, and the tab never goes without a token on
its way to the new one. A 401 for the old token, or its socket's 4401,
names a token the tab does not hold and signs nothing out.

**It is best effort.** The new session stands whatever the logout
answers. A 401 or a 422 means the old session is gone already; any
other failure leaves it to lapse at its expiry. The CLI says so on
stderr. The portal says nothing, since the person has nothing to do
about it.

**The provider's logout is not followed.** The logout may answer the
identity provider's logout address for the old session. A sign-out goes
there; a sign-in does not, since the provider's session in that browser
is the one the person just signed in with.

## Consequences

For one round trip, between the new session and the logout's answer,
the server holds two live sessions for the tab. A logout that never
arrives leaves the old session live until it expires. An end in the
exchange's own write would close both gaps, and it needs a way to
present a second credential that API Access allows.

A switch sends no logout: its exchange ends the old session itself. One
exchange per sign-in holds here too
([ADR 0037](0037-a-sign-in-is-exchanged-once.md)).
