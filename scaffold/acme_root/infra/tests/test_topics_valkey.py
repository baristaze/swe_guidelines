"""The Valkey listener outlives the driver: a failed subscriber is counted,
logged, and reopened with backoff, and the next message still reaches its
handlers. The subscribers are injected; nothing here connects."""

import asyncio
import logging
from datetime import timedelta
from typing import Any

import pytest
from glide import GlideError
from prometheus_client import REGISTRY

from acme.infra.base import new_id, utcnow
from acme.infra.impl.valkey import ValkeyConnection
from acme.infra.topics import TopicPayload, Topics, WorkAvailablePayload
from acme.infra.topics.valkey import TopicsValkeyImpl


class Message:
    def __init__(self, channel: str, payload: TopicPayload) -> None:
        self.channel = channel.encode()
        self.message = payload.model_dump_json().encode()


class FailingSubscriber:
    """Every receive is the driver giving up."""

    closed = False

    async def get_pubsub_message(self) -> Any:
        raise GlideError("connection reset by the server")

    async def close(self) -> None:
        self.closed = True


class DroppingSubscriber:
    """Hands out its messages, then the driver gives up."""

    def __init__(self, messages: list[Message]) -> None:
        self._messages = list(messages)

    async def get_pubsub_message(self) -> Any:
        if self._messages:
            return self._messages.pop(0)
        raise GlideError("connection dropped")

    async def close(self) -> None:
        pass


class DeliveringSubscriber:
    """Hands out its messages, then waits until closed."""

    def __init__(self, messages: list[Message]) -> None:
        self._messages = list(messages)
        self._closed = asyncio.Event()

    async def get_pubsub_message(self) -> Any:
        if self._messages:
            return self._messages.pop(0)
        await self._closed.wait()
        raise GlideError("closed")

    async def close(self) -> None:
        self._closed.set()


def failures_counted() -> float:
    return (
        REGISTRY.get_sample_value(
            "acme_outcomes_total", {"subsystem": "topics", "outcome": "listener_failed"}
        )
        or 0.0
    )


def work_available() -> WorkAvailablePayload:
    return WorkAvailablePayload(
        idempotency_key=new_id(), produced_at=utcnow(), org_id=new_id(), lane="default", kind="NOOP"
    )


def listener_over(
    monkeypatch: pytest.MonkeyPatch, subscribers: list[Any], backoff: tuple[float, ...]
) -> tuple[TopicsValkeyImpl, list[Any]]:
    """A listener whose subscribers are handed out in order; the ones opened so
    far are the second value."""
    remaining = list(subscribers)
    opened: list[Any] = []

    async def open_next(self: TopicsValkeyImpl) -> Any:
        subscriber = remaining.pop(0)
        opened.append(subscriber)
        return subscriber

    monkeypatch.setattr(TopicsValkeyImpl, "_open_subscriber", open_next)
    monkeypatch.setattr(TopicsValkeyImpl, "RECONNECT_BACKOFF_SECONDS", backoff)
    topics = TopicsValkeyImpl(ValkeyConnection("valkey://127.0.0.1:1/0", timedelta(seconds=1)))
    return topics, opened


async def test_a_failed_listener_reconnects_and_keeps_delivering(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sent = work_available()
    first = FailingSubscriber()
    second = DeliveringSubscriber([Message("acme:topics:work_available", sent)])
    topics, opened = listener_over(monkeypatch, [first, second], (0.01,))
    received: asyncio.Queue[TopicPayload] = asyncio.Queue()

    async def handler(payload: TopicPayload) -> None:
        await received.put(payload)

    topics.subscribe(Topics.WORK_AVAILABLE, "test", handler)
    before = failures_counted()
    with caplog.at_level(logging.WARNING, logger="acme.infra.topics.valkey"):
        await topics.start()
        got = await asyncio.wait_for(received.get(), timeout=2)
    await topics.close()

    assert got == sent
    assert opened == [first, second] and first.closed
    assert failures_counted() == before + 1
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [
        "topics listener failed (GlideError: connection reset by the server); reconnecting in 0.0s"
    ]


async def test_a_received_message_resets_the_backoff(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Consecutive failures climb the backoff; a message proves the bus healthy
    and the next failure starts from the first step again, so a listener that
    hiccups a few times over its life never waits the full ladder."""
    channel = "acme:topics:work_available"
    first, last = work_available(), work_available()
    subscribers: list[Any] = [
        FailingSubscriber(),
        FailingSubscriber(),
        DroppingSubscriber([Message(channel, first)]),
        DeliveringSubscriber([Message(channel, last)]),
    ]
    topics, opened = listener_over(monkeypatch, subscribers, (0.01, 0.06))
    received: asyncio.Queue[TopicPayload] = asyncio.Queue()

    async def handler(payload: TopicPayload) -> None:
        await received.put(payload)

    topics.subscribe(Topics.WORK_AVAILABLE, "test", handler)
    before = failures_counted()
    with caplog.at_level(logging.WARNING, logger="acme.infra.topics.valkey"):
        await topics.start()
        got = [await asyncio.wait_for(received.get(), timeout=2) for _ in range(2)]
    await topics.close()

    assert got == [first, last]
    assert opened == subscribers
    assert failures_counted() == before + 3
    delays = [
        r.getMessage().rsplit(" in ", 1)[1] for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert delays == ["0.0s", "0.1s", "0.0s"]


async def test_a_bus_that_is_down_at_boot_does_not_fail_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The subscriber is the listener's to open: a bus that is down when the
    process starts is carried by the backoff, and the first message after it
    comes back still reaches its handlers."""
    sent = work_available()
    delivering = DeliveringSubscriber([Message("acme:topics:work_available", sent)])
    attempts = 0

    async def open_after_one_failure(self: TopicsValkeyImpl) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise GlideError("connection refused")
        return delivering

    monkeypatch.setattr(TopicsValkeyImpl, "_open_subscriber", open_after_one_failure)
    monkeypatch.setattr(TopicsValkeyImpl, "RECONNECT_BACKOFF_SECONDS", (0.01,))
    topics = TopicsValkeyImpl(ValkeyConnection("valkey://127.0.0.1:1/0", timedelta(seconds=1)))
    received: asyncio.Queue[TopicPayload] = asyncio.Queue()

    async def handler(payload: TopicPayload) -> None:
        await received.put(payload)

    topics.subscribe(Topics.WORK_AVAILABLE, "test", handler)
    await topics.start()
    got = await asyncio.wait_for(received.get(), timeout=2)
    await topics.close()

    assert got == sent
    assert attempts == 2
