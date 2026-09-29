# ADR 0047: A sign-in over a held session ends it from the client

**Status**: accepted (2026-09-26)

## Context

A switch presents the tab's session to `POST /v1/auth/sessions`, and
the server ends it in the write that makes the new one. The guideline,
Stages: "An exchange presented with a session ends that session in the
same write, so a tab never holds two live sessions." And One Tenant at
a Time: "The app never holds two sessions."

A sign-in is different. A tab still signed in can open `/login` or
`/login/dev` and sign in again, as another person or into another org.
`acme login` can run over a session the file still keeps. That
exchange presents the sign-in credential (`lgn_`), not the session.
The portal took up the new session and dropped the old token; the CLI
overwrote its file. Neither ended the old session, so it stayed live on
the server until it expired, seven days on, and its socket stayed open.

The server cannot end what it is not shown. Showing it the held session
too means a second credential on the exchange. The guideline's API
Access gives a request one bearer, and a second one in a header or the
body is a credential outside that rule, logged and proxied the way a
body field is.

## Decision

**The client ends the session it replaced, after it holds the new one.**
The portal (`takeUpSignIn`) and the CLI (`end_replaced`) read the token
they hold before the sign-in, take up the new session, and then send
`POST /v1/auth/logout` with the old session's own token. That is the
route a sign-out uses, so no route changes. The CLI sends it to the API
that issued the old session, never to another.

**The order is new first, old after.** A sign-in that fails leaves the
tab with the session it had. The tab never holds no token on its way to
the new one, so the signed-in shell, the switch's hold, and the remount
per org see nothing new. A 401 for the old
token, or its socket's 4401, names a token the tab no longer holds and
signs nothing out.

**It is best effort.** The new session stands whatever the logout
answers. A 401 or a 422 means the old session is gone already. Any other
failure leaves it to lapse at its expiry; the CLI says so on stderr, the
portal says nothing, since the person is signing in and has nothing to
do about it.

**The provider's logout is not followed.** The logout may answer the
identity provider's logout address for the old session. A sign-out
goes there. A sign-in does not: the provider's session in that browser
is the one the person just signed in with.

## Consequences

Between the new session and the logout's answer the server holds two
live sessions for the tab, for one round trip. A logout that never
arrives leaves the old session live until it expires. A server-side end in the exchange's own write would close both
gaps, and it waits for a way to present a second credential that API
Access allows.

A switch is unchanged: its exchange ends the old session itself, and the
client sends no logout. So is the sign-out, and so is one exchange per
sign-in (ADR 0037).
