"""The limits on authenticated routes (ADR 0059): each credential spends a
budget of reads and one of writes once it resolves, and each address spends
a budget of failed authentications, past which its requests are refused
before any credential is looked up. Both answer 429 with Retry-After in the
one envelope, and both fail open when the cache cannot count."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_support import OWNER, SMALL_BUDGET, build_container, seed_request, sign_in, sign_in_as

from acme.infra.cache import CacheScope
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.gateway.ratelimit import RefusedAddresses
from acme.services.api.settings import ApiSettings

PEER = "203.0.113.20"
NEIGHBOUR = "203.0.113.21"


@asynccontextmanager
async def clients_of(container: AppContainer) -> AsyncIterator[dict[str, httpx.AsyncClient]]:
    """The app inside its lifespan, and one client per address."""
    app = create_app(container)
    async with app.router.lifespan_context(app):
        clients: dict[str, httpx.AsyncClient] = {}
        for address in (PEER, NEIGHBOUR):
            transport = httpx.ASGITransport(
                app=app, client=(address, 40000), raise_app_exceptions=False
            )
            clients[address] = httpx.AsyncClient(transport=transport, base_url="http://test")
        try:
            yield clients
        finally:
            for client in clients.values():
                await client.aclose()


def lookups_counted(container: AppContainer, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every credential the tenancy manager was asked to look up, in order.
    The lookup is where the database is read, so a request that never
    reaches it never reads the database."""
    tenancy = container.managers.tenancy
    seen: list[str] = []
    original = tenancy.authenticate

    async def spy(rctx: Any, credential: str) -> Any:
        seen.append(credential)
        return await original(rctx, credential)

    monkeypatch.setattr(tenancy, "authenticate", spy)
    return seen


