"""The limits on authenticated routes (ADR 0059): each credential spends a
budget of reads, one of writes, and one of a holder's calls once it resolves
(ADR 0106), and each address spends a budget of failed authentications, past
which its requests are refused before any credential is looked up. Both
answer 429 with Retry-After in the one envelope, and both fail open when the
cache cannot count."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_support import (
    OWNER,
    SMALL_BUDGET,
    a_held_lease,
    ask,
    build_container,
    seed_request,
    sign_in,
    sign_in_as,
)

from acme.infra.cache import CacheScope
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.gateway.ratelimit import RefusedAddresses
from acme.services.api.settings import ApiSettings

PEER = "203.0.113.20"
NEIGHBOUR = "203.0.113.21"
FILES = "/v1/media/files"
UPLOAD = {"name": "report.pdf", "content_type": "application/pdf", "size_bytes": 5}


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
    return await sign_in_as(client, container, OWNER["email"], ctx.org_id)


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
            assert (await client.get(FILES, headers=owner)).status_code == 200
        refused = await client.get(FILES, headers=owner)
        # Writes have a budget of their own, which the reads did not spend.
        written = await client.post(FILES, json=UPLOAD, headers=owner)
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
            await client.get(FILES, headers=first)
        assert (await client.get(FILES, headers=first)).status_code == 429
        second = await another_session(client, container, first)
        assert (await client.get(FILES, headers=second)).status_code == 200


async def test_a_credential_past_its_write_budget_is_refused(tmp_path: Path) -> None:
    container = build_container(tmp_path, credential_rate_limit_writes=SMALL_BUDGET)
    async with clients_of(container) as clients:
        client = clients[PEER]
        owner = await sign_in(client, container)
        for _ in range(SMALL_BUDGET):
            created = await client.post(FILES, json=UPLOAD, headers=owner)
            assert created.status_code == 201
        refused = await client.post(FILES, json=UPLOAD, headers=owner)
        read = await client.get(FILES, headers=owner)
    assert refused.status_code == 429
    assert "Retry-After" in refused.headers
    assert read.status_code == 200


def holder_budgets(tmp_path: Path) -> AppContainer:
    """A container whose credentials have small budgets of writes and of a
    holder's calls, so a test can spend each to its end."""
    return build_container(
        tmp_path,
        credential_rate_limit_writes=SMALL_BUDGET,
        credential_rate_limit_holds=SMALL_BUDGET,
    )


async def other_writes(client: httpx.AsyncClient, owner: dict[str, str], n: int) -> list[int]:
    """The answers to n writes of the holder's own that are not a holder's
    call: asks that wait in line for a label no resource offers."""
    return [(await ask(client, owner, labels=["elsewhere"])).status_code for _ in range(n)]


async def test_a_holder_whose_writes_are_spent_still_renews_in_a_budget_of_its_own(
    tmp_path: Path,
) -> None:
    """A holder that streams its output spends its writes on it. The renewal
    that keeps its lease is counted apart, so the lease outlives the burst;
    and it is counted, not free, since that budget has an end of its own."""
    container = holder_budgets(tmp_path)
    async with clients_of(container) as clients:
        client = clients[PEER]
        owner = await sign_in(client, container)
        lease_id = await a_held_lease(client, container, owner)
        # The ask that took the lease spent the first write.
        writes = await other_writes(client, owner, SMALL_BUDGET)
        renew = f"/v1/leases/{lease_id}/renew"
        renewals = [await client.post(renew, headers=owner) for _ in range(SMALL_BUDGET)]
        past = await client.post(renew, headers=owner)
    assert writes == [201] * (SMALL_BUDGET - 1) + [429]
    assert [r.status_code for r in renewals] == [200] * SMALL_BUDGET
    assert all(r.json()["status"] == "active" for r in renewals)
    assert past.status_code == 429
    assert past.json()["error"]["code"] == "rate_limited"


async def test_renewals_past_their_budget_are_refused_and_spend_none_of_the_writes(
    tmp_path: Path,
) -> None:
    """The holds budget bounds a client that renews too often: past it, a
    renewal and a release are refused with Retry-After. None of them spent a
    write: the writes the holder had left are all still there."""
    container = holder_budgets(tmp_path)
    async with clients_of(container) as clients:
        client = clients[PEER]
        owner = await sign_in(client, container)
        lease_id = await a_held_lease(client, container, owner)
        renew = f"/v1/leases/{lease_id}/renew"
        renewals = [
            (await client.post(renew, headers=owner)).status_code for _ in range(SMALL_BUDGET)
        ]
        refused = [await client.post(renew, headers=owner) for _ in range(SMALL_BUDGET)]
        released = await client.post(f"/v1/leases/{lease_id}/release", headers=owner)
        writes = await other_writes(client, owner, SMALL_BUDGET)
    assert renewals == [200] * SMALL_BUDGET
    assert [r.status_code for r in refused] == [429] * SMALL_BUDGET
    assert 1 <= int(refused[0].headers["Retry-After"]) <= 60
    assert released.status_code == 429
    # The ask that took the lease spent one write, and nothing else did.
    assert writes == [201] * (SMALL_BUDGET - 1) + [429]


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
        answers = [(await client.get(FILES, headers=dead)).status_code for _ in range(100)]
        from_the_same_address = await client.get(FILES, headers=live)
        from_another = await clients[NEIGHBOUR].get(FILES, headers=live)
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
        answers = [(await clients[PEER].get(FILES)).status_code for _ in range(20)]
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
        served = [(await client.get(FILES, headers=owner)).status_code for _ in range(20)]
        refused = [(await client.get(FILES, headers=dead)).status_code for _ in range(20)]
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
            await client.get(FILES, headers=dead)
        cache_down(container, monkeypatch)
        assert (await client.get(FILES, headers=dead)).status_code == 429


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
    # Ten leases renewed once a second, the fastest pace a holder renews at.
    assert settings.credential_rate_limit_holds >= 600
    assert settings.failed_authentication_limit >= 500
