"""Rate limits counted in the shared cache so every replica shares one
budget. Three kinds, each keyed on its own subject, never on a digest of the
bearer:

- A route's budget. The sign-in routes carry `rate_limited("login")`, keyed
  on the client address, since no credential exists yet.
- A credential's budget, reads apart from writes, keyed on the session or
  key id. Every authenticated request spends it, after the credential
  resolves, in one increment.
- An address's budget of failed authentications. Once it is spent, a
  request from that address is refused before the credential is looked up.

The client address is what the proxy handling and the edge middleware settle
on (see `edge.py`); nothing here reads a forwarded header. Every limit fails
open: a cache that cannot count allows the request. They guard against
runaway clients and are not a security boundary (ADR 0059)."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import Depends, Request
from starlette.requests import HTTPConnection

from acme.infra.cache import CacheScope
from acme.infra.observability import OUTCOMES
from acme.om.base import EMPTY_UUID, Platform
from acme.om.context import CredentialScope, IdentityContext, TenantContext
from acme.om.exceptions import NotAuthenticated, PlatformException
from acme.services.api.gateway.admission import READ_METHODS
from acme.services.api.gateway.resolve import container_of

FAILED_AUTHENTICATIONS = "auth-failed"
"""The key prefix of the failed-authentication count, beside the route names."""


class RateLimited(PlatformException):
    http_status = 429
    code = "rate_limited"

    def __init__(self, retry_after: timedelta, message: str = "rate limit exceeded") -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RateLimit(Platform):
    limit: int
    window: timedelta


class RateLimitOptions(Platform):
    """Every budget, built from settings at boot and held on the container."""

    login: RateLimit
    reads: RateLimit
    writes: RateLimit
    failed_authentications: RateLimit

    def of(self, route: str) -> RateLimit:
        budgets = {"login": self.login, "reads": self.reads, "writes": self.writes}
        return budgets[route]


class RefusedAddresses:
    """The addresses whose failed authentications spent the shared budget,
    each until the end of the window the shared count named. The shared
    count decides; this process only remembers the answer it was given, so
    a refusal costs no round trip at all. Another replica learns it from its
    own next failure from that address. An entry is added only past a spent
    budget and dropped when its window ends."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}

    def refused_for(self, address: str) -> timedelta | None:
        until = self._until.get(address)
        if until is None:
            return None
        left = until - time.monotonic()
        if left <= 0:
            del self._until[address]
            return None
        return timedelta(seconds=left)

    def refuse(self, address: str, left: timedelta) -> None:
        now = time.monotonic()
        self._until = {a: u for a, u in self._until.items() if u > now}
        self._until[address] = now + left.total_seconds()


def address_of(connection: HTTPConnection) -> str:
    """The client address as the proxy handling and the edge settled it."""
    client = connection.client.host if connection.client else "unknown"
    return f"addr:{client}"


def credential_of(ctx: CredentialScope) -> str:
    """The session or key id: the one subject of an authenticated request."""
    return f"cred:{ctx.credential_id}"


async def count(request: Request, route: str, org_id: UUID, subject: str) -> None:
    """One increment of the route's budget for the subject, counted under the
    tenant when the subject has one, else under the system scope."""
    container = container_of(request)
    budget = container.rate_limits.of(route)
    cache = container.infra.get_cache(CacheScope.RATE_LIMIT)
    total, remaining = await cache.increment(org_id, f"{route}:{subject}", budget.window)
    if total > budget.limit:
        OUTCOMES.labels(subsystem="rate_limit", outcome="rejected").inc()
        raise RateLimited(remaining)
    OUTCOMES.labels(subsystem="rate_limit", outcome="allowed").inc()


async def spend_credential(request: Request, ctx: TenantContext | IdentityContext) -> None:
    """The credential's own budget: reads (GET, HEAD) apart from writes, the
    split admission uses. A session or key counts under its tenant; a sign-in
    or operator credential, which has none, under the system scope."""
    route = "reads" if request.method in READ_METHODS else "writes"
    org_id = ctx.org_id if isinstance(ctx, TenantContext) else EMPTY_UUID
    await count(request, route, org_id, credential_of(ctx))


@asynccontextmanager
async def failures_counted(request: Request) -> AsyncIterator[None]:
    """Around the credential's lookup. An address this process was told is
    over its budget is refused before the lookup, and so before the
    database. A lookup that fails as `NotAuthenticated` (unknown, expired,
    or revoked) counts one against the address; the failure itself still
    answers 401, and the one that spends the budget makes the next answer
    429."""
    container = container_of(request)
    address = address_of(request)
    left = container.refused_addresses.refused_for(address)
    if left is not None:
        OUTCOMES.labels(subsystem="rate_limit", outcome="address_refused").inc()
        raise RateLimited(left, "too many failed authentications from this address")
    try:
        yield
    except NotAuthenticated:
        OUTCOMES.labels(subsystem="rate_limit", outcome="authentication_failed").inc()
        budget = container.rate_limits.failed_authentications
        cache = container.infra.get_cache(CacheScope.RATE_LIMIT)
        key = f"{FAILED_AUTHENTICATIONS}:{address}"
        total, remaining = await cache.increment(EMPTY_UUID, key, budget.window)
        if total >= budget.limit:
            container.refused_addresses.refuse(address, remaining)
        raise


def rate_limited(route: str) -> Any:
    """A route dependency: `dependencies=[rate_limited("login")]`, counted per
    client address. The budget named by `route` must exist on
    RateLimitOptions. An authenticated route needs none: the credential's
    budget is spent where the credential resolves."""

    async def by_address(request: Request) -> None:
        await count(request, route, EMPTY_UUID, address_of(request))

    return Depends(by_address)
