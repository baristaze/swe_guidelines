"""Admission: the process refuses past the bound of what it has in flight,
fast and in the unavailable shape, counting reads and writes apart so that a
storm in one lane leaves the other its slots, and never refuses the probes
that say so."""

import asyncio
import itertools
from pathlib import Path

import httpx
import pytest
from api_support import build_container
from fastapi import FastAPI

from acme.services.api.container import AppContainer

READS = 1
"""One read in flight, so a second read is the refused one."""

WRITES = 2
"""Two writes, so each lane is held to a bound of its own and not to one."""

paths = itertools.count()


@pytest.fixture
def container(tmp_path: Path) -> AppContainer:
    """Overrides the container of `conftest`, so the `app` and `client`
    fixtures above run against a process whose lanes are one and two."""
    return build_container(tmp_path, admission_limit_reads=READS, admission_limit_writes=WRITES)


def holding_route(app: FastAPI, path: str, entered: asyncio.Event, release: asyncio.Event) -> None:
    """A route that holds its slot, under both methods: it says it is in
    flight and waits for the test to let it go, the way a slow dependency
    holds a real request. Each hold gets a path of its own, so the route a
    later hold adds never answers for an earlier one."""

    async def holding() -> dict[str, bool]:
        entered.set()
        await release.wait()
        return {"held": True}

    app.get(path, include_in_schema=False)(holding)
    app.post(path, include_in_schema=False)(holding)


async def hold(
    app: FastAPI, client: httpx.AsyncClient, method: str = "GET"
) -> tuple[asyncio.Task[httpx.Response], asyncio.Event, str]:
    """Takes one slot of the method's lane and returns the held request, the
    event that ends it, and the path it is holding."""
    entered, release = asyncio.Event(), asyncio.Event()
    path = f"/v1/holding-{next(paths)}"
    holding_route(app, path, entered, release)
    held = asyncio.create_task(client.request(method, path))
    await asyncio.wait_for(entered.wait(), timeout=5)
    return held, release, path


async def test_admission_refuses_past_the_bound(app: FastAPI, client: httpx.AsyncClient) -> None:
    held, release, path = await hold(app, client)

    refused = await client.get(path, headers={"Origin": "http://localhost:5173"})

    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "unavailable"
    assert refused.json()["error"]["message"] == "the process is at its bound of reads in flight"
    assert refused.headers["Retry-After"] == "1"
    # Where admission sits: inside the request id, so the refusal carries one,
    # and inside CORS, so a browser may read the body and the wait.
    assert refused.headers["x-request-id"] != ""
    assert refused.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "Retry-After" in refused.headers["access-control-expose-headers"]
    release.set()
    assert (await held).status_code == 200


async def test_the_slot_is_free_again_once_the_request_answers(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    held, release, path = await hold(app, client)
    assert (await client.get(path)).status_code == 503

    release.set()
    await held

    # The same route, now answering at once: the count came back down, so the
    # process admits again instead of staying closed after a burst.
    assert (await client.get(path)).status_code == 200


async def test_a_saturated_read_lane_still_admits_a_write(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """What the two lanes are for: a client back from an outage replays its
    backlog with reads, and the commands behind it are still admitted."""
    held, release, path = await hold(app, client)
    assert (await client.get(path)).status_code == 503

    written, ends, _ = await hold(app, client, "POST")

    ends.set()
    assert (await written).status_code == 200
    release.set()
    assert (await held).status_code == 200


async def test_a_saturated_write_lane_still_admits_a_read(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    first, ends_first, _ = await hold(app, client, "POST")
    second, ends_second, path = await hold(app, client, "POST")
    assert (await client.post(path)).status_code == 503

    read, release, _ = await hold(app, client)

    release.set()
    assert (await read).status_code == 200
    ends_first.set()
    ends_second.set()
    assert (await first).status_code == 200
    assert (await second).status_code == 200


async def test_each_lane_refuses_at_its_own_bound(app: FastAPI, client: httpx.AsyncClient) -> None:
    """One bound is not the other's: the write that follows the read's last
    slot is admitted, and the write lane refuses one slot later."""
    read, release, read_path = await hold(app, client)
    assert (await client.get(read_path)).status_code == 503

    first, ends_first, _ = await hold(app, client, "POST")
    second, ends_second, write_path = await hold(app, client, "POST")
    refused = await client.post(write_path)

    assert refused.status_code == 503
    assert refused.json()["error"]["message"] == "the process is at its bound of writes in flight"
    release.set()
    ends_first.set()
    ends_second.set()
    assert [(await held).status_code for held in (read, first, second)] == [200, 200, 200]


async def test_the_probes_answer_while_admission_is_saturated(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """A saturated process must still be able to say that it is saturated, and
    the collector must still be able to read the counter that says by how much.
    Both lanes are full here: the probes are outside each of them."""
    read, release, _ = await hold(app, client)
    first, ends_first, _ = await hold(app, client, "POST")
    second, ends_second, _ = await hold(app, client, "POST")

    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200
    assert (await client.get("/metrics")).status_code == 200

    release.set()
    ends_first.set()
    ends_second.set()
    assert [(await held).status_code for held in (read, first, second)] == [200, 200, 200]
