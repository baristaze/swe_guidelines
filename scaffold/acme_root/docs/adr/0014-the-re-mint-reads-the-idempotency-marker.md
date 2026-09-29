# ADR 0014: Tenancy's re-mint reads the idempotency marker in its own statement

**Status**: accepted (2026-09-20)

## Context

"Shape of an Operation" says the re-mint of a secret on a rerun is the
one write of a rerun that changes what is stored, so "the statement
writes the digest only while the marker still holds the attempt making
the write", and "The Gateway" repeats it: `finish`, the release, and
that re-mint are conditional on the attempt token. `NET-24` and
`NET-25` rate a re-mint with no such guard `high`.

That is one statement that sees two namespaces' rows. Tenancy owns the
api key; idempotency owns the marker. Before this record the re-mint
fenced on `created_at` instead, comparing the stored row's birth time
with the attempt's, which admits the clock-equal case and, under skew
beyond the pending lease, admits the zombie outright. The repository's
own log said so: an attempt whose `finish` was refused for having lost
the marker re-minted anyway, handed out a second secret, and silently
invalidated the one the retry had already returned to the client.

## Decision

The re-mint reads the marker in its own `WHERE`. Postgres carries an
`EXISTS` over `idempotency_records` on `(org_id, target_id,
attempt_id, status IS NULL)`; the memory impl asks an
`AttemptFenceInterface` the storage root injects, the twin of the
`OutboxLandingInterface` it already injects to land outbox rows. An
attempt with no marker, which is a request that carried no
`Idempotency-Key`, does not re-mint at all.

The fence is keyed on the api key's own id rather than on the caller's
`Idempotency-Key` string, so it is tighter (the marker must be the one
for this id, not merely one this principal holds) and no HTTP header
value enters the tenancy namespace.

This is the second sanctioned crossing of a swimlane, and it rests on
the sentence that licenses the first: "Two system rows are touched by
every namespace: the outbox row, which announces a write, and the
idempotency marker, which owns a retry." Tenancy already lands the
outbox row inside its own statement; it now reads the marker in the
same one.

## Consequences

"No cross-role statements" still holds, and it is the condition this
decision depends on: `api_keys` and `idempotency_records` are both in
the `core` role, so the statement touches one role. That is pinned by a
test, `test_the_re_mint_reads_the_marker_inside_one_role`, not by this
paragraph. Moving either table to a role of its own breaks the fence,
and the test says so before a deployment does.

The fence reads `(org_id, target_id)`, which no index served, so the
markers table gains `ix_idempotency_records_org_id_target_id`; without
it the fence scans every marker inside the retention under a write.

The attempt travels from the gateway to storage as an `Attempt` value
object rather than two bare ids of the same type, so a caller cannot
swap them. The handler a creating `POST` runs now takes that object
instead of the target id alone; both call sites follow, and the one
that needs no token takes `attempt.target_id`.
