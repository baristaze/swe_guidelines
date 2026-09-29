# ADR 0028: Sign-in is the identity provider's, and the identity stays Acme's

**Status**: accepted (2026-09-26). The paragraph "The exchange is PKCE,
not a secret", and the key the reconcile command runs with, are
superseded (2026-09-23) by [ADR 0033](0033-the-workos-key-is-the-applications.md):
the key is the Acme App application's, and it is the exchange's client
secret. When `identities.password_hash` is dropped is superseded
(2026-09-25) by
[ADR 0038](0038-a-dead-column-leaves-the-mapping-before-the-table.md):
the hashes are cleared and the column leaves the mapping, and the
release after drops it. The drop is done (2026-09-25): migration
202609290000 takes the column out of the table.

## Context

Acme signed people in with an email and a password it hashed and kept.
Every hard part of that door was Acme's own: a password nobody can
recover without a verified mailbox, a sign-up that anyone could make
with an address that is not theirs, a delay against guessing, and no
single sign-on for a team whose company runs its own identity provider.

The guideline names the other shape (The Network Layer, The Gateway):
"An external identity provider is one more credential kind." The
provider proves who the person is, and hands the tenancy manager an
issuer and a subject; the manager finds or creates the identity keyed
on that pair, through `read_identity_by_issuer_subject`, and everything
after it (the exchange into a tenant, the memberships, the sessions) is
what every person already has. The provider is an integration: one
interface, a real client, and a twin (Twins for External Services).

## Decision

**WorkOS AuthKit is the sign-in.** An email code or link, Google,
GitHub, and an org's single sign-on all come through AuthKit's hosted
page. The portal's `/login` starts it (it is the "initiate login URI"
AuthKit sends a person to when a sign-in did not start at Acme), and
the portal's `/auth/callback` receives the code and hands it to the
API, which exchanges it server-side. The tab that started the sign-in
keeps a random `state` in its session storage and refuses a callback
that does not bring it back, which binds the round trip to that tab.

**The exchange is PKCE, not a secret.** An AuthKit application has a
client secret of its own, which is not the environment's API key: the
exchange with the key is refused (`invalid_client`). So the start
answers a verifier and sends WorkOS only its S256 digest, the tab keeps
the verifier beside its state, and the callback hands both back. A code
is worth nothing without the verifier, and the API holds one credential,
the environment's API key, which it uses for the management calls
(organizations, invitations, the admin portal) and never sends to a
browser. The device sign-in needs no secret either.

**The identity is Acme's.** The provider's answer is an issuer, a
subject, and a verified email. The identity row, its id, the sessions,
the memberships, and the personal org are Acme's own, as they were.
The identity is found by the issuer and the subject; else by the
verified email, and linked from then on (a person the seeding, the
operator plane, or an older release made); else made, with the
person's personal org, in one commit. A first sign-in is the sign-up.
An address the provider has not verified is refused.

**Acme keeps no password.** The password form, the sign-up form, the
password routes, and the operator's password reset go. The column
`identities.password_hash` stays one release, nullable and deferred:
no read names it and no write sets it, because the release before this
one reads it in every identity read and a migration runs before the
services roll. The release after this one drops the column and the
hashes it still holds. The per-email delay stays, and now guards the
second factor.

**The operator's second factor is a step of its own.** A sign-in no
longer carries a code, so `POST /v1/auth/second-factor` takes a sign-in
credential and a TOTP code and answers with a new sign-in that records
it. The operator gate reads it as it did.

**The command line signs in with the provider's device flow.** `acme
login` asks the API to start one, prints the code and the address, and
asks again every few seconds until the person confirms it in any
browser. It works over SSH and on a machine with no browser, needs no
listener on a local port, and is the flow the provider built for a
terminal. The API makes both calls, so the command line talks to
nothing but the API.

**An org's organization at the provider is made lazily.** The first
time an owner or an admin invites someone or opens single sign-on, the
org gets an organization at WorkOS whose external id is the org's id,
and the org keeps its id (`orgs.provider_org_id`). A rerun finds it by
the external id, so it is made once.

**Invitations go through the provider, and the row is Acme's.**
WorkOS sends the email with the link; `core.invitations` holds the
role the person gets, who asked, and where it stands. Accepting is a
sign-in: the provider names the organization, the manager reads the
invitation the provider recorded as accepted by that person (not the
address it was sent to, since an invitation to a company's domain may
be accepted with another address of the same domain), and lands the
membership with its role and the accepted row in one commit. Inviting
is one manager operation, `invite_member`, so a limit on an org's
members is checked in one place, before anything is sent.

**Single sign-on is a team org's, and the admin sets it up.** An owner
or an admin of a team org opens WorkOS's admin portal from the org's
settings and connects their identity provider there. Acme has no SAML
screens. A sign-in through the org's single sign-on makes the person a
member only when their address is in a domain the org verified at
WorkOS (`sso_joins`); anyone else joins by invitation. That is the
conservative rule: the org's own identity provider vouched for the
person, and the org proved it holds the domain, so both sides agree the
person is the org's. A personal org has no single sign-on.

**No webhook.** A sign-in and an invitation both finish in a request
Acme answers, which reads what it needs from WorkOS then. Nothing the
product shows depends on a state WorkOS changes on its own.

**The twin runs the tests and CI; the local stack uses WorkOS's
staging environment.** `IdentityProviderTwinImpl` answers every call of the
interface in memory and is refused at boot outside `local` and `test`.
AuthKit's hosted page is the part that cannot be twinned faithfully,
which is the exception the guideline names for a shared development
tenant: the local stack signs in through the staging environment's
application, whose redirects include the local portal. Without its key
the local stack still runs, and the local sign-in (ADR 0029) is its
door.

**One reconcile command holds WorkOS's configuration.** `acme-ops
workos-bootstrap` reads `deployment/workos/environments.yaml` and
converges each WorkOS environment on it; a person runs it with that
environment's key.

## Consequences

ADR 0026's "no issuer-subject lookup without an external provider" is
closed: `read_identity_by_issuer_subject` is the fifth pre-identity
lookup the guideline lists.

A sign-in is as available as WorkOS is. When it cannot be reached, the
API answers `503` and nobody new signs in; a person already signed in
keeps their session.

Account recovery is WorkOS's: its email code proves the address, so an
operator no longer resets anything.

The stress run on a deployed environment needs people it can sign in
without a browser; the local sign-in is refused there, so the run fails
before it provisions anything, and says why.
