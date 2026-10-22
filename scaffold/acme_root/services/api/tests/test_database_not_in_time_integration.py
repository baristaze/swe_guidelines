"""A database that did not answer in time, through the API over Postgres.

Two bounds end a call the database does not serve: a statement past its
deadline, which Postgres cancels, and a checkout past its bound, when the pool
has no connection to give. Each answers 503 `unavailable` with the request id,
the shape the portal and the command line read as "try again". Each is logged
as a warning and counted, and neither is an unhandled error, which is what an
ERROR line or an escaped exception would report to the error tracker.

A lease's renewal is the call that must not wait on a full pool: it is served
from the pool's reserve, within its deadline. Any other request, a read or a
write, waits on the pool alone and is refused past the bound with the reserve
free beside it, so the bulk work that filled the pool never drains the
reserve.

The bounds here are a second at most, so each case ends quickly; the
defaults are not changed by anything here.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from api_support import OWNER, a_held_lease, ask, client_over, seed_request, sign_in_as
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from acme.infra.observability import OUTCOMES, RequestIdFilter
from acme.om.base import new_id
from acme.om.storage.roles import DatabaseRole
from acme.services.api.container import AppContainer
from acme.services.api.settings import ApiSettings

pytestmark = pytest.mark.integration


def settings_over_postgres(tmp_path: Path, **overrides: Any) -> ApiSettings:
    """The database from ACME_DATABASE_URL and everything else in the
    process; refused unless the database is local."""
    settings = ApiSettings(
        **{
            "cache_backend": "memory",
            "topics_backend": "memory",
            "buckets_backend": "local",
            "buckets_root": tmp_path / "buckets",
            "queues_backend": "memory",
            "secrets_backend": "local",
            "sentry_dsn": None,
            "otel_endpoint": None,
            "identity_provider": "twin",
            **overrides,
        }
    )
    settings.refuse_remote()
    return settings


@asynccontextmanager
async def over_postgres(
    tmp_path: Path, **overrides: Any
) -> AsyncIterator[tuple[AppContainer, httpx.AsyncClient, dict[str, str]]]:
    """The API over Postgres under the bounds a test names, and the headers of
    a fresh owner's session."""
    container = AppContainer.build(settings_over_postgres(tmp_path, **overrides))
    async with client_over(container) as client:
        suffix = new_id().hex[-8:]
        email = f"ann-{suffix}@example.test"
        _, org = await container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", f"ajax-{suffix}", email, OWNER["name"]
        )
        yield container, client, await sign_in_as(client, container, email, org.id)


@asynccontextmanager
async def files_locked(container: AppContainer) -> AsyncIterator[None]:
    """Another transaction holds `core.files` in the one mode a read waits on,
    the way a migration's `DROP INDEX` queued behind a long transaction does.
    It is its own connection, outside the API's pool."""
    engine = create_async_engine(container.settings.role_urls()[DatabaseRole.CORE])
    try:
        async with engine.begin() as connection:
            await connection.execute(text("LOCK TABLE core.files IN ACCESS EXCLUSIVE MODE"))
            yield
    finally:
        await engine.dispose()


def counted(outcome: str) -> float:
    return OUTCOMES.labels(subsystem="storage", outcome=outcome)._value.get()


def assert_unavailable(answer: httpx.Response) -> None:
    assert answer.status_code == 503, answer.text
    assert answer.json()["error"] == {
        "code": "unavailable",
        "message": "internal error",
        "request_id": answer.headers["x-request-id"],
    }


def lines_of(caplog: pytest.LogCaptureFixture, answer: httpx.Response) -> list[logging.LogRecord]:
    request_id = answer.headers["x-request-id"]
    return [r for r in caplog.records if getattr(r, "request_id", None) == request_id]


Served = tuple[AppContainer, httpx.AsyncClient, dict[str, str]]


@pytest.fixture
async def short_deadline(tmp_path: Path) -> AsyncIterator[Served]:
    """Every statement carries a deadline of 0.3 s. Built before the test runs,
    so the boot's logging is in place before the test's capture is."""
    async with over_postgres(tmp_path, database_statement_timeout_seconds=0.3) as served:
        yield served


