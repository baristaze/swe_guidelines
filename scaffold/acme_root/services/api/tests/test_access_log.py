"""One line per request, from the middleware, naming the route template and
never the path or the query string: a path is the caller's own text, and the
socket ticket travels as a query parameter, so uvicorn's own access log and
its socket-accepted line must print neither."""

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import uvicorn
from uvicorn.server import ServerState

from acme.infra.observability import RequestIdFilter
from acme.services.api.gateway.observability import TargetRedactor
from acme.services.api.main import configure_server_logging, server_options
from acme.services.api.settings import ApiSettings


async def test_the_middleware_logs_the_template_not_the_query(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="acme.services.api.gateway.observability"):
        response = await client.get("/v1/events?after_seq=7&ticket=wst_secret")
    assert response.status_code == 401
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "acme.services.api.gateway.observability"
    ]
    assert len(lines) == 1, lines
    assert "GET /v1/events 401" in lines[0]
    assert "wst_secret" not in lines[0] and "after_seq" not in lines[0]


async def test_the_access_line_carries_the_route_and_its_time_as_fields(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The fields the alarms module's log metric filter reads a route's
    latency from."""
    with caplog.at_level(logging.INFO, logger="acme.services.api.gateway.observability"):
        await client.get("/v1/events?after_seq=7")
    (access,) = [r for r in caplog.records if r.name == "acme.services.api.gateway.observability"]
    fields = access.http  # type: ignore[attr-defined]
    assert {k: fields[k] for k in ("method", "route", "status")} == {
        "method": "GET",
        "route": "/v1/events",
        "status": 401,
    }
    assert isinstance(fields["duration_ms"], float) and fields["duration_ms"] >= 0


async def test_the_access_line_carries_the_request_id(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The line is written in the middleware's `finally`, so the id has to
    still be in the log context when it runs, not reset above it."""
    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.INFO, logger="acme.services.api.gateway.observability"):
        response = await client.get("/v1/events?after_seq=7")
    lines = [r for r in caplog.records if r.name == "acme.services.api.gateway.observability"]
    assert len(lines) == 1, [r.getMessage() for r in lines]
    assert lines[0].request_id == response.headers["x-request-id"]  # type: ignore[attr-defined]


def test_uvicorn_keeps_no_access_log_and_prints_no_path_and_no_query_string() -> None:
    assert (
        server_options(ApiSettings.model_validate({"_env_file": None, "environment": "test"}))[
            "access_log"
        ]
        is False
    )
    configure_server_logging()
    uvicorn_error = logging.getLogger("uvicorn.error")
    assert any(isinstance(f, TargetRedactor) for f in uvicorn_error.filters)
    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:1", "/v1/WIPZ_DOWN_ALL_RULZ?ticket=wst_secret"),
        None,
    )
    uvicorn_error.filter(record)
    assert record.getMessage() == '127.0.0.1:1 - "WebSocket -" [accepted]'


TARGETS = {
    "origin form": b"/v1/WIPZ_DOWN_ALL_RULZ?ticket=wst_secret",
    "absolute form": b"http://elsewhere.example/v1/WIPZ_DOWN_ALL_RULZ?ticket=wst_secret",
    "no leading slash": b"v1/WIPZ_DOWN_ALL_RULZ?ticket=wst_secret",
}
"""A target in each form a caller sends one in."""

CALLERS_TEXT = ("WIPZ_DOWN_ALL_RULZ", "elsewhere.example", "ticket", "wst_secret")
"""What no line may hold of them: the path's free text, the host, and the
query string."""

ANSWERS: dict[str, list[dict[str, Any]]] = {
    "[accepted]": [{"type": "websocket.accept"}],
    "403": [{"type": "websocket.close"}],
    "404": [
        {"type": "websocket.http.response.start", "status": 404, "headers": []},
        {"type": "websocket.http.response.body", "body": b""},
    ],
}
"""What an app answers a socket's opening with, by how uvicorn's line ends:
each is a line of its own in uvicorn."""

UPGRADE = (
    b"Host: api.example\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
)


class Wire(asyncio.Transport):
    """The connection of a protocol that is fed by hand."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return {"peername": ("127.0.0.1", 1), "sockname": ("127.0.0.1", 8000)}.get(name, default)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


@pytest.mark.parametrize("answer", ANSWERS)
@pytest.mark.parametrize("target", TARGETS.values(), ids=list(TARGETS))
async def test_no_line_of_a_socket_holds_its_target(
    target: bytes, answer: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The lines are uvicorn's own, with the arguments it passes them: the
    socket protocol the server's options select reads the opening request and
    answers it. The level is the one that writes the most, where the socket
    library writes the request line out too. So a release of either that
    names the target in a place the filter does not know fails here."""

    async def app(scope: Any, receive: Any, send: Any) -> None:
        for message in ANSWERS[answer]:
            await send(message)

    configure_server_logging()
    with caplog.at_level(logging.DEBUG, logger="uvicorn.error"):
        settings = ApiSettings.model_validate({"_env_file": None, "environment": "test"})
        config = uvicorn.Config(app, **server_options(settings))
        config.load()
        state = ServerState()
        protocol_class: Any = config.ws_protocol_class
        protocol = protocol_class(
            config=config, server_state=state, app_state={}, _loop=asyncio.get_running_loop()
        )
        protocol.connection_made(Wire())
        protocol.data_received(b"GET " + target + b" HTTP/1.1\r\n" + UPGRADE)
        await asyncio.wait(set(state.tasks), timeout=5)
        protocol.connection_lost(None)
    lines = [r.getMessage() for r in caplog.records if r.name == "uvicorn.error"]
    assert f'127.0.0.1:1 - "WebSocket -" {answer}' in lines
    assert not [line for line in lines if any(text in line for text in CALLERS_TEXT)]


@pytest.mark.parametrize("target", TARGETS.values(), ids=list(TARGETS))
def test_the_filter_drops_the_target_from_an_access_line(target: bytes) -> None:
    """uvicorn's access line, which the server keeps off, with the arguments
    uvicorn passes it: the path quoted again, then the query string."""
    path, mark, query = target.decode().partition("?")
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", quote(path) + mark + query, "1.1", 401),
        None,
    )
    assert TargetRedactor().filter(record)
    assert record.getMessage() == '127.0.0.1:1 - "GET - HTTP/1.1" 401'
