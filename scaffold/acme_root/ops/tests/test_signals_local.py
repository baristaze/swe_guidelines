"""The local readers over fake stores: the queries they send and what they
make of the answers."""

import json
from datetime import UTC, datetime, timedelta

import httpx

from acme.ops.signals.local import (
    SignalsLocalImpl,
    carries_request_id,
    find_trace,
    prometheus_selector,
)

RID = "11111111-2222-7333-8444-555555555555"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class Stores:
    """Prometheus, Jaeger, and GlitchTip behind one transport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.counter_now = 42.0
        self.counter_then: float | None = 30.0
        self.events = list(EVENTS)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "prom" and path == "/api/v1/query":
            at = float(request.url.params["time"])
            value = self.counter_now if at >= NOW.timestamp() else self.counter_then
            result = [] if value is None else [{"metric": {}, "value": [at, str(value)]}]
            return httpx.Response(200, json={"status": "success", "data": {"result": result}})
        if host == "jaeger" and path == "/api/v3/traces":
            return httpx.Response(200, json=JAEGER_BODY)
        if host == "glitchtip":
            if request.headers.get("authorization") != "Bearer tok":
                return httpx.Response(401, json={"detail": "no"})
            if path == "/api/0/projects/acme/acme/issues/":
                if "environment:local" not in request.url.params["query"]:
                    return httpx.Response(200, json=[])
                return httpx.Response(
                    200, json=[{"id": 7, "title": "unhandled error on GET /v1/users"}]
                )
            if path == "/api/0/issues/7/events/":
                return httpx.Response(200, json=self.events)
        return httpx.Response(404)


def event(event_id: str, request_id: str, environment: str) -> dict[str, object]:
    return {
        "eventID": event_id,
        "tags": [
            {"key": "request_id", "value": request_id},
            {"key": "environment", "value": environment},
        ],
    }


EVENTS = [event("aaa", "other", "local"), event("bbb", RID, "local")]
"""The seeded project holds every environment's events; locally they are the
`local` ones."""


def span(trace_id: str, name: str, request_id: str | None) -> dict[str, object]:
    attributes = [{"key": "url.path", "value": {"stringValue": "/v1/users"}}]
    if request_id:
        attributes.append({"key": "acme.request_id", "value": {"stringValue": request_id}})
    return {"traceId": trace_id, "name": name, "attributes": attributes}


JAEGER_BODY = {
    "result": {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            span("t1", "GET /v1/users", "other"),
                            span("t2", "POST /v1/api-keys", RID),
                            span("t2", "storage.insert", None),
                        ]
                    }
                ]
            }
        ]
    }
}


def reader(stores: Stores, lines: list[str]) -> SignalsLocalImpl:
    return SignalsLocalImpl(
        prometheus_url="http://prom",
        jaeger_url="http://jaeger",
        error_tracker_url="http://glitchtip",
        error_tracker_token="tok",
        logs=lambda: lines,
        transport=httpx.MockTransport(stores),
        now=lambda: NOW,
    )


def test_a_selector_matches_exactly_or_by_pattern() -> None:
    assert prometheus_selector("c", {}) == "c"
    assert prometheus_selector("c", {"status": "~5..", "route": "/v1/users"}) == (
        'c{route="/v1/users",status=~"5.."}'
    )


def test_a_line_carries_the_id_as_json_or_in_brackets() -> None:
    assert carries_request_id(json.dumps({"request_id": RID, "message": "x"}), RID)
    assert carries_request_id(f"12:00 INFO  [{RID}] acme: GET /v1/users 200", RID)
    assert not carries_request_id(json.dumps({"request_id": "other"}), RID)
    assert not carries_request_id("{not json", RID)


async def test_log_lines_come_from_the_handed_stream() -> None:
    lines = [
        json.dumps({"request_id": "other"}),
        json.dumps({"request_id": RID, "message": "hit"}) + "\n",
    ]
    found = await reader(Stores(), lines).log_lines(RID)
    assert found == [json.dumps({"request_id": RID, "message": "hit"})]


async def test_the_metric_delta_is_now_minus_then_over_the_summed_selector() -> None:
    stores = Stores()
    delta = await reader(stores, []).metric_delta(
        "acme_http_requests_total", {"route": "/v1/users"}, NOW - timedelta(minutes=1)
    )
    assert delta == 12.0
    queries = [r.url.params["query"] for r in stores.requests]
    assert queries == ['sum(acme_http_requests_total{route="/v1/users"})'] * 2
    stores.counter_then = None  # no series at the start of the window: from zero
    assert await reader(stores, []).metric_delta("c", {}, NOW - timedelta(minutes=1)) == 42.0
    stores.counter_then, stores.counter_now = 50.0, 5.0  # a reset: from the reset
    assert await reader(stores, []).metric_delta("c", {}, NOW - timedelta(minutes=1)) == 5.0
    stores.counter_now = None  # type: ignore[assignment]
    stores.counter_then = None
    assert await reader(stores, []).metric_delta("c", {}, NOW - timedelta(minutes=1)) is None


async def test_the_trace_is_matched_on_the_request_id_attribute() -> None:
    stores = Stores()
    found = await reader(stores, []).trace(RID)
    assert found is not None
    assert found.trace_id == "t2" and found.span_names == ("POST /v1/api-keys", "storage.insert")
    sent = stores.requests[0].url.params
    assert sent["query.service_name"] == "api" and "query.start_time_min" in sent
    assert find_trace(JAEGER_BODY, "missing") is None


async def test_the_error_event_is_the_one_tagged_with_the_id_in_this_environment() -> None:
    stores = Stores()
    found = await reader(stores, []).error_event(RID)
    assert found is not None
    assert (found.event_id, found.issue_id) == ("bbb", "7")
    assert found.title == "unhandled error on GET /v1/users"
    assert stores.requests[0].url.params["query"] == f"environment:local request_id:{RID}"
    assert await reader(stores, []).error_event("missing") is None
    assert "Prometheus http://prom" in reader(stores, []).describe()
    glitchtip = " ".join(
        [
            "GlitchTip http://glitchtip",
            "org acme project acme",
            "by environment local and tag request_id",
        ]
    )
    assert glitchtip in reader(stores, []).describe()


async def test_a_local_run_passes_over_an_event_of_another_environment() -> None:
    """The seeded project is the product's one project, so a `staging` event
    can sit in the same issue; `local` is what the read asks for."""
    stores = Stores()
    stores.events = [event("ccc", RID, "staging")]
    assert await reader(stores, []).error_event(RID) is None
