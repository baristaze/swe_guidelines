import inspect
from datetime import timedelta

import pytest

from acme.infra.observability import OUTCOMES
from acme.infra.queues import Queues, QueuesInterface
from acme.infra.queues.memory import QueueMemoryImpl

NO_WAIT = timedelta(0)


def outcome(name: str) -> float:
    return OUTCOMES.labels(subsystem="queue", outcome=name)._value.get()


async def test_send_receive_delete() -> None:
    queue = QueueMemoryImpl()
    await queue.send(Queues.WEBHOOKS, b"one")
    received = await queue.receive(Queues.WEBHOOKS, 10, NO_WAIT, timedelta(seconds=30))
    assert [m.body for m in received] == [b"one"]
    assert (await queue.depth(Queues.WEBHOOKS)).in_flight == 1
    await queue.delete(Queues.WEBHOOKS, received[0].receipt)
    assert await queue.receive(Queues.WEBHOOKS, 10, NO_WAIT, timedelta(seconds=30)) == []


async def test_invisible_messages_come_back_after_the_timeout() -> None:
    queue = QueueMemoryImpl()
    await queue.send(Queues.WEBHOOKS, b"retry")
    first = await queue.receive(Queues.WEBHOOKS, 1, NO_WAIT, timedelta(seconds=-1))
    second = await queue.receive(Queues.WEBHOOKS, 1, NO_WAIT, timedelta(seconds=30))
    assert first[0].id == second[0].id
    assert second[0].attempts == 2
    await queue.change_visibility(Queues.WEBHOOKS, second[0].receipt, timedelta(seconds=-1))
    assert len(await queue.receive(Queues.WEBHOOKS, 1, NO_WAIT, timedelta(seconds=30))) == 1


async def test_dead_letters_are_visible_in_depth_the_log_and_the_metric(
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue = QueueMemoryImpl(max_receives=2)
    await queue.send(Queues.WEBHOOKS, b"poison")
    delivered = []
    for _ in range(2):
        received = await queue.receive(Queues.WEBHOOKS, 1, NO_WAIT, timedelta(seconds=-1))
        assert len(received) == 1
        delivered.append(received[0].id)
    message_id = delivered[0]
    before = outcome("dead_lettered")
    with caplog.at_level("WARNING", logger="acme.infra.queues.memory"):
        assert await queue.receive(Queues.WEBHOOKS, 1, NO_WAIT, timedelta(seconds=-1)) == []
    depth = await queue.depth(Queues.WEBHOOKS)
    assert depth.dead_lettered == 1 and depth.visible == 0
    assert outcome("dead_lettered") == before + 1
    assert any(
        message_id in r.getMessage() and "webhooks" in r.getMessage() for r in caplog.records
    )


async def test_the_same_body_sent_twice_is_two_messages() -> None:
    queue = QueueMemoryImpl()
    await queue.send(Queues.WEBHOOKS, b"x")
    await queue.send(Queues.WEBHOOKS, b"x")
    assert (await queue.depth(Queues.WEBHOOKS)).visible == 2
    delivered = await queue.receive(Queues.WEBHOOKS, 2, NO_WAIT, timedelta(seconds=30))
    assert len({message.id for message in delivered}) == 2


async def test_send_answers_nothing() -> None:
    """`send` returns None for the reason `publish` does: the observable id is
    the producer-set idempotency key the body carries, and a broker-assigned id
    carries no durable meaning across retries and replays. The receipt on a
    delivered message is not that id either; it is the handle of one delivery."""
    queue = QueueMemoryImpl()
    assert await queue.send(Queues.WEBHOOKS, b"x") is None
    assert inspect.signature(QueuesInterface.send).return_annotation is None


async def test_every_outcome_is_counted() -> None:
    queue = QueueMemoryImpl()
    sent, received, deleted = outcome("sent"), outcome("received"), outcome("deleted")
    await queue.send(Queues.WEBHOOKS, b"m")
    messages = await queue.receive(Queues.WEBHOOKS, 10, NO_WAIT, timedelta(seconds=30))
    await queue.delete(Queues.WEBHOOKS, messages[0].receipt)
    assert (outcome("sent"), outcome("received"), outcome("deleted")) == (
        sent + 1,
        received + 1,
        deleted + 1,
    )
