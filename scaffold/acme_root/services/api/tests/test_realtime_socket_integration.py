"""The close code on the wire, as a real client sees it: a websockets client
against a uvicorn instance serving the app, not the test client, because
only the server decides what a close before the accept becomes."""

import asyncio
import json
import logging
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import uvicorn
import websockets
from api_support import OWNER, build_container, seed_request, sign_in_as
from websockets.exceptions import ConnectionClosed

from acme.infra.topics import EntityChangedPayload, Topics
from acme.om.base import new_id, utcnow
from acme.om.tenancy.types.org import Org
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.gateway.auth import CLOSE_UNAUTHENTICATED
from acme.services.api.main import server_options
from acme.services.api.settings import ApiSettings

pytestmark = pytest.mark.integration


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@dataclass
class Serving:
    address: str
    org: Org
    server: uvicorn.Server

    async def settled(self) -> None:
        """Waits for the server's socket handlers to end, so what a client's
        close did to the handler is known before the test looks."""
        if handlers := set(self.server.server_state.tasks):
            await asyncio.wait(handlers, timeout=5)


@asynccontextmanager
async def serving(container: AppContainer) -> AsyncIterator[Serving]:
    """The container on a real port, with one org seeded. A test that needs a
    bound of its own builds the container and opens this itself; the fixture
    below is the same thing over the plain test container."""
    _, org = await container.managers.tenancy.bootstrap(
        seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
    )
    port = free_port()
    settings = ApiSettings.model_validate({"_env_file": None, "environment": "test"})
    config = uvicorn.Config(
        create_app(container), host="127.0.0.1", port=port, **server_options(settings)
    )
    # The steps of `Server.serve`, so the caller knows when the port is open.
    config.load()
    server = uvicorn.Server(config)
    server.lifespan = config.lifespan_class(config)
    await server.startup()
    running = asyncio.create_task(server.main_loop())
    try:
        yield Serving(f"127.0.0.1:{port}", org, server)
    finally:
        server.should_exit = True
        await running
        await server.shutdown()


@pytest.fixture
async def served(tmp_path: Path) -> AsyncIterator[Serving]:
    """The API on a real port, over memory storage, with one org seeded."""
    async with serving(build_container(tmp_path)) as running:
        yield running


async def test_a_refused_ticket_is_a_4401_close_on_the_wire(served: Serving) -> None:
    address = served.address
    async with websockets.connect(f"ws://{address}/v1/realtime?ticket=wst_not_a_ticket") as ws:
        with pytest.raises(ConnectionClosed) as closed:
            await ws.recv()
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == CLOSE_UNAUTHENTICATED
    assert closed.value.rcvd.reason == "not_authenticated"


async def ticket_for(served: Serving) -> str:
    async with httpx.AsyncClient(base_url=f"http://{served.address}") as client:
        headers = await sign_in_as(client, OWNER["email"], served.org.id)
        return (await client.post("/v1/realtime/tickets", headers=headers)).json()["ticket"]


async def test_a_good_ticket_opens_the_channel(served: Serving) -> None:
    ticket = await ticket_for(served)
    async with websockets.connect(f"ws://{served.address}/v1/realtime?ticket={ticket}") as ws:
        hello = await ws.recv()
    assert '"type":"hello"' in str(hello)


async def test_a_client_that_closes_is_not_an_error_on_the_server(
    served: Serving, caplog: pytest.LogCaptureFixture
) -> None:
    """The server, not the test client, decides what a send after the peer
    left becomes; here a client's own close ends the handler quietly."""
    ticket = await ticket_for(served)
    with caplog.at_level(logging.ERROR):
        async with websockets.connect(f"ws://{served.address}/v1/realtime?ticket={ticket}") as ws:
            await ws.recv()
        await served.settled()
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


FLOOD = 64
"""Far more events than the stream lane below holds."""

STREAM_LANE = 4
"""The flooded socket's stream lane, small so the flood overruns it."""


async def test_a_flood_of_hints_drops_hints_and_not_the_pong(tmp_path: Path) -> None:
    """The lanes on the wire. The tenant's stream is flooded while a ping is
    in flight: the stream lane overruns and the pong does not, so the client
    learns the head seq from the pong, sees the gap between it and the hints
    that reached it, and replays the rest over `/v1/events`. The flood is the
    burst a sweep makes when it relays a batch of rows back to back, which is
    the one moment a socket is offered more than it can write. That control
    is drained first, and never evicted, is held frame by frame in
    `test_send_buffer.py`; what the wire adds is the end of it, which is what
    the client does with the gap."""
    container = build_container(tmp_path, realtime_send_buffer_size=STREAM_LANE)
    async with serving(container) as served:
        async with httpx.AsyncClient(base_url=f"http://{served.address}") as client:
            headers = await sign_in_as(client, OWNER["email"], served.org.id)
            ticket = (await client.post("/v1/realtime/tickets", headers=headers)).json()["ticket"]
            channel = f"ws://{served.address}/v1/realtime?ticket={ticket}"
            async with websockets.connect(channel) as ws:
                hello = json.loads(await ws.recv())
                assert hello["type"] == "hello" and hello["seq"] == 0
                # The stream fills while the socket is subscribed to nothing,
                # so every one of these is a hint it has yet to be told about.
                targets: list[UUID] = []
                for index in range(FLOOD):
                    created = await client.post(
                        "/v1/media/files",
                        headers=headers,
                        json={
                            "name": f"report-{index}.pdf",
                            "content_type": "application/pdf",
                            "size_bytes": 5,
                        },
                    )
                    assert created.status_code == 201, created.text
                    targets.append(UUID(created.json()["id"]))
                await ws.send(json.dumps({"op": "subscribe", "topic": Topics.ENTITY_CHANGED.value}))
                assert json.loads(await ws.recv())["type"] == "subscribed"

                await ws.send(json.dumps({"op": "ping"}))
                for seq, target in enumerate(targets, start=1):
                    await container.infra.get_topics().publish(
                        Topics.ENTITY_CHANGED,
                        EntityChangedPayload(
                            idempotency_key=new_id(),
                            produced_at=utcnow(),
                            org_id=served.org.id,
                            kind="media.file.created",
                            target_id=target,
                            seq=seq,
                        ),
                    )
                frames = [
                    json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    for _ in range(1 + STREAM_LANE)
                ]

            # The pong is on the wire with the head seq, and the hints that
            # survived are the newest: the lane dropped the oldest of them.
            assert [frame["seq"] for frame in frames if frame["type"] == "pong"] == [FLOOD]
            hints = [frame["payload"]["seq"] for frame in frames if frame["type"] == "event"]
            assert hints == list(range(FLOOD - STREAM_LANE + 1, FLOOD + 1))
            # What the client does about the gap between what it knew and the
            # first hint it saw: it replays from the last seq it trusted.
            assert hints[0] > hello["seq"] + 1
            replayed = await client.get(
                "/v1/events", headers=headers, params={"after_seq": hello["seq"], "limit": FLOOD}
            )
            assert [event["seq"] for event in replayed.json()] == list(range(1, FLOOD + 1))
