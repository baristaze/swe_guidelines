import pytest

from acme.infra.base import new_id, utcnow
from acme.infra.exceptions import PayloadMismatch
from acme.infra.topics import (
    NO_ACTOR,
    TOPIC_PAYLOADS,
    EntityChangedPayload,
    TopicPayload,
    Topics,
    WorkAvailablePayload,
)
from acme.infra.topics.memory import TopicsMemoryImpl


def work_available() -> WorkAvailablePayload:
    return WorkAvailablePayload(
        idempotency_key=new_id(),
        produced_at=utcnow(),
        org_id=new_id(),
        lane="default",
        kind="NOOP",
    )


async def test_publish_reaches_every_subscriber_until_unsubscribed() -> None:
    topics = TopicsMemoryImpl()
    seen_a: list[TopicPayload] = []
    seen_b: list[TopicPayload] = []

    async def a(payload: TopicPayload) -> None:
        seen_a.append(payload)

    async def b(payload: TopicPayload) -> None:
        seen_b.append(payload)

    unsubscribe_a = topics.subscribe(Topics.WORK_AVAILABLE, "a", a)
    topics.subscribe(Topics.WORK_AVAILABLE, "b", b)
    first = work_available()
    await topics.publish(Topics.WORK_AVAILABLE, first)
    unsubscribe_a()
    await topics.publish(Topics.WORK_AVAILABLE, work_available())
    assert seen_a == [first]
    assert len(seen_b) == 2


async def test_a_failing_handler_does_not_stop_the_others() -> None:
    topics = TopicsMemoryImpl()
    seen: list[TopicPayload] = []

    async def boom(payload: TopicPayload) -> None:
        raise RuntimeError("boom")

    async def ok(payload: TopicPayload) -> None:
        seen.append(payload)

    topics.subscribe(Topics.WORK_AVAILABLE, "boom", boom)
    topics.subscribe(Topics.WORK_AVAILABLE, "ok", ok)
    await topics.publish(Topics.WORK_AVAILABLE, work_available())
    assert len(seen) == 1


async def test_payload_type_is_fixed_by_the_map() -> None:
    topics = TopicsMemoryImpl()
    wrong = EntityChangedPayload(
        idempotency_key=new_id(),
        produced_at=utcnow(),
        org_id=new_id(),
        kind="tenancy.user.created",
        target_id=new_id(),
        seq=1,
        actor_id=new_id(),
    )
    with pytest.raises(PayloadMismatch):
        await topics.publish(Topics.WORK_AVAILABLE, wrong)


def test_a_payload_ignores_a_field_it_does_not_know() -> None:
    # A tolerant reader: a producer one release ahead adds a field and every
    # consumer still parses the frame.
    payload = WorkAvailablePayload.model_validate(
        {
            "idempotency_key": str(new_id()),
            "produced_at": utcnow().isoformat(),
            "org_id": str(new_id()),
            "lane": "default",
            "kind": "NOOP",
            "added_later": True,
        }
    )
    assert payload.lane == "default" and not hasattr(payload, "added_later")


def test_a_payload_from_a_producer_one_release_behind_still_parses() -> None:
    # The other direction, and the reason a payload gains only optional,
    # defaulted fields: during a rolling deploy the old replicas publish
    # frames without the newest field, and a new replica's listener parses
    # them or drops every one, revocations included.
    frame = {
        "idempotency_key": str(new_id()),
        "produced_at": utcnow().isoformat(),
        "org_id": str(new_id()),
        "kind": "tenancy.session.revoked",
        "target_id": str(new_id()),
        "seq": 7,
    }
    payload = TOPIC_PAYLOADS[Topics.ENTITY_CHANGED].model_validate(frame)
    assert isinstance(payload, EntityChangedPayload)
    assert payload.kind == "tenancy.session.revoked" and payload.seq == 7
    # Nobody is named, so no client mistakes the change for its own.
    assert payload.actor_id == NO_ACTOR
