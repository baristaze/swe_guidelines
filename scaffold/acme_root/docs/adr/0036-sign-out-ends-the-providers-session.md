# ADR 0036: Sign-out ends the identity provider's session too

**Status**: accepted (2026-09-24)

## Context

A person signs in through WorkOS AuthKit's hosted page (ADR 0028).
AuthKit keeps a session of its own in that browser. Acme keeps its own
sessions and never used AuthKit's, so signing out ended Acme's session
alone. AuthKit's stayed, and the next `/login` on that browser signed the
same person straight back in with no prompt. On a shared computer, the
next person to open Acme is the last one.

WorkOS documents the sign-out for this: take the session id from the
`sid` claim of the access token the code exchange answers, end the app's
own session, and send the browser to WorkOS's logout for that session.
WorkOS then sends the browser to a `return_to` that is one of the
application's sign-out URIs, or to the default one.

## Decision

**The sign-out ends both sessions.** A sign-in through the hosted page
keeps AuthKit's session id with Acme's login, server-side, in
`core.sessions.provider_session_id`. The exchange into a tenant and every
switch carry it to the new session. `POST /v1/auth/logout` ends Acme's
session and answers `provider_logout_url`, WorkOS's logout for that
session. The portal goes there last, and WorkOS sends it back to the
portal's `/signed-out`, which waits for the person to ask before a new
sign-in starts.

**The return is named twice, and must agree.** The API takes a
`return_to` only when the environment names it
(`ACME_SIGN_OUT_RETURN_URIS`, set by Terraform to the portal's
`/signed-out`), as it takes a sign-in callback only when it names that.
WorkOS takes it only when the Acme App lists it as a sign-out URI. That
list has no API, so it is a dashboard step, held in
`deployment/workos/environments.yaml` and printed by the bootstrap as a
check.

**The id is read, not verified.** The access token comes straight from
WorkOS over TLS, in the answer to the API's own request. The API reads
the one claim it needs and uses nothing else of the token, so it does
not check the signature. The id is not a secret, and it leaves the
server only inside the logout address the browser follows.

**The device sign-in and the local sign-in sign out of Acme alone.**
The browser that confirms a device code may be another machine's, and a
terminal has no browser to send to a logout. So a device sign-in keeps
no AuthKit session, and `acme logout` ends Acme's session only. The
local sign-in has no provider at all.

## Consequences

A session a release before this one issued carries no AuthKit session,
so its sign-out ends Acme's session alone, as it did. The next sign-in
keeps one.

The column is added nullable, and nothing reads it but the sign-out, so
the release before this one runs on the migrated schema. Nothing is
contracted later.

A sign-out URI missing from the Acme App leaves the person at WorkOS
after it ends their session, not back at the portal. The runbook names
the step and the value per environment.
