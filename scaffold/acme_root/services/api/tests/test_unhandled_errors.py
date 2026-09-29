"""An unhandled exception still gets the gateway's treatment: the envelope
with the request id, the id in the response header, the status counted, and
the log line carrying the id."""

import logging

import httpx
import pytest
from fastapi import FastAPI

from acme.infra.observability import RequestIdFilter


async def test_a_500_keeps_the_request_id(
    app: FastAPI, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    @app.get("/boom", include_in_schema=False)
    async def boom() -> None:
        raise RuntimeError("the database ate my homework")

    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.ERROR):
        response = await client.get("/boom")
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "internal_error" and body["message"] == "internal error"
    assert response.headers["x-request-id"] == body["request_id"]

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, [r.getMessage() for r in errors]
    assert errors[0].request_id == body["request_id"]  # type: ignore[attr-defined]
    assert "the database ate my homework" in (errors[0].exc_text or "")

    metrics = await client.get("/metrics")
    counted = [
        line for line in metrics.text.splitlines() if line.startswith("acme_http_requests_total")
    ]
    assert any('route="/boom"' in line and 'status="500"' in line for line in counted), counted
