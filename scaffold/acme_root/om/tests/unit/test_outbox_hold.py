"""The hold the API keeps around a request: a row that names a held request is
kept, not relayed, until the hold is released; then each org's rows relay
together. A row of any other request relays at once, and a hold abandoned
leaves its rows to the sweep."""

import logging
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.outbox_storage import claim_all, make_row
from contracts.task_storage import make_task

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.outbox.impl.relay import OutboxOptions, OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl


class CountedMarks(OutboxStorageMemoryImpl):
    """Counts the calls that mark rows done, and how many rows each named."""

    def __init__(self) -> None:
        super().__init__()
        self.marks: list[int] = []

    async def mark_done(self, org_id: UUID, row_ids: Sequence[UUID]) -> None:
        self.marks.append(len(row_ids))
        await super().mark_done(org_id, row_ids)


async def landed_rows(
    outbox: OutboxStorageMemoryImpl, org: UUID, count: int, request_id: UUID
) -> list[OutboxRow]:
    """`count` tasks written by one request, each with its row."""
    tasks = TasksStorageMemoryImpl(outbox)
    rows: list[OutboxRow] = []
    for _ in range(count):
        task = make_task()
        rows.append(make_row(org, task.id).model_copy(update={"request_id": request_id}))
        await tasks.create_task(org, task, (rows[-1],))
    return rows


@pytest.fixture
def infra(tmp_path: Path) -> InfraLocalImpl:
    return InfraLocalImpl(tmp_path)


@pytest.fixture
def outbox() -> CountedMarks:
    return CountedMarks()


@pytest.fixture
def events() -> EventStorageMemoryImpl:
    return EventStorageMemoryImpl()


@pytest.fixture
def relay(
    outbox: CountedMarks, events: EventStorageMemoryImpl, infra: InfraLocalImpl
) -> OutboxRelayImpl:
    return OutboxRelayImpl(
        outbox, events, infra.get_topics(), options=OutboxOptions(grace=timedelta(0))
    )


async def test_a_held_request_relays_on_release_each_org_together(
    relay: OutboxRelayImpl, outbox: CountedMarks, events: EventStorageMemoryImpl
) -> None:
    """Two writes of one org and one of another, in one request: nothing is
    appended or marked while the hold is open; the release makes one append
    and one mark per org, each org's rows in the order they came."""
    request = new_id()
    org, other = new_id(), new_id()
    first = await landed_rows(outbox, org, 2, request)
    theirs = await landed_rows(outbox, other, 1, request)
    second = await landed_rows(outbox, org, 3, request)
    relay.hold(request)
    assert await relay.relay_all(org, first)
    assert await relay.relay(other, theirs[0])
    assert await relay.relay_all(org, second)
    assert relay.held(request) == 6
    assert not await events.read_after(org, 0, 10)
    assert outbox.marks == []

    assert await relay.release(request) == 6
    assert [e.id for e in await events.read_after(org, 0, 10)] == [
        row.id for row in (*first, *second)
    ]
    assert [e.id for e in await events.read_after(other, 0, 10)] == [theirs[0].id]
    assert outbox.marks == [5, 1]
    assert await claim_all(outbox) == []
    assert relay.held(request) == 0
    assert await relay.release(request) == 0, "released once; the hold is gone"


async def test_a_row_of_another_request_relays_at_once(
    relay: OutboxRelayImpl, outbox: CountedMarks, events: EventStorageMemoryImpl
) -> None:
    """A worker's rows, or another request's, never wait on a hold."""
    org = new_id()
    held, other = new_id(), new_id()
    rows = await landed_rows(outbox, org, 2, other)
    relay.hold(held)
    assert await relay.relay_all(org, rows)
    assert [e.id for e in await events.read_after(org, 0, 10)] == [row.id for row in rows]
    assert outbox.marks == [2]
    assert relay.held(held) == 0
    assert await relay.release(held) == 0


async def test_two_holds_of_one_request_id_relay_every_row(
    relay: OutboxRelayImpl, outbox: CountedMarks, events: EventStorageMemoryImpl
) -> None:
    """A caller may send the same request id twice at once. Each release
    relays what is held when it runs, so no row waits past the last one."""
    request, org = new_id(), new_id()
    relay.hold(request)
    relay.hold(request)
    first = await landed_rows(outbox, org, 1, request)
    await relay.relay_all(org, first)
    assert await relay.release(request) == 1
    second = await landed_rows(outbox, org, 1, request)
    await relay.relay_all(org, second)
    assert relay.held(request) == 1, "the other holder is still open"
    assert await relay.release(request) == 1
    assert [e.id for e in await events.read_after(org, 0, 10)] == [first[0].id, second[0].id]
    second_again = await landed_rows(outbox, org, 1, request)
    await relay.relay_all(org, second_again)
    assert outbox.marks == [1, 1, 1], "with no hold left, the row relays at once"


async def test_a_hold_abandoned_leaves_the_rows_to_the_sweep(
    relay: OutboxRelayImpl, outbox: CountedMarks, caplog: pytest.LogCaptureFixture
) -> None:
    request, org = new_id(), new_id()
    rows = await landed_rows(outbox, org, 3, request)
    relay.hold(request)
    await relay.relay_all(org, rows)
    with caplog.at_level(logging.WARNING, logger="acme.om.outbox.impl.relay"):
        assert relay.abandon(request) == 3
    assert "3 outbox rows held for after the answer are left to the sweep" in caplog.text
    assert outbox.marks == []
    assert await relay.release(request) == 0
    assert await relay.relay_pending(10) == 3, "the sweep relays them"
    assert outbox.marks == [3]


async def test_the_sweep_is_never_held(
    relay: OutboxRelayImpl, outbox: CountedMarks, events: EventStorageMemoryImpl
) -> None:
    """The sweep claims rows by age, not by request: a row whose request is
    still held (a slow answer) is relayed by the sweep past its grace, and the
    release after it relays it again, which is harmless."""
    request, org = new_id(), new_id()
    rows = await landed_rows(outbox, org, 2, request)
    relay.hold(request)
    await relay.relay_all(org, rows)
    assert await relay.relay_pending(10) == 2
    assert await relay.release(request) == 2
    assert [e.id for e in await events.read_after(org, 0, 10)] == [row.id for row in rows]
    assert await relay.oldest_pending_age() == timedelta(0)
