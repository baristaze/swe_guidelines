"""A write answers first and relays its outbox rows after: the hint and the
event follow the answer's last byte, under the request's id and span. A relay
that fails after the answer is logged and the sweep relays the rows."""

import asyncio
import logging
import time
from collections.abc import MutableMapping, Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from api_support import add_member, sign_in_as
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from acme.infra.observability import RequestIdFilter
from acme.infra.topics import EntityChangedPayload, TopicPayload, Topics
from acme.om.context import Role
from acme.om.events.types.event import Event
from acme.om.exceptions import Unavailable
from acme.om.outbox.impl.relay import OutboxOptions, OutboxRelayImpl
from acme.services.api.container import AppContainer
from acme.services.api.gateway.relay import RELAY_SPAN

Message = MutableMapping[str, Any]
FILES = "/v1/media/files"
UPLOAD = {"name": "report.pdf", "content_type": "application/pdf", "size_bytes": 5}


def hints(container: AppContainer) -> list[EntityChangedPayload]:
    """Every ENTITY_CHANGED the process publishes, as the realtime fan-out hears it."""
    heard: list[EntityChangedPayload] = []

    async def record(payload: TopicPayload) -> None:
        assert isinstance(payload, EntityChangedPayload)
        heard.append(payload)

    container.infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", record)
    return heard


def answered_client(app: FastAPI, at_last_byte: Any) -> httpx.AsyncClient:
    """A client over the app that calls `at_last_byte()` once the answer's
    last byte is sent, before the app returns."""

    async def shim(scope: Any, receive: Any, send: Any) -> None:
        async def sent(message: Message) -> None:
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                await at_last_byte()

        await app(scope, receive, sent)

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=shim), base_url="http://test")


def the_sweep(container: AppContainer) -> OutboxRelayImpl:
    """The maintenance sweep's relay over the same storage, with no grace, so
    it claims the rows at once instead of ten seconds on."""
    storage = container.storage
    return OutboxRelayImpl(
        storage.get_outbox_storage(),
        storage.get_event_storage(),
        container.infra.get_topics(),
        lambda: container.managers.work,
        OutboxOptions(grace=timedelta(0)),
    )


async def test_the_hint_follows_the_answer_and_the_row_gets_done(
    app: FastAPI, container: AppContainer, owner: dict[str, str]
) -> None:
    heard = hints(container)
    outbox = container.storage.get_outbox_storage()
    at_answer: dict[str, Any] = {}

    async def at_last_byte() -> None:
        at_answer["hints"] = len(heard)
        at_answer["pending"] = await outbox.oldest_pending_at()

    async with answered_client(app, at_last_byte) as client:
        response = await client.post(FILES, headers=owner, json=UPLOAD)
    assert response.status_code == 201, response.text
    assert at_answer == {"hints": 0, "pending": at_answer["pending"]}
    assert at_answer["pending"] is not None, "the row was committed, not yet relayed"

    file_id = UUID(response.json()["id"])
    assert [(h.kind, h.target_id) for h in heard] == [("media.file.created", file_id)]
    assert await outbox.oldest_pending_at() is None, "the row is done"


async def test_every_write_of_a_request_relays_after_it(
    app: FastAPI, container: AppContainer, owner: dict[str, str]
) -> None:
    """Removing a member lands a row for the member and one for each of their
    sessions; all of them follow the answer."""
    heard = hints(container)
    async with answered_client(app, _nothing) as client:
        org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
        bob = await add_member(container, org_id, "bob@example.test", Role.MEMBER)
        for _ in range(2):
            await sign_in_as(client, "bob@example.test", org_id)
        heard.clear()
        at_answer: list[int] = []

        async def at_last_byte() -> None:
            at_answer.append(len(heard))

        async with answered_client(app, at_last_byte) as removing:
            response = await removing.delete(f"/v1/memberships/{bob.id}", headers=owner)
    assert response.status_code == 200, response.text
    assert at_answer == [0]
    assert sorted(h.kind for h in heard) == [
        "tenancy.session.revoked",
        "tenancy.session.revoked",
        "tenancy.user.deleted",
    ]
    assert await container.storage.get_outbox_storage().oldest_pending_at() is None


async def _nothing() -> None:
    return None


