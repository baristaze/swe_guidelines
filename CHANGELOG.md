# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.63.0 (2026-10-21)

A person signs in through the identity provider alone, on every stack,
and nothing keeps a password. Minor, with two reversals: the guideline's
password sign-in, and the scaffold's sign-in by an address alone.

### Changed

- The guideline keeps no password. A person signs in through the
  identity provider, by an email code, an OAuth provider (Google,
  Microsoft, Apple, GitHub), or the org's single sign-on. A first
  sign-in is the sign-up, an address the provider has not verified is
  refused, and recovery is the provider's email code. Every stack signs
  in through the provider, a local one through its development
  environment. The per-address delay in storage guards the second
  factor. CTX-28, CTX-36, CTX-38, NET-09, NET-11, NET-35, OPS-07,
  OPS-20, DEL-05, and DEL-34 follow (reversal: open sign-up, the
  operator's password reset, and the password sign-in leave the
  guideline).
- The scaffold signs in only through WorkOS, the local stack through
  the Staging application, and answers `503` there without its key. The
  WorkOS runbook names the methods: email code, Google, Microsoft,
  Apple, GitHub, and single sign-on, with passwords off.
- Every test suite signs in through the identity twin, with one helper
  per suite. Each id the twin mints carries a mark of its instance, so
  a fresh twin over a shared database matches no earlier person.
- `make seed` makes each seeded person a plus-address of one base
  address, `SEED_EMAIL` in `.env` (git's `user.email` when unset):
  `<local>+acme+local+ajax@<domain>` owns Ajax, the second org's owner
  is an admin of Ajax and a local operator, and `+personal` is a member.
  It refuses a base on a domain that takes no mail. A copy's CI
  telemetry job sets its own base. An existing checkout sets
  `SEED_EMAIL` in `.env` and runs `make reset`.
- An unmatched portal path shows a "Page not found" page, and a sign-in
  the provider cannot serve says so.

### Added

- `acme-api mint-local-login --email ...`, the local mint: it prints a
  sign-in token for each address, from the local database, and refuses
  at boot outside `local` and `test` (`ACME_LOCAL_LOGIN_MINT`). It has
  no route and no page. The traffic generator mints a run's people with
  it in one process. ADR 0104.

### Removed

- The sign-in by an address alone: `POST /v1/auth/dev-sign-in`,
  `ACME_DEV_SIGN_IN_ENABLED`, the portal's `/login/dev`, and
  `--dev-email` on `acme login`, `acme-ops token`, and `acme-ops work
  requeue` (reversal: ADR 0029 is removed). `SEED_MEMBER_EMAIL` and
  `SEED_ADMIN_EMAIL` are no longer set on their own: the seed derives
  them from the base.
