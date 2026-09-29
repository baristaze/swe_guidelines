# ADR 0028: Sign-in is the identity provider's, and the identity stays Acme's

**Status**: accepted (2026-09-28)
## Context

Every hard part of a sign-in door is a product of its own: a password
nobody recovers without a verified mailbox, a sign-up anyone can make
with an address that is not theirs, a delay against guessing, and single
sign-on for a team whose company runs its own identity provider.

The guideline names the other shape (The Network Layer, The Gateway):
"An external identity provider is one more credential kind." The
provider proves who the person is and hands the tenancy manager an
issuer and a subject. The manager finds or creates the identity keyed
on that pair, through `read_identity_by_issuer_subject`. Everything
after it (the exchange into a tenant, the memberships, the sessions) is
what every person has. The provider is an integration: one interface, a
real client, and a twin (Twins for External Services).

## Decision

**WorkOS AuthKit is the sign-in.** An email code or link, Google,
GitHub, and an org's single sign-on all come through AuthKit's hosted
page. The portal's `/login` starts it through `POST /v1/auth/sign-in`,
and the portal's `/auth/callback` hands the code to
`POST /v1/auth/callback`, where the API exchanges it server-side. The
tab that started the sign-in keeps a random `state` in its session
storage and refuses a callback that does not bring it back, which binds
the round trip to that tab.

**The exchange is a confidential client's, with PKCE.** The API holds
the application's key and sends it as the client secret, beside the
PKCE verifier the tab kept
([ADR 0033](0033-the-workos-key-is-the-applications.md)). The key never
reaches a browser.

**The identity is Acme's.** The provider's answer is an issuer, a
subject, and a verified email. The identity row, its id, the sessions,
the memberships, and the personal org are Acme's own. The identity is
found by the issuer and the subject; else by the verified email, and
linked from then on (a person the seeding or the operator plane made);
else made, with the person's personal org, in one commit. A first
sign-in is the sign-up. An address the provider has not verified is
refused.

**Acme keeps no password.** There is no password form, no sign-up form,
and no reset. The per-address delay guards the second factor.

**The operator's second factor is a step of its own.**
`POST /v1/auth/second-factor` takes a sign-in and a TOTP code, and
answers a new sign-in that records it.

**The command line signs in with the provider's device flow.**
`acme login` asks the API to start one (`POST /v1/auth/device`), prints
the code and the address, and asks again every few seconds
(`/auth/device/token`) until the person confirms it in any browser. It
works over SSH and on a machine with no browser, and the command line
talks to nothing but the API.

**An org's organization at the provider is made lazily.** The first
time an owner or an admin invites someone or opens single sign-on, the
org gets an organization at WorkOS whose external id is the org's id,
and the org keeps its id (`orgs.provider_org_id`). A rerun finds it by
the external id, so it is made once.

**Invitations go through the provider; the row is Acme's.** WorkOS
sends the email with the link. `core.invitations` holds the role the
person gets, who asked, and where it stands. Accepting is a sign-in: the
manager reads the invitation the provider recorded as accepted by that
person, and lands the membership with its role and the accepted row in
one commit. Inviting is one manager operation, `invite_member`.

**Single sign-on is a team org's, and its admin sets it up.** An owner
or an admin opens WorkOS's admin portal from the org's settings
(`POST /v1/orgs/current/sso-link`) and connects their identity provider
there. A sign-in through the org's single sign-on makes the person a
member only when their address is in a domain the org verified at
WorkOS (`rules.sso_joins`); anyone else joins by invitation. A personal
org has no single sign-on.

**The provider's webhook is checked at the edge and handled by the
worker.** `POST /webhooks/identity` checks the provider's signature over
the timestamp and the body (`WorkOS-Signature`, a three-minute window)
before anything is queued, and refuses a delivery that fails. It queues
the delivery on `Queues.WEBHOOKS`, keyed by a UUID v5 over the event's
id. The maintenance worker records each delivery that names an org as
the audit entry `identity.event.received` in that org's stream, once
per delivery ([ADR 0027](0027-what-a-delivery-creates-takes-an-id-derived-from-it.md)).
A delivery that names no org, or an org that is gone, is dropped. The
route takes no credential and no rate limit: it has no credential and no
path token to count. Its secret, `ACME_WORKOS_WEBHOOK_SECRET`, is a
process credential injected at start.

**The twin runs the tests and CI; the local stack uses WorkOS's staging
environment.** `IdentityProviderTwinImpl` answers every call of the
interface in memory and is refused at boot outside `local` and `test`.
AuthKit's hosted page cannot be twinned faithfully, which is the
exception the guideline names for a shared development tenant: the local
stack signs in through the staging environment's application, whose
redirects include the local portal. Without its key the local stack
still runs, and the local sign-in
([ADR 0029](0029-a-local-sign-in-by-address.md)) is its door.

**One reconcile command holds WorkOS's configuration.**
`acme-ops workos-bootstrap` reads `deployment/workos/environments.yaml`
and converges each WorkOS environment on it. A person runs it with that
environment's key.

## Consequences

`read_identity_by_issuer_subject` is the fifth lookup the guideline
lists before an identity is known.

A sign-in is as available as WorkOS is. When it cannot be reached, the
API answers `503` and nobody new signs in. A person already signed in
keeps their session.

Account recovery is WorkOS's: its email code proves the address, so an
operator resets nothing.

A traffic run against a deployed environment needs people it can sign
in without a browser. The local sign-in is refused there, so such a run
refuses to start and says why.
