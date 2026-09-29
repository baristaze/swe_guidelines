# ADR 0033: The WorkOS key is the application's, never the environment's

**Status**: accepted (2026-09-23). Supersedes the paragraph "The exchange
is PKCE, not a secret" of [ADR 0028](0028-sign-in-is-the-identity-providers.md).

## Context

A WorkOS environment holds several applications. WorkOS made one when it
made the environment, the default application, and Acme signs people in
through another, the "Acme App" (staging
`client_STAGING_PLACEHOLDER`, production
`client_PRODUCTION_PLACEHOLDER`). WorkOS's documentation says each
application gets "its own client ID, session configuration, redirect
URIs, and credentials". An application's credentials are the API keys on
its own API keys tab (Applications, the application, API keys).

The key on the environment's API Keys page is the default application's.
WorkOS says so when it is used for the Acme App: an exchange with it as
the client secret answers `invalid_client`, "You may be using a client
secret from a different application". The same key used as the client
secret of the default application gets past the secret and is refused
only for the code. Its redirect list is the default application's too.

So a key taken from the environment's page is wrong twice. The code
exchange refuses it. And every management call made with it runs in the
default application's context: WorkOS says an invitation sent through
the API keeps "the application context of the API key", so an invitation
sent with the environment's key lands its person in the default
application, not in Acme.

## Decision

**The key is the Acme App application's, never the environment's.**
Each Acme environment holds one WorkOS credential: an API key made on the
Acme App's own API keys tab, in the WorkOS environment it signs in
through. The setting stays `ACME_WORKOS_API_KEY` and the secret stays
`acme/<env>/workos_api_key`; what they hold changes, and a rename would
move a secret for no gain.

**The code exchange is a confidential client's.** The API is a server
that holds a secret, which is what a confidential client is. The
exchange sends the application's key as the client secret, and every
management call (organizations, invitations, the Admin Portal link)
carries the same key. One SDK client holds it: `AsyncWorkOSClient` with
the key and the client id.

**PKCE stays on the browser's sign-in.** The start answers a verifier,
AuthKit sees only its digest, and the exchange sends the verifier beside
the secret. WorkOS checks the verifier when the secret is present too.
RFC 9700 (the OAuth 2.0 Security Best Current Practice) asks for PKCE on
confidential clients as well: it binds a code to the tab that started the
sign-in, so a code taken from the redirect is worth nothing without it.
The WorkOS SDK sends both when both are given, and WorkOS's reference
makes the verifier optional when a secret is present, not forbidden.

**The device sign-in stays a public client's.** WorkOS's device flow
takes no client secret, and the SDK sends none with it. The command line
still talks only to the API, and the API makes both calls with the same
client.

**The API proves the key before it serves.** At start it exchanges a code
WorkOS never issued, with the key as the client secret. WorkOS answers
`invalid_grant` when the key is the application's and `invalid_client`
when it is anything else: the environment's key, another application's,
or a key of the other WorkOS environment. On `invalid_client` the API
refuses to start and names the application and the tab the key lives on.
WorkOS out of reach does not stop the start; the log says the key is
not proven, and a sign-in answers `503` as it would anyway.
`acme-ops workos-bootstrap` makes the same check first and stops on it.

**The bootstrap writes the application's redirect list.** With the
application's key, WorkOS's redirect URI API reads and writes that
application's list. The bootstrap adds a missing redirect under
`--apply` and asks AuthKit again. The default redirect and the Redirects
tab's other fields have no API; the bootstrap names each as a check on
the tab.

## Consequences

An invitation Acme sends carries the Acme App's context. Its person
lands on the Acme App's login initiation URI, not the default
application's.

A key from the environment's API Keys page stops the API at start. So
does a staging key in production, and a production key in staging: each
is another application's key, and WorkOS answers `invalid_client`. That
is the check a crossed environment key needed.

The start makes one call to WorkOS. It creates nothing at WorkOS.

Rotating the key is rotating the application's: make a new key on the
same tab, write it, roll the API, then revoke the old one.
