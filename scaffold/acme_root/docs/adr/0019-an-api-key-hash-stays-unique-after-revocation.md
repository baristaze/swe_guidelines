# ADR 0019: An api key's hash stays unique after the key is revoked

**Status**: accepted (2026-09-21), amended (2026-09-22): the lookup
is `read_api_key_by_digest`.

## Context

STO-26 says: "A uniqueness constraint on a `SoftDeletable` table is a
partial unique index `WHERE deleted_at IS NULL`, so a deleted row
frees its key and the same value can be created again. The memory
impl refuses only among the living, and a contract case creates,
deletes, and creates again."

`api_keys` is soft-deletable: revoking a key sets `deleted_at`. Its
unique index `uq_api_keys_key_hash` is full, not partial. The memory
impl matches it: `_require_key_hash_free` refuses a hash any row holds,
revoked or not.

The rule exists so a deleted slug, email, or membership can be used
again. A key hash is not a name anyone picks. It digests a fresh
random secret the platform mints, so the same hash is never created a
second time. Freeing it on revocation buys nothing.

Keeping the revoked row in the index does buy something. The lookup,
`read_api_key_by_digest` (named `read_api_key_by_hash` until
0.31.0's names for the pre-identity lookups), reads by hash over living and revoked rows on
purpose. The manager then refuses a revoked key as revoked
(`CredentialExpired`), not as unknown (`InvalidCredential`), so a
caller holding a revoked key learns why it stopped working. The lookup
answers one row or none, and the full index is what makes that true
across the dead rows too.

## Decision

`uq_api_keys_key_hash` stays a full unique index, and this record is
the deviation. The memory impl keeps refusing a hash a revoked row
holds, so the two impls agree.

`[[tool.arch-check.exception]]` in `pyproject.toml` excuses STO-26 on
`om/src/acme/om/tenancy/storage/tables/api_keys.py` and names this
record. Every other soft-deletable table keeps its unique keys partial.

## Consequences

A revoked key's hash is refused on every later write until the purge
removes the row. Since a fresh secret never repeats a hash, no real
create is refused by it.

The lookup can keep reading revoked rows and answering "revoked". If
the lookup ever filters `deleted_at IS NULL`, the reason for the full
index is gone, and the index becomes partial in the same change.

A review that reads STO-26 against the tenancy tables finds a full
unique index on a soft-deletable table and cites this record. The
checker does the same through the exception entry, and it reports the
entry as stale the day the index turns partial.
