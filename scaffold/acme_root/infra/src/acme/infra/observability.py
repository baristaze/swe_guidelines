"""Logging with the request-id filter, error reporting, tracing, and the
process metrics. Configured once at the app container's boot and never per
module."""

import json
import logging
import sys
import traceback
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import sentry_sdk
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Link, Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

from acme.infra.exceptions import InfraUnavailable

request_id_var: ContextVar[str | None] = ContextVar("acme_request_id", default=None)
"""Set at the entry point that builds the context, so log lines get it for free.

This is the one piece of ambient state in the platform, and it is ambient only
to the log and error sinks in this module. The authoritative request id is
`request_id` on the context, which every stage inherits and every operation is
handed; nothing decides anything from this variable. So the boundary is a rule:
it is read only here, and an entry point that sets it resets its token in a
`finally`, so it never outlives the unit of work that set it. Both halves are
checked by `infra/tests/test_observability_boundary.py` rather than trusted."""

caused_by_request_id_var: ContextVar[str | None] = ContextVar(
    "acme_caused_by_request_id", default=None
)
"""The request that caused the work an entry point runs, where a handoff named
one; empty at the edge, where nothing caused the request.

It is the request id's sibling and it lives under the same boundary: read only
here, set only by an entry point that resets its token in a `finally`, and
authoritative nowhere. The stage the work runs under is what an operation
reads, `caused_by_request_id` on it; this variable exists so the log lines of
a run name its cause for free."""

HTTP_REQUESTS = Counter(
    "acme_http_requests_total",
    "HTTP requests by route template and status",
    ["route", "method", "status"],
)
HTTP_LATENCY = Histogram(
    "acme_http_request_seconds", "HTTP request latency by route template", ["route", "method"]
)
OUTCOMES = Counter(
    "acme_outcomes_total",
    "Outcomes of queues, caches, rate limits, and sweeps",
    ["subsystem", "outcome"],
)
SWEEP_SECONDS = Histogram(
    "acme_sweep_seconds",
    "How long one maintenance sweep pass took",
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120),
)
# The sweep's reads of the work queue and the outbox, one each per pass, as
# the pass read them. The same four numbers are fields of the pass's log
# line, which the alarms read in the cloud; these are what Grafana draws.
WORK_OLDEST_READY_SECONDS = Gauge(
    "acme_work_oldest_ready_seconds",
    "How long the work item ready longest has waited for a worker, on any lane",
)
WORK_FAILED_RECENTLY = Gauge(
    "acme_work_failed_recently",
    "Work items that failed in the last fifteen minutes and are still failed",
)
OUTBOX_OLDEST_PENDING_SECONDS = Gauge(
    "acme_outbox_oldest_pending_seconds",
    "How long ago the oldest outbox row that is neither relayed nor failed landed",
)
OUTBOX_FAILED_RECENTLY = Gauge(
    "acme_outbox_failed_recently",
    "Outbox rows that failed for good in the last fifteen minutes",
)


@dataclass(frozen=True)
class ProcessIdentity:
    """Who wrote a log line: the service and the environment it ran in. One
    query reads across processes on these two, so every line carries them."""

    service: str = "unknown"
    environment: str = "unknown"


_process = ProcessIdentity()


def name_process(service: str, environment: str) -> None:
    """Names the process for every line it writes from here on. Called once at
    boot, with the values settings already hand the sinks; the filter reads
    the answer instead of the environment, so no call site chooses and no
    formatter reads `os.environ`."""
    global _process
    _process = ProcessIdentity(service=service, environment=environment)


