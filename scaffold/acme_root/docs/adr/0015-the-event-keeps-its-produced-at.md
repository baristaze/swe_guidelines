# ADR 0015: The event keeps `produced_at`, because its public record carries no id

**Status**: accepted (2026-09-28)

## Context

Naming Entities says an append-only record such as an audit entry is
`Identifiable` and nothing else: never updated, so no `updated_at`;
never hidden, so no `deleted_at`; and "its birth time is the one in its
id, since every id is a `uuid_v7` with the millisecond in front, so the
'when' of an audit entry costs no column".

`Event` is `Identifiable` and carries an explicit `produced_at`. Every
construction sets it to the moment its id was minted, so inside the
object model the value is the id's millisecond.

It is not redundant at the edge. `EventView`, the public record of the
stream, is `{seq, kind, target_id, produced_at, actor_id}` and publishes
no `id`. Dropping the column would leave the published record with no
time and no id to derive one from. The topic envelope carries
`produced_at` too. And two readers filter on the column through
`ix_events_produced_at`: the trim of the stream
([ADR 0040](0040-the-event-stream-has-a-floor.md)) and the count of the
day's events behind the platform's size
([ADR 0074](0074-the-platforms-size-is-a-tally-the-sweep-keeps.md)).

## Decision

`Event` keeps `produced_at`, and this record is the deviation from
Naming Entities.

The route out, in order: give `EventView` the record's `id`, and add a
pure rule that reads the moment out of a `uuid_v7`. Then move the
readers to the id, derive the wire's `produced_at` from it, and drop
the column.

## Consequences

A review that reads Naming Entities against
`om/src/acme/om/events/types/event.py` finds an append-only record with
a birth-time column and cites this record. The cost is one column and
its index on the `activity` role.

The last step cannot be undone: a down migration can add the column
back but not its values. So the id reaches the wire first, on its own,
before anything is dropped.
