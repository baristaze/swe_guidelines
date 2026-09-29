"""The one channel: a socket opened on a single-use ticket, one subscription
to `entity_changed`, pings at the interval the hello names from a timer of
their own, and an async iterator of changes in stream order. A push ahead of
the cursor is a replay of `/v1/events` after it, never a skip; a dropped
socket reconnects with backoff and replays the same way, so a consumer sees
every change once. A replay refused because the stream is trimmed past the
cursor is a resync: the consumer reads its state afresh, and the cursor goes
on from the head the refusal names."""

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from typing import Literal, Protocol

import httpx
import websockets

from acme.client.client import LIMIT_MAX, ApiClient, ApiError, trust_store
from acme.client.envelopes import (
    ENTITY_CHANGED,
    PING_COMMAND,
    EntityChanged,
    ErrorEnvelope,
    EventEnvelope,
    HelloEnvelope,
    PongEnvelope,
    parse_envelope,
    subscribe_command,
)
from acme.client.stream import Cursor, Gap, Next, Seen, behind, is_last_page, place

log = logging.getLogger(__name__)

SOCKET_PATH = "/v1/realtime"
BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
MIN_PING_SECONDS = 1.0  # a hello that names less would spin
CLOSE_UNAUTHENTICATED = 4401


def reconnect_delay_seconds(attempt: int, jitter: Callable[[], float] = random.random) -> float:
    """The wait before reconnect `attempt` (0 is the first): the curve above,
    halved and topped up from `jitter`. A socket drops for a shared reason, so
    every listener is dropped at once and a bare curve brings them all back at
    the same instant; half the window is jitter, so they do not. Half of it is
    fixed, which keeps the shortest wait of one attempt at the longest wait of
    the one before it wherever the curve doubles, so the growth is still
    assertable under randomness. The curve itself is unchanged: its values are
    the top of each window, the last step included, where the cap is less than
    a doubling and the two windows overlap by a second."""
    full = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
    return full / 2 + (full / 2) * jitter()


State = Literal["connecting", "open", "reconnecting", "closed"]


class SocketLike(Protocol):
    """What the channel needs of a socket; `ClientConnection` is one, a test double another."""

    async def recv(self) -> str | bytes: ...

    async def send(self, message: str) -> None: ...


Connect = Callable[[str, dict[str, str]], AbstractAsyncContextManager[SocketLike]]
"""Opens the socket at a URL with the app headers; injected by tests."""


class ChannelRefused(Exception):
    """The socket closed with 4401: the credential behind the ticket is gone.
    Reconnecting would not help; the consumer signs in again."""


