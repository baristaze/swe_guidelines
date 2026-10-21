# ADR 0104: The local stack signs in through the provider, and machines through a local mint

**Status**: accepted (2026-10-21)

## Context

Sign-in is the identity provider's, and Acme keeps no password
([ADR 0028](0028-sign-in-is-the-identity-providers.md)). A door that
admits a person by an address alone is a password with no secret:
whoever reaches it is whoever they type. The local stack is where a
developer and an agent see what a person sees. A local sign-in that
differs from the real one hides the real one's defects, an admin's
included.

Some callers of a local stack have no browser and no mailbox: the
traffic generator and its stress runs, a layer's browser checks, and its
demo recorders. Each needs a seeded person signed in. The tests and CI
have the identity provider's twin, which issues the code a browser
brings back (Twins for External Services).

## Decision

**Every stack signs a person in through the provider.** The local stack
signs in through WorkOS's Staging environment, whose redirects list the
local portal's callbacks. The methods are the same everywhere: an email
code, Google, Microsoft, Apple, GitHub, or the org's single sign-on, with
passwords off. Without the application's key the local stack runs and
nobody signs in: a sign-in answers `503`. An admin signs in to the
operator plane locally as everywhere, through the device flow and then
the second factor.

**No route signs a person in by address.** Every route under `/v1/auth`
that answers a login or a session takes the provider's code, a device
the person confirmed, or a login made from one of those. A test pins the
set.

**The tests sign in through the twin.** Each suite has one helper. It has
the twin issue a code and finishes it through the real callback, or
confirms a device for the command line.

**Machines take a login from the local mint.**
`acme-api mint-local-login --email <address>...` prints a login for
each address, one a line, on the local database. It finds the identity
by the address, or makes it with its personal org, as a first sign-in
does. It has no route and no page. It turns on the manager's
`local_login_mint` option for its own process alone. The API settings
refuse `ACME_LOCAL_LOGIN_MINT` at boot outside `local` and `test`, naming
the setting, so the mint refuses a deployed environment before it builds
or writes anything. A caller mints every person it needs with one
process.

**Seeded people have real addresses.** `make seed` derives each person
from one base address, `SEED_EMAIL`, which `.env` sets and nothing
commits; without it, git's `user.email`. Each person is
`<local>+acme+local+<tag>@<domain>`. The owner of the first org takes its
slug. The owner of the second org, who is an admin of the first and an
operator on the local allowlist, takes the second org's slug. The member
takes `personal`. WorkOS delivers each one's code to the base's mailbox,
so the seed refuses a base on a domain that takes no mail (`.test`,
`.example`, `.invalid`, `.localhost`).

## Consequences

The local stack shows the real sign-in, defects and all. A developer
with no WorkOS key reaches every page but the signed-in ones; a test
reaches them through the twin, and a machine through the mint.

The mint is a credential by address. It is safe because it runs only on
a local or test database and is no part of a process that serves. Its
name says what it is, so a search for it finds every place it runs.

A traffic run against a deployed environment has no way to sign in its
people, so it refuses to start there and says why.