def cache_down(container: AppContainer, monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer every increment gets from a Valkey that is down or from an
    open breaker: no count, and the window asked for."""
    cache = container.infra.get_cache(CacheScope.RATE_LIMIT)

    async def unreachable(org_id: Any, key: str, ttl: timedelta) -> tuple[int, timedelta]:
        return 0, ttl

    monkeypatch.setattr(cache, "increment", unreachable)


async def another_session(
    client: httpx.AsyncClient, container: AppContainer, headers: dict[str, str]
) -> dict[str, str]:
    """A second session of the owner the first one belongs to, in its org."""
    token = headers["Authorization"].removeprefix("Bearer ")
    ctx = await container.managers.tenancy.authenticate(seed_request(), token)
    return await sign_in_as(client, OWNER["email"], ctx.org_id)


async def revoked_session(
    client: httpx.AsyncClient, container: AppContainer, headers: dict[str, str]
) -> dict[str, str]:
    """A session that was real and is signed out: the lookup finds it revoked."""
    dead = await another_session(client, container, headers)
    assert (await client.post("/v1/auth/logout", headers=dead)).status_code == 200
    return dead


async def test_a_credential_past_its_read_budget_is_refused_with_retry_after(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path, credential_rate_limit_reads=SMALL_BUDGET)
    async with clients_of(container) as clients:
        client = clients[PEER]
        owner = await sign_in(client, container)
        for _ in range(SMALL_BUDGET):
            assert (await client.get("/v1/tasks", headers=owner)).status_code == 200
        refused = await client.get("/v1/tasks", headers=owner)
        # Writes have a budget of their own, which the reads did not spend.
        written = await client.post("/v1/tasks", json={"title": "still"}, headers=owner)
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert refused.json()["error"]["request_id"]
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert written.status_code == 201, written.text


async def test_the_budget_is_the_credential_s_not_the_address_s(tmp_path: Path) -> None:
    """Two sessions behind one address each spend their own."""
    container = build_container(tmp_path, credential_rate_limit_reads=SMALL_BUDGET)
    async with clients_of(container) as clients:
        client = clients[PEER]
        first = await sign_in(client, container)
        for _ in range(SMALL_BUDGET):
            await client.get("/v1/tasks", headers=first)
        assert (await client.get("/v1/tasks", headers=first)).status_code == 429
        second = await another_session(client, container, first)
        assert (await client.get("/v1/tasks", headers=second)).status_code == 200


async def test_a_credential_past_its_write_budget_is_refused(tmp_path: Path) -> None:
    container = build_container(tmp_path, credential_rate_limit_writes=SMALL_BUDGET)
    async with clients_of(container) as clients:
        client = clients[PEER]
        owner = await sign_in(client, container)
        for index in range(SMALL_BUDGET):
            created = await client.post("/v1/tasks", json={"title": f"t{index}"}, headers=owner)
            assert created.status_code == 201
        refused = await client.post("/v1/tasks", json={"title": "one too many"}, headers=owner)
        read = await client.get("/v1/tasks", headers=owner)
    assert refused.status_code == 429
    assert "Retry-After" in refused.headers
    assert read.status_code == 200


async def test_a_bad_token_flood_stops_reaching_the_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failures up to the budget are looked up and answered 401; every
    request after them is answered 429 without a lookup, a live credential
    from the same address included, and another address is untouched."""
    container = build_container(tmp_path, failed_authentication_limit=SMALL_BUDGET)
    async with clients_of(container) as clients:
        client = clients[PEER]
        live = await sign_in(clients[NEIGHBOUR], container)
        dead = await revoked_session(client, container, live)
        seen = lookups_counted(container, monkeypatch)
        answers = [(await client.get("/v1/tasks", headers=dead)).status_code for _ in range(100)]
        from_the_same_address = await client.get("/v1/tasks", headers=live)
        from_another = await clients[NEIGHBOUR].get("/v1/tasks", headers=live)
    assert answers[:SMALL_BUDGET] == [401] * SMALL_BUDGET
    assert answers[SMALL_BUDGET:] == [429] * (100 - SMALL_BUDGET)
    assert len(seen) == SMALL_BUDGET + 1  # the failures, and the neighbour's request
    assert from_the_same_address.status_code == 429
    refusal = from_the_same_address.json()["error"]
    assert refusal == {
        "code": "rate_limited",
        "message": "too many failed authentications from this address",
        "request_id": refusal["request_id"],
    }
    assert int(from_the_same_address.headers["Retry-After"]) >= 1
    assert from_another.status_code == 200


async def test_a_failed_sign_in_credential_counts_too(tmp_path: Path) -> None:
    """The identity stage's lookup (a sign-in or operator credential) spends
    the same budget as the tenant stage's."""
    container = build_container(tmp_path, failed_authentication_limit=SMALL_BUDGET)
    async with clients_of(container) as clients:
        client = clients[PEER]
        unknown = {"Authorization": "Bearer lgn_" + "x" * 43}
        for _ in range(SMALL_BUDGET):
            answer = await client.get("/v1/auth/memberships", headers=unknown)
            assert answer.status_code == 401
        assert (await client.get("/v1/auth/memberships", headers=unknown)).status_code == 429


async def test_a_missing_bearer_is_not_a_failed_lookup(tmp_path: Path) -> None:
    """No credential, nothing looked up: a browser that has not signed in yet
    spends nothing."""
    container = build_container(tmp_path, failed_authentication_limit=SMALL_BUDGET)
    async with clients_of(container) as clients:
        answers = [(await clients[PEER].get("/v1/tasks")).status_code for _ in range(20)]
    assert answers == [401] * 20


async def test_with_the_cache_down_both_limits_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail open: a credential past its budget is served, and every bad token
    is looked up and answered 401, as before the limits existed."""
    container = build_container(
        tmp_path, credential_rate_limit_reads=SMALL_BUDGET, failed_authentication_limit=SMALL_BUDGET
    )
    async with clients_of(container) as clients:
        client = clients[PEER]
        owner = await sign_in(client, container)
        dead = await revoked_session(client, container, owner)
        cache_down(container, monkeypatch)
        seen = lookups_counted(container, monkeypatch)
        served = [(await client.get("/v1/tasks", headers=owner)).status_code for _ in range(20)]
        refused = [(await client.get("/v1/tasks", headers=dead)).status_code for _ in range(20)]
    assert served == [200] * 20
    assert refused == [401] * 20
    assert len(seen) == 40


async def test_an_address_refused_before_the_outage_stays_refused_to_its_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process remembers what the shared count told it; the outage does
    not lift a refusal already given."""
    container = build_container(tmp_path, failed_authentication_limit=SMALL_BUDGET)
    async with clients_of(container) as clients:
        client = clients[PEER]
        dead = await revoked_session(client, container, await sign_in(client, container))
        for _ in range(SMALL_BUDGET):
            await client.get("/v1/tasks", headers=dead)
        cache_down(container, monkeypatch)
        assert (await client.get("/v1/tasks", headers=dead)).status_code == 429


def test_a_refusal_is_remembered_until_its_window_ends() -> None:
    refused = RefusedAddresses()
    refused.refuse("addr:203.0.113.20", timedelta(seconds=30))
    refused.refuse("addr:203.0.113.21", timedelta(seconds=0))
    left = refused.refused_for("addr:203.0.113.20")
    assert left is not None and timedelta(seconds=29) < left <= timedelta(seconds=30)
    assert refused.refused_for("addr:203.0.113.21") is None
    assert refused.refused_for("addr:203.0.113.22") is None


def test_the_credential_budgets_are_generous() -> None:
    """Far above a person clicking fast or a poller at once a second; a
    traffic run's profiles are checked against them where they live."""
    settings = ApiSettings.model_validate({"_env_file": None})
    assert settings.credential_rate_limit_reads >= 1200
    assert settings.credential_rate_limit_writes >= 600
    assert settings.failed_authentication_limit >= 500
