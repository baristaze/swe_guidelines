"""Helpers the API tests share: the test container and the sign-in flow,
which goes through the identity provider's twin as the portal's goes through
the provider: the twin issues the code a browser brings back, and the
callback route exchanges it."""

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
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.root import IntegrationsInterface
from acme.om.base import new_id, utcnow
from acme.om.context import AppContext, AppType, OperatorRole, RequestContext, Role
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.storage.root import StorageInterface
from acme.om.tenancy.rules import totp_code, totp_step
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.issued import IssuedLogin
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.user import User
from acme.services.api.container import AppContainer
from acme.services.api.settings import ApiSettings

OWNER = {"email": "ann@example.test", "name": "Ann"}
TOTP_KEY = "YWNtZS10ZXN0cy10b3RwLWtleS10aGlydHktdHdvISE="
"""A Fernet-shaped key for the test container's TOTP secrets, tests only."""
VERIFIER = "v" * 43
"""The PKCE verifier a test's callback sends: the twin checks one only for a
code issued with a challenge, and the helpers here issue none."""


def seed_request() -> RequestContext:
    """The request stage a test's seeding mints at its edge, as `make seed` does."""
    return RequestContext(request_id=new_id(), app=AppContext(type=AppType.CLI, version="cli@test"))


def build_container(
    tmp_path: Path,
    storage: StorageInterface | None = None,
    integrations: IntegrationsInterface | None = None,
    **overrides: object,
) -> AppContainer:
    """The test container over the memory storage root, the local infra
    root, and the identity provider's twin, which every test signs people in
    through. A test that needs a bound or a deadline of its own names the
    settings it overrides, one that needs storage to behave a certain way
    passes its own root, and one that needs no provider passes
    `absent_integrations()`. The developer's `.env` is never read: its DSN
    would send every error a test raises on purpose to the local tracker."""
    settings = ApiSettings.model_validate(
        {
            "_env_file": None,
            "environment": "test",
            "totp_encryption_key": TOTP_KEY,
            **overrides,
        }
    )
    return AppContainer.for_tests(
        storage or StorageMemoryImpl(),
        InfraLocalImpl(tmp_path),
        settings,
        integrations or IntegrationsOverImpl(IdentityProviderTwinImpl()),
    )


def twin_of(container: AppContainer) -> IdentityProviderTwinImpl:
    """The identity provider's twin the container signs people in through."""
    provider = container.integrations.get_identity_provider()
    assert isinstance(provider, IdentityProviderTwinImpl), "this container has no twin"
    return provider


def callback_body(container: AppContainer, email: str, name: str = "") -> dict[str, str]:
    """What the portal posts to `/v1/auth/callback` once the person finished
    the provider's sign-in: the code the twin issued for them, and the
    verifier. A person nobody knew is signed up by it, with their personal
    org, named `name` when one is given."""
    first, _, last = name.partition(" ")
    code = twin_of(container).issue_code(email, first_name=first or None, last_name=last or None)
    return {"code": code, "code_verifier": VERIFIER}


async def login_token(client: httpx.AsyncClient, container: AppContainer, email: str) -> str:
    """A person signs in through the twin and the callback route: the login
    credential, before any tenant is chosen."""
    answer = await client.post("/v1/auth/callback", json=callback_body(container, email))
    assert answer.status_code == 200, answer.text
    return answer.json()["token"]


async def provider_login(container: AppContainer, email: str) -> IssuedLogin:
    """The same sign-in for a test that drives the manager and no route: the
    twin's code, finished by the manager's own callback."""
    code = twin_of(container).issue_code(email)
    return await container.managers.tenancy.sign_in.sign_in_with_code(seed_request(), code)


async def sign_in_as(
    client: httpx.AsyncClient, container: AppContainer, email: str, org_id: UUID
) -> dict[str, str]:
    """Signs a person in and chooses a tenant; returns the tenant headers."""
    session = await client.post(
        "/v1/auth/sessions",
        json={"org_id": str(org_id)},
        headers={"Authorization": f"Bearer {await login_token(client, container, email)}"},
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
    return await sign_in_as(client, container, OWNER["email"], org.id)


async def minted_owner(client: httpx.AsyncClient, container: AppContainer) -> dict[str, str]:
    """Bootstraps an org and signs its owner in by the local mint, for a test
    whose container fronts no twin (no provider, or a stubbed WorkOS) and is
    about something past the sign-in; the container is built with
    `local_login_mint` on. Returns the tenant headers."""
    _, org = await container.managers.tenancy.bootstrap(
        seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
    )
    login = await container.managers.tenancy.sign_in.mint_local_login(
        seed_request(), OWNER["email"]
    )
    session = await client.post(
        "/v1/auth/sessions", json={"org_id": str(org.id)}, headers=bearer(login.token)
    )
    assert session.status_code == 200, session.text
    return {**bearer(session.json()["token"]), "X-App": "portal", "X-App-Version": "portal@test"}


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
    first = await login_token(client, container, email)
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
        headers=bearer(await login_token(client, container, email)),
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
