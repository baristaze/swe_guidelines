# ADR 0019: An API key's hash stays unique after the key is revoked

**Status**: accepted (2026-09-28)

## Context

STO-26 makes a unique key on a `SoftDeletable` table a partial index
`WHERE deleted_at IS NULL`, so a deleted slug, address, or membership
frees its value for reuse. The memory impl refuses only among the
living, and a contract case creates, deletes, and creates again.
`api_keys` is soft-deletable: revoking a key sets `deleted_at`.

A key hash is not a name anyone picks. It digests a fresh random secret
the platform mints, so the same hash never comes up twice, and freeing
it on revocation buys nothing. Keeping the revoked row in the index
buys something. The lookup, `read_api_key_by_digest`, reads living and
revoked rows on purpose, so the manager refuses a revoked key as
revoked (`CredentialExpired`), not as unknown (`InvalidCredential`). A
caller holding a revoked key learns why it stopped working. The full
index is what keeps that lookup to one row or none.

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

A review that reads STO-26 against the tenancy tables cites this
record. If the lookup filters `deleted_at IS NULL`, the full index has
no reason left, and it turns partial in the same change. The checker
then reports the exception entry as stale.
