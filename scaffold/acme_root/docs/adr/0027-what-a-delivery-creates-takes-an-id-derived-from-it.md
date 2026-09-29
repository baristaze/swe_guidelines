# ADR 0027: What a delivery creates takes an id derived from it

**Status**: accepted (2026-09-28)
## Context

OM-12 (Identifiers): "Every id is `uuid_v7`, minted above storage with
`new_id()`." Idempotency on the Consumer Side says a message from
outside carries a key derived from the provider's delivery, a UUID v5
over the provider's name and its delivery id, and the handler dedupes
on it. The key lives on the row the effect produces, or the marker and
the effect are one atomic write.

The queue is at least once, so the same delivery can reach its handler
twice. A marker in a table of its own would be a second write beside
the effect.

## Decision

A record an outside delivery creates takes an id derived from the
delivery: `derived_id(key, at)` in `acme.om.base`. It is a v7 whose time
is `at` and whose random bits come from the key. The key is the
delivery's UUID v5, and `at` is when the provider made the delivery.

The one caller is the identity provider's webhook. The maintenance
worker records each delivery that names an org as an audit entry,
`identity.event.received`
([ADR 0011](0011-audit-entries-are-events.md)), under the derived id.
The append is idempotent on the event's id, so a copy the queue hands
over again appends nothing.

This is the deviation from OM-12. Every other id is `new_id()`.

## Consequences

The effect carries its own key, with no marker table and no column. The
id still sorts by time and still reads as a v7.

The id's random bits are not random: anyone who knew a delivery's key
and its time could compute the id. Neither leaves the platform, and an
id is never a credential.

A new provider whose deliveries create records uses the same function
with its own key.
