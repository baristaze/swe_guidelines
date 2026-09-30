"""Request id middleware: accept or mint, stamp on the scope, echo in the
response, open the server span with it attached, count the request, log it
as one line naming the route template, and answer an unhandled exception
with the envelope while the id is still in hand."""

import logging
import re
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from uuid import UUID

from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode
from starlette.datastructures import Headers, MutableHeaders

from acme.infra.observability import HTTP_LATENCY, HTTP_REQUESTS, request_id_var
from acme.om.base import new_id
from acme.services.api.gateway.envelope import INTERNAL_ERROR, error_response

log = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

REQUEST_ID_HEADER = "x-request-id"
REQUEST_ID_ATTRIBUTE = "acme.request_id"

tracer = trace.get_tracer("acme.services.api")
"""Resolved against whatever provider boot configured; the no-op one otherwise."""


def parse_request_id(value: str | None) -> UUID:
    if value:
        try:
            return UUID(value)
        except ValueError:
            pass
    return new_id()


def request_id_of(scope: Scope) -> UUID:
    return scope["state"]["request_id"]


TARGET = re.compile(r"(?:WebSocket|%s) (%s)")
"""Where a line names a request's target. uvicorn's socket lines write it
after the word `WebSocket`. Its access line writes it after the method, and
so does the request line the socket library logs at the debug level, on the
logger uvicorn hands it."""


class TargetRedactor(logging.Filter):
    """uvicorn's own lines name a request's target: its path, which is the
    caller's own text, and its query string, the socket's single-use ticket
    among them. They are written under the request's id, which a caller may
    also choose, so the filter writes `-` in the target's place. It tells the
    target by its place in the line, never by its text: a caller sends one in
    any form, with a scheme and a host before the path, or with no slash to
    open it. The middleware's line and the span name the route. Installed on
    the logger uvicorn writes them to."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and isinstance(record.args, tuple):
            match = TARGET.search(record.msg)
            if match:
                place = record.msg.count("%", 0, match.start(1))
                record.args = tuple(
                    "-" if index == place else arg for index, arg in enumerate(record.args)
                )
        return True


HTTP_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)
"""The methods a metric label may name. A client picks the verb, so anything
else becomes "OTHER": an unmatched route already labels itself "unmatched",
and a series per invented verb on it is a cardinality leak anyone can open."""


def method_label(method: str) -> str:
    return method if method in HTTP_METHODS else "OTHER"


UNMATCHED = "unmatched"
"""What a log line, a span, and a series name a request by when no route took
it: the path is the caller's own text, and none of the three carries it."""


def route_template_of(scope: Scope) -> str | None:
    """The matched route's full template, prefix included. FastAPI keeps the
    prefixed path on the effective route context it stores in the scope and
    leaves the sub-router's own path on the route; the test pins the label."""
    context = scope.get("fastapi", {}).get("effective_route_context")
    return getattr(context, "path", None) or getattr(scope.get("route"), "path", None)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = parse_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        scope.setdefault("state", {})["request_id"] = request_id
        method = scope["method"] if scope["type"] == "http" else "WEBSOCKET"
        token = request_id_var.set(str(request_id))
        status = {"code": 0}
        started = time.perf_counter()
        answered: dict[str, float] = {}

        async def send_with_request_id(message: MutableMapping[str, Any]) -> None:
            if message["type"] in ("http.response.start", "websocket.accept"):
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, str(request_id))
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                answered.setdefault("at", time.perf_counter())

        # The server span is opened here, around everything downstream, so the
        # context the gateway builds reads a real trace id. The route template
        # is only known once routing has run, so the name is finished at the end.
        # The span holds nothing the caller wrote: no path, no query string, no
        # header, as a name or as an attribute, and no text of an exception
        # that leaves it, which may quote what the caller sent. Whoever reads
        # a trace under a request's id reads the platform's words alone.
        with tracer.start_as_current_span(
            method,
            kind=SpanKind.SERVER,
            attributes={REQUEST_ID_ATTRIBUTE: str(request_id)},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                # Starlette's own catch-all runs outside this middleware, once
                # the id has left the log context and the span has closed; a
                # response that has not started is answered here instead,
                # with the header, the log line, and the status all carrying
                # the id. One that has started, or a socket, is re-raised, and
                # its span is marked failed; the log line has the traceback.
                if scope["type"] != "http" or status["code"] != 0:
                    span.set_status(StatusCode.ERROR)
                    raise
                # The template, never the path: a path is the caller's text.
                log.exception(
                    "unhandled error on %s %s", method, route_template_of(scope) or UNMATCHED
                )
                response = error_response(request_id, 500, *INTERNAL_ERROR)
                await response(scope, receive, send_with_request_id)
            finally:
                template = route_template_of(scope) or UNMATCHED
                span.update_name(f"{method} {template}")
                span.set_attribute("http.route", template)
                if scope["type"] == "http":
                    # The latency is the caller's: up to the answer's last
                    # byte. What runs after it (the outbox relay,
                    # `gateway/relay.py`) is in the span, not in the latency.
                    elapsed = answered.get("at", time.perf_counter()) - started
                    span.set_attribute("http.response.status_code", status["code"])
                    label = method_label(method)
                    HTTP_REQUESTS.labels(route=template, method=label, status=status["code"]).inc()
                    HTTP_LATENCY.labels(route=template, method=label).observe(elapsed)
                    # The access line, in place of uvicorn's: the template, so
                    # a query string (the socket ticket rides in one) is never
                    # written out. The same four values ride as fields, which
                    # is what a log metric filter reads a route's latency from.
                    millis = round(elapsed * 1000, 1)
                    log.info(
                        "%s %s %d %.1fms",
                        method,
                        template,
                        status["code"],
                        millis,
                        extra={
                            "http": {
                                "method": label,
                                "route": template,
                                "status": status["code"],
                                "duration_ms": millis,
                            }
                        },
                    )
                # Last, after every line this request writes: the access line
                # above carries the id only while the context still holds it.
                request_id_var.reset(token)