class RequestIdFilter(logging.Filter):
    """The fields no call site passes by hand: the process on every line, the
    request on the lines a request wrote, and the request that caused it where
    a handoff named one."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = _process.service
        record.environment = _process.environment
        record.request_id = request_id_var.get() or "-"
        record.caused_by_request_id = caused_by_request_id_var.get() or "-"
        return True


PLATFORM_PACKAGE = __name__.partition(".")[0]
"""The root package every module of this tree sits under (ADR 0001): the one
that defines each exception the platform raises."""


def failure_text(error: BaseException) -> str | None:
    """The text of `error` that a log line, the tracker, and a failure record
    carry. Only a failure (a 5xx) the platform raised under its own roots
    keeps its text, which names a backend, an operation, a code, or an id.

    Any other exception's text quotes what it was handed. Pydantic's
    `ValidationError` quotes the input it refused. A driver's constraint
    error quotes the row, in its `DETAIL` line. A refusal (a 4xx) quotes what
    the caller sent, and the envelope hands that back to the caller alone.
    So such an exception leaves the process as its type and its frames, and
    its words stay in it, whatever type is raised next."""
    status = getattr(error, "http_status", None)
    ours = type(error).__module__.partition(".")[0] == PLATFORM_PACKAGE
    if ours and isinstance(status, int) and status >= 500:
        return str(error)
    return None


def described(error: BaseException) -> str:
    """What a log line, the tracker, and a failure record say of `error`: its
    type, and the text `failure_text` keeps."""
    text = failure_text(error)
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


CAUSE = "\nThe above exception was the direct cause of the following exception:\n\n"
CONTEXT = "\nDuring handling of the above exception, another exception occurred:\n\n"


def _chain(error: BaseException) -> Iterator[tuple[BaseException, str]]:
    """`error`, then the exception it was raised from or while handling, and
    so on back. Each comes with the line a traceback prints after it, before
    the exception it led to."""
    seen: set[int] = set()
    current: BaseException | None = error
    joint = ""
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current, joint
        if current.__cause__ is not None:
            current, joint = current.__cause__, CAUSE
        elif not current.__suppress_context__:
            current, joint = current.__context__, CONTEXT
        else:
            current = None


def traceback_of(error: BaseException) -> str:
    """The traceback Python prints for `error`, oldest exception first, with
    every frame and each exception's type, and only the text `failure_text`
    keeps. A note added to an exception is left out with the rest of its
    text."""
    blocks: list[str] = []
    for exception, joint in _chain(error):
        kind = type(exception)
        name = kind.__qualname__
        if kind.__module__ not in ("builtins", "__main__"):
            name = f"{kind.__module__}.{name}"
        text = failure_text(exception)
        frames = traceback.format_tb(exception.__traceback__)
        head = "Traceback (most recent call last):\n" + "".join(frames) if frames else ""
        blocks.append(f"{head}{name}{': ' + text if text else ''}\n{joint}")
    return "".join(reversed(blocks)).rstrip("\n")


def message_of(record: logging.LogRecord) -> str:
    """The record's message as `getMessage` builds it, with each exception in
    it, the template or an argument, `described`."""
    template = described(record.msg) if isinstance(record.msg, BaseException) else str(record.msg)
    args = record.args
    if not args:
        return template
    if isinstance(args, Mapping):
        return template % {key: _held(value) for key, value in args.items()}
    return template % tuple(_held(arg) for arg in args)


def _held(arg: object) -> object:
    return described(arg) if isinstance(arg, BaseException) else arg


class JsonFormatter(logging.Formatter):
    """One JSON object a line. An exception in it, logged with the line or
    named in its message, says what `described` says and no more."""

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message_of(record),
            "service": getattr(record, "service", _process.service),
            "environment": getattr(record, "environment", _process.environment),
            "request_id": getattr(record, "request_id", "-"),
        }
        # Only where a handoff named one: a line with no cause says so by
        # carrying no field, rather than by carrying an empty one.
        caused_by = getattr(record, "caused_by_request_id", "-")
        if caused_by != "-":
            line["caused_by_request_id"] = caused_by
        # The access line's own fields, as fields and not only as text, so a
        # log metric filter reads one route's latency (the alarms module).
        http = getattr(record, "http", None)
        if isinstance(http, dict):
            line["http"] = http
        # The sweep's line carries its pass the same way, for the same reason.
        sweep = getattr(record, "sweep", None)
        if isinstance(sweep, dict):
            line["sweep"] = sweep
        if record.exc_info and record.exc_info[1] is not None:
            line["exception"] = traceback_of(record.exc_info[1])
        return json.dumps(line)


HTTP_CLIENT_LOGGERS = ("httpx", "httpcore", "urllib3")
"""The HTTP clients' own loggers. Below WARNING they write a request's URL
with its query: `httpx` every request at INFO, `urllib3` a redirect. A
provider's lookup names what it looks for in its query, an invitee's address
among them, and the line and the breadcrumb it becomes would carry it. So
they write from WARNING up, whatever the level; the request's own breadcrumb
keeps its method, its status, and its URL's scheme and host
(`outgoing_breadcrumb`)."""


def configure_logging(level: str, json_logs: bool) -> None:
    for name in HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s [%(request_id)s] %(name)s: %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(level.upper())


SCRUBBED_KEYS = [*DEFAULT_DENYLIST, "bearer", "idempotency-key", "idempotency_key", "database_url"]
"""What the scrubber blanks wherever it appears in an event, headers and
nested values included: the SDK's own list (passwords, tokens, cookies, the
authorization header) and the few names this platform adds."""

ERROR_REPORTING_PRIVACY: dict[str, Any] = {
    # No personal data, no request body, and no frame locals: a local of a
    # frame that holds a password or a bearer would otherwise ride along.
    "send_default_pii": False,
    "include_local_variables": False,
    "max_request_body_size": "never",
    "event_scrubber": EventScrubber(denylist=SCRUBBED_KEYS, recursive=True),
}
"""The privacy half of the tracker's settings, held apart so a test reads it."""