@pytest.fixture
async def pool_of_one(tmp_path: Path) -> AsyncIterator[Served]:
    """One connection per pool, and a checkout waits 0.3 s for it."""
    async with over_postgres(
        tmp_path, database_pool_size=1, database_checkout_timeout_seconds=0.3
    ) as served:
        yield served


async def test_a_statement_past_its_deadline_answers_unavailable(
    short_deadline: Served, caplog: pytest.LogCaptureFixture
) -> None:
    container, client, owner = short_deadline
    before = counted("statement_timeout")
    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.WARNING):
        async with files_locked(container):
            answer = await client.get("/v1/media/files", headers=owner)

    assert_unavailable(answer)
    assert counted("statement_timeout") == before + 1
    lines = lines_of(caplog, answer)
    assert [r.levelno for r in lines] == [logging.WARNING]
    assert "unavailable on GET /v1/media/files" in lines[0].getMessage()
    assert "a statement on the core role (runtime login) passed its deadline" in (
        lines[0].getMessage()
    )


async def test_a_checkout_past_its_bound_answers_unavailable(
    pool_of_one: Served, caplog: pytest.LogCaptureFixture
) -> None:
    """Another checkout holds the runtime login's one connection: the
    request's own checkout waits the bound and gets nothing."""
    container, client, owner = pool_of_one
    before = counted("checkout_timeout")
    runtime = container.storage._sessions[DatabaseRole.CORE]  # type: ignore[attr-defined]
    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.WARNING):
        async with runtime() as holder:
            await holder.connection()
            answer = await client.get("/v1/media/files", headers=owner)

    assert_unavailable(answer)
    assert counted("checkout_timeout") == before + 1
    lines = lines_of(caplog, answer)
    assert [r.levelno for r in lines] == [logging.WARNING]
    assert "unavailable on GET /v1/media/files" in lines[0].getMessage()
    assert "no connection to the core role (runtime login) within the checkout bound" in (
        lines[0].getMessage()
    )
    # The pool serves again once the other checkout lets go.
    assert (await client.get("/v1/media/files", headers=owner)).status_code == 200


CHECKOUT_BOUND = 1.0
"""How long a call waits on a full pool before it answers 503, in the cases
of the reserve: long enough that a call which waited on a pool shows it."""


@pytest.fixture
async def pools_of_one_and_a_reserve(tmp_path: Path) -> AsyncIterator[Served]:
    """One connection per pool, a reserve of one beside each, and a checkout
    waits a second."""
    async with over_postgres(
        tmp_path,
        database_pool_size=1,
        database_pool_reserve=1,
        database_checkout_timeout_seconds=CHECKOUT_BOUND,
    ) as served:
        yield served


@asynccontextmanager
async def every_pool_held(container: AppContainer) -> AsyncIterator[None]:
    """Another checkout holds the one connection of every pool the process
    opened, on every role and under both logins, as a slow database's writes
    do. The reserves are left free."""
    engines = container.storage._engines  # type: ignore[attr-defined]
    async with AsyncExitStack() as stack:
        for (_, _, reserve), engine in engines.items():
            if not reserve:
                await stack.enter_async_context(engine.connect())
        yield


async def test_a_renewal_beside_full_pools_is_served_from_the_reserve_in_time(
    pools_of_one_and_a_reserve: Served,
) -> None:
    container, client, owner = pools_of_one_and_a_reserve
    lease_id = await a_held_lease(client, container, owner)

    async with every_pool_held(container):
        started = time.monotonic()
        renewed = await client.post(f"/v1/leases/{lease_id}/renew", headers=owner)
        served = time.monotonic() - started

    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["status"] == "active"
    # It never waited on a pool: one wait would have cost the whole bound.
    assert served < CHECKOUT_BOUND


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_a_request_outside_the_holds_lane_never_takes_the_reserve(
    pools_of_one_and_a_reserve: Served, method: str
) -> None:
    """A read and a write, each in its own lane, beside the same full pools
    and the same free reserves: each waits the bound and is refused."""
    container, client, owner = pools_of_one_and_a_reserve

    async with every_pool_held(container):
        started = time.monotonic()
        if method == "GET":
            answer = await client.get("/v1/leases", headers=owner)
        else:
            answer = await ask(client, owner, leasable_resource_id=str(uuid4()))
        waited = time.monotonic() - started

    assert_unavailable(answer)
    assert waited >= CHECKOUT_BOUND
