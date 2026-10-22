"""The whole API in-process behind the CLI: Starlette's TestClient runs the
app with its lifespan on its own thread, and a transport hops each of the
CLI's requests over to it, so every command runs under its own `asyncio.run`
the way it does in a terminal. The local store stands where the object store
does: a request to a URL it signed reaches it apart from the API, as a PUT
of a part reaches the object store."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_support import OWNER, add_member, build_container, callback_body, run, seed_request
from starlette.testclient import TestClient
from typer.testing import CliRunner

from acme.apps.cli import main
from acme.client.client import ApiClient
from acme.infra.buckets.local import BucketsLocalImpl
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.om.context import Role
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer

BOB = {"email": "bob@example.test"}


class Hop(httpx.AsyncBaseTransport):
    """Sends the CLI's request through the TestClient, on a worker thread,
    and keeps each one it sent."""

    def __init__(self, tc: TestClient) -> None:
        self._tc = tc
        self.sent: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)

        # The test client answers with its own response type, which is read
        # for its parts and rebuilt as the one the CLI's transport returns.
        def send() -> Any:
            return self._tc.request(
                request.method,
                str(request.url),
                content=request.read(),
                headers=dict(request.headers),
            )

        response = await asyncio.to_thread(send)
        return httpx.Response(
            response.status_code, headers=response.headers, content=response.content
        )


class Store(httpx.AsyncBaseTransport):
    """The local store, reached as the object store is: each request goes to
    the local impl's store app, and is kept. `cut_at`, when set, is the
    count of a PUT the wire drops before the store sees it."""

    def __init__(self, container: AppContainer) -> None:
        buckets = container.infra.get_buckets()
        assert isinstance(buckets, BucketsLocalImpl)
        self._store = httpx.ASGITransport(app=buckets.store)
        self.sent: list[httpx.Request] = []
        self.cut_at: int | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        puts = sum(1 for r in self.sent if r.method == "PUT")
        if request.method == "PUT" and puts == self.cut_at:
            raise httpx.ConnectError("the wire dropped", request=request)
        await request.aread()  # kept whole, so a test reads what was sent
        return await self._store.handle_async_request(request)


class Stack:
    def __init__(
        self, tc: TestClient, container: AppContainer, org_id: Any, twin: IdentityProviderTwinImpl
    ) -> None:
        self.tc = tc
        self.twin = twin
        self.container = container
        self.org_id = org_id
        self.runner = CliRunner()
        self.api = Hop(tc)
        self.store = Store(container)
        self._tokens: dict[str, str] = {}

    def client(self, token: str | None) -> ApiClient:
        return ApiClient(
            "http://test",
            app="cli",
            app_version="cli@test",
            token=token,
            transport=self.api,
            store_transport=self.store,
        )

    def auth_session_token(self, email: str) -> str:
        """One session per person; signing in on every command would trip the login rate limit."""
        if email not in self._tokens:
            self._tokens[email] = self._sign_in(email)
        return self._tokens[email]

    def _sign_in(self, email: str) -> str:
        login = self.tc.post("/v1/auth/callback", json=callback_body(self.container, email))
        assert login.status_code == 200, login.text
        session = self.tc.post(
            "/v1/auth/sessions",
            json={"org_id": str(self.org_id)},
            headers={"Authorization": f"Bearer {login.json()['token']}"},
        )
        assert session.status_code == 200, session.text
        return session.json()["token"]

    def confirm_as(self, email: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """The person at the browser: the page the CLI opens is confirmed at
        the twin as `email`, and the wait between asks is none. Answers the
        addresses the CLI opened."""
        opened: list[str] = []

        def confirm(url: str) -> None:
            opened.append(url)
            self.twin.confirm_device(url.rpartition("user_code=")[2], email)

        async def no_wait(seconds: float) -> None:
            return None

        monkeypatch.setattr(main, "open_browser", confirm)
        monkeypatch.setattr(main, "pause", no_wait)
        return opened

    def login(self, *args: str, monkeypatch: pytest.MonkeyPatch, email: str = OWNER["email"]):
        """`acme login` with the device sign-in confirmed as `email`."""
        self.confirm_as(email, monkeypatch)
        return self.acme("login", *args, token=None)

    def acme(self, *args: str, token: str | None = "owner", env: dict[str, str] | None = None):
        """Runs one command as the owner (or as `token`); returns the result."""
        variables = {"ACME_API_URL": "http://test", **(env or {})}
        if token == "owner":
            token = self.auth_session_token(OWNER["email"])
        if token is not None:
            variables["ACME_TOKEN"] = token
        return self.runner.invoke(main.app, list(args), env=variables, catch_exceptions=False)


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Stack]:
    twin = IdentityProviderTwinImpl()
    container = build_container(tmp_path, integrations=IntegrationsOverImpl(twin))
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    # Two people: the owner, and Bob as a member.
    run(add_member(container, org.id, BOB["email"], Role.MEMBER))
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ACME_TOKEN", raising=False)
    with TestClient(create_app(container)) as tc:
        stack = Stack(tc, container, org.id, twin)
        monkeypatch.setattr(main, "build_client", lambda _url, token: stack.client(token))
        yield stack
