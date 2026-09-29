"""The limits on authenticated routes over the compose stack's Postgres and
Valkey (ADR 0059): a credential past its budget is answered 429 with
Retry-After, a flood of dead tokens stops reaching the database once its
address has spent its budget, and with Valkey unreachable both limits allow."""

import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_support import OWNER, seed_request, sign_in_as

from acme.om.base import new_id
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.settings import ApiSettings

pytestmark = pytest.mark.integration

BUDGET = 5
FLOOD = 100


def settings_over_the_stack(tmp_path: Path, **overrides: Any) -> ApiSettings:
    """The database from ACME_DATABASE_URL and the cache on the compose
    Valkey, unless a test names another; refused unless both are local."""
    settings = ApiSettings(
        **{
            "cache_backend": "valkey",
            "topics_backend": "memory",
            "buckets_backend": "local",
            "buckets_root": tmp_path / "buckets",
            "queues_backend": "memory",
            "secrets_backend": "local",
            "dev_sign_in_enabled": True,
            "sentry_dsn": None,
            "otel_endpoint": None,
            "credential_rate_limit_reads": BUDGET,
            "failed_authentication_limit": BUDGET,
            **overrides,
        }
    )
    settings.refuse_remote()
    return settings


def an_address() -> str:
    """An address of the benchmarking block no other run shares: the shared
    Valkey keeps each address's count for its window."""
    return f"198.18.{random.randrange(256)}.{random.randrange(1, 255)}"


@asynccontextmanager
async def over_the_stack(
    tmp_path: Path, **overrides: Any
) -> AsyncIterator[tuple[AppContainer, httpx.AsyncClient, dict[str, str]]]:
    """One process over the stack, a client from an address of its own, and
    the headers of a fresh owner's session."""
    container = AppContainer.build(settings_over_the_stack(tmp_path, **overrides))
    await container.start()
    try:
        suffix = new_id().hex[-8:]
        email = f"ann-{suffix}@example.test"
        _, org = await container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", f"ajax-{suffix}", email, OWNER["name"]
        )
        app = create_app(container)
        transport = httpx.ASGITransport(
            app=app, client=(an_address(), 40000), raise_app_exceptions=False
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield container, client, await sign_in_as(client, email, org.id)
    finally:
        await container.close()


def lookups_counted(container: AppContainer, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    tenancy = container.managers.tenancy
    seen: list[str] = []
    original = tenancy.authenticate

    async def spy(rctx: Any, credential: str) -> Any:
        seen.append(credential)
        return await original(rctx, credential)

    monkeypatch.setattr(tenancy, "authenticate", spy)
    return seen


async def signed_out(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    assert (await client.post("/v1/auth/logout", headers=headers)).status_code == 200
    return headers


async def test_a_credential_past_its_budget_is_answered_429_with_retry_after(
    tmp_path: Path,
) -> None:
    async with over_the_stack(tmp_path) as (_, client, owner):
        answers = [(await client.get("/v1/media/files", headers=owner)) for _ in range(BUDGET + 1)]
    assert [a.status_code for a in answers] == [200] * BUDGET + [429]
    refused = answers[-1]
    assert refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 60


async def test_a_dead_token_flood_stops_reaching_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with over_the_stack(tmp_path) as (container, client, owner):
        dead = await signed_out(client, owner)
        seen = lookups_counted(container, monkeypatch)
        answers = [
            (await client.get("/v1/media/files", headers=dead)).status_code for _ in range(FLOOD)
        ]
    assert answers == [401] * BUDGET + [429] * (FLOOD - BUDGET)
    assert len(seen) == BUDGET


async def test_with_valkey_unreachable_both_limits_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing listens on the port: the first calls spend the timeout, the
    breaker opens, and every limit allows from then on."""
    async with over_the_stack(
        tmp_path, valkey_url="valkey://127.0.0.1:1/0", valkey_timeout_seconds=0.2
    ) as (container, client, owner):
        served = [
            (await client.get("/v1/media/files", headers=owner)).status_code for _ in range(20)
        ]
        dead = await signed_out(client, owner)
        seen = lookups_counted(container, monkeypatch)
        refused = [
            (await client.get("/v1/media/files", headers=dead)).status_code for _ in range(20)
        ]
    assert served == [200] * 20
    assert refused == [401] * 20
    assert len(seen) == 20
