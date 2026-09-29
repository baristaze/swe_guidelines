"""The whole API in-process behind the CLI: Starlette's TestClient runs the
app with its lifespan on its own thread, and a transport hops each of the
CLI's requests over to it, so every command runs under its own `asyncio.run`
the way it does in a terminal."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_support import OWNER, add_member, build_container, on_plan, run, seed_request
from starlette.testclient import TestClient
from typer.testing import CliRunner

from acme.apps.cli import main
from acme.client.client import ApiClient
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import Role
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer

BOB = {"email": "bob@example.test"}


class Hop(httpx.AsyncBaseTransport):
    """Sends the CLI's request through the TestClient, on a worker thread."""

    def __init__(self, tc: TestClient) -> None:
        self._tc = tc

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
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


class Stack:
    def __init__(
        self, tc: TestClient, container: AppContainer, org_id: Any, twin: IdentityProviderTwinImpl
    ) -> None:
        self.tc = tc
        self.twin = twin
        self.container = container
        self.org_id = org_id
        self.runner = CliRunner()
        self._tokens: dict[str, str] = {}

    def client(self, token: str | None) -> ApiClient:
        return ApiClient(
            "http://test", app="cli", app_version="cli@test", token=token, transport=Hop(self.tc)
        )

    def session_token(self, email: str) -> str:
        """One session per person; signing in on every command would trip the login rate limit."""
        if email not in self._tokens:
            self._tokens[email] = self._sign_in(email)
        return self._tokens[email]

    def _sign_in(self, email: str) -> str:
        login = self.tc.post("/v1/auth/dev-sign-in", json={"email": email})
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
            token = self.session_token(OWNER["email"])
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
    # Team: two people and more tasks than Free allows, the case most commands
    # are about; the plan's own refusal has a test of its own.
    run(on_plan(container, org.id, Plan.TEAM))
    run(add_member(container, org.id, BOB["email"], Role.MEMBER))
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ACME_TOKEN", raising=False)
    with TestClient(create_app(container)) as tc:
        stack = Stack(tc, container, org.id, twin)
        monkeypatch.setattr(main, "build_client", lambda _url, token: stack.client(token))
        yield stack
