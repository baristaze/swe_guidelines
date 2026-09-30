"""One line per request, from the middleware, naming the route template and
never the path or the query string: a path is the caller's own text, and the
socket ticket travels as a query parameter, so uvicorn's own access log and
its socket-accepted line must print neither."""

import logging

import httpx
import pytest

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
