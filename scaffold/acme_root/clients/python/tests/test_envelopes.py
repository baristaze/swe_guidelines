"""Frames route on `type`; unknown fields are ignored; malformed frames are None."""

import json
from uuid import uuid4

from acme.client.envelopes import (
    PING_COMMAND,
    EntityChanged,
    EventEnvelope,
    HelloEnvelope,
    parse_envelope,
    subscribe_command,
)
from acme.client.types import EventView


def test_a_push_parses_with_its_payload_and_tolerates_extra_fields() -> None:
    actor, target = uuid4(), uuid4()
    raw = json.dumps(
        {
            "type": "event",
            "sent_at": None,
            "topic": "entity_changed",
            "payload": {
                "kind": "tasks.task.created",
                "target_id": str(target),
                "seq": 3,
                "actor_id": str(actor),
                "added_later": 1,
            },
            "added_later": True,
        }
    )
    envelope = parse_envelope(raw)
    assert isinstance(envelope, EventEnvelope)
    assert envelope.payload.seq == 3 and envelope.payload.actor_id == actor
    assert envelope.payload.entity == "task" and envelope.payload.action == "created"


def test_hello_and_malformed_frames() -> None:
    hello = parse_envelope(
        json.dumps(
            {
                "type": "hello",
                "sent_at": None,
                "org_id": str(uuid4()),
                "user_id": str(uuid4()),
                "seq": 12,
                "ping_interval_seconds": 25,
            }
        )
    )
    assert isinstance(hello, HelloEnvelope) and hello.ping_interval_seconds == 25
    assert hello.seq == 12
    assert parse_envelope("not json") is None
    assert parse_envelope(json.dumps({"type": "unknown"})) is None
    assert parse_envelope(json.dumps({"type": "event", "topic": "x"})) is None


def test_a_replayed_record_becomes_the_push_it_would_have_been() -> None:
    event = EventView.model_validate(
        {
            "seq": 4,
            "kind": "tasks.task.updated",
            "target_id": str(uuid4()),
            "produced_at": "2026-09-18T12:00:00Z",
            "actor_id": str(uuid4()),
        }
    )
    changed = EntityChanged.of_event(event)
    assert (changed.seq, changed.kind, changed.actor_id) == (4, event.kind, event.actor_id)


def test_the_commands_are_what_the_service_accepts() -> None:
    assert json.loads(subscribe_command()) == {"op": "subscribe", "topic": "entity_changed"}
    assert json.loads(PING_COMMAND) == {"op": "ping"}
