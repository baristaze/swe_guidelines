"""A revocation crosses processes: two API containers over the compose
stack's Postgres and Valkey, one serving a socket, the other revoking the
session behind it; the socket closes with 4401 on the wire. When the bus
never carries the message, the serving process's own recheck closes it
within its interval. A change of role closes the socket with 1012. The pong
carries the head heard on the bus, and reads it once that is too old."""

import asyncio
import json
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets
from api_support import OWNER, add_member, seed_request, sign_in_as
from websockets.exceptions import ConnectionClosed

from acme.om.base import new_id
from acme.om.opcontext import IdentityContext, OpContext, Role
from acme.om.tenancy.types.org import Org
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.gateway.auth import CLOSE_UNAUTHENTICATED
from acme.services.api.main import server_options
from acme.services.api.realtime.socket import CLOSE_RECONNECT
from acme.services.api.services.realtime import CREDENTIAL_REVOKED, RIGHTS_CHANGED
from acme.services.api.settings import ApiSettings
from acme.services.api.types.media import StartUploadRequest

pytestmark = pytest.mark.integration


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def settings_over_the_stack(
    tmp_path: Path, topics_backend: str = "valkey", **overrides: object
) -> ApiSettings:
    """The database from ACME_DATABASE_URL, the bus and the cache on the
    compose Valkey; refused unless both are local. A process given the
    memory bus publishes where no other process hears: every message it
    sends is lost to the others."""
    settings = ApiSettings.model_validate(
        {
            "cache_backend": "valkey",
            "topics_backend": topics_backend,
            "buckets_root": tmp_path / "buckets",
            **OVER_THE_STACK,
            **overrides,
        }
    )
    settings.refuse_remote()
    return settings


OVER_THE_STACK: dict[str, object] = {
    "buckets_backend": "local",
    "queues_backend": "memory",
    "secrets_backend": "local",
    # The people of the run sign in by address alone, as the local stack does.
    "dev_sign_in_enabled": True,
    # The rest comes from the environment and .env, the tracker's DSN and
    # the collector aside: a test reports nothing anywhere.
    "sentry_dsn": None,
    "otel_endpoint": None,
}


@dataclass(frozen=True)
class TwoProcesses:
    address: str  # where process B serves
    revoker: AppContainer  # process A
    server: AppContainer  # process B
    org: Org
    email: str  # the owner seeded for this run; the database is shared


@asynccontextmanager
async def serving(
    tmp_path: Path, revoker_bus: str = "valkey", **server_settings: object
) -> AsyncIterator[TwoProcesses]:
    """Process B serves the socket on a real port; process A is the one that
    revokes. Each has its own listener on the bus, as two replicas do, unless
    A is given the memory bus, which no other process hears."""
    a = AppContainer.build(settings_over_the_stack(tmp_path, revoker_bus))
    settings = settings_over_the_stack(tmp_path, "valkey", **server_settings)
    b = AppContainer.build(settings)
    await a.start()
    suffix = new_id().hex[-8:]  # the random tail; a uuid7 leads with the clock
    email = f"ann-{suffix}@example.test"
    _, org = await a.managers.tenancy.bootstrap(
        seed_request(), "Ajax", f"ajax-{suffix}", email, OWNER["name"]
    )
    port = free_port()
    config = uvicorn.Config(create_app(b), host="127.0.0.1", port=port, **server_options(settings))
    config.load()
    server = uvicorn.Server(config)
    server.lifespan = config.lifespan_class(config)
    await server.startup()
    serving = asyncio.create_task(server.main_loop())
    try:
        yield TwoProcesses(f"127.0.0.1:{port}", a, b, org, email)
    finally:
        server.should_exit = True
        await serving
        await server.shutdown()
        await a.close()


@pytest.fixture
async def two_processes(tmp_path: Path) -> AsyncIterator[TwoProcesses]:
    async with serving(tmp_path) as processes:
        yield processes


