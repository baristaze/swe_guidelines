"""A refused ticket is a close with 4401 on an open socket, never a
handshake failure: the socket is accepted first, then the ticket is
redeemed. An admitted socket lives as long as the credential behind its
ticket and no longer."""

import asyncio
import itertools
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from api_support import OWNER, add_member, build_container, run, seed_request, sign_in_as
from httpx import ASGITransport
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.types import Message, Scope
from starlette.websockets import WebSocketDisconnect
from uvicorn.protocols.utils import ClientDisconnected

from acme.infra.exceptions import BackendFailed
from acme.infra.topics import Topics
from acme.om.base import utcnow
from acme.om.exceptions import Unavailable
from acme.om.opcontext import OpContext, Role
from acme.om.tenancy.rules import hash_token
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.gateway.auth import CLOSE_UNAUTHENTICATED
from acme.services.api.realtime.envelopes import ErrorEnvelope
from acme.services.api.realtime.send_buffer import SendBuffer
from acme.services.api.realtime.socket import CLOSE_RECONNECT, recheck_until_refused
from acme.services.api.services.realtime import (
    CREDENTIAL_REVOKED,
    MEMBERSHIP_ENDED,
    RIGHTS_CHANGED,
    RealtimeServiceInterface,
)


def registrations(
    service: RealtimeServiceInterface, monkeypatch: pytest.MonkeyPatch
) -> dict[str, int]:
    """How many sockets the handler registered with the realtime service, and
    how many it unregistered: a socket that ended and stayed registered is
    one the bus still ends, and a tenant whose head the process still keeps."""
    counts = {"attached": 0, "detached": 0}
    attach = service.attach

    def counted(principal: SocketPrincipal, end: Callable[[str], None]) -> Callable[[], None]:
        counts["attached"] += 1
        detach = attach(principal, end)

        def counted_detach() -> None:
            counts["detached"] += 1
            detach()

        return counted_detach

    monkeypatch.setattr(service, "attach", counted)
    return counts


def test_a_refused_ticket_closes_the_accepted_socket_with_4401(tmp_path: Path) -> None:
    with TestClient(create_app(build_container(tmp_path))) as tc:
        with tc.websocket_connect("/v1/realtime?ticket=wst_not_a_ticket") as ws:
            # The handshake succeeded; the refusal is the first thing received.
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    assert closed.value.reason == "not_authenticated"


def test_a_missing_ticket_closes_the_accepted_socket_with_4401(tmp_path: Path) -> None:
    """No ticket at all is the same refusal as a bad one, not a handshake
    failure the client cannot tell from any other."""
    with TestClient(create_app(build_container(tmp_path))) as tc:
        with tc.websocket_connect("/v1/realtime") as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    assert closed.value.reason == "not_authenticated"


def test_a_client_that_drops_mid_stream_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The drainer dies the way it does when the peer is gone; the teardown
    that follows is the normal end of a socket, not an unhandled exception."""
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )

    async def dead_drain(self: SendBuffer, websocket: object) -> None:
        raise OSError("client gone")

    monkeypatch.setattr(SendBuffer, "drain", dead_drain)
    with TestClient(create_app(container)) as tc:
        login = tc.post("/v1/auth/dev-sign-in", json={"email": OWNER["email"]})
        session = tc.post(
            "/v1/auth/sessions",
            json={"org_id": str(org.id)},
            headers={"Authorization": f"Bearer {login.json()['token']}"},
        )
        headers = {"Authorization": f"Bearer {session.json()['token']}"}
        ticket = tc.post("/v1/realtime/tickets", headers=headers).json()["ticket"]
        with caplog.at_level(logging.ERROR):
            with tc.websocket_connect(f"/v1/realtime?ticket={ticket}"):
                pass  # the client leaves at once
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


async def test_the_close_after_the_peer_left_is_not_an_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The app driven the way uvicorn drives it: once the peer's disconnect
    has been read, every send raises the server's disconnect error, which
    Starlette turns into a 1006 disconnect. The handler's own close after
    that is the normal end of a socket: nothing escapes the app and nothing
    is logged as an error."""
    container = build_container(tmp_path)
    _, org = await container.managers.tenancy.bootstrap(
        seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
    )
    app = create_app(container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = await sign_in_as(client, OWNER["email"], org.id)
            ticket = (await client.post("/v1/realtime/tickets", headers=headers)).json()["ticket"]
        inbound: asyncio.Queue[Message] = asyncio.Queue()
        outbound: list[Message] = []
        gone = False

        async def receive() -> Message:
            nonlocal gone
            message = await inbound.get()
            if message["type"] == "websocket.disconnect":
                gone = True
            return message

        async def send(message: Message) -> None:
            if gone:
                raise ClientDisconnected()
            outbound.append(message)

        await inbound.put({"type": "websocket.connect"})
        await inbound.put({"type": "websocket.disconnect", "code": 1001})
        with caplog.at_level(logging.ERROR):
            await app(socket_scope(ticket), receive, send)
    assert [m["type"] for m in outbound] == ["websocket.accept", "websocket.send"]
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


def socket_scope(ticket: str) -> Scope:
    """The ASGI scope of a socket handshake on the channel route."""
    return {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": "/v1/realtime",
        "raw_path": b"/v1/realtime",
        "root_path": "",
        "query_string": f"ticket={ticket}".encode(),
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 40000),
        "server": ("test", 80),
        "subprotocols": [],
    }


