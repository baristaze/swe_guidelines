# ADR 0020: The CLI signs in as a person and holds one session

**Status**: accepted (2026-09-28)

## Context

DEL-17 (Client App Architecture, The CLI Is Different): "The CLI talks
REST with an API key, attaches an idempotency key to every creating
call, turns the outcome of a followed operation into an exit code, and
trusts the operating system's certificate store." One Tenant at a Time
adds: "An API key is scoped to one membership, so the CLI works in one
tenant by construction. It has no picker and never holds two
credentials."

A person at a terminal has no API key until they sign in somewhere to
mint one. `acme login` signs the person in instead: through the
identity provider's device flow, or locally with `--dev-email`
([ADR 0029](0029-a-local-sign-in-by-address.md)). It exchanges the
sign-in for a session in one org and keeps that session in
`$ACME_HOME/session.json` (`~/.config/acme` by default).

## Decision

The CLI signs in as a person and follows the person's rules instead of
the key's. It holds one session, in one org.

- `acme login` takes the org by `--org`, or the only membership.
- `acme orgs` lists the person's memberships.
- `acme switch <slug>` presents the kept session to the exchange. The
  API ends it in the same write that makes the new one, and the file
  keeps the new one. So the CLI never holds two sessions, which is the
  property an API key gives by construction.
- A sign-in over a kept session ends the old one once the new one is
  kept ([ADR 0047](0047-a-sign-in-over-a-held-session-ends-it-from-the-client.md)).

`ACME_TOKEN` may hold an API key, and then the CLI is exactly what
DEL-17 describes. An API key there is the environment's credential: the
CLI never switches it and never ends it.

## Consequences

DEL-17's "with an API key" reads as a recorded deviation. A review that
sees `acme login` cites this record. The other three parts of DEL-17
hold as written.

The CLI has a choice of org at sign-in and a switch, which the section
says it does not need. Both go through the routes the portal uses, so
the API has one path, not two.

The deviation ends if the CLI moves to API keys minted in the portal.
Then `login`, `orgs`, and `switch` go with it.
