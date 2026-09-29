# ADR 0015: The event keeps `produced_at`, because its public record carries no id

**Status**: accepted (2026-09-20)

## Context

"Naming Entities" says an append-only record such as an audit entry or
a ledger line is `Identifiable` and nothing else: never updated, so no
`updated_at`; never hidden, so no `deleted_at`; and "its birth time is
the one in its id, since every id is a `uuid_v7` with the millisecond
in front, so the 'when' of an audit entry costs no column".

Acme's `Event` is `Identifiable` and carries an explicit
`produced_at`. The rule's premise holds here: every construction of an
`Event` sets `produced_at` to the moment its own id was minted. The
relay appends under the outbox row's id and passes that row's
`created_at`, and both the second relay path and `audit_event` pair a
fresh id with the same `utcnow()`. Nothing orders or filters on the
column; the stream is ordered by `seq`. So inside the object model the
column is redundant, exactly as the rule says.

It is not redundant at the edge. `EventView`, the public record of the
stream, is `{seq, kind, target_id, produced_at, actor_id}` and
**publishes no `id`**. The rule pays for itself only where the id
travels. Dropping the column would leave the published record with no
time and no id to derive one from, which is a loss of information at
the wire rather than the removal of a duplicate. The same field is
also an infra contract: the topic envelope carries `produced_at`, so
the object model would have to derive a moment from an id to keep it,
and no such rule exists in the repository.

## Decision

`Event` keeps `produced_at` for now, and this record is the deviation.

The route out is named, so this is a deferral and not a disagreement.
In order: give `EventView` the record's `id`, which is the field the
rule assumes a reader has; add one pure rule that reads the moment out
of a `uuid_v7`, beside the one that already builds a bound from a
moment; derive the wire's `produced_at` from the id at the service
impl's translation; then drop the field and the column. The middle
steps are worth doing on their own, because they are what make the
rule's premise true at the edge rather than only inside.

## Consequences

A review that reads "Naming Entities" against `om/events/types/event.py`
finds an append-only record with a birth-time column and cites this
record. What Acme carries meanwhile is one redundant column on the
`activity` role and a value that is the id's millisecond either way, so
nothing reads differently.

The last step is irreversible in one direction: a down migration can
re-add the column but not its values. That is another reason the id
reaches the wire first, on its own, before anything is dropped, so the
information exists in the published record before it stops existing in
the row.
