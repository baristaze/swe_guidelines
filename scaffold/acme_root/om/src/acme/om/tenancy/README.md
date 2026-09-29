# Tenancy

The org, the people in it, the credentials they hold, and the operator
plane. This is one of the kinds of thing
[Acme is made of](../../../../README.md).

## What it holds

- **Org**: a name, a slug unique among living orgs, and a kind,
  personal or team. Each identity has exactly one personal org. An org
  that invites people or sets up single sign-on also has an
  organization at the identity provider, made the first time it is
  needed.
- **Identity**: one person across every org: a verified email, the
  provider's issuer and subject, a time zone, and, for an operator, a
  role on the allowlist (`read`, or `write`, which includes read).
- **User**: the identity inside one org, with the name the org sees.
- **Membership**: the user's place in the org, with a role.
- **Invitation**: an email, a role, who asked, and whether it is
  pending, accepted, or revoked.
- **Sign-in** and **session**: a sign-in proves the person and lists
  their orgs; it is exchanged once for a session in one org. A session
  lasts 30 days, or 14 idle.
- **API key**, **socket ticket**, **second factor** (an operator's
  TOTP), and **operator token**: each stored as a digest or sealed,
  never in the clear.
- **Platform size**: the count of orgs, users, and the day's events the
  sweep keeps for the operator plane.

## What can happen

- **Sign in** through the identity provider's hosted page, its device
  flow for the CLI, or, on a developer's machine, by address alone. A
  first sign-in is the sign-up: the identity, its personal org, its
  user, and the owner membership land together.
- **Exchange and switch.** A sign-in becomes a session in one org. A
  session presented to the exchange is a switch, and ends in the same
  write.
- **Sign out.** The credential presented ends. A hosted-page session
  also ends the provider's session in that browser.
- **Manage an org**: create a team org, invite, change a role, remove a
  member, set up single sign-on, list and revoke sessions and API keys.
- **Delete.** An owner deletes a team org, and a person deletes their
  account. Each closes at once for everyone it touches; the provider's
  side goes through the work queue.
- **Operate.** The grant job puts an identity on the allowlist. An
  operator enrols a second factor, mints tokens, and reads or writes
  across orgs.
- **Sweep.** Removed members, ended credentials, and closed
  invitations are purged after their retention.

## The rules

- **The role ladder.** Viewer reads. Member also writes and manages
  their own keys. Admin and owner also manage members. Nothing a person
  issues ranks above the issuer, and the service role is no rung of it.
- **A key never mints a key.** Only a session creates an API key.
- **A sign-in is exchanged once**
  ([ADR 0037](../../../../../docs/adr/0037-a-sign-in-is-exchanged-once.md)).
- **An address is one address in any case**
  ([ADR 0072](../../../../../docs/adr/0072-an-address-is-one-address-in-any-case.md)).
- **A personal org stays its person's.** Its person is never removed
  from it, never changes role in it, and it goes only with the account.
- **The operator plane reads and writes with a token.** A sign-in with
  a verified TOTP code mints one token and does nothing else
  ([ADR 0068](../../../../../docs/adr/0068-an-operator-credential-ends-by-itself.md)).
- **Another org's things do not exist.** A user, a membership, or a key
  of another org is answered as one that never existed.
- **Events carry ids.** A change about a person names ids, never an
  email or a name, so erasing the person erases them.

## How another namespace composes it

Tenancy mints the context every other manager takes. A request's
credential becomes an `OpContext` through `authenticate`, and a claimed
work item's through `service_context`, on the service role with the
person who asked as its attribution. A manager checks what it needs
with `ctx.require(permission)`, and never reads tenancy's tables.

When an org is deleted, the sweep calls each namespace's
`purge_tenant` once the org is past its retention, and tenancy marks
the org purged when nothing is left. A new namespace adds its purge to
the worker's map.
