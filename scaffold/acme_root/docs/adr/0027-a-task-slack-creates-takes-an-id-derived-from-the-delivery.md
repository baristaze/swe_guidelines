# ADR 0027: A task Slack creates takes an id derived from the delivery

**Status**: accepted (2026-09-22)
Amended (2026-09-23): the delivery arrives over HTTP and its key comes
from Slack's own id; see the note at the end.

## Context

"Identifiers" says every id is a `uuid_v7` minted above storage with
`new_id()`. "Idempotency on the Consumer Side" says a message from
outside carries a key derived from the provider's delivery, and the
handler dedupes on it: the key lives on the row the effect produces,
or the marker and the effect are one atomic write.

`/acme add <title>` arrives over Slack's Socket Mode connection. The
bridge acknowledges it, gives it a key (a UUID v5 over the provider's
name and the delivery's id), and queues it; a worker creates the task.
The queue is at least once, so the same delivery can be handled twice.
The task has no column for an outside key, and a marker in a table of
its own would be a second write beside the task's.

## Decision

The task takes an id derived from the delivery: `derived_id(key, at)`
in `acme.om.base`, a v7 whose time is when the bridge received the
delivery and whose random bits come from the key. The create primitive
already treats an id it has seen as a retry and answers the row as
stored, so a second handling of the delivery creates nothing. The id
still sorts by time and still reads as a v7.

Every other id stays `new_id()`. The derivation is for an entity an
outside delivery creates, and today that is this one task. The key
itself is the UUID v5 "Queues" asks for; the static checker reads any
`uuid5` as an id being minted, so `pyproject.toml` names the one module
that derives it as an exception to `OM-12`, citing this record.

## Consequences

The effect carries its own key, as the rule prefers, with no marker
table and no column. The cost is that the id's random bits are not
random: anyone who knew a delivery's key and the millisecond it arrived
could compute the task's id. Neither leaves the platform, and an id is
never a credential here.

## Amendment (2026-09-23)

Slack's calls arrive over HTTP
([ADR 0035](0035-slack-is-a-distributed-app-over-http-installed-per-org.md)).
The API checks each one, acknowledges it, and queues it. The key is a
UUID v5 over the provider's name and Slack's own id for the delivery:
the `trigger_id` of a command, and the `event_id` of an event. The
derived id, the time it carries, and the create's retry are as decided
above. The module that derives the key is
`acme.integrations.slack.requests`, which the static checker does not
read as minting an entity's id, so `pyproject.toml` names no exception
for it.
