# ADR 0019: An API key's hash stays unique after the key is revoked

**Status**: accepted (2026-09-28)
## Context

STO-26: "A uniqueness constraint on a `SoftDeletable` table is a partial
unique index `WHERE deleted_at IS NULL`, so a deleted row frees its key
and the same value can be created again. The memory impl refuses only
among the living, and a contract case creates, deletes, and creates
again."

`api_keys` is soft-deletable: revoking a key sets `deleted_at`. Its
unique index `uq_api_keys_key_hash` is full, not partial.

The rule exists so a deleted slug, address, or membership can be used
again. A key hash is not a name anyone picks. It digests a fresh random
secret the platform mints, so the same hash never comes up twice, and
freeing it on revocation buys nothing.

Keeping the revoked row in the index does buy something. The lookup,
`read_api_key_by_digest`, reads living and revoked rows on purpose. The
manager then refuses a revoked key as revoked (`CredentialExpired`),
not as unknown (`InvalidCredential`), so a caller holding a revoked key
learns why it stopped working. The lookup answers one row or none, and
the full index is what makes that true across the revoked rows too.

## Decision

`uq_api_keys_key_hash` stays a full unique index, and this record is the
deviation from STO-26. The memory impl refuses a hash a revoked row
holds, so the two impls agree.

`[[tool.arch-check.exception]]` in `pyproject.toml` excuses STO-26 on
`om/src/acme/om/tenancy/storage/tables/api_keys.py` and names this
record. Every other soft-deletable table keeps its unique keys partial.

## Consequences

A revoked key's hash is refused on every later write until the purge
removes the row. A fresh secret never repeats a hash, so no real create
is refused by it.

If the lookup ever filters `deleted_at IS NULL`, the reason for the
full index is gone, and the index becomes partial in the same change.

A review that reads STO-26 against the tenancy tables cites this
record. The checker does the same through the exception entry, and
reports the entry as stale the day the index turns partial.
