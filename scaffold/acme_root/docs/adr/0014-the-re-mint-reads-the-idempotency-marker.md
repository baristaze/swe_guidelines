# ADR 0014: Tenancy's re-mint reads the idempotency marker in its own statement

**Status**: accepted (2026-09-28)

## Context

Shape of an Operation says the re-mint of a secret on a rerun is the
one write of a rerun that changes what is stored, so "the statement
writes the digest only while the marker still holds the attempt making
the write". The Gateway repeats it: `finish`, the release, and the
re-mint are conditional on the attempt token. NET-24 and NET-25 rate a
re-mint with no such guard `high`.

That is one statement that sees two namespaces' rows. Tenancy owns the
API key; idempotency owns the marker. A fence on anything weaker, such
as the row's birth time, admits an attempt that lost its marker: it
re-mints, hands out a second secret, and silently invalidates the one
the retry already returned.

## Decision

The re-mint reads the marker in its own `WHERE`. In Postgres,
`issue_api_key` carries an `EXISTS` over `idempotency_records` on
`(org_id, target_id, attempt_id, status IS NULL)`. The memory impl asks
an `AttemptFenceInterface` the storage root injects, the twin of the
`OutboxLandingInterface` it injects to land outbox rows. An attempt with
no marker, a request that carried no `Idempotency-Key`, does not
re-mint at all.

The fence is keyed on the API key's own id, not on the caller's
`Idempotency-Key` string. So it is tighter (the marker must be the one
for this id), and no HTTP header value enters the tenancy namespace.

This is the second sanctioned crossing of a swimlane. It rests on the
sentence that licenses the first: "Two system rows are touched by every
namespace: the outbox row, which announces a write, and the idempotency
marker, which owns a retry." Tenancy lands the outbox row inside its own
statement, and it reads the marker in the same one.

## Consequences

"No cross-role statements" still holds, and this decision depends on
it: `api_keys` and `idempotency_records` are both in the `core` role.
`test_the_re_mint_reads_the_marker_inside_one_role` pins it. Moving
either table to another role breaks the fence, and the test says so
before a deployment does.

The fence finds its marker through `ix_idempotency_records_attempt_id`,
the partial index on the pending markers by attempt, which the purge
reads too.

The attempt travels from the gateway to storage as an `Attempt` value
object, not as two bare ids of the same type, so a caller cannot swap
them.
