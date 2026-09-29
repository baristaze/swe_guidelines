"""Helpers the API tests share: the test container and the sign-in flow."""

import asyncio
import base64
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.root import IntegrationsInterface
from acme.om.base import new_id, utcnow
from acme.om.opcontext import AppContext, AppType, OperatorRole, RequestContext, Role
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.storage.root import StorageInterface
from acme.om.tenancy.rules import totp_code, totp_step
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.user import User
from acme.services.api.container import AppContainer
from acme.services.api.settings import ApiSettings

OWNER = {"email": "ann@example.test", "name": "Ann"}
TOTP_KEY = "YWNtZS10ZXN0cy10b3RwLWtleS10aGlydHktdHdvISE="
"""A Fernet-shaped key for the test container's TOTP secrets, tests only."""


def seed_request() -> RequestContext:
    """The request stage a test's seeding mints at its edge, as `make seed` does."""
    return RequestContext(request_id=new_id(), app=AppContext(type=AppType.CLI, version="cli@test"))


def build_container(
    tmp_path: Path,
    storage: StorageInterface | None = None,
    integrations: IntegrationsInterface | None = None,
    **overrides: object,
) -> AppContainer:
    """The test container over the memory storage root and the local infra
    root, with the local sign-in on. A test that needs a bound or a deadline
    of its own names the settings it overrides, one that needs storage to
    behave a certain way passes its own root, and one that signs in through
    the identity provider passes the integrations root over the twin. The
    developer's `.env` is never read: its DSN would send every error a test
    raises on purpose to the local tracker."""
    settings = ApiSettings.model_validate(
        {
            "_env_file": None,
            "environment": "test",
            "totp_encryption_key": TOTP_KEY,
            "dev_sign_in_enabled": True,
            **overrides,
        }
    )
    return AppContainer.for_tests(
        storage or StorageMemoryImpl(), InfraLocalImpl(tmp_path), settings, integrations
    )


async def dev_login(client: httpx.AsyncClient, email: str) -> str:
    """The local sign-in by address alone, which the test container has on:
    the login credential, before any tenant is chosen."""
    login = await client.post("/v1/auth/dev-sign-in", json={"email": email})
    assert login.status_code == 200, login.text
    return login.json()["token"]


async def sign_in_as(client: httpx.AsyncClient, email: str, org_id: UUID) -> dict[str, str]:
    """Signs a person in and chooses a tenant; returns the tenant headers."""
    login = await client.post("/v1/auth/dev-sign-in", json={"email": email})
    assert login.status_code == 200, login.text
    session = await client.post(
        "/v1/auth/sessions",
        json={"org_id": str(org_id)},
        headers={"Authorization": f"Bearer {login.json()['token']}"},
    )
    assert session.status_code == 200, session.text
    return {
        "Authorization": f"Bearer {session.json()['token']}",
        "X-App": "portal",
        "X-App-Version": "portal@test",
    }


async def sign_in(client: httpx.AsyncClient, container: AppContainer) -> dict[str, str]:
    """Bootstraps an org, signs its owner in, and returns the tenant headers."""
    _, org = await container.managers.tenancy.bootstrap(
        seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
    )
    return await sign_in_as(client, OWNER["email"], org.id)


async def add_member(container: AppContainer, org_id: UUID, email: str, role: Role) -> User:
    """Seeds a second member straight into storage, as the seeding does."""
    storage = container.storage.get_tenancy_storage()
    now = utcnow()
    identity_id, user_id = new_id(), new_id()
    await storage.write_identity(
        Identity(
            id=identity_id,
            created_at=now,
            updated_at=now,
            created_by=identity_id,
            updated_by=identity_id,
            email=email,
        )
    )
    user = User(
        id=user_id,
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
        identity_id=identity_id,
        email=email,
        display_name=email.split("@")[0].title(),
    )
    await storage.write_user(org_id, user)
    await storage.write_membership(
        org_id,
        Membership(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=user_id,
            updated_by=user_id,
            user_id=user_id,
            role=role,
        ),
    )
    return user


def secret_of(otpauth_uri: str) -> bytes:
    """The secret an authenticator app reads out of an `otpauth://` URI."""
    encoded = parse_qs(urlparse(otpauth_uri).query)["secret"][0]
    return base64.b32decode(encoded + "=" * (-len(encoded) % 8))


def code_at(secret: bytes, steps_ahead: int) -> str:
    """The code an authenticator shows `steps_ahead` time steps from now. A
    test draws each code at a later step than the one before, since the plane
    accepts a step once; the window reaches one step ahead of the clock."""
    return totp_code(secret, totp_step(utcnow()) + steps_ahead)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-App": "cli", "X-App-Version": "cli@test"}


async def enrol_operator(
    client: httpx.AsyncClient, container: AppContainer, email: str, role: OperatorRole
) -> tuple[dict[str, str], bytes]:
    """An operator through their first sign-in to the plane, over the API, and
    the token that sign-in mints with the whole of the entry. Returns that
    token's headers, which the plane reads and writes with, and the secret."""
    signed_in, secret = await enrolled_sign_in(client, container, email, role)
    token = await client.post(
        "/v1/admin/me/tokens", headers=signed_in, json={"permission": role.value}
    )
    assert token.status_code == 200, token.text
    return bearer(token.json()["token"]), secret


async def enrolled_sign_in(
    client: httpx.AsyncClient, container: AppContainer, email: str, role: OperatorRole
) -> tuple[dict[str, str], bytes]:
    """An operator through their first sign-in to the plane, over the API: put
    on the allowlist (seeded into an org of their own), admitted to enrol,
    the secret minted and confirmed with this step's code, then signed in
    again with the next step's. Returns that sign-in's headers, which mint
    one token and do nothing else, and the secret. The next sign-in of the
    same operator in one test is refused as a reused step unless the clock
    has moved on, so a test signs in once; the grant job mints any other
    token a test needs for the same identity."""
    slug = email.split("@")[0]
    await container.managers.tenancy.bootstrap(
        seed_request(), slug.title(), slug, email, "Op", operator_role=role
    )
    first = await dev_login(client, email)
    enrolling = bearer(first)
    minted = await client.post("/v1/admin/me/totp", headers=enrolling)
    assert minted.status_code == 200, minted.text
    secret = secret_of(minted.json()["otpauth_uri"])
    confirmed = await client.post(
        "/v1/admin/me/totp/confirm", headers=enrolling, json={"totp_code": code_at(secret, 0)}
    )
    assert confirmed.status_code == 200, confirmed.text
    login = await client.post(
        "/v1/auth/second-factor",
        headers=bearer(await dev_login(client, email)),
        json={"totp_code": code_at(secret, 1)},
    )
    assert login.status_code == 200, login.text
    return bearer(login.json()["token"]), secret


SMALL_BUDGET = 5
"""A rate limit's budget in a test that spends it: the settings' own is
sized for a crowd behind one address, far past what a test should send."""


@asynccontextmanager
async def client_over(container: AppContainer) -> AsyncIterator[httpx.AsyncClient]:
    """The app over a container a test built with settings of its own, inside
    its lifespan, and a client of it."""
    from acme.services.api.app import create_app

    app = create_app(container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """For the few sync tests that need a manager call before the app runs."""
    return asyncio.run(coro)
