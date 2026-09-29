"""The whole application in-process over the memory storage root and the
local infra root, run inside its lifespan."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from api_support import build_container, sign_in
from fastapi import FastAPI
from httpx import ASGITransport

from acme.services.api.app import create_app
from acme.services.api.container import AppContainer


@pytest.fixture
def container(tmp_path: Path) -> AppContainer:
    return build_container(tmp_path)


@pytest.fixture
async def app(container: AppContainer) -> AsyncIterator[FastAPI]:
    app = create_app(container)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def owner(client: httpx.AsyncClient, container: AppContainer) -> dict[str, str]:
    return await sign_in(client, container)
