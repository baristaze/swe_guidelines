# ADR 0072: An address is one address in any case

**Status**: accepted (2026-09-28)

## Context

A person's own sign-in through the identity provider finds the identity
by the issuer and the subject. Every other path finds a person by the
address: the first sign-in through the provider, which falls back to the
address and links a person the seeding or an operator made; the operator
allowlist's grant, disable, and grant token; adding a member by address,
from the seeding or the operator plane; the local sign-in; and an
invitation: the pending one per address, the refusal of a member, and
the lookup a member's sign-in makes.

Matched as typed, `Dee@example.test` and `dee@example.test` would be two
addresses, and could be two people.

## Decision

**One rule folds an address: all of it in lower case**
(`tenancy.rules.fold_email`). The local part and the domain alike, by
Unicode's full lower-case mapping, which is Python's `str.lower`. This
is a choice. The standard makes the domain case-insensitive and leaves
the local part to the receiving host, and in practice no mail host
tells two cases apart.

**Every address is folded where it is written and where it is looked
up.** An identity is made with its address folded, and a user takes its
identity's address. An invitation keeps the folded address and sends it
to the provider so. The digest (`rules.email_digest`) folds before it
hashes, so every lookup of an identity, and the sign-in delay keyed on
the digest, reads one row for every spelling. The invitation lookups
fold the address they are given.

**The database folds the same way.** `core.identities.email_digest` is
a generated column over `lower(email COLLATE pg_unicode_fast)`. The
builtin `pg_unicode_fast` collation gives Unicode's full mapping
whatever the database's locale, and Postgres 18 and Python 3.14 both
follow Unicode 16.0, so the process and the database compute the same
digest for any spelling. A writer that does not fold meets the unique
index with a second spelling instead of making a second person.

## Consequences

- One address is one identity, one pending invitation in an org, and one
  sign-in delay, however it is typed, on every path.
- An address shows in lower case everywhere: in a member list, in the
  invitation the provider sends, and on the operator plane.
- Rows loaded from elsewhere that hold two people with one address in
  two cases do not fit the unique index. Which one is the real one is a
  person's call, never a migration's.
