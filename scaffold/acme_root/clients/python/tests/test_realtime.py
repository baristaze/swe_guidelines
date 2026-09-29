"""The channel over a fake socket: order, gaps replayed from the stream,
reconnects that replay after the cursor and wait a jittered backoff, pings on
their own timer, and a refused ticket that stops."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from acme.client import realtime
from acme.client.client import ApiClient
from acme.client.realtime import (
    BACKOFF_SECONDS,
    Channel,
    ChannelRefused,
    Connect,
    SocketLike,
    reconnect_delay_seconds,
)

ORG, USER = uuid4(), uuid4()


def push(seq: int, kind: str = "tenancy.invitation.created") -> str:
    return json.dumps(
        {
            "type": "event",
            "sent_at": None,
            "topic": "entity_changed",
            "payload": {"kind": kind, "target_id": str(uuid4()), "seq": seq, "actor_id": str(USER)},
        }
    )


HELLO = json.dumps(
    {
        "type": "hello",
        "sent_at": None,
        "org_id": str(ORG),
        "user_id": str(USER),
        "seq": 0,
        "ping_interval_seconds": 25,
    }
)


def hello_at(seq: int) -> str:
    return json.dumps({**json.loads(HELLO), "seq": seq})


QUICK_HELLO = json.dumps({**json.loads(HELLO), "ping_interval_seconds": 0})
"""An interval of 0 is clamped to `MIN_PING_SECONDS`, which a test shortens."""

SUBSCRIBED = json.dumps({"type": "subscribed", "sent_at": None, "topic": "entity_changed"})


def pong(seq: int) -> str:
    return json.dumps({"type": "pong", "sent_at": None, "seq": seq})


PING = '{"op": "ping"}'


class FakeSocket:
    """Frames in order; a `Close` entry ends the session the way the server
    would. Once the frames run out the socket is silent until the channel
    pings, and the ping is answered with a push, so a test that reads past
    the script holds the channel to pinging through silence."""

    def __init__(self, frames: list[str | Close]) -> None:
        self.frames = list(frames)
        self.sent: list[str] = []
        self._pinged = asyncio.Event()

    async def recv(self) -> str | bytes:
        if not self.frames:
            await self._pinged.wait()
            self._pinged.clear()
            self.frames.append(push(1))
        frame = self.frames.pop(0)
        if isinstance(frame, Close):
            raise ConnectionClosedError(frame, None)
        return frame

    async def send(self, message: str) -> None:
        self.sent.append(message)
        if message == PING:
            self._pinged.set()


class BusySocket:
    """A hello, then a push every `every` seconds without end: a read never
    waits long enough for silence to be noticed. Records when each ping is sent."""

    def __init__(self, every: float) -> None:
        self.every = every
        self.seq = 0
        self.sent: list[str] = []
        self.pinged_at: list[float] = []

    async def recv(self) -> str | bytes:
        if self.seq == 0:
            self.seq = 1
            return QUICK_HELLO
        await asyncio.sleep(self.every)
        self.seq += 1
        return push(self.seq - 1)

    async def send(self, message: str) -> None:
        self.sent.append(message)
        if message == PING:
            self.pinged_at.append(time.monotonic())


def transport(
    events: list[dict[str, Any]],
    ticket_failures: list[Exception | int] | None = None,
    floor: int = 0,
    asked: list[int] | None = None,
) -> httpx.MockTransport:
    """`ticket_failures` are consumed one per ticket request before tickets succeed:
    an exception to raise, or a status to answer with. A read of the stream
    after a seq below `floor` is refused as the API refuses it, naming the
    last event's seq as the head; `asked` records every `after_seq` read."""
    failures = list(ticket_failures or [])

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/realtime/tickets":
            if failures:
                failure = failures.pop(0)
                if isinstance(failure, Exception):
                    raise failure
                return httpx.Response(failure, json={"error": {"code": "down", "message": "x"}})
            return httpx.Response(201, json={"ticket": "tkt_1", "expires_in_seconds": 30})
        if request.url.path == "/v1/events":
            after = int(request.url.params["after_seq"])
            if asked is not None:
                asked.append(after)
            if after < floor:
                head = max((e["seq"] for e in events), default=floor)
                gone = {"code": "stream_truncated", "message": "gone", "request_id": None}
                stream = {"floor": floor, "head": head}
                return httpx.Response(410, json={"error": {**gone, "stream": stream}})
            return httpx.Response(200, json=[e for e in events if e["seq"] > after])
        raise AssertionError(request.url)

    return httpx.MockTransport(handle)


