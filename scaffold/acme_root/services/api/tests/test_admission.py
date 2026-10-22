"""Admission: the process refuses past the bound of what it has in flight,
fast and in the unavailable shape, counting reads, writes, and a holder's
calls apart so that a storm in one lane leaves the others their slots, and
never refuses the probes that say so."""

import asyncio
import itertools
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_support import a_held_lease, build_container
from fastapi import FastAPI

from acme.om.storage.reserve import in_holder_call
from acme.services.api.container import AppContainer

READS = 1
"""One read in flight, so a second read is the refused one."""

WRITES = 2
"""Two writes, so each lane is held to a bound of its own and not to one."""

HOLDS = 1
"""One of a holder's calls, so a second renewal is the refused one."""

paths = itertools.count()


@pytest.fixture
def container(tmp_path: Path) -> AppContainer:
    """Overrides the container of `conftest`, so the `app` and `client`
    fixtures above run against a process whose lanes are one, two, and one."""
    return build_container(
        tmp_path,
        admission_limit_reads=READS,
        admission_limit_writes=WRITES,
        admission_limit_holds=HOLDS,
    )


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
    The read and the write lanes are full here: the probes are outside every
    lane."""
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


async def test_a_full_write_lane_still_admits_a_holders_calls(
    app: FastAPI, client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    """What the holds lane is for: a slowdown that fills the write lane sheds
    the writes, and the renewal that keeps a lease is still admitted, and so
    is the release that hands it on. Shed with the writes, they would make
    the slowdown a wave of lost leases."""
    lease_id = await a_held_lease(client, container, owner)
    first, ends_first, _ = await hold(app, client, "POST")
    second, ends_second, path = await hold(app, client, "POST")
    assert (await client.post(path)).status_code == 503

    renewed = await client.post(f"/v1/leases/{lease_id}/renew", headers=owner)
    released = await client.post(f"/v1/leases/{lease_id}/release", headers=owner)

    assert renewed.status_code == 200 and renewed.json()["status"] == "active"
    assert released.status_code == 200 and released.json()["status"] == "released"
    ends_first.set()
    ends_second.set()
    assert [(await held).status_code for held in (first, second)] == [200, 200]


async def test_the_holds_lane_refuses_at_its_own_bound(
    app: FastAPI,
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A holder's calls have a bound of their own, not none: past it a
    renewal is refused like any request, and the write lane, which it never
    takes from, still admits."""
    lease_id = await a_held_lease(client, container, owner)
    entered, release = asyncio.Event(), asyncio.Event()
    leases = container.managers.leases
    renew_now = leases.renew

    async def slow_renew(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await renew_now(*args, **kwargs)

    monkeypatch.setattr(leases, "renew", slow_renew)
    renew = f"/v1/leases/{lease_id}/renew"
    held = asyncio.create_task(client.post(renew, headers=owner))
    await asyncio.wait_for(entered.wait(), timeout=5)

    # Bounded, so a renewal admitted past the bound fails here, not hangs.
    refused = await asyncio.wait_for(client.post(renew, headers=owner), timeout=5)
    written, ends, _ = await hold(app, client, "POST")

    assert refused.status_code == 503
    assert refused.json()["error"]["message"] == "the process is at its bound of holds in flight"
    assert refused.headers["Retry-After"] == "1"
    ends.set()
    assert (await written).status_code == 200
    release.set()
    assert (await held).status_code == 200


async def test_a_holders_call_runs_marked_for_the_reserve_and_nothing_else_does(
    app: FastAPI,
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one mark picks the lane and the reserve: a renewal and a release
    run marked, so their statements may take the pool's reserve, and a read
    or a write in its own lane runs unmarked, so it never can. The mark ends
    with the call."""
    lease_id = await a_held_lease(client, container, owner)
    seen: dict[str, bool] = {}
    leases = container.managers.leases

    def watched(name: str) -> Any:
        call = getattr(leases, name)

        async def recording(*args: Any, **kwargs: Any) -> Any:
            seen[name] = in_holder_call()
            return await call(*args, **kwargs)

        return recording

    for name in ("renew", "release"):
        monkeypatch.setattr(leases, name, watched(name))

    async def marked() -> dict[str, bool]:
        return {"marked": in_holder_call()}

    app.get("/v1/marked", include_in_schema=False)(marked)
    app.post("/v1/marked", include_in_schema=False)(marked)

    assert (await client.post(f"/v1/leases/{lease_id}/renew", headers=owner)).status_code == 200
    assert (await client.post(f"/v1/leases/{lease_id}/release", headers=owner)).status_code == 200
    assert seen == {"renew": True, "release": True}
    for method in ("GET", "POST"):
        assert (await client.request(method, "/v1/marked")).json() == {"marked": False}
    assert in_holder_call() is False