def test_a_binary_frame_is_a_bad_command_and_the_socket_stays_open(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    with TestClient(create_app(container)) as tc:
        owner = sign_in(tc, OWNER["email"], org.id)
        with open_socket(tc, owner) as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_bytes(b"\x00\x01")
            refused = ErrorEnvelope.model_validate(ws.receive_json())
            assert refused.code == "bad_command"
            ws.send_json({"op": "ping"})
            assert ws.receive_json()["type"] == "pong"


def session_token(container: AppContainer) -> str:
    """Signs the owner in and returns the session token."""
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    tenancy = container.managers.tenancy

    async def issue() -> str:
        login = await tenancy.dev_sign_in(seed_request(), OWNER["email"])
        identity = await tenancy.authenticate_login(seed_request(), login.token)
        return (await tenancy.exchange_login(identity, org.id)).token

    return run(issue())


def expire_session_in(container: AppContainer, token: str, seconds: float) -> None:
    """Shortens the session behind the token in storage."""
    storage = container.storage.get_tenancy_storage()
    found = run(storage.read_session_by_digest(hash_token(token)))
    assert found is not None
    org_id, session = found
    shortened = session.model_copy(update={"expires_at": utcnow() + timedelta(seconds=seconds)})
    run(storage.write_session(org_id, shortened))


def test_a_socket_is_closed_with_4401_when_the_session_behind_it_expires(tmp_path: Path) -> None:
    """The client does nothing; the server closes at the session's expiry.
    The session is shortened once the app is up and the ticket issued, so a
    slow start does not spend the lifetime before the socket opens."""
    container = build_container(tmp_path)
    token = session_token(container)
    with TestClient(create_app(container)) as tc:
        headers = {"Authorization": f"Bearer {token}"}
        ticket = tc.post("/v1/realtime/tickets", headers=headers).json()["ticket"]
        expire_session_in(container, token, 1.0)
        with tc.websocket_connect(f"/v1/realtime?ticket={ticket}") as ws:
            assert ws.receive_json()["type"] == "hello"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    assert closed.value.reason == "credential_expired"


def sign_in(tc: TestClient, email: str, org_id: UUID) -> dict[str, str]:
    login = tc.post("/v1/auth/dev-sign-in", json={"email": email})
    session = tc.post(
        "/v1/auth/sessions",
        json={"org_id": str(org_id)},
        headers={"Authorization": f"Bearer {login.json()['token']}"},
    )
    return {"Authorization": f"Bearer {session.json()['token']}"}


def open_socket(tc: TestClient, headers: dict[str, str]) -> WebSocketTestSession:
    ticket = tc.post("/v1/realtime/tickets", headers=headers).json()["ticket"]
    return tc.websocket_connect(f"/v1/realtime?ticket={ticket}")


def test_revoking_the_session_behind_a_socket_closes_it_and_no_other(tmp_path: Path) -> None:
    """The revocation travels the bus like any change: the socket of the
    session revoked closes with 4401, a socket of another session stays."""
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    with TestClient(create_app(container)) as tc:
        first = sign_in(tc, OWNER["email"], org.id)
        second = sign_in(tc, OWNER["email"], org.id)
        with open_socket(tc, first) as ws:
            assert ws.receive_json()["type"] == "hello"
            assert tc.post("/v1/auth/logout", headers=second).status_code == 200
            ws.send_json({"op": "ping"})
            assert ws.receive_json()["type"] == "pong"  # still open: another session went
            assert tc.post("/v1/auth/logout", headers=first).status_code == 200
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    assert closed.value.reason == CREDENTIAL_REVOKED


def test_removing_a_member_closes_their_socket(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    bob = run(add_member(container, org.id, "bob@example.test", Role.MEMBER))
    with TestClient(create_app(container)) as tc:
        owner = sign_in(tc, OWNER["email"], org.id)
        member = sign_in(tc, "bob@example.test", org.id)
        with open_socket(tc, member) as ws:
            assert ws.receive_json()["type"] == "hello"
            assert tc.delete(f"/v1/memberships/{bob.id}", headers=owner).status_code == 200
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    assert closed.value.reason == MEMBERSHIP_ENDED


def test_revoking_an_api_key_closes_the_socket_it_opened(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    with TestClient(create_app(container)) as tc:
        owner = sign_in(tc, OWNER["email"], org.id)
        issued = tc.post("/v1/api-keys", headers=owner, json={"name": "ci", "role": "member"})
        key = {"Authorization": f"Bearer {issued.json()['key']}"}
        with open_socket(tc, key) as ws:
            assert ws.receive_json()["type"] == "hello"
            key_id = issued.json()["api_key"]["id"]
            assert tc.delete(f"/v1/api-keys/{key_id}", headers=owner).status_code == 200
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    assert closed.value.reason == CREDENTIAL_REVOKED


def test_a_hello_that_cannot_read_the_head_leaves_no_task_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The head read is the socket's first I/O and it can fail: a database out
    of reach is exactly when every client reconnects at once. The drainer must
    not exist yet when it does, or each of those reconnects leaves a task
    waiting on its buffer for the life of the process; and the socket,
    registered before the read, is unregistered as it closes."""
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )

    async def unreachable(self: object, ctx: object) -> int:
        raise BackendFailed("postgres", "read_head", "connection refused")

    monkeypatch.setattr(type(container.services.get_realtime_service()), "head", unreachable)
    registered = registrations(container.services.get_realtime_service(), monkeypatch)

    drains: list[str] = []
    real_drain = SendBuffer.drain

    async def counted_drain(self: SendBuffer, websocket: object) -> None:
        drains.append("started")
        await real_drain(self, websocket)  # type: ignore[arg-type]

    monkeypatch.setattr(SendBuffer, "drain", counted_drain)
    with TestClient(create_app(container)) as tc:
        headers = sign_in(tc, OWNER["email"], org.id)
        with pytest.raises(WebSocketDisconnect) as closed:
            with open_socket(tc, headers) as ws:
                ws.receive_json()  # the hello that the failed read never built
    assert drains == [], "the drainer was created before the read that failed"
    # The socket was accepted before the route ran, so the failure is a close
    # frame and not an HTTP response the server would refuse as a protocol error.
    assert closed.value.code == 1011
    assert closed.value.reason == "internal_error"
    assert registered == {"attached": 1, "detached": 1}


def test_a_revocation_during_the_hello_still_closes_the_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ticket was good when it was redeemed, and the session is revoked
    while the socket reads its head: the socket is attached before that read,
    so the revocation finds it and it closes with 4401 instead of living until
    the session would have expired."""
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    service = container.services.get_realtime_service()
    working = service.head
    revoked: list[bool] = []

    async def head_while_revoked(ctx: OpContext) -> int:
        if not revoked:
            revoked.append(True)
            assert ctx.credential_id is not None
            await container.managers.tenancy.revoke_session(ctx, ctx.credential_id)
        return await working(ctx)

    monkeypatch.setattr(service, "head", head_while_revoked)
    with TestClient(create_app(container)) as tc:
        headers = sign_in(tc, OWNER["email"], org.id)
        with open_socket(tc, headers) as ws:
            # The hello may or may not go out before the close does.
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:
                    assert ws.receive_json()["type"] == "hello"
    assert revoked == [True]
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    assert closed.value.reason == CREDENTIAL_REVOKED


def socket_handlers(container: AppContainer) -> int:
    """The topic handlers this process holds for sockets. One per open socket
    that subscribed; a socket that ended and left one behind is a leak."""
    topics = container.infra.get_topics()
    subscribers = topics._subscribers._handlers.get(Topics.ENTITY_CHANGED, {})  # type: ignore[attr-defined]
    return sum(1 for consumer, _ in subscribers.values() if consumer.startswith("socket:"))


def test_a_command_that_fails_closes_the_socket_and_leaves_no_subscription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every client pings, and a ping reads the stream head when the head it
    heard is too old (here, always): a database blip during one ends the
    command loop with a backend error. The socket is accepted, so there is
    no HTTP response left to answer with; it closes with 1011 and its
    subscription and drainer go with it, instead of the error escaping the
    teardown and leaving the handler in the dispatcher."""
    container = build_container(tmp_path, realtime_head_max_age_seconds=0)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    service = type(container.services.get_realtime_service())
    working = service.head
    failing = {"now": False}

    async def head(self: object, ctx: object) -> int:
        if failing["now"]:
            raise BackendFailed("postgres", "read_head", "connection refused")
        return await working(self, ctx)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "head", head)
    with TestClient(create_app(container)) as tc:
        headers = sign_in(tc, OWNER["email"], org.id)
        with open_socket(tc, headers) as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_json({"op": "subscribe", "topic": "entity_changed"})
            assert ws.receive_json()["type"] == "subscribed"
            assert socket_handlers(container) == 1
            failing["now"] = True
            ws.send_json({"op": "ping"})
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == 1011
    assert closed.value.reason == "internal_error"
    assert socket_handlers(container) == 0, "the socket's subscription outlived it"


class KeptLines(logging.Handler):
    """The lines one logger writes, kept on that logger itself: the app's
    boot replaces the root's handlers, and a capture there can lose them."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_a_socket_the_database_did_not_answer_in_time_logs_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hello's head read, then a ping's, meet a database that did not
    answer in time. Each socket closes with 1011 as on any failure of its
    handler, and the client reconnects; every line it writes is a warning,
    which the error tracker does not take for an event."""
    container = build_container(tmp_path, realtime_head_max_age_seconds=0)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    service = type(container.services.get_realtime_service())
    working = service.head
    reads = {"left": 0}

    async def head(self: object, ctx: object) -> int:
        if reads["left"] == 0:
            raise Unavailable(
                "the database did not answer in time: a statement passed its deadline"
            )
        reads["left"] -= 1
        return await working(self, ctx)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "head", head)
    kept = KeptLines()
    socket_log = logging.getLogger("acme.services.api.realtime.socket")
    socket_log.addHandler(kept)
    closes: list[tuple[int, str | None]] = []
    try:
        with TestClient(create_app(container)) as tc:
            headers = sign_in(tc, OWNER["email"], org.id)
            # The hello cannot read the head.
            with pytest.raises(WebSocketDisconnect) as closed:
                with open_socket(tc, headers) as ws:
                    ws.receive_json()
            closes.append((closed.value.code, closed.value.reason))
            # The hello reads it, and the ping after it cannot.
            reads["left"] = 1
            with open_socket(tc, headers) as ws:
                assert ws.receive_json()["type"] == "hello"
                ws.send_json({"op": "ping"})
                with pytest.raises(WebSocketDisconnect) as closed:
                    ws.receive_json()
            closes.append((closed.value.code, closed.value.reason))
    finally:
        socket_log.removeHandler(kept)
    assert closes == [(1011, "internal_error"), (1011, "internal_error")]
    messages = [r.getMessage() for r in kept.records]
    assert any("the stream head could not be read" in m for m in messages), messages
    assert any(m.startswith("commands-") for m in messages), messages
    assert {r.levelno for r in kept.records} == {logging.WARNING}


RECHECK = 0.2
"""A recheck interval short enough for a test to wait out."""


def revoke_in_storage(container: AppContainer, token: str) -> None:
    """Revokes the session behind the token in storage alone: no outbox row,
    so no message on the bus, as when the bus lost it."""
    storage = container.storage.get_tenancy_storage()
    found = run(storage.read_session_by_digest(hash_token(token)))
    assert found is not None
    org_id, session = found
    run(storage.write_session(org_id, session.model_copy(update={"revoked_at": utcnow()})))


def test_a_revocation_the_bus_lost_closes_the_socket_within_the_recheck(tmp_path: Path) -> None:
    """The session is revoked and no message is sent: the socket's own
    recheck finds it and closes with 4401 within one interval."""
    container = build_container(tmp_path, realtime_recheck_seconds=RECHECK)
    token = session_token(container)
    with TestClient(create_app(container)) as tc:
        headers = {"Authorization": f"Bearer {token}"}
        with open_socket(tc, headers) as ws:
            assert ws.receive_json()["type"] == "hello"
            revoke_in_storage(container, token)
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == CLOSE_UNAUTHENTICATED
    assert closed.value.reason == "not_authenticated"


def test_a_socket_whose_credential_holds_stays_open_through_its_rechecks(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path, realtime_recheck_seconds=RECHECK)
    token = session_token(container)
    with TestClient(create_app(container)) as tc:
        with open_socket(tc, {"Authorization": f"Bearer {token}"}) as ws:
            assert ws.receive_json()["type"] == "hello"
            for _ in range(4):
                time.sleep(RECHECK)  # the app's loop, on its own thread, rechecks meanwhile
                ws.send_json({"op": "ping"})
                assert ws.receive_json()["type"] == "pong"


def test_a_change_of_role_closes_the_socket_to_reconnect(tmp_path: Path) -> None:
    """The member is still signed in, so the close is 1012 and not 4401: the
    client reconnects, and its new ticket carries the role it has now."""
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    bob = run(add_member(container, org.id, "bob@example.test", Role.ADMIN))
    with TestClient(create_app(container)) as tc:
        owner = sign_in(tc, OWNER["email"], org.id)
        member = sign_in(tc, "bob@example.test", org.id)
        with open_socket(tc, member) as ws:
            assert ws.receive_json()["type"] == "hello"
            changed = tc.patch(f"/v1/memberships/{bob.id}", headers=owner, json={"role": "member"})
            assert changed.status_code == 200
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
        assert closed.value.code == CLOSE_RECONNECT
        assert closed.value.reason == RIGHTS_CHANGED
        # The reconnect is admitted, under the new role.
        with open_socket(tc, member) as ws:
            assert ws.receive_json()["type"] == "hello"


def test_a_change_of_role_the_bus_lost_closes_the_socket_within_the_recheck(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path, realtime_recheck_seconds=RECHECK)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    bob = run(add_member(container, org.id, "bob@example.test", Role.ADMIN))
    storage = container.storage.get_tenancy_storage()
    with TestClient(create_app(container)) as tc:
        member = sign_in(tc, "bob@example.test", org.id)
        with open_socket(tc, member) as ws:
            assert ws.receive_json()["type"] == "hello"
            membership = run(storage.read_membership_for_user(org.id, bob.id))
            assert membership is not None
            demoted = membership.model_copy(update={"role": Role.MEMBER})
            run(storage.write_membership(org.id, demoted, ()))
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == CLOSE_RECONNECT
    assert closed.value.reason == RIGHTS_CHANGED


async def test_the_recheck_runs_every_interval_until_it_is_refused(tmp_path: Path) -> None:
    """The first check waits its phase, every later one the interval; a check
    that holds keeps the loop going, and the first refusal ends the socket
    with its reason and ends the loop."""
    container = build_container(tmp_path)
    tenancy = container.managers.tenancy
    _, org = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"])
    login = await tenancy.dev_sign_in(seed_request(), OWNER["email"])
    identity = await tenancy.authenticate_login(seed_request(), login.token)
    ctx = await tenancy.authenticate(
        seed_request(), (await tenancy.exchange_login(identity, org.id)).token
    )
    principal = await tenancy.redeem_ticket(
        seed_request(), (await tenancy.issue_ticket(ctx)).ticket
    )
    service = container.services.get_realtime_service()
    answers = iter([None, None, "not_authenticated"])
    asked: list[float] = []

    async def recheck(asked_about: SocketPrincipal) -> str | None:
        assert asked_about == principal
        asked.append(time.monotonic())
        return next(answers)

    service.recheck = recheck  # type: ignore[method-assign]
    ended: list[str] = []
    began = time.monotonic()
    await asyncio.wait_for(
        recheck_until_refused(service, principal, ended.append, 0.05, phase=lambda _: 0.0),
        timeout=2.0,
    )
    assert ended == ["not_authenticated"]
    assert len(asked) == 3
    assert asked[0] - began < 0.05, "the first check waits its phase, here none"
    assert all(later - earlier >= 0.04 for earlier, later in itertools.pairwise(asked))


def test_a_recheck_that_cannot_be_made_closes_the_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database out of reach is no proof the credential holds: the socket
    closes with 1011, as on any failure of its handler, is unregistered, and
    the client reconnects once a ticket can be minted again."""
    container = build_container(tmp_path, realtime_recheck_seconds=RECHECK)
    service = container.services.get_realtime_service()

    async def unreachable(principal: object) -> str | None:
        raise BackendFailed("postgres", "read_session", "connection refused")

    monkeypatch.setattr(service, "recheck", unreachable)
    registered = registrations(service, monkeypatch)
    token = session_token(container)
    with TestClient(create_app(container)) as tc:
        with open_socket(tc, {"Authorization": f"Bearer {token}"}) as ws:
            assert ws.receive_json()["type"] == "hello"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == 1011
    assert closed.value.reason == "internal_error"
    assert registered == {"attached": 1, "detached": 1}
