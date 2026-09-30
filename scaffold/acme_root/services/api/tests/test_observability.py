"""The gateway opens one server span per request and stamps the request id
on it, so the context the tenancy manager builds carries a real trace id
whenever a tracer provider is configured. The span holds nothing the caller
wrote."""

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from acme.om.context import TenantContext
from acme.services.api.container import AppContainer


def ensure_tracer_provider() -> None:
    """The global provider can be set once per process; the tests share one
    that exports nowhere."""
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())


async def test_the_request_context_carries_the_trace_id_and_its_traceparent(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_tracer_provider()
    built: list[TenantContext] = []
    original = container.managers.tenancy.authenticate

    async def spy(*args, **kwargs):
        ctx = await original(*args, **kwargs)
        built.append(ctx)
        return ctx

    monkeypatch.setattr(container.managers.tenancy, "authenticate", spy)
    response = await client.get("/v1/me", headers=owner)
    assert response.status_code == 200, response.text
    assert len(built) == 1
    trace_id = built[0].trace_id
    assert trace_id is not None and len(trace_id) == 32 and int(trace_id, 16) != 0
    assert built[0].request_id is not None
    # The trace context too, as the header a handoff carries on: every row
    # this request lands reads it off the stage.
    traceparent = built[0].traceparent
    assert traceparent is not None and traceparent.split("-")[1] == trace_id


async def test_the_request_id_is_echoed_and_the_route_is_counted(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/v1/media/files", headers={"x-request-id": "not-a-uuid"})
    assert response.status_code == 401
    assert response.headers["x-request-id"] != "not-a-uuid"
    metrics = await client.get("/metrics")
    counted = [
        line for line in metrics.text.splitlines() if line.startswith("acme_http_requests_total")
    ]
    assert any('route="/v1/media/files"' in line and 'status="401"' in line for line in counted)


async def test_an_invented_verb_opens_no_new_metric_series(client: httpx.AsyncClient) -> None:
    """The route of an unauthenticated 404 already labels itself "unmatched",
    and the method is whatever the client typed: without a bounded label, a
    series per invented verb is a cardinality leak anyone can open."""
    assert (await client.request("PROPFIND", "/nowhere")).status_code == 404
    metrics = await client.get("/metrics")
    counted = [
        line for line in metrics.text.splitlines() if line.startswith("acme_http_requests_total")
    ]
    assert not any("PROPFIND" in line for line in counted)
    assert any('route="unmatched"' in line and 'method="OTHER"' in line for line in counted)


WORDS = "WIPZ_DOWN_ALL_RULZ"
"""A caller's own words. None of their characters is in a verb, in a route's
template, in an id, or in a status, so a span that shares one with them holds
some of what the caller wrote."""


@contextmanager
def finished_spans() -> Iterator[InMemorySpanExporter]:
    """Every span that ends inside the block."""
    ensure_tracer_provider()
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


def held(span: ReadableSpan) -> str:
    """What a reader of the trace is shown of a span: its name, the value of
    each attribute, and whatever its events carry."""
    values = [span.name, *(str(v) for v in (span.attributes or {}).values())]
    for event in span.events:
        values += [event.name, *(str(v) for v in (event.attributes or {}).values())]
    return " ".join([*values, span.status.description or ""])


async def test_a_span_holds_nothing_the_caller_wrote(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """A trace is read under the request's id by whoever looks into it, and a
    caller picks that id, the path, the query, and the headers. The span is
    named by the route, or `unmatched`, and no attribute holds the rest."""

    @app.get("/held/{word}", include_in_schema=False)
    async def route(word: str) -> dict[str, str]:
        return {}

    said = {"user-agent": WORDS, "referer": f"https://{WORDS}.example", "x-note": WORDS}
    with finished_spans() as exporter:
        nowhere = await client.get(f"/{WORDS}/nowhere?say={WORDS}", headers=said)
        somewhere = await client.get(f"/held/{WORDS}?say={WORDS}", headers=said)
        spans = exporter.get_finished_spans()
    assert (nowhere.status_code, somewhere.status_code) == (404, 200)
    servers = [span for span in spans if span.kind is SpanKind.SERVER]
    assert sorted(span.name for span in servers) == ["GET /held/{word}", "GET unmatched"]
    for span in spans:
        assert not set(WORDS) & set(held(span)), held(span)
    assert {key for span in servers for key in span.attributes or {}} == {
        "acme.request_id",
        "http.route",
        "http.response.status_code",
    }


async def test_an_exception_that_leaves_a_span_marks_it_failed_and_leaves_no_text(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """An answer that has started cannot be replaced, so its exception leaves
    the span. An exception's text may quote what the caller sent: the span
    says it failed, and the log line holds the traceback."""

    @app.get("/half/{word}", include_in_schema=False)
    async def route(word: str) -> StreamingResponse:
        async def body():
            yield b"x"
            raise RuntimeError(f"no {word}")

        return StreamingResponse(body())

    with finished_spans() as exporter:
        await client.get(f"/half/{WORDS}")
        spans = exporter.get_finished_spans()
    (span,) = [span for span in spans if span.name == "GET /half/{word}"]
    assert span.status.status_code is StatusCode.ERROR
    assert span.events == ()
    assert not set(WORDS) & set(held(span)), held(span)
