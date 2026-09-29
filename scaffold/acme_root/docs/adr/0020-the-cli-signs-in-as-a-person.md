# ADR 0020: The CLI signs in as a person and holds one session

**Status**: accepted (2026-09-21)

## Context

DEL-17 (Client App Architecture, The CLI Is Different) says: "The CLI
talks REST with an API key, attaches an idempotency key to every
creating call, turns the outcome of a followed operation into an exit
code, and trusts the operating system's certificate store." Since
v0.26.0, "One Tenant at a Time" adds: "An API key is scoped to one
membership, so the CLI works in one tenant by construction. It has no
picker and never holds two credentials."

`acme` does not start from an API key. `acme login` takes an email
and a password, exchanges the sign-in for a session in one org, and
keeps that session in `$ACME_HOME/session.json`. `ACME_TOKEN` may
hold an API key instead, and then the CLI is exactly what the section
describes. The session is what a person at a terminal has without first
signing in to the portal to mint a key, and it is what `listen` and the
recorded demo run under.

## Decision

The CLI keeps signing in as a person, and follows the person's rules
instead of the key's. It holds one session, in one org. `login` takes
the org by `--org`, or the only membership. `acme orgs` lists the
memberships under the identity stage. `acme switch <slug>` presents
the kept session to the exchange, the API ends it in the same write,
and the file keeps the new one. So the CLI never holds two sessions,
which is the property the section's API key gives by construction. An
API key in `ACME_TOKEN` is never switched: it is the environment's
credential, and it is scoped to its membership.

## Consequences

DEL-17's "with an API key" reads as a recorded deviation, not a
finding: a review that sees `acme login` cites this record. The other
three parts of DEL-17 hold as they are. The CLI has a choice of org at
sign-in and a switch, which the section says it does not need; both go
through the same routes the portal uses, so the API has one path, not
two. The deviation ends if the CLI moves to API keys minted in the
portal, and then `orgs` and `switch` go with it.
