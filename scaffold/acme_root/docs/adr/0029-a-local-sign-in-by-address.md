# ADR 0029: A local sign-in by address alone, refused in a deployed environment

**Status**: accepted (2026-09-28)

## Context

Sign-in is the identity provider's
([ADR 0028](0028-sign-in-is-the-identity-providers.md)): a person goes
through a hosted page in a browser and comes back with a code. Several
callers need a person without that round trip: `make seed`, the traffic
generator, and the unit, API, and integration tests, which sign in
hundreds of people. None of them has a browser, a mailbox, or a WorkOS
account, and CI has no WorkOS key.

What a Process Refuses has a setting that is only safe locally refused
by the process at boot, not by a checklist.

## Decision

The API has one more sign-in door, the local sign-in:
`POST /v1/auth/dev-sign-in` with an email and an optional name answers
what every sign-in answers. The identity that holds the address is
signed in, or a new one is made with its personal org, as a first
sign-in makes one. The portal shows it at `/login/dev` when its runtime
config says so, which only the local build does. The command line has
`acme login --dev-email`.

It is off unless `ACME_DEV_SIGN_IN_ENABLED=true`. The API settings refuse
that value at boot, naming the setting, unless `ACME_ENVIRONMENT` is
`local` or `test`, so a staging or production process never starts
with it. Off, the route answers `404` exactly as a route that does not
exist does, before the body is read. The manager refuses it too, so a
process built some other way cannot reach it either.

## Consequences

A developer's stack signs anyone in by typing an address. That is safe
only because nothing but a developer's machine and CI can turn it on.

A traffic run against a deployed environment has no way to sign in its
people, so it refuses to start there and says why.

The name says what it is: `dev-sign-in` in the route, `dev_sign_in` in
the manager and the settings. A search for it finds every place it is
reachable.
