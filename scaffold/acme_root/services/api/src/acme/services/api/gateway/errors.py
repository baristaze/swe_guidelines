"""One handler translates PlatformException, and its infra sibling, into the
error envelope with the status the exception names. Anything else is a 500
with the same shape, rendered by the observability middleware while the
request id is still in hand; the catch-all here is the last resort for what
escapes outside it. Routers never set error status codes."""

import logging
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from acme.infra.exceptions import InfraException
from acme.infra.observability import failure_level
from acme.om.exceptions import LastOwner, PlatformException, SignInDelayed, StreamTruncated
from acme.services.api.gateway.envelope import INTERNAL_ERROR, error_response
from acme.services.api.gateway.observability import request_id_of
from acme.services.api.gateway.ratelimit import RateLimited
from acme.services.api.types.common import LastOwnerDetail, OwnedOrgRef, StreamTruncatedDetail

log = logging.getLogger(__name__)

STARLETTE_CODES = {404: "not_found", 405: "method_not_allowed"}
"""The stable codes for the two Starlette raises for itself; anything else it
raises is presented under `http_error` with its own status."""


def envelope(
    request: Request,
    status: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    stream: StreamTruncatedDetail | None = None,
    last_owner: LastOwnerDetail | None = None,
) -> JSONResponse:
    return error_response(
        request_id_of(request.scope), status, code, message, headers, stream, last_owner
    )


def presented(
    request: Request,
    exc: PlatformException | InfraException,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """A refusal (4xx) tells the client what was wrong. A failure (5xx) is
    the process's problem: its message names backends, hosts, and codes the
    client cannot act on, so it goes to the log under the request id and the
    client reads "internal error" with the exception's own code and status.
    An `unavailable` failure, a dependency that did not answer in time, is
    logged as a warning and any other as an error (`failure_level`): the
    first is the client's to retry, the second a defect to look at.

    These handlers are reached from a socket too, where the scope carries no
    method, so the line is written from the scope and not from a `Request`
    attribute that only an HTTP scope has."""
    if exc.http_status >= 500:
        log.log(
            failure_level(exc),
            "%s on %s %s: %s",
            exc.code,
            request.scope.get("method", "WEBSOCKET"),
            request.scope.get("path", "-"),
            exc.message,
            exc_info=exc,
        )
        return envelope(request, exc.http_status, exc.code, INTERNAL_ERROR[1], headers)
    stream = None
    if isinstance(exc, StreamTruncated):
        # The refusal names where the stream goes on from.
        stream = StreamTruncatedDetail(floor=exc.floor, head=exc.head)
    last_owner = None
    if isinstance(exc, LastOwner):
        # The refusal names the orgs that would be left with nobody to run them.
        last_owner = LastOwnerDetail(
            orgs=[OwnedOrgRef(id=UUID(i), name=n, slug=s) for i, n, s in exc.orgs]
        )
    return envelope(request, exc.http_status, exc.code, exc.message, headers, stream, last_owner)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformException)
    async def platform_exception(request: Request, exc: PlatformException) -> JSONResponse:
        headers = None
        if isinstance(exc, RateLimited | SignInDelayed):
            headers = {"Retry-After": str(max(1, int(exc.retry_after.total_seconds())))}
        return presented(request, exc, headers)

    @app.exception_handler(InfraException)
    async def infra_exception(request: Request, exc: InfraException) -> JSONResponse:
        # Infra is rooted apart from the object model (it imports nothing from
        # it) but carries the same status and code, so it is presented alike
        # (ADR 0005).
        return presented(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Starlette raises its own for an unmatched path (404) and a method a
        # route does not take (405). They are refusals like any other and get
        # the envelope, not the `{"detail": ...}` its default handler writes.
        code = STARLETTE_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
        return envelope(request, exc.status_code, code, message, dict(exc.headers or {}))

    @app.exception_handler(RequestValidationError)
    async def request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        return envelope(request, 422, "validation_failed", details)

    @app.exception_handler(Exception)
    async def catch_all(request: Request, exc: Exception) -> JSONResponse:
        # Starlette runs this outside every middleware, after the request id
        # is gone from the log context; the middleware answers first and only
        # what is raised beyond it reaches here.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return envelope(request, 500, *INTERNAL_ERROR)
