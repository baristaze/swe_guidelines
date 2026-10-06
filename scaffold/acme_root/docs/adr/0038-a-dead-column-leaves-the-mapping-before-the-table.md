# ADR 0038: A dead column leaves the mapping one release before it leaves the table

**Status**: accepted (2026-09-28)

## Context

A column moves by expand and contract. A migration runs before the
services roll, so the previous release serves the new schema for the
minutes of the roll, and again after a fast rollback. A column may
leave the table only when no statement of the previous release names
it.

Taking it out of every read is not enough. SQLAlchemy's mapper names
every mapped column in the insert it emits: a column the object has no
value for is sent as `NULL`, and one with a server default is read back
in the insert's `RETURNING`. A deferred column is still mapped. So a
drop under the previous release fails every insert it makes, for the
minutes of the roll, and for good if the circuit breaker rolls the
deploy back.

## Decision

**A dead column leaves in two releases.** The first takes it out of the
mapping and keeps it in the table: the column is declared in the
table's `__table_args__` and named in the mapper's
`exclude_properties`. No statement names it, and the schema check still
compares it. The second release drops it with a migration, and the two
lines go.

**The data goes as early as it can.** A value nothing reads is cleared
in the first release when it is a secret, such as a hash. Clearing
needs no drop, and a secret is better gone a release sooner.

**The proof is the previous release's own tests.** Before a contract
lands, the previous release runs its integration tests against the
contracted schema: CI's `release-before` job runs them on every pull
request that changes a migration (ADR 0084). A column any of them names
stays one more release.

## Consequences

A column's removal takes two pull requests in two releases: code and
mapping first, then a migration that drops.

Deferring a column is not the first release. It keeps the column out of
the reads, and every insert still names it.

After a fast rollback to the previous release, that release may write
the column again. Nothing of the current release reads it.
