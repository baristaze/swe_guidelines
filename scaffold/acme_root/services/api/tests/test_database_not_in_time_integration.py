"""A database that did not answer in time, through the API over Postgres.

Two bounds end a call the database does not serve: a statement past its
deadline, which Postgres cancels, and a checkout past its bound, when the pool
has no connection to give. Each answers 503 `unavailable` with the request id,
the shape the portal and the command line read as "try again". Each is logged
as a warning and counted, and neither is an unhandled error, which is what an
ERROR line or an escaped exception would report to the error tracker.

The bounds here are a fraction of a second, so each case ends at once; the
defaults are not changed by anything here.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_support import OWNER, client_over, seed_request, sign_in_as
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
            "dev_sign_in_enabled": True,
            "sentry_dsn": None,
            "otel_endpoint": None,
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
        yield container, client, await sign_in_as(client, email, org.id)


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