def record(seq: int) -> dict[str, Any]:
    return {
        "seq": seq,
        "kind": "tenancy.invitation.updated",
        "target_id": str(uuid4()),
        "produced_at": "2026-09-18T12:00:00Z",
        "actor_id": str(USER),
    }


def client_over(
    events: list[dict[str, Any]],
    ticket_failures: list[Exception | int] | None = None,
    floor: int = 0,
    asked: list[int] | None = None,
) -> ApiClient:
    return ApiClient(
        "http://test",
        app="cli",
        app_version="cli@test",
        token="ses_1",
        transport=transport(events, ticket_failures, floor, asked),
    )


async def collect(channel: Channel, count: int) -> list[int]:
    seqs: list[int] = []
    async for change in channel:
        seqs.append(change.seq)
        if len(seqs) == count:
            break
    return seqs


def connect_to(sockets: list[SocketLike], urls: list[str]) -> Connect:
    @asynccontextmanager
    async def connect(url: str, headers: dict[str, str]) -> AsyncIterator[SocketLike]:
        urls.append(url)
        assert headers == {"X-App": "cli", "X-App-Version": "cli@test"}
        yield sockets.pop(0)

    return connect


async def test_pushes_arrive_in_order_and_a_gap_is_replayed_from_the_stream() -> None:
    socket = FakeSocket([HELLO, SUBSCRIBED, push(1), push(2), push(2), push(5)])
    urls: list[str] = []
    states: list[str] = []
    channel = Channel(
        client_over([record(3), record(4), record(5)]),
        on_state=states.append,
        connect=connect_to([socket], urls),
    )
    assert await collect(channel, 5) == [1, 2, 3, 4, 5]
    assert channel.cursor == 5
    assert urls == ["ws://test/v1/realtime?ticket=tkt_1"]
    assert json.loads(socket.sent[0]) == {"op": "subscribe", "topic": "entity_changed"}
    assert states == ["connecting", "open"]


async def test_the_first_open_hook_runs_after_the_hello_and_before_any_change() -> None:
    """A consumer's own read of the state comes after the head the channel
    starts from, so a change committed between the two is still yielded."""
    socket = FakeSocket([HELLO, SUBSCRIBED, push(1)])
    order: list[str] = []

    async def read_state() -> None:
        order.append(f"hook, sent {len(socket.sent)}")

    channel = Channel(client_over([]), connect=connect_to([socket], []), on_first_open=read_state)
    async for change in channel:
        order.append(f"change {change.seq}")
        break
    assert order == ["hook, sent 1", "change 1"], "after the subscribe, before the first change"


async def test_a_first_open_hook_that_fails_runs_again_on_the_next_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime, "BACKOFF_SECONDS", (0.0,))
    first = FakeSocket([HELLO, SUBSCRIBED, push(1)])
    second = FakeSocket([HELLO, SUBSCRIBED, push(1)])
    calls: list[int] = []

    async def read_state() -> None:
        calls.append(len(calls))
        if len(calls) == 1:
            raise httpx.ConnectError("refused")

    channel = Channel(
        client_over([]), connect=connect_to([first, second], []), on_first_open=read_state
    )
    assert await collect(channel, 1) == [1]
    assert calls == [0, 1]


async def test_a_pong_past_the_cursor_replays_what_no_frame_announced() -> None:
    # The push for seq 2 was dropped and nothing followed it; the pong says
    # the stream stands at 3, so the client replays after its cursor.
    socket = FakeSocket([HELLO, SUBSCRIBED, push(1), pong(1), pong(3)])
    channel = Channel(client_over([record(2), record(3)]), connect=connect_to([socket], []))
    assert await collect(channel, 3) == [1, 2, 3]
    assert channel.cursor == 3


async def test_a_dropped_socket_reconnects_and_replays_after_the_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime, "BACKOFF_SECONDS", (0.0,))
    first = FakeSocket([HELLO, SUBSCRIBED, push(1), Close(1006, "gone")])
    second = FakeSocket([HELLO, SUBSCRIBED, push(4)])
    urls: list[str] = []
    states: list[str] = []
    channel = Channel(
        client_over([record(1), record(2), record(3)]),
        on_state=states.append,
        connect=connect_to([first, second], urls),
    )
    assert await collect(channel, 4) == [1, 2, 3, 4]
    assert len(urls) == 2
    assert states == ["connecting", "open", "reconnecting", "open"]