async def test_a_revocation_in_one_process_closes_the_socket_in_another(
    two_processes: TwoProcesses,
) -> None:
    address, revoker, org = two_processes.address, two_processes.revoker, two_processes.org
    async with httpx.AsyncClient(base_url=f"http://{address}") as client:
        headers = await sign_in_as(client, two_processes.email, org.id)
        ticket = (await client.post("/v1/realtime/tickets", headers=headers)).json()["ticket"]
    token = headers["Authorization"].removeprefix("Bearer ")
    async with websockets.connect(f"ws://{address}/v1/realtime?ticket={ticket}") as ws:
        assert '"type":"hello"' in str(await ws.recv())
        # Process A revokes the session; its relay publishes on the shared bus.
        ictx = await revoker.managers.tenancy.authenticate_login(seed_request(), token)
        await revoker.managers.tenancy.logout(ictx)
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), timeout=10)
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == CLOSE_UNAUTHENTICATED
    assert closed.value.rcvd.reason == CREDENTIAL_REVOKED


RECHECK_SECONDS = 1.0


async def ticket_for(address: str, headers: dict[str, str]) -> str:
    async with httpx.AsyncClient(base_url=f"http://{address}") as client:
        return (await client.post("/v1/realtime/tickets", headers=headers)).json()["ticket"]


async def headers_of(address: str, email: str, org: Org) -> dict[str, str]:
    async with httpx.AsyncClient(base_url=f"http://{address}") as client:
        return await sign_in_as(client, email, org.id)


async def context_of(container: AppContainer, headers: dict[str, str]) -> OpContext:
    token = headers["Authorization"].removeprefix("Bearer ")
    return await container.managers.tenancy.authenticate(seed_request(), token)


async def identity_of(container: AppContainer, headers: dict[str, str]) -> IdentityContext:
    """The identity stage the session behind `headers` proves: what a
    sign-out takes."""
    token = headers["Authorization"].removeprefix("Bearer ")
    return await container.managers.tenancy.authenticate_login(seed_request(), token)


async def closed_within(ws: websockets.ClientConnection, seconds: float) -> ConnectionClosed:
    with pytest.raises(ConnectionClosed) as closed:
        while True:
            await asyncio.wait_for(ws.recv(), timeout=seconds)
    return closed.value


async def test_a_revocation_the_bus_never_carried_closes_within_the_recheck(
    tmp_path: Path,
) -> None:
    """Process A revokes on a bus B does not hear: the message is lost to B.
    B's own recheck finds the session revoked and closes the socket with
    4401 within one interval."""
    async with serving(
        tmp_path, revoker_bus="memory", realtime_recheck_seconds=RECHECK_SECONDS
    ) as processes:
        address, revoker = processes.address, processes.revoker
        headers = await headers_of(address, processes.email, processes.org)
        ticket = await ticket_for(address, headers)
        async with websockets.connect(f"ws://{address}/v1/realtime?ticket={ticket}") as ws:
            assert '"type":"hello"' in str(await ws.recv())
            await revoker.managers.tenancy.logout(await identity_of(revoker, headers))
            started = time.monotonic()
            closed = await closed_within(ws, RECHECK_SECONDS + 5)
            waited = time.monotonic() - started
    assert closed.rcvd is not None
    assert closed.rcvd.code == CLOSE_UNAUTHENTICATED
    assert closed.rcvd.reason == "not_authenticated"
    assert waited <= RECHECK_SECONDS + 1.0, f"closed after {waited:.2f}s"


