"""The relay over Postgres and the compose stack's Valkey: a publish Valkey
refuses leaves its row pending, the published rows beside it are marked, and
the sweep publishes the rest once Valkey answers again. A Valkey that cannot
be reached is a connection to a port nothing listens on, since the stack's own
Valkey is shared by every suite that runs."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from uuid import UUID

import pytest
from contracts.outbox_storage import claim_all, make_row
from contracts.task_storage import make_task

from acme.infra.impl.settings import InfraSettings
from acme.infra.impl.valkey import ValkeyConnection
from acme.infra.observability import OUTCOMES
from acme.infra.topics import (
    EntityChangedPayload,
    TopicHandler,
    TopicPayload,
    Topics,
    TopicsInterface,
)
from acme.infra.topics.valkey import TopicsValkeyImpl
from acme.om.base import new_id, utcnow
from acme.om.events.storage.impl.postgres import EventStoragePostgresImpl
from acme.om.outbox.impl.relay import OutboxOptions, OutboxRelayImpl
from acme.om.outbox.storage.impl.postgres import OutboxStoragePostgresImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import LoginSessions
from acme.om.tasks.storage.impl.postgres import TasksStoragePostgresImpl

pytestmark = pytest.mark.integration

NO_DELAY = OutboxOptions(grace=timedelta(0), backoff_base=timedelta(0), max_attempts=5)
TIMEOUT = timedelta(milliseconds=250)


class Routed(TopicsInterface):
    """Each change's publish goes to the Valkey that answers, or, for the
    targets named, to the one that cannot be reached."""

    def __init__(self, up: TopicsInterface, down: TopicsInterface, targets: set[UUID]) -> None:
        self._up, self._down, self._targets = up, down, targets

    async def publish(self, topic: Topics, payload: TopicPayload) -> bool:
        target = payload.target_id if isinstance(payload, EntityChangedPayload) else None
        bus = self._down if target in self._targets else self._up
        return await bus.publish(topic, payload)

    def subscribe(self, topic: Topics, consumer: str, handler: TopicHandler) -> Callable[[], None]:
        return self._up.subscribe(topic, consumer, handler)

    def describe(self) -> str:
        return "topics=routed"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
async def down() -> AsyncIterator[TopicsValkeyImpl]:
    connection = ValkeyConnection("valkey://127.0.0.1:1", TIMEOUT)
    try:
        yield TopicsValkeyImpl(connection)
    finally:
        await connection.close()


@pytest.fixture
async def up() -> AsyncIterator[tuple[TopicsValkeyImpl, asyncio.Queue[UUID]]]:
    """The stack's Valkey, on a channel prefix of this test's own, with a
    listener that is hearing it before the test begins."""
    settings = InfraSettings()
    connection = ValkeyConnection(settings.valkey_url, timedelta(seconds=2))
    topics = TopicsValkeyImpl(connection, channel_prefix=f"acme-it:{new_id()}:")
    heard: asyncio.Queue[UUID] = asyncio.Queue()

    async def hear(payload: TopicPayload) -> None:
        assert isinstance(payload, EntityChangedPayload)
        await heard.put(payload.target_id)

    topics.subscribe(Topics.ENTITY_CHANGED, "test", hear)
    await topics.start()
    try:
        probe = new_id()
        for _ in range(50):
            await topics.publish(Topics.ENTITY_CHANGED, a_change(probe))
            try:
                if await asyncio.wait_for(heard.get(), timeout=0.1) == probe:
                    break
            except TimeoutError:
                continue
        else:
            pytest.fail("the listener never heard the stack's Valkey")
        while not heard.empty():
            heard.get_nowait()
        yield topics, heard
    finally:
        await topics.close()
        await connection.close()


def a_change(target: UUID) -> EntityChangedPayload:
    return EntityChangedPayload(
        idempotency_key=new_id(),
        produced_at=utcnow(),
        org_id=new_id(),
        kind="tasks.task.created",
        target_id=target,
        seq=1,
    )


async def landed(sessions: LoginSessions, org: UUID, count: int) -> list[OutboxRow]:
    tasks = TasksStoragePostgresImpl(sessions)
    rows: list[OutboxRow] = []
    for _ in range(count):
        task = make_task()
        rows.append(make_row(org, task.id))
        assert await tasks.create_task(org, task, (rows[-1],))
    return rows


async def drain(heard: asyncio.Queue[UUID], count: int) -> set[UUID]:
    return {await asyncio.wait_for(heard.get(), timeout=5) for _ in range(count)}


def publish_failures() -> float:
    return OUTCOMES.labels(subsystem="outbox", outcome="publish_failed")._value.get()


async def test_a_refused_publish_stays_pending_and_the_sweep_sends_it_once_valkey_answers(
    pg_sessions: LoginSessions,
    down: TopicsValkeyImpl,
    up: tuple[TopicsValkeyImpl, asyncio.Queue[UUID]],
) -> None:
    bus, heard = up
    outbox = OutboxStoragePostgresImpl(pg_sessions)
    events = EventStoragePostgresImpl(pg_sessions)
    org = new_id()
    rows = await landed(pg_sessions, org, 2)
    counted = publish_failures()

    # The request path, and one sweep, while Valkey cannot be reached.
    unreachable = OutboxRelayImpl(outbox, events, down, options=NO_DELAY)
    assert not await unreachable.relay_all(org, rows)
    assert publish_failures() == counted + 2
    assert await unreachable.oldest_pending_age() > timedelta(0)
    assert await unreachable.relay_pending(100) == 0
    assert publish_failures() == counted + 4
    assert len(await events.read_after(org, 0, 10)) == 2, "the events are in, once"

    # Valkey answers: the next pass publishes both and marks them done.
    answering = OutboxRelayImpl(outbox, events, bus, options=NO_DELAY)
    assert await answering.relay_pending(100) == 2
    assert await drain(heard, 2) == {row.target_id for row in rows}
    assert await answering.oldest_pending_age() == timedelta(0)
    assert await claim_all(outbox) == []
    assert len(await events.read_after(org, 0, 10)) == 2


async def test_a_batch_marks_the_rows_valkey_took_and_leaves_the_refused_one(
    pg_sessions: LoginSessions,
    down: TopicsValkeyImpl,
    up: tuple[TopicsValkeyImpl, asyncio.Queue[UUID]],
) -> None:
    bus, heard = up
    outbox = OutboxStoragePostgresImpl(pg_sessions)
    events = EventStoragePostgresImpl(pg_sessions)
    org = new_id()
    rows = await landed(pg_sessions, org, 3)
    refused = rows[1]
    relay = OutboxRelayImpl(
        outbox, events, Routed(bus, down, {refused.target_id}), options=NO_DELAY
    )

    assert not await relay.relay_all(org, rows)
    assert await drain(heard, 2) == {rows[0].target_id, rows[2].target_id}
    pending = await claim_all(outbox)
    assert [row.id for row in pending] == [refused.id]

    answering = OutboxRelayImpl(outbox, events, bus, options=NO_DELAY)
    assert await answering.relay_pending(100) == 1
    assert await drain(heard, 1) == {refused.target_id}
    assert await claim_all(outbox) == []
