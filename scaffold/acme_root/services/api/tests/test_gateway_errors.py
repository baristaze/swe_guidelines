"""What the envelope says: a refusal names its reason, a failure the client
cannot act on says "internal error" and keeps its reason in the log, under
the request id."""

import logging

import httpx
import pytest
from fastapi import FastAPI

from acme.infra.exceptions import BackendFailed, BackendUnreachable
from acme.infra.observability import RequestIdFilter
from acme.om.exceptions import PlatformException, Unavailable


class Overloaded(PlatformException):
    http_status = 503
    code = "overloaded"


async def test_a_5xx_message_stays_in_the_log(
    app: FastAPI, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    @app.get("/infra-fails", include_in_schema=False)
    async def infra_fails() -> None:
        raise BackendFailed("s3", "get", "AccessDenied")

    @app.get("/overloaded", include_in_schema=False)
    async def overloaded() -> None:
        raise Overloaded("pool exhausted on db-7")

    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.ERROR):
        infra = await client.get("/infra-fails")
        platform = await client.get("/overloaded")

    assert infra.status_code == 500
    assert infra.json()["error"] == {
        "code": "backend_failed",
        "message": "internal error",
        "request_id": infra.headers["x-request-id"],
    }
    assert platform.status_code == 503
    assert platform.json()["error"]["code"] == "overloaded"
    assert platform.json()["error"]["message"] == "internal error"

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.request_id for r in errors] == [  # type: ignore[attr-defined]
        infra.headers["x-request-id"],
        platform.headers["x-request-id"],
    ]
    assert "s3 get failed with AccessDenied" in errors[0].getMessage()
    assert "pool exhausted on db-7" in errors[1].getMessage()


async def test_an_unavailable_failure_is_a_warning_and_not_an_error(
    app: FastAPI, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A dependency that did not answer in time is "not right now": the
    client comes back, so it is logged as a warning, which the error tracker
    does not take for an event. Read by its code, from either root."""

    @app.get("/database-slow", include_in_schema=False)
    async def database_slow() -> None:
        raise Unavailable("the database did not answer in time: a statement passed its deadline")

    @app.get("/backend-slow", include_in_schema=False)
    async def backend_slow() -> None:
        raise BackendUnreachable("s3", "get", "ReadTimeoutError")

    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.WARNING):
        answers = [await client.get("/database-slow"), await client.get("/backend-slow")]

    for answer in answers:
        assert answer.status_code == 503
        assert answer.json()["error"] == {
            "code": "unavailable",
            "message": "internal error",
            "request_id": answer.headers["x-request-id"],
        }
    lines = [r for r in caplog.records if r.name == "acme.services.api.gateway.errors"]
    assert [(r.levelno, r.request_id) for r in lines] == [  # type: ignore[attr-defined]
        (logging.WARNING, answer.headers["x-request-id"]) for answer in answers
    ]
    assert "a statement passed its deadline" in lines[0].getMessage()


async def test_a_refusal_still_names_its_reason(client: httpx.AsyncClient) -> None:
    refused = await client.get("/v1/media/files")
    assert refused.status_code == 401
    assert refused.json()["error"]["message"] == "missing bearer credential"


async def test_a_404_and_a_405_answer_the_envelope(client: httpx.AsyncClient) -> None:
    """Starlette raises its own HTTPException for a path it does not match and
    a method a route does not take. They are refusals like any other, so they
    carry the envelope and the request id, not `{"detail": ...}`."""
    missing = await client.get("/v1/no-such-thing")
    assert missing.status_code == 404
    body = missing.json()["error"]
    assert body["code"] == "not_found"
    assert body["request_id"] == missing.headers["x-request-id"]

    wrong_method = await client.delete("/v1/events")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"


async def test_a_500_is_answered_inside_cors(app: FastAPI, client: httpx.AsyncClient) -> None:
    """The request-id middleware writes the 500 envelope itself. Outside CORS
    the browser blocks that body and the x-request-id it carries, while a 401
    from the same origin comes through, so a failure reaches the portal as a
    fetch error with nothing to report."""

    @app.get("/cors-boom", include_in_schema=False)
    async def cors_boom() -> None:
        raise RuntimeError("the pool is gone")

    origin = {"Origin": "http://localhost:5173"}
    failed = await client.get("/cors-boom", headers=origin)
    assert failed.status_code == 500
    assert failed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-request-id" in failed.headers["access-control-expose-headers"].lower()

    # The same headers a refusal already carried.
    refused = await client.get("/v1/events", headers=origin)
    assert refused.status_code == 401
    assert refused.headers["access-control-allow-origin"] == "http://localhost:5173"
