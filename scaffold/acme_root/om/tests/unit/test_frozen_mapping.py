"""`FrozenMapping` over a payload of dumped JSON: the freeze reaches what the
mapping holds, and the dump hands plain JSON containers back."""

from types import MappingProxyType
from typing import Any

import pytest
from pydantic import Field

from acme.om.base import FrozenMapping, Platform
from acme.om.events.types.event import Event
from acme.om.outbox.types.row import OutboxRow
from acme.om.work.types.work_item import WorkItem

NESTED: dict[str, Any] = {
    "tags": ["a", "b"],
    "who": {"ids": [1, 2], "name": "x"},
    "rows": [{"n": 1}, {"n": 2}],
    "count": 3,
}


class Carrier(Platform):
    payload: FrozenMapping = Field(default_factory=dict, validate_default=True)


def write(target: Any, key: str | int, value: Any) -> None:
    """A write through whatever the payload handed back, so the refusal is the
    container's and not the type checker's."""
    target[key] = value


def test_the_payload_itself_is_frozen() -> None:
    carrier = Carrier(payload=dict(NESTED))
    assert isinstance(carrier.payload, MappingProxyType)
    with pytest.raises(TypeError):
        write(carrier.payload, "count", 4)


def test_a_nested_list_inside_a_payload_is_frozen() -> None:
    carrier = Carrier(payload=dict(NESTED))
    assert isinstance(carrier.payload["tags"], tuple)
    with pytest.raises(TypeError):
        write(carrier.payload["tags"], 0, "c")
    assert carrier.model_dump()["payload"]["tags"] == ["a", "b"]


def test_a_nested_mapping_inside_a_payload_is_frozen() -> None:
    carrier = Carrier(payload=dict(NESTED))
    assert isinstance(carrier.payload["who"], MappingProxyType)
    with pytest.raises(TypeError):
        write(carrier.payload["who"], "name", "y")


def test_a_mapping_inside_a_nested_list_is_frozen() -> None:
    carrier = Carrier(payload=dict(NESTED))
    assert isinstance(carrier.payload["rows"][0], MappingProxyType)
    with pytest.raises(TypeError):
        write(carrier.payload["rows"][0], "n", 9)


def test_the_source_dict_does_not_stay_a_handle_on_the_payload() -> None:
    source: dict[str, Any] = {"tags": ["a"], "who": {"name": "x"}}
    carrier = Carrier(payload=source)
    source["tags"].append("b")
    source["who"]["name"] = "y"
    source["extra"] = True
    assert carrier.payload["tags"] == ("a",)
    assert carrier.payload["who"]["name"] == "x"
    assert "extra" not in carrier.payload


def test_the_dump_rebuilds_plain_dicts_and_lists() -> None:
    carrier = Carrier(payload=dict(NESTED))
    dumped = carrier.model_dump()["payload"]
    assert dumped == NESTED
    assert type(dumped) is dict
    assert type(dumped["tags"]) is list
    assert type(dumped["who"]) is dict
    assert type(dumped["rows"][0]) is dict


def test_the_json_dump_round_trips_through_validation() -> None:
    carrier = Carrier(payload=dict(NESTED))
    assert carrier.model_dump_json() == (
        '{"payload":{"tags":["a","b"],"who":{"ids":[1,2],"name":"x"},'
        '"rows":[{"n":1},{"n":2}],"count":3}}'
    )
    again = Carrier.model_validate(carrier.model_dump())
    assert again == carrier
    assert again.payload["tags"] == ("a", "b")
    assert Carrier.model_validate_json(carrier.model_dump_json()) == carrier


@pytest.mark.parametrize("entity_type", [Event, OutboxRow, WorkItem])
def test_every_payload_field_validates_its_default(entity_type: type[Platform]) -> None:
    # Pydantic does not validate a default, so the empty case is
    # Field(default_factory=dict, validate_default=True), or the default is
    # the one dict that escapes the freeze.
    field = entity_type.model_fields["payload"]
    assert field.validate_default is True
    assert field.default_factory is dict


def test_an_entity_payload_is_frozen_all_the_way_down() -> None:
    event = Event.model_validate(
        {
            "id": "00000000-0000-7000-8000-000000000001",
            "org_id": "00000000-0000-7000-8000-000000000005",
            "kind": "tasks.task.created",
            "target_id": "00000000-0000-7000-8000-000000000002",
            "produced_at": "2026-01-01T00:00:00Z",
            "actor_id": "00000000-0000-7000-8000-000000000003",
            "request_id": "00000000-0000-7000-8000-000000000004",
            "app": "portal",
            "payload": dict(NESTED),
        }
    )
    assert isinstance(event.payload["who"], MappingProxyType)
    assert event.payload["tags"] == ("a", "b")
    assert event.model_dump(mode="json")["payload"] == NESTED
    assert Event.model_validate(event.model_dump()) == event
