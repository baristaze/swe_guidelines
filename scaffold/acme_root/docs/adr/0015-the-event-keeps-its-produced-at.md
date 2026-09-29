# ADR 0015: The event keeps `produced_at`, because its public record carries no id

**Status**: accepted (2026-09-28)

## Context

Naming Entities says an append-only record such as an event is
`Identifiable` and carries its time in a field of its own, `created_at`
or a name of its own: never updated, so no `updated_at`; never hidden,
so no `deleted_at`. An id is not a clock (Identifiers), so a record's
time is never read out of its id.

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

`Event` keeps `produced_at`, the time field of its own that Naming
Entities asks of an append-only record, under the event's own name, and
indexed.

## Consequences

A review that reads Naming Entities against
`om/src/acme/om/events/types/event.py` finds the field the rule asks
for. The cost is one column and its index on the `activity` role.