def outgoing_event(event: Any, hint: Any) -> Any:
    """The last word on an event before it leaves. It takes the request id as
    a tag. Of the request it keeps the method alone: the SDK's web
    integration fills `request` with the URL, the query string, and the
    headers, each the caller's own text, and the tracker shows an event to
    whoever looks into the request. The route is the event's transaction; a
    request no route took is named by its URL there, so it is `unmatched`.

    Of each exception it keeps the type, the frames, and only the text
    `failure_text` keeps. Of a log line's message it keeps what `message_of`
    writes: the SDK has turned each argument into its text by now, so the
    message is built again from the record."""
    request_id = request_id_var.get()
    if request_id:
        event.setdefault("tags", {})["request_id"] = request_id
    request = event.get("request")
    if isinstance(request, dict):
        event["request"] = {key: request[key] for key in ("method",) if key in request}
    info = event.get("transaction_info")
    if isinstance(info, dict) and info.get("source") == "url":
        event["transaction"] = "unmatched"
    hint = hint if isinstance(hint, dict) else {}
    raised = (hint.get("exc_info") or (None, None))[1]
    kept: set[str] = set()
    if isinstance(raised, BaseException):
        kept = {text for exception, _ in _chain(raised) if (text := failure_text(exception))}
    for value in (event.get("exception") or {}).get("values") or ():
        if isinstance(value, dict) and value.get("value") not in kept:
            value.pop("value", None)
    record, entry = hint.get("log_record"), event.get("logentry")
    if isinstance(record, logging.LogRecord) and isinstance(entry, dict):
        entry["formatted"] = message_of(record)
        entry["params"] = []
        if isinstance(record.msg, BaseException):
            entry["message"] = described(record.msg)
    return event


OUTBOUND_KEPT = ("http.method", "http.response.status_code")
"""What the breadcrumb of an outbound request keeps beside its URL."""