async def test_a_relay_that_fails_after_the_answer_is_logged_and_the_sweep_relays_it(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events = container.storage.get_event_storage()

    async def refused(org_id: UUID, appended: Sequence[Event]) -> tuple[Event, ...]:
        raise RuntimeError("the stream is down")

    monkeypatch.setattr(events, "append_events", refused)
    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.ERROR, logger="acme.om.outbox.impl.relay"):
        response = await client.post(FILES, headers=owner, json=UPLOAD)
    assert response.status_code == 201, "the answer never waits on the relay"
    (failed,) = [r for r in caplog.records if r.name == "acme.om.outbox.impl.relay"]
    assert "failed; the sweep retries" in failed.getMessage()
    assert failed.request_id == response.headers["x-request-id"]  # type: ignore[attr-defined]
    outbox = container.storage.get_outbox_storage()
    assert await outbox.oldest_pending_at() is not None

    monkeypatch.undo()
    heard = hints(container)
    assert await the_sweep(container).relay_pending(10) == 1
    assert [h.target_id for h in heard] == [UUID(response.json()["id"])]
    assert await outbox.oldest_pending_at() is None


async def test_a_relay_the_stream_did_not_answer_in_time_is_a_warning(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The stream's database did not answer in time: the sweep relays the rows
    later, as on any failure of the relay, and the line is a warning, not an
    error the tracker reports."""
    events = container.storage.get_event_storage()

    async def not_in_time(org_id: UUID, appended: Sequence[Event]) -> tuple[Event, ...]:
        raise Unavailable("the database did not answer in time: a statement passed its deadline")

    monkeypatch.setattr(events, "append_events", not_in_time)
    with caplog.at_level(logging.WARNING, logger="acme.om.outbox.impl.relay"):
        response = await client.post(FILES, headers=owner, json=UPLOAD)
    assert response.status_code == 201
    (failed,) = [r for r in caplog.records if r.name == "acme.om.outbox.impl.relay"]
    assert failed.levelno == logging.WARNING
    assert "failed; the sweep retries" in failed.getMessage()
    assert await container.storage.get_outbox_storage().oldest_pending_at() is not None


async def test_the_relay_is_a_span_of_the_request(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        response = await client.post(FILES, headers=owner, json=UPLOAD)
        assert response.status_code == 201, response.text
        spans = exporter.get_finished_spans()
    finally:
        processor.shutdown()
    (relayed,) = [s for s in spans if s.name == RELAY_SPAN]
    (server,) = [s for s in spans if s.name == "POST /v1/media/files"]
    assert relayed.parent is not None and server.context is not None
    assert relayed.parent.span_id == server.context.span_id
    assert relayed.attributes is not None and relayed.attributes["acme.outbox.rows"] == 1
    assert server.attributes is not None
    assert server.attributes["acme.request_id"] == response.headers["x-request-id"]


async def test_the_access_line_times_the_answer_not_the_relay(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The line's window opens after the request is sent and closes at the
    answer's last byte, before the relay starts, so its duration is at most
    the time from the send to the relay's start. A line that timed the relay
    too would run its 300 ms past that. A fixed bound never tells the two
    apart: on a loaded machine the answer alone can take that long, when a
    pass of the garbage collector lands inside it."""
    events = container.storage.get_event_storage()
    append = events.append_events
    relay_started: list[float] = []

    async def slow(org_id: UUID, appended: Sequence[Event]) -> tuple[Event, ...]:
        relay_started.append(time.perf_counter())
        await asyncio.sleep(0.3)
        return await append(org_id, appended)

    monkeypatch.setattr(events, "append_events", slow)
    with caplog.at_level(logging.INFO, logger="acme.services.api.gateway.observability"):
        sent = time.perf_counter()
        response = await client.post(FILES, headers=owner, json=UPLOAD)
    assert response.status_code == 201, response.text
    (access,) = [r for r in caplog.records if r.name == "acme.services.api.gateway.observability"]
    (relayed,) = relay_started
    # The line rounds to a tenth of a millisecond; so does the bound.
    until_the_relay = round((relayed - sent) * 1000, 1)
    assert access.http["duration_ms"] <= until_the_relay  # type: ignore[attr-defined]