class Channel:
    """`async for change in Channel(client)`. `on_state` hears every transition."""

    def __init__(
        self,
        client: ApiClient,
        *,
        on_state: Callable[[State], None] | None = None,
        connect: Connect | None = None,
        on_first_open: Callable[[], Awaitable[None]] | None = None,
        on_resync: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """`on_first_open` runs once, after the first hello and before any
        change is yielded: a consumer that keeps its own copy of the state
        reads it there, so nothing committed after the head it starts from
        is missed and nothing before it is replayed. It runs again on the
        next connect when it fails.

        `on_resync` runs when the stream is trimmed past the cursor: the
        changes between are gone, so the consumer reads its state afresh
        there, and the cursor then moves to the head the refusal named. When
        it fails, the cursor stays, and the next replay resyncs again."""
        self._client = client
        self._on_state = on_state or (lambda _: None)
        self._on_first_open = on_first_open
        self._on_resync = on_resync
        self._connect = connect or self._connect_default
        self._attempt = 0
        self.cursor: Cursor = None
        self.state: State = "closed"

    def _connect_default(
        self, url: str, headers: dict[str, str]
    ) -> AbstractAsyncContextManager[SocketLike]:
        ssl_context = trust_store() if url.startswith("wss") else None
        return websockets.connect(
            url,
            additional_headers=headers,
            ssl=ssl_context,
            max_size=None,
            open_timeout=self._client.timeout,
        )

    def _set(self, state: State) -> None:
        if state != self.state:
            self.state = state
            self._on_state(state)

    async def __aiter__(self) -> AsyncIterator[EntityChanged]:
        self._attempt = 0
        while True:
            self._set("connecting" if self.state == "closed" else "reconnecting")
            try:
                async for change in self._session():
                    yield change
            except ChannelRefused:
                self._set("closed")
                raise
            except ApiError as error:
                # The ticket or the replay: a refused credential ends it; the
                # API being down or overloaded is a reason to wait and retry.
                if error.status == 401:
                    self._set("closed")
                    raise ChannelRefused(error.code) from None
                if error.status < 500:
                    self._set("closed")
                    raise
                log.info("channel paused, the API answered %s: %s", error.status, error)
            except (
                OSError,
                httpx.TransportError,
                websockets.exceptions.WebSocketException,
                TimeoutError,
            ) as error:
                log.info("channel dropped: %s", error)
            delay = reconnect_delay_seconds(self._attempt)
            self._attempt += 1
            self._set("reconnecting")
            await asyncio.sleep(delay)

    async def _session(self) -> AsyncIterator[EntityChanged]:
        ticket = await self._client.ticket()
        url = f"{self._client.websocket_url(SOCKET_PATH)}?ticket={ticket.ticket}"
        try:
            async with self._connect(url, self._client.headers) as socket:
                hello = parse_envelope(await socket.recv())
                if not isinstance(hello, HelloEnvelope):
                    raise websockets.exceptions.ProtocolError("expected hello")
                # The server is there: the next drop starts the backoff over,
                # whether or not a push arrives in between.
                self._attempt = 0
                await socket.send(subscribe_command(ENTITY_CHANGED))
                self._set("open")
                interval = max(float(hello.ping_interval_seconds), MIN_PING_SECONDS)
                keepalive = asyncio.create_task(self._keepalive(socket, interval))
                try:
                    if self.cursor is None:
                        # The first session starts where the stream stands; a later
                        # reconnect then has a position to replay from even if no
                        # push ever reached this session. The consumer's own read
                        # comes after that head, and the cursor is set once it
                        # has landed.
                        if self._on_first_open is not None:
                            await self._on_first_open()
                        self.cursor = hello.seq
                    else:
                        # Anything that happened while the socket was down.
                        async for change in self._replay(self.cursor):
                            yield change
                    async for change in self._frames(socket):
                        yield change
                finally:
                    keepalive.cancel()
                    with suppress(asyncio.CancelledError):
                        await keepalive
        except websockets.exceptions.ConnectionClosed as closed:
            if closed.rcvd is not None and closed.rcvd.code == CLOSE_UNAUTHENTICATED:
                raise ChannelRefused(closed.rcvd.reason) from None
            raise

    async def _keepalive(self, socket: SocketLike, interval: float) -> None:
        """A ping every `interval`, on a timer of its own, whatever comes in.
        The server counts only what the client sends as a sign of life, so a
        socket busy with pushes, or one mid-replay, would idle out without
        it. A send that fails is left to the reader, which sees the same
        closed socket."""
        while True:
            await asyncio.sleep(interval)
            try:
                await socket.send(PING_COMMAND)
            except OSError, websockets.exceptions.WebSocketException:
                return

    async def _frames(self, socket: SocketLike) -> AsyncIterator[EntityChanged]:
        while True:
            envelope = parse_envelope(await socket.recv())
            if isinstance(envelope, ErrorEnvelope):
                log.warning("channel error %s: %s", envelope.code, envelope.message)
            if isinstance(envelope, PongEnvelope):
                # A pong names the head; a head past the cursor is a gap no frame announced.
                if (after := behind(self.cursor, envelope.seq)) is not None:
                    async for change in self._replay(after):
                        yield change
                continue
            if not isinstance(envelope, EventEnvelope) or envelope.topic != ENTITY_CHANGED:
                continue
            match place(self.cursor, envelope.payload.seq):
                case Next(cursor=cursor):
                    self.cursor = cursor
                    yield envelope.payload
                case Seen():
                    continue
                case Gap(after=after):
                    async for change in self._replay(after):
                        yield change
                    # The frame itself is behind or at the cursor now, or the
                    # replay ended short of it and the next replay catches up.
                    if place(self.cursor, envelope.payload.seq).kind == "next":
                        self.cursor = envelope.payload.seq
                        yield envelope.payload

    async def _replay(self, after: int) -> AsyncIterator[EntityChanged]:
        """Every record after `after`, page by page, in order; the cursor
        follows each one so a crash mid-replay resumes where it stopped."""
        while True:
            try:
                events = await self._client.events_after(after, LIMIT_MAX)
            except ApiError as error:
                if error.code != "stream_truncated" or error.stream_head is None:
                    raise
                await self._resync(error.stream_head)
                return
            for event in events:
                if place(self.cursor, event.seq).kind == "next":
                    self.cursor = event.seq
                    yield EntityChanged.of_event(event)
            if not events or is_last_page(len(events), LIMIT_MAX):
                return
            after = events[-1].seq

    async def _resync(self, head: int) -> None:
        """No page closes a gap below the floor. The consumer reads afresh,
        then the cursor goes on from `head`, so the next replay starts at or
        above the floor and is never refused the same way again."""
        log.info("the stream is trimmed past seq %s; resyncing at %s", self.cursor, head)
        if self._on_resync is not None:
            await self._on_resync()
        if self.cursor is None or head > self.cursor:
            self.cursor = head
