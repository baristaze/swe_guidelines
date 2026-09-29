"""Logging with the request-id filter, error reporting, tracing, and the
process metrics. Configured once at the app container's boot and never per
module."""

import json
import logging
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

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


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
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
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line)


def configure_logging(level: str, json_logs: bool) -> None:
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


def configure_error_reporting(
    dsn: str | None, environment: str, service_name: str, release: str | None = None
) -> None:
    """Sentry-compatible reporting (GlitchTip locally), only when a DSN is set.
    Unhandled exceptions and ERROR log records become events, tagged with the
    service and the request id; traces stay with OpenTelemetry."""
    if not dsn:
        return

    def tag_request_id(event: Any, hint: Any) -> Any:
        request_id = request_id_var.get()
        if request_id:
            event.setdefault("tags", {})["request_id"] = request_id
        return event

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=f"{service_name}@{release}" if release else None,
        server_name=service_name,
        traces_sample_rate=0.0,
        before_send=tag_request_id,
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