async def test_an_api_that_is_down_while_reconnecting_is_waited_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime, "BACKOFF_SECONDS", (0.0,))
    # The API is down when the channel opens: the ticket call fails at the
    # transport, then the API answers 503 while it comes up, then it is back;
    # later the socket drops and the reconnect finds it healthy.
    first = FakeSocket([HELLO, SUBSCRIBED, push(1), Close(1006, "gone")])
    second = FakeSocket([HELLO, SUBSCRIBED, push(2)])
    failures: list[Exception | int] = [httpx.ConnectError("refused"), 503]
    states: list[str] = []
    channel = Channel(
        client_over([record(1)], failures),
        on_state=states.append,
        connect=connect_to([first, second], []),
    )
    assert await collect(channel, 2) == [1, 2]
    assert states == ["connecting", "reconnecting", "open", "reconnecting", "open"]


async def test_a_ticket_refused_with_401_stops_the_channel() -> None:
    channel = Channel(client_over([], [401]), connect=connect_to([], []))
    with pytest.raises(ChannelRefused):
        await collect(channel, 1)
    assert channel.state == "closed"


async def test_what_happened_before_the_first_push_is_replayed_on_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime, "BACKOFF_SECONDS", (0.0,))
    # The stream stands at 4 when the socket opens; nothing arrives before it
    # drops; two events happen meanwhile; the reconnect replays them.
    first = FakeSocket([hello_at(4), SUBSCRIBED, Close(1006, "gone")])
    second = FakeSocket([hello_at(6), SUBSCRIBED, push(7)])
    channel = Channel(
        client_over([record(3), record(5), record(6)]), connect=connect_to([first, second], [])
    )
    assert await collect(channel, 3) == [5, 6, 7]
    assert channel.cursor == 7


async def test_a_refused_ticket_stops_the_channel() -> None:
    socket = FakeSocket([Close(4401, "invalid_credential")])
    channel = Channel(client_over([]), connect=connect_to([socket], []))
    with pytest.raises(ChannelRefused):
        await collect(channel, 1)
    assert channel.state == "closed"


async def test_a_4401_mid_stream_stops_the_channel_after_what_arrived() -> None:
    """The server closes an open socket with 4401 when the session behind it
    expires or is revoked; what arrived before stays delivered, and the
    channel raises its refusal instead of reconnecting."""
    socket = FakeSocket([HELLO, SUBSCRIBED, push(1), Close(4401, "credential_expired")])
    states: list[str] = []
    channel = Channel(client_over([]), on_state=states.append, connect=connect_to([socket], []))
    seen: list[int] = []
    with pytest.raises(ChannelRefused, match="credential_expired"):
        async for change in channel:
            seen.append(change.seq)
    assert seen == [1]
    assert channel.state == "closed"
    assert states == ["connecting", "open", "closed"]


async def test_silence_is_answered_with_a_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(realtime, "MIN_PING_SECONDS", 0.01)
    socket = FakeSocket([QUICK_HELLO])
    channel = Channel(client_over([]), connect=connect_to([socket], []))
    assert await collect(channel, 1) == [1]
    assert socket.sent[1:] == [PING]


async def test_pings_keep_their_schedule_while_frames_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server counts only what the client sends as a sign of life, so a
    socket busy with pushes still pings every interval, from its own timer."""
    monkeypatch.setattr(realtime, "MIN_PING_SECONDS", 0.02)
    socket = BusySocket(every=0.004)  # five pushes per interval
    channel = Channel(client_over([]), connect=connect_to([socket], []))
    assert await collect(channel, 50) == list(range(1, 51))  # about ten intervals
    assert len(socket.pinged_at) >= 5
    gaps = [b - a for a, b in zip(socket.pinged_at, socket.pinged_at[1:], strict=False)]
    assert all(gap < 0.02 * 3 for gap in gaps), gaps


async def test_a_hello_starts_the_backoff_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quiet listener whose socket drops after every hello is not backed
    off further each time: the hello says the server is there."""
    monkeypatch.setattr(realtime, "BACKOFF_SECONDS", (0.0, 3600.0))
    sockets: list[SocketLike] = [
        FakeSocket([hello_at(1), SUBSCRIBED, Close(1006, "gone")]),
        FakeSocket([hello_at(1), SUBSCRIBED, Close(1006, "gone")]),
        FakeSocket([hello_at(1), SUBSCRIBED, push(2)]),
    ]
    states: list[str] = []
    channel = Channel(client_over([]), on_state=states.append, connect=connect_to(sockets, []))
    assert await asyncio.wait_for(collect(channel, 1), 5) == [2]
    assert states == ["connecting", "open", "reconnecting", "open", "reconnecting", "open"]