def outgoing_breadcrumb(crumb: Any, hint: Any) -> Any:
    """The last word on a breadcrumb, which leaves with the next event. A log
    line's is its message, as `message_of` writes it. An outbound request's
    is its method, its status, and its URL's scheme and host, which name the
    provider. The rest of the URL stays out, the path included, even for
    debugging: a webhook's capability lives in its path, and anyone who holds
    the path can post to it. A query names what the call looked up, an
    invitee's address among them."""
    record = hint.get("log_record") if isinstance(hint, dict) else None
    if isinstance(record, logging.LogRecord):
        crumb["message"] = message_of(record)
    data = crumb.get("data")
    if crumb.get("type") == "http" and isinstance(data, dict):
        kept = {key: data[key] for key in OUTBOUND_KEPT if key in data}
        url = data.get("url")
        if isinstance(url, str) and (where := _where_to(url)):
            kept["url"] = where
        crumb["data"] = kept
    return crumb


def _where_to(url: str) -> str | None:
    """`url` as its scheme and its host: no credentials, no path, no query,
    and no fragment."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], "", "", ""))


def configure_error_reporting(
    dsn: str | None, environment: str, service_name: str, release: str | None = None
) -> None:
    """Sentry-compatible reporting (GlitchTip locally), only when a DSN is set.
    Unhandled exceptions and ERROR log records become events, tagged with the
    service and the request id; traces stay with OpenTelemetry."""
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=f"{service_name}@{release}" if release else None,
        server_name=service_name,
        traces_sample_rate=0.0,
        before_send=outgoing_event,
        before_breadcrumb=outgoing_breadcrumb,
        **ERROR_REPORTING_PRIVACY,
    )
    sentry_sdk.set_tag("service", service_name)


def failure_level(error: BaseException) -> int:
    """The level a boundary logs a failure at. The unavailable shape says "not
    right now": a dependency did not answer in time, and the caller comes
    back. That is a warning, which the tracker does not take for an event;
    the counters are what watch it. Any other failure is an error, which the
    tracker reports. The shape is read by its code, which both exception
    roots carry, never by its class."""
    if getattr(error, "code", None) == InfraUnavailable.code:
        return logging.WARNING
    return logging.ERROR


def span_exporter(endpoint: str, timeout: timedelta) -> OTLPSpanExporter:
    """The one client that sends traces out; every export is bounded by the
    timeout from settings."""
    return OTLPSpanExporter(
        endpoint=endpoint.rstrip("/") + "/v1/traces", timeout=timeout.total_seconds()
    )


def configure_tracing(endpoint: str | None, service_name: str, timeout: timedelta) -> None:
    """The provider is configured only when an endpoint is set; otherwise the
    no-op tracer runs and the code paths stay identical."""
    if not endpoint:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(span_exporter(endpoint, timeout)))
    trace.set_tracer_provider(provider)


def current_trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")


TRACEPARENT = "traceparent"
_propagator = TraceContextTextMapPropagator()


def current_traceparent() -> str | None:
    """The W3C `traceparent` of the span in progress, which is the form a
    handoff carries: an id names a trace, and only the header carries what a
    later span links to. Empty when no tracer is configured or no span is
    open, and the far side then starts a trace of its own."""
    return traceparent_of(trace.get_current_span())


def traceparent_of(span: Span) -> str | None:
    """The W3C `traceparent` of `span`, whether it is in progress or not yet:
    for a stage minted as its span starts. Empty for the no-op tracer's span."""
    carrier: dict[str, str] = {}
    _propagator.inject(carrier, context=trace.set_span_in_context(span))
    return carrier.get(TRACEPARENT)


def links_to(traceparent: str | None) -> tuple[Link, ...]:
    """The link a span raises against the trace context a handoff carried. A
    link and not a parent: a durable queue holds an item well past the end of
    the request that filled it, so the causal edge joins two traces instead of
    stretching one over both. Empty when the handoff carried no trace context
    or carried one this version cannot read, and the span then starts a trace
    of its own."""
    if not traceparent:
        return ()
    extracted = _propagator.extract({TRACEPARENT: traceparent})
    span_context = trace.get_current_span(extracted).get_span_context()
    if not span_context.is_valid:
        return ()
    return (Link(span_context),)


def metrics_exposition() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
