# ADR 0011: An audit entry is a change event with an audit kind

**Status**: accepted (2026-09-28)

## Context

OM-16 (Namespaces as Swimlanes) makes audit (who did what, when, and
from which app) a kind of change event: an audit entry is a `ChangeEvent` with an
audit kind, in the change events namespace's stream, until audit gains a
reader of its own or must be kept longer than the stream. Every change event
already carries the actor, the request, and the app, so an audit entry
adds only its facts.

## Decision

One append-only stream carries both. An entity change event and an audit entry
are one type, `ChangeEvent`, in one table, `activity.change_events`, ordered by one
`seq`. The kind tells them apart: `<namespace>.<entity>.<action>` for an
entity change event, an audit kind for an entry. The payload is fixed per
kind.

The audit kinds are four:

| Kind | Written by | Records |
|------|------------|---------|
| `work.item.failed` | the work manager | a work item failed for good |
| `work.item.requeued` | the operator plane | an operator sent a failed item back |
| `outbox.row.failed` | the outbox relay | an outbox row failed for good |
| `identity.event.received` | the maintenance worker | a delivery of the identity provider's webhook that names the org |

The shape is the `audit_change_event(ctx, event_id, kind, target_id, facts)`
helper beside the change events manager. It reads the tenant, the actor, the
request, and the app from the context and nothing else, so the caller
never claims its own provenance. A producer with a context builds its
entry through it and appends through
`ChangeEventsManagerInterface.append_change_event`. The relay and the operator's
requeue build the same shape from the provenance they hold and append
it through the change event storage.

Every audit payload carries ids, kinds, counts, and the error a failure
left, never a person's address or name, so an erasure has nothing to
redact in the stream (STO-34).

## Consequences

An audit entry is read by replaying the stream, filtered by kind; there
is no place to query audit alone. The change events table's retention is the
audit's retention
([ADR 0040](0040-the-event-stream-has-a-floor.md)), and a purge of the
stream is a purge of the audit.

An `audit` namespace of its own, with its types, manager, and table,
comes when audit gains a reader of its own: an operator screen that
lists who did what, or an export. The kinds and the payloads move with
it unchanged. A `ChangeEvent` with an audit kind built by hand anywhere but
the relay and the operator's requeue is a finding.
