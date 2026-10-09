# ADR 0090: An integration acts as the member its proven address names

**Status**: accepted (2026-10-08)

## Context

An integration brings in what a person does at a provider the org
connected: a request they write there, which a signed delivery carries
here and which names them by their address. The work it asks for is
the person's, so it runs under their authority in the org. The
integration has to turn the address into that person, with no
credential of theirs.

Every other stage above the request stage comes from a credential its
caller presents. An integration presents the provider's signature,
which proves the delivery and not the person. And an address alone
proves nobody: the seeding, the operator plane, and the local sign-in
each make a person from an address someone typed.

## Decision

**`member_context` is a transition of the tenancy manager.** It takes
the request stage, the org, and the address, and returns the
`TenantContext` of the live member of that org whose identity holds the
address, or none. It reads the identity by the address's digest, so
every spelling finds one person (ADR 0072). The org, the user, and the
membership must all be live.

**Only a proven address counts.** The identity provider links a person,
by its issuer and subject, only after it has verified the address it
vouches for. An identity with no link holds an address someone typed,
and gets none until its person signs in through the provider.

**The member's own role, on the credential kind `INTERNAL`.** The
context carries the member's role, its permissions, and their teams, so
the call does what the person may and nothing more. It is never the
service role: the person asked, and the person acts. Its credential is
internal, since no credential of the person's was presented, so an
operation only a signed-in person may do, such as making an org or
deleting the account, refuses it as it refuses a key.

**None, never a refusal.** A person who is unknown, unproven, no member
of the org, or removed is no failed credential: the provider's
signature held. The handler gets none, acts as nobody in the org, and
may answer the person at the provider.

**The caller is an integration's handler alone.** It takes the org from
the integration its token found, and the address from the payload its
provider signed, after the signature check. An address a person typed,
one read from a stored row, or one from a request's body never reaches
the call.

## Consequences

- An integration's work carries the person who asked as its actor, with
  their authority at the moment the delivery arrives, and a member who
  has left or been removed stops acting at once.
- A member the seeding or an operator added cannot be acted for until
  they sign in through the provider once. A person who signs in locally
  by address alone stays unproven; a local run proves one through the
  provider's twin.
- The address decides who the person is, as it does at a sign-in: when
  an address moves to another person at the provider, their first
  sign-in links the identity to them, and the integration follows it.
- No credential stands behind the context, so nothing revokes it. It
  lives for the one delivery's handling and is asked for again on the
  next.