async def test_a_change_of_role_in_one_process_closes_the_socket_in_another(
    tmp_path: Path,
) -> None:
    """The bus names the membership; B closes its member's socket with 1012,
    and the reconnect is admitted under the new role."""
    async with serving(tmp_path) as processes:
        address, revoker, org = processes.address, processes.revoker, processes.org
        bob = await add_member(revoker, org.id, f"bob-{new_id().hex[-8:]}@example.test", Role.ADMIN)
        identity = await revoker.storage.get_tenancy_storage().read_identity(bob.identity_id)
        assert identity is not None
        member = await headers_of(address, identity.email, org)
        owner = await headers_of(address, processes.email, org)
        ticket = await ticket_for(address, member)
        async with websockets.connect(f"ws://{address}/v1/realtime?ticket={ticket}") as ws:
            assert '"type":"hello"' in str(await ws.recv())
            await revoker.managers.tenancy.update_membership_role(
                await context_of(revoker, owner), bob.id, Role.MEMBER
            )
            closed = await closed_within(ws, 10)
        assert closed.rcvd is not None
        assert closed.rcvd.code == CLOSE_RECONNECT
        assert closed.rcvd.reason == RIGHTS_CHANGED
        ticket = await ticket_for(address, member)
        async with websockets.connect(f"ws://{address}/v1/realtime?ticket={ticket}") as ws:
            assert '"type":"hello"' in str(await ws.recv())


def upload(name: str) -> StartUploadRequest:
    """A write the relay announces on the bus: its event gets the next seq."""
    return StartUploadRequest(name=f"{name}.pdf", content_type="application/pdf", size_bytes=5)


async def next_of(ws: websockets.ClientConnection, kind: str) -> dict[str, object]:
    """The next frame of `kind`, skipping any other."""
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if frame["type"] == kind:
            return frame


async def test_the_pong_carries_the_head_heard_on_the_bus(tmp_path: Path) -> None:
    """A starts an upload; B hears its hint and answers the next ping with its
    seq, and reads nothing for it: the hello's read is the only one."""
    async with serving(tmp_path) as processes:
        address, writer, server, org = (
            processes.address,
            processes.revoker,
            processes.server,
            processes.org,
        )
        reads: list[int] = []
        events = server.managers.events
        read = events.get_head

        async def counted(ctx: OpContext) -> int:
            reads.append(1)
            return await read(ctx)

        events.get_head = counted  # type: ignore[method-assign]
        headers = await headers_of(address, processes.email, org)
        ticket = await ticket_for(address, headers)
        async with websockets.connect(f"ws://{address}/v1/realtime?ticket={ticket}") as ws:
            hello = await next_of(ws, "hello")
            await ws.send(json.dumps({"op": "subscribe", "topic": "entity_changed"}))
            await next_of(ws, "subscribed")
            await writer.services.get_media_service().start_upload(
                await context_of(writer, headers), upload("heard"), new_id()
            )
            hint = await next_of(ws, "event")
            await ws.send(json.dumps({"op": "ping"}))
            pong = await next_of(ws, "pong")
    seq = hint["payload"]["seq"]  # type: ignore[index]
    assert seq == hello["seq"] + 1  # type: ignore[operator]
    assert pong["seq"] == seq
    assert reads == [1], "the pong read the head it had heard"


async def test_a_hint_b_never_heard_shows_once_the_head_is_too_old(tmp_path: Path) -> None:
    """A writes on a bus B does not hear, as when B's subscription is down:
    B's pong answers the head it knows until that is older than the bound,
    then reads it, and the client sees the gap."""
    max_age = 1.0
    async with serving(
        tmp_path, revoker_bus="memory", realtime_head_max_age_seconds=max_age
    ) as processes:
        address, writer, org = processes.address, processes.revoker, processes.org
        headers = await headers_of(address, processes.email, org)
        ticket = await ticket_for(address, headers)
        async with websockets.connect(f"ws://{address}/v1/realtime?ticket={ticket}") as ws:
            hello = await next_of(ws, "hello")
            await writer.services.get_media_service().start_upload(
                await context_of(writer, headers), upload("unheard"), new_id()
            )
            await ws.send(json.dumps({"op": "ping"}))
            within = await next_of(ws, "pong")
            await asyncio.sleep(max_age + 0.2)
            await ws.send(json.dumps({"op": "ping"}))
            past = await next_of(ws, "pong")
    assert within["seq"] == hello["seq"]
    assert past["seq"] == hello["seq"] + 1  # type: ignore[operator]
