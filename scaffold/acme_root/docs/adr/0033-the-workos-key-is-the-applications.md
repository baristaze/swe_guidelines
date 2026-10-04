# ADR 0033: The WorkOS key is the application's, never the environment's

**Status**: accepted (2026-09-28)

## Context

A WorkOS environment holds several applications: the default one
WorkOS makes with the environment, and the "Acme App" that Acme signs
people in through (staging `client_STAGING_PLACEHOLDER`, production
`client_PRODUCTION_PLACEHOLDER`). Each application has its own client
id, redirect list, session settings, and API keys.

The key on the environment's API Keys page is the default
application's. As the Acme App's client secret, it makes the code
exchange answer `invalid_client`. For a management call, it acts in the
default application: an invitation sent with it lands its person there,
not in Acme.

## Decision

**The key is the Acme App's.** Each Acme environment holds one WorkOS
credential: an API key made on the Acme App's own API keys tab, in the
WorkOS environment it signs in through. The setting is
`ACME_WORKOS_API_KEY`, and the secret is `acme/<env>/workos_api_key`.

**The code exchange is a confidential client's.** The API holds a
secret, so it sends the application's key as the client secret. Every
management call (organizations, invitations, the Admin Portal link)
carries the same key. One SDK client holds it, with the client id.

**PKCE stays on the browser's sign-in.** The start answers a verifier,
AuthKit sees only its digest, and the exchange sends the verifier
beside the secret. RFC 9700 asks for PKCE on confidential clients too:
it binds a code to the tab that started the sign-in, so a code taken
from the redirect is worth nothing alone.

**The device sign-in stays a public client's.** WorkOS's device flow
takes no client secret. The command line talks only to the API, which
makes both calls with the same client.

**The API proves the key before it serves.** At start it exchanges a
code WorkOS never issued, with the key as the client secret. WorkOS
answers `invalid_grant` for the application's key and `invalid_client`
for any other: the environment's, another application's, or one from
the other WorkOS environment. On `invalid_client` the API refuses to
start and names the application and the tab the key lives on. A WorkOS
out of reach does not stop the start: the log says the key is not
proven, and a sign-in answers `503`. `acme-ops workos-bootstrap` makes
the same check first and stops on it.

**The bootstrap writes the application's redirect list.** With the
application's key, WorkOS's redirect URI API reads and writes that
application's list, and the bootstrap adds a missing redirect under
`--apply`. The default redirect and the tab's other fields have no API,
so the bootstrap prints each as a check.

## Consequences

An invitation Acme sends carries the Acme App's context, and its person
lands on the Acme App's login initiation URI.

A key from the environment's page stops the API at start. So does a
staging key in production, or a production key in staging.

The start makes one call to WorkOS and creates nothing there.

Rotating the key: make a new key on the same tab, write it, roll the
API, then revoke the old one.
