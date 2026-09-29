"""The gateway opens one server span per request and stamps the request id
on it, so the context the tenancy manager builds carries a real trace id
whenever a tracer provider is configured."""

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from acme.om.opcontext import OpContext
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
    built: list[OpContext] = []
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
    response = await client.get("/v1/tasks", headers={"x-request-id": "not-a-uuid"})
    assert response.status_code == 401
    assert response.headers["x-request-id"] != "not-a-uuid"
    metrics = await client.get("/metrics")
    counted = [
        line for line in metrics.text.splitlines() if line.startswith("acme_http_requests_total")
    ]
    assert any('route="/v1/tasks"' in line and 'status="401"' in line for line in counted)


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
