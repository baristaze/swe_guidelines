# ADR 0011: An audit entry is an event with an audit kind

**Status**: accepted (2026-09-19)

## Context

OM-16 (Namespaces as Swimlanes) says: "Cross-cutting namespaces are
namespaces like any other. Tenancy (organizations, users, memberships,
credentials) and audit (who did what, when, from which app) are
first-class swimlanes with their own types, managers, and storage, not
utilities hanging off the root." "Realtime at the Edge" gives the shape:
"A manager records one event per write through the outbox of Database
Roles; an audit entry is the same shape plus the principal and the app."

Acme has no `audit` namespace. An audit entry is an `Event` in the
`events` namespace with an audit kind (`work.item.failed`,
`outbox.row.failed`), appended to the `activity.events` table, the same
append-only stream that carries every entity event behind a push. The
actor, the request, and the app are on every event already, because
the stream records who produced what; an audit entry adds its facts as
the payload. The shape is a module-level helper beside the events
manager, `audit_event(ctx, event_id, kind, target_id, facts)`, which reads the
actor, the request, and the app from the context and nothing else. The
producers are the two dead letters: the work manager builds its entry
through the helper and appends it through the manager under its
context; the outbox relay runs with no context, so it builds the same
shape from the provenance the row carries and appends it through the
event storage, the path the entity events take. Nothing reads audit
entries apart from what reads the stream: a client replaying after a
gap sees them in order with everything else.

## Decision

One append-only stream carries both. An entity event and an audit
entry are one type, `Event`, in one table, ordered by one `seq`; the
kind tells them apart (`<namespace>.<entity>.<action>` for an entity
event, an audit kind for an entry), and the payload is fixed per kind
(the entity's snapshot, or the entry's facts). The audit shape is the
`audit_event` helper beside the events manager: a producer with a
context builds its entry through it and appends through
`EventsManagerInterface.append_event`, so the principal and the app come from
the context and never from the caller. The relay is the one producer
without a context, and it builds the same shape from the row's
provenance and appends through the event storage, as it does for the
entity events; the manager's `append` is for audit entries alone.

An `audit` namespace of its own, with its types, manager, and storage,
appears when audit gains a reader of its own: an operator screen that
lists who did what, or an export. The kinds and the payloads recorded
today move with it unchanged, since the entry's shape is already the
event's plus the facts.

## Consequences

OM-16's "first-class swimlane" is met by the events namespace and this
record: a review that looks for an audit namespace and finds none
cites it. What Acme gives up is a place to query audit alone: an
audit entry is read by replaying the stream, and the kind is the
filter. The events table's retention is the audit retention, and a
purge of the stream is a purge of the audit; an audit namespace with
its own table is the shape that separates them, and that is the day
it is built. A producer with a context goes through `audit_event` and
the manager's `append_event`; an `Event` with an audit kind constructed by
hand anywhere but the relay is a finding.
