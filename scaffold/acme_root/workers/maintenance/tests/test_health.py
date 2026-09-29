"""The worker's own HTTP surface: /healthz reports the liveness probe, on the
loop's event loop, and /metrics still serves; the `health` subcommand asks
the same URL and exits by its status."""

import asyncio
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from acme.workers.maintenance.health import WorkerHttpServer
from acme.workers.maintenance.main import health
from acme.workers.maintenance.settings import MaintenanceSettings


@dataclass
class Served:
    port: int
    alive: bool = True
    probed: int = 0


@pytest.fixture
async def served() -> AsyncIterator[Served]:
    state = Served(port=0)

    async def probe() -> bool:
        state.probed += 1
        return state.alive

    server = WorkerHttpServer("127.0.0.1", 0, probe, asyncio.get_running_loop())
    state.port = server.port
    server.start()
    try:
        yield state
    finally:
        server.stop()


async def get(port: int, path: str) -> tuple[int, str]:
    def request() -> tuple[int, str]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    return await asyncio.to_thread(request)


async def test_healthz_reports_the_probe(served: Served) -> None:
    assert await get(served.port, "/healthz") == (200, '{"status": "ok"}')
    served.alive = False
    assert await get(served.port, "/healthz") == (503, '{"status": "missing"}')
    assert served.probed == 2


async def test_metrics_still_serve_and_anything_else_is_404(served: Served) -> None:
    status, body = await get(served.port, "/metrics")
    assert status == 200 and "acme_outcomes_total" in body
    assert (await get(served.port, "/nothing"))[0] == 404
    assert served.probed == 0


async def test_the_health_subcommand_exits_by_the_status(served: Served) -> None:
    settings = MaintenanceSettings.model_validate(
        {"environment": "test", "metrics_host": "0.0.0.0", "metrics_port": served.port}
    )
    assert await asyncio.to_thread(health, settings) == 0
    served.alive = False
    assert await asyncio.to_thread(health, settings) == 1
