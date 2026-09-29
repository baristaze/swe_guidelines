"""The devx profile's stores, read through their own HTTP APIs: Prometheus
(`/api/v1/query` at two instants), Jaeger (`/api/v3/traces` over a window,
matched on the `acme.request_id` span attribute, since the query API does
not filter on attributes server side), GlitchTip (the Sentry-shaped issue and
event routes of the seeded project, under the read-only token the seed
creates, filtered on the `local` environment the same way a deployed
environment filters on its own), and the log lines
of a stream the caller hands over, since a host process writes them to its
own stderr and no store keeps them."""

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from acme.ops.signals import ErrorEventFound, SignalsInterface, TraceFound
from acme.ops.signals.sentry import find_error_event

REQUEST_ID_ATTRIBUTE = "acme.request_id"
DEFAULT_LOOKBACK = timedelta(hours=1)
SEARCH_DEPTH = 500

LogSource = Callable[[], Iterable[str]]


def prometheus_selector(name: str, labels: Mapping[str, str]) -> str:
    """A label value that starts with `~` is a regular expression (`status:
    ~5..` reads every 5xx series); any other is matched exactly."""
    matchers = ",".join(
        f'{key}=~"{value[1:]}"' if value.startswith("~") else f'{key}="{value}"'
        for key, value in sorted(labels.items())
    )
    return f"{name}{{{matchers}}}" if matchers else name


def carries_request_id(line: str, request_id: str) -> bool:
    """A JSON line by its `request_id` field; a readable line by the id
    between the brackets the formatter puts it in."""
    if line.startswith("{"):
        try:
            record = json.loads(line)
        except ValueError:
            return False
        return isinstance(record, dict) and record.get("request_id") == request_id
    return f"[{request_id}]" in line


class SignalsLocalImpl(SignalsInterface):
    def __init__(
        self,
        *,
        prometheus_url: str,
        jaeger_url: str,
        error_tracker_url: str,
        error_tracker_token: str,
        logs: LogSource,
        error_tracker_org: str = "acme",
        error_tracker_project: str = "acme",
        environment: str = "local",
        service: str = "api",
        lookback: timedelta = DEFAULT_LOOKBACK,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.prometheus_url = prometheus_url.rstrip("/")
        self.jaeger_url = jaeger_url.rstrip("/")
        self.error_tracker_url = error_tracker_url.rstrip("/")
        self.error_tracker_token = error_tracker_token
        self.error_tracker_org = error_tracker_org
        self.error_tracker_project = error_tracker_project
        self.environment = environment
        self.logs = logs
        self.service = service
        self.lookback = lookback
        self._transport = transport
        self._now = now

    @property
    def reads_error_events(self) -> bool:
        """Always: GlitchTip is part of the local stack, and a stack without
        it is refused before a reader is built."""
        return True

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=10.0)

    async def log_lines(self, request_id: str) -> list[str]:
        return [line.rstrip("\n") for line in self.logs() if carries_request_id(line, request_id)]

    async def _instant(self, http: httpx.AsyncClient, query: str, at: datetime) -> float | None:
        response = await http.get(
            f"{self.prometheus_url}/api/v1/query",
            params={"query": query, "time": at.timestamp()},
        )
        response.raise_for_status()
        body = response.json()
        results = body.get("data", {}).get("result", [])
        if body.get("status") != "success" or not results:
            return None
        return float(results[0]["value"][1])

    async def metric_delta(
        self, name: str, labels: Mapping[str, str], since: datetime
    ) -> float | None:
        """The series' sum now minus its sum at `since`; a series that did
        not exist then counts from zero, and a counter that reset counts
        from its reset. Two instants rather than `increase()`, which needs
        two samples inside the window and reads a burst before the first
        scrape of a fresh process as nothing."""
        query = f"sum({prometheus_selector(name, labels)})"
        async with self._http() as http:
            now_value = await self._instant(http, query, self._now())
            if now_value is None:
                return None
            then_value = await self._instant(http, query, since) or 0.0
        return now_value if now_value < then_value else now_value - then_value

    async def trace(self, request_id: str) -> TraceFound | None:
        now = self._now()
        async with self._http() as http:
            response = await http.get(
                f"{self.jaeger_url}/api/v3/traces",
                params={
                    "query.service_name": self.service,
                    "query.start_time_min": (now - self.lookback).isoformat(),
                    "query.start_time_max": now.isoformat(),
                    "query.search_depth": SEARCH_DEPTH,
                },
            )
            response.raise_for_status()
            body = response.json()
        return find_trace(body, request_id)

    async def error_event(self, request_id: str) -> ErrorEventFound | None:
        async with self._http() as http:
            return await find_error_event(
                http,
                self.error_tracker_url,
                self.error_tracker_token,
                self.error_tracker_org,
                self.error_tracker_project,
                self.environment,
                request_id,
            )

    def describe(self) -> str:
        return (
            f"logs: the handed stream; metrics: Prometheus {self.prometheus_url}; "
            f"traces: Jaeger {self.jaeger_url} by {REQUEST_ID_ATTRIBUTE}; "
            f"errors: GlitchTip {self.error_tracker_url} org {self.error_tracker_org} "
            f"project {self.error_tracker_project} by environment {self.environment} "
            "and tag request_id"
        )


def find_trace(body: dict[str, Any], request_id: str) -> TraceFound | None:
    """The OTLP-shaped answer of Jaeger's v3 API: the trace of the span whose
    attribute is the id, with every span name that trace has."""
    spans_by_trace: dict[str, list[str]] = {}
    matched: str | None = None
    for resource in body.get("result", {}).get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            for span in scope.get("spans", []):
                trace_id = str(span.get("traceId", ""))
                spans_by_trace.setdefault(trace_id, []).append(str(span.get("name", "")))
                for attribute in span.get("attributes", []):
                    value = attribute.get("value", {})
                    if (
                        attribute.get("key") == REQUEST_ID_ATTRIBUTE
                        and value.get("stringValue") == request_id
                    ):
                        matched = trace_id
    if matched is None:
        return None
    return TraceFound(trace_id=matched, span_names=tuple(spans_by_trace[matched]))
