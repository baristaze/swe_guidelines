"""Per socket: two bounded send lanes and a drainer task that writes them to
the wire. A frame that reports the state of the socket is a control frame and
an event hint is a stream frame; the drainer empties control first, so a
burst of hints never delays the pong that carries the head seq and never
evicts it. When the stream lane is full the oldest stream frame is dropped
and the drop is logged; the client that notices a gap in the stream replays
from storage. Not to be confused with the transactional outbox of the object
model."""

import asyncio
import logging
from collections import deque

from fastapi import WebSocket

from acme.om.base import utcnow
from acme.services.api.realtime.envelopes import (
    Envelope,
    ErrorEnvelope,
    HelloEnvelope,
    PongEnvelope,
    SubscribedEnvelope,
    UnsubscribedEnvelope,
)

log = logging.getLogger(__name__)

CONTROL_FRAMES: tuple[type[Envelope], ...] = (
    HelloEnvelope,
    PongEnvelope,
    SubscribedEnvelope,
    UnsubscribedEnvelope,
    ErrorEnvelope,
)
"""The frames that say where the socket itself stands: who it is, where the
tenant's stream stands, what it is subscribed to, and what it refused. They
are named here rather than derived, so a frame type added later rides the
stream lane, whose drops are ordinary, until someone decides otherwise."""


class SendBuffer:
    """Two lanes, each bounded on its own. `offer` puts a frame in the lane
    its type names and `drain` empties control first, so the lane a burst
    fills is never the lane the client's own answers wait in."""

    def __init__(self, maxsize: int, control_maxsize: int) -> None:
        self._maxsize = maxsize
        self._control_maxsize = control_maxsize
        self._stream: deque[Envelope] = deque()
        self._control: deque[Envelope] = deque()
        self._wakeup = asyncio.Event()
        self.dropped = 0
        self.control_dropped = 0

    def offer(self, envelope: Envelope) -> None:
        frame = envelope.model_copy(update={"sent_at": utcnow()})
        if isinstance(envelope, CONTROL_FRAMES):
            self._offer_control(frame)
        else:
            self._offer_stream(frame)
        self._wakeup.set()

    def _offer_stream(self, frame: Envelope) -> None:
        """A full stream lane is the burst the bound is there for: the oldest
        hint goes, and the client that sees the gap replays from storage."""
        if len(self._stream) >= self._maxsize:
            dropped = self._stream.popleft()
            self.dropped += 1
            log.warning("send buffer full; dropped a %s frame", type(dropped).__name__)
        self._stream.append(frame)

    def _offer_control(self, frame: Envelope) -> None:
        """A full control lane is not a burst: these frames answer what the
        client asked for and a socket offers few of them, so a lane that
        fills is a fault of this process and the line is an error. The oldest
        goes, as in the stream lane, since the newest carries the most recent
        thing the client has yet to hear."""
        if len(self._control) >= self._control_maxsize:
            dropped = self._control.popleft()
            self.control_dropped += 1
            log.error("control lane full; dropped a %s frame", type(dropped).__name__)
        self._control.append(frame)

    async def drain(self, websocket: WebSocket) -> None:
        while True:
            while self._control or self._stream:
                lane = self._control or self._stream
                frame = lane.popleft()
                await websocket.send_text(frame.model_dump_json())
            self._wakeup.clear()
            await self._wakeup.wait()
