"""The three operational routes: liveness with no I/O, readiness under a
deadline of its own, and the metrics exposition."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from api_support import build_container
from httpx import ASGITransport
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.util import greenlet_spawn

from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.services.api.app import create_app

READINESS_DEADLINE = 0.05
"""Shorter than every wait in this file, so a test that answers late fails."""


class HangingStorage(StorageMemoryImpl):
    """Storage whose healthcheck does not return, the way a hung database or
    an exhausted pool keeps the real one waiting."""

    async def healthcheck(self) -> bool:
        await asyncio.sleep(60)
        return True


@pytest.fixture
async def hanging_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    container = build_container(
        tmp_path, HangingStorage(), readiness_timeout_seconds=READINESS_DEADLINE
    )
    app = create_app(container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_healthz_answers_with_the_version(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
    assert "x-request-id" in response.headers


async def test_readyz_awaits_the_storage_healthcheck(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"ready": True}


async def test_readyz_answers_503_when_the_healthcheck_does_not_return(
    hanging_client: httpx.AsyncClient,
) -> None:
    """A timeout is a negative answer, not a missing one: the route answers
    under its own deadline instead of waiting on the dependency it reports on."""
    started = time.perf_counter()
    response = await hanging_client.get("/readyz")
    assert time.perf_counter() - started < 5
    assert response.status_code == 503
    assert response.json() == {"ready": False}


async def test_the_readiness_deadline_interrupts_a_pool_wait() -> None:
    """The deadline is only worth having if it reaches the wait the defect is
    about: a checkout from a pool with nothing left. The storage impl's
    `engine.connect()` runs that checkout through SQLAlchemy's greenlet
    bridge, which throws what the awaiting task is cancelled with back into
    the greenlet, so the wait ends at the deadline and not at the pool's own
    timeout. This pins that, on the same pool class the async engine builds."""

    def connection() -> Any:
        """As much of a DBAPI connection as handing one out and taking it
        back needs; nothing in this test runs a statement on it."""
        return SimpleNamespace(close=lambda: None, rollback=lambda: None)

    pool = AsyncAdaptedQueuePool(connection, pool_size=1, max_overflow=0, timeout=30)
    held = await greenlet_spawn(pool.connect)
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(READINESS_DEADLINE):
            await greenlet_spawn(pool.connect)
    assert time.perf_counter() - started < 5
    await greenlet_spawn(held.close)


async def test_metrics_are_exposed_outside_the_versioned_api(client: httpx.AsyncClient) -> None:
    await client.get("/healthz")
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "acme_http_requests_total" in response.text


async def test_a_deployed_environment_serves_no_interactive_docs(tmp_path: Path) -> None:
    app = create_app(build_container(tmp_path, interactive_docs=False))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for path in ["/docs", "/redoc", "/openapi.json"]:
                assert (await client.get(path)).status_code == 404, path
            assert (await client.get("/healthz")).status_code == 200
