# ADR 0072: An address is one address in any case

**Status**: accepted (2026-09-28)

## Context

An address reaches a person in many ways. A person's own sign-in
through the identity provider finds the identity by the issuer and the
subject first, and the address decides every other way:

- the first sign-in through the provider, which falls back to the
  address, and links a person the seeding or an operator made;
- the operator allowlist: its grant, its disable, and the grant job's
  token;
- adding a member by address, from the seeding or the operator plane;
- the local sign-in;
- an invitation: the pending one per address, the refusal of a member,
  and the lookup a member's sign-in makes.

Matched as typed, `Dee@example.test` and `dee@example.test` would be two
addresses, and could be two people.

The domain of an address is case-insensitive by the standard. Its local
part is the receiving host's to interpret, and in practice no mail host
tells two cases apart.

## Decision

**One rule folds an address: all of it in lower case**
(`tenancy.rules.fold_email`). The local part and the domain alike, by
Unicode's full lower-case mapping, which is Python's `str.lower`. This is
a choice. An address that differs only in case is taken for the same
mailbox, which is how every mail host we know treats it.

**Every address is folded where it is written and where it is looked
up.** An identity is made with its address folded, and a user takes its
identity's address. An invitation keeps the folded address and sends it
to the provider so. The digest (`rules.email_digest`) folds before it
hashes, so every lookup of an identity, and the sign-in delay keyed on
the digest, reads one row for every spelling. The invitation lookups
fold the address they are given.

**The database folds the same way.** `core.identities.email_digest` is a
generated column over `lower(email COLLATE pg_unicode_fast)`. The
builtin `pg_unicode_fast` collation gives Unicode's full mapping whatever
the database's locale, and on Postgres 18 and Python 3.14 both follow
Unicode 16.0. So the digest the process computes and the one the database
computes agree for any spelling. A writer that does not fold meets the
unique index with a second spelling instead of making a second person.

## Consequences

- One address is one identity, one pending invitation in an org, and one
  sign-in delay, however it is typed, on every path.
- An address shows in lower case everywhere: in a member list, in the
  invitation the provider sends, and on the operator plane.
- Rows loaded from elsewhere that hold two people with one address in
  two cases do not fit the unique index. Which of the two is the real one
  is a person's call, never a migration's.
