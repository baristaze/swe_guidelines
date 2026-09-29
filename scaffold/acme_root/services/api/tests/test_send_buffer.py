"""Per socket, the send buffer is two bounded lanes: the drainer writes the
control lane first and the stream lane after it, a full stream lane drops its
oldest frame, logs the drop, and counts it, and a burst in the stream lane
never costs the client a frame that says where its socket stands."""

import asyncio
import json
import logging
from typing import cast

import pytest
from fastapi import WebSocket

from acme.infra.topics import Topics
from acme.om.base import new_id
from acme.services.api.realtime.envelopes import Envelope, EventEnvelope, PongEnvelope
from acme.services.api.realtime.send_buffer import SendBuffer
from acme.services.api.types.events import EntityChangedView

LOGGER = "acme.services.api.realtime.send_buffer"

ACTOR = new_id()


def event(seq: int) -> EventEnvelope:
    """A push at `seq`: the stream lane's frame, and the one a burst is made of."""
    return EventEnvelope(
        topic=Topics.ENTITY_CHANGED.value,
        payload=EntityChangedView(
            kind="tasks.task.created", target_id=new_id(), seq=seq, actor_id=ACTOR
        ),
    )


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.written = asyncio.Event()

    async def send_text(self, data: str) -> None:
        self.sent.append(data)
        self.written.set()


def mark(frame: str) -> tuple[str, int]:
    """A frame as the test reads it: its type and the seq it carries, in the
    envelope for a control frame and in the payload for a push."""
    sent = json.loads(frame)
    return sent["type"], sent["seq"] if "seq" in sent else sent["payload"]["seq"]


async def drained(buffer: SendBuffer, count: int) -> list[tuple[str, int]]:
    """Runs the drainer until `count` frames are on the wire; the marks, in order."""
    socket = FakeSocket()
    drainer = asyncio.create_task(buffer.drain(cast(WebSocket, socket)))
    try:
        while len(socket.sent) < count:
            socket.written.clear()
            await asyncio.wait_for(socket.written.wait(), timeout=1)
    finally:
        drainer.cancel()
    return [mark(frame) for frame in socket.sent]


def offer_all(buffer: SendBuffer, *envelopes: Envelope) -> None:
    for envelope in envelopes:
        buffer.offer(envelope)


async def test_a_control_frame_survives_a_stream_burst(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The pong is offered before the burst and the burst is longer than the
    stream lane: under one queue the pong would have been the first frame
    evicted, which is the case these two lanes exist for."""
    buffer = SendBuffer(maxsize=2, control_maxsize=4)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        buffer.offer(PongEnvelope(seq=9))
        offer_all(buffer, *(event(seq) for seq in (1, 2, 3, 4, 5)))

    assert (buffer.dropped, buffer.control_dropped) == (3, 0)
    assert await drained(buffer, 3) == [("pong", 9), ("event", 4), ("event", 5)]


async def test_the_drainer_writes_control_first_then_the_stream_in_arrival_order() -> None:
    buffer = SendBuffer(maxsize=4, control_maxsize=4)
    offer_all(buffer, event(1), PongEnvelope(seq=7), event(2), PongEnvelope(seq=8))

    assert await drained(buffer, 4) == [("pong", 7), ("pong", 8), ("event", 1), ("event", 2)]


async def test_a_full_stream_lane_drops_the_oldest_frame_logs_and_counts_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    buffer = SendBuffer(maxsize=2, control_maxsize=2)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        offer_all(buffer, *(event(seq) for seq in (1, 2, 3)))

    assert (buffer.dropped, buffer.control_dropped) == (1, 0)
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (logging.WARNING, "send buffer full; dropped a EventEnvelope frame")
    ]
    assert await drained(buffer, 2) == [("event", 2), ("event", 3)]


async def test_a_full_control_lane_drops_its_oldest_and_says_so_as_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A control lane that fills is a fault of this process and not a burst,
    so the drop is counted apart and the line is an error."""
    buffer = SendBuffer(maxsize=4, control_maxsize=2)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        offer_all(buffer, *(PongEnvelope(seq=seq) for seq in (1, 2, 3)))

    assert (buffer.dropped, buffer.control_dropped) == (0, 1)
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (logging.ERROR, "control lane full; dropped a PongEnvelope frame")
    ]
    assert await drained(buffer, 2) == [("pong", 2), ("pong", 3)]


async def test_a_buffer_with_room_drops_nothing(caplog: pytest.LogCaptureFixture) -> None:
    buffer = SendBuffer(maxsize=3, control_maxsize=3)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        offer_all(buffer, event(1), event(2), PongEnvelope(seq=3))

    assert (buffer.dropped, buffer.control_dropped) == (0, 0)
    assert caplog.records == []
    assert await drained(buffer, 3) == [("pong", 3), ("event", 1), ("event", 2)]
