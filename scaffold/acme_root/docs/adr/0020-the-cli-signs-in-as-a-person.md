# ADR 0020: The CLI signs in as a person and holds one session

**Status**: accepted (2026-09-28)

## Context

DEL-17 (The CLI Is Different) has the CLI talk REST with an API key,
attach an idempotency key to every creating call, turn a followed
operation's outcome into an exit code, and trust the operating system's
certificate store. An API key is scoped to one membership, so the CLI
works in one tenant by construction, with no picker and never two
credentials.

A person at a terminal has no API key until they sign in somewhere to
mint one.

## Decision

The CLI signs in as a person and follows the person's rules instead of
the key's. It holds one session, in one org. `acme login` signs the
person in through the identity provider's device flow, or locally with
`--dev-email` ([ADR 0029](0029-a-local-sign-in-by-address.md)). It
exchanges the sign-in for a session in one org and keeps it in
`$ACME_HOME/session.json` (`~/.config/acme` by default).

- `acme login` takes the org by `--org`, or the only membership.
- `acme orgs` lists the person's memberships.
- `acme switch <slug>` presents the kept session to the exchange. The
  API ends it in the same write that makes the new one, and the file
  keeps the new one. So the CLI never holds two sessions, the property
  an API key gives by construction.
- A sign-in over a kept session ends the old one once the new one is
  kept ([ADR 0047](0047-a-sign-in-over-a-held-session-ends-it-from-the-client.md)).

`ACME_TOKEN` may hold an API key, and then the CLI is exactly what
DEL-17 describes. An API key there is the environment's credential: the
CLI never switches it and never ends it.

## Consequences

DEL-17's "with an API key" is a recorded deviation, and a review that
sees `acme login` cites this record. The other three parts of DEL-17
hold as written.

The org choice at sign-in and the switch go through the routes the
portal uses, so the API has one path, not two.

The deviation ends if the CLI moves to API keys minted in the portal.
Then `login`, `orgs`, and `switch` go with it.
