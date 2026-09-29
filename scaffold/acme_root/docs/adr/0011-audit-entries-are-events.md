# ADR 0011: An audit entry is an event with an audit kind

**Status**: accepted (2026-09-28)
## Context

OM-16 (Namespaces as Swimlanes): "Cross-cutting namespaces are
namespaces like any other. Tenancy (organizations, users, memberships,
credentials) and audit (who did what, when, from which app) are
first-class swimlanes with their own types, managers, and storage, not
utilities hanging off the root." Realtime at the Edge gives the shape:
"an audit entry is the same shape plus the principal and the app."

Every event already carries the actor, the request, and the app, since
the stream records who produced what. An audit entry adds only its
facts.

## Decision

One append-only stream carries both. An entity event and an audit entry
are one type, `Event`, in one table, `activity.events`, ordered by one
`seq`. The kind tells them apart: `<namespace>.<entity>.<action>` for an
entity event, an audit kind for an entry. The payload is fixed per
kind.

The audit kinds are four:

| Kind | Written by | Records |
|------|------------|---------|
| `work.item.failed` | the work manager | a work item failed for good |
| `work.item.requeued` | the operator plane | an operator sent a failed item back |
| `outbox.row.failed` | the outbox relay | an outbox row failed for good |
| `identity.event.received` | the maintenance worker | a delivery of the identity provider's webhook that names the org |

The shape is the `audit_event(ctx, event_id, kind, target_id, facts)`
helper beside the events manager. It reads the tenant, the actor, the
request, and the app from the context and nothing else, so the caller
never claims its own provenance. A producer with a context builds its
entry through it and appends through
`EventsManagerInterface.append_event`. The relay and the operator's
requeue build the same shape from the provenance they hold and append
it through the event storage.

Every audit payload carries ids, kinds, counts, and the error a failure
left, never a person's address or name. So an erasure has nothing to
redact in the stream (STO-34).

## Consequences

OM-16's "first-class swimlane" is met by the events namespace and this
record. A review that looks for an audit namespace and finds none cites
it.

An audit entry is read by replaying the stream, filtered by kind. There
is no place to query audit alone. The events table's retention is the
audit's retention
([ADR 0040](0040-the-event-stream-has-a-floor.md)), and a purge of the
stream is a purge of the audit.

An `audit` namespace of its own, with its types, manager, and table,
comes when audit gains a reader of its own: an operator screen that
lists who did what, or an export. The kinds and the payloads move with
it unchanged. An `Event` with an audit kind built by hand anywhere but
the relay and the operator's requeue is a finding.