async def test_a_trimmed_stream_is_read_afresh_once_and_goes_on_from_the_head() -> None:
    """The cursor stands at 1 and the trim took everything up to 3. The push
    of 5 is a gap no page can close: the replay is refused with the head, the
    consumer reads its state afresh, and the cursor moves to 5. The next push
    and pong read nothing below the floor again."""
    socket = FakeSocket([HELLO, SUBSCRIBED, push(1), push(5), push(6), pong(6), push(7)])
    asked: list[int] = []
    resyncs: list[int | None] = []

    async def read_state() -> None:
        resyncs.append(channel.cursor)

    channel = Channel(
        client_over([record(4), record(5)], floor=3, asked=asked),
        connect=connect_to([socket], []),
        on_resync=read_state,
    )
    assert await collect(channel, 3) == [1, 6, 7]
    assert resyncs == [1], "once, before the cursor moves"
    assert asked == [1], "one refused read, and no other"
    assert channel.cursor == 7


async def test_a_resync_whose_read_fails_leaves_the_cursor_to_resync_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime, "BACKOFF_SECONDS", (0.0,))
    first = FakeSocket([HELLO, SUBSCRIBED, push(1), push(5)])
    second = FakeSocket([hello_at(5), SUBSCRIBED, push(6)])
    asked: list[int] = []
    calls: list[int] = []

    async def read_state() -> None:
        calls.append(len(calls))
        if len(calls) == 1:
            raise httpx.ConnectError("refused")

    channel = Channel(
        client_over([record(4), record(5)], floor=3, asked=asked),
        connect=connect_to([first, second], []),
        on_resync=read_state,
    )
    assert await collect(channel, 2) == [1, 6]
    assert calls == [0, 1]
    assert asked == [1, 1], "the reconnect replays from the cursor the failed resync left"


def test_the_reconnect_delay_grows_and_carries_jitter() -> None:
    """The curve is unchanged: its values are the top of each window. Half of
    each wait is fixed, so the delay grows whatever the randomness answers."""
    for attempt, full in enumerate(BACKOFF_SECONDS):
        assert reconnect_delay_seconds(attempt, lambda: 1.0) == full
        assert reconnect_delay_seconds(attempt, lambda: 0.0) == full / 2
    shortest = [reconnect_delay_seconds(a, lambda: 0.0) for a in range(len(BACKOFF_SECONDS))]
    longest = [reconnect_delay_seconds(a, lambda: 1.0) for a in range(len(BACKOFF_SECONDS))]
    assert shortest == sorted(shortest) and longest == sorted(longest)
    # Where the curve doubles, an attempt's shortest wait is the longest wait
    # of the one before it, so two attempts never draw the same delay. The
    # last step is the cap, less than a doubling, so those two windows do
    # overlap, by the second between 15 and 16.
    for a in range(1, len(BACKOFF_SECONDS)):
        if BACKOFF_SECONDS[a] >= 2 * BACKOFF_SECONDS[a - 1]:
            assert shortest[a] == longest[a - 1]
        else:
            assert (BACKOFF_SECONDS[a - 1], shortest[a], longest[a - 1]) == (16.0, 15.0, 16.0)
    spread = {reconnect_delay_seconds(2, lambda r=r: r) for r in (0.0, 0.25, 0.5, 0.75)}
    assert len(spread) == 4 and all(2.0 <= delay <= 4.0 for delay in spread)
    # The last value holds past the end of the curve, jitter and all.
    assert reconnect_delay_seconds(99, lambda: 1.0) == BACKOFF_SECONDS[-1]
    assert all(0.5 <= reconnect_delay_seconds(0) <= 1.0 for _ in range(50))
