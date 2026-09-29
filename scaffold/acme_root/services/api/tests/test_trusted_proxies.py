"""Behind a load balancer every request arrives from its address; the login
rate limit keys on the client only when the proxy is trusted to say who the
client is, and never on a header an untrusted peer sent. Through the portal's
CDN edge, one hop further in, the edge's secret is what makes the address the
edge appended the client's, and nothing else does."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from api_support import build_container
from httpx import ASGITransport
from pydantic import ValidationError

from acme.infra.cache import CacheScope
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.gateway.edge import EdgeClientMiddleware
from acme.services.api.main import server_options
from acme.services.api.settings import ApiSettings

PROXY = "10.10.3.7"
OUTSIDER = "198.51.100.7"
CLIENT = "203.0.113.9"
EDGE = "130.176.1.10"
EDGE_SECRET = "0123456789abcdef0123456789abcdef"


@dataclass
class Served:
    """The app the way `serve` hands it to uvicorn, proxy handling included,
    and every rate-limit key the sign-in routes counted, in order."""

    app: Any
    keys: list[str] = field(default_factory=list)

    async def login_from(
        self, peer: str, forwarded: str | None, edge: str | None = None
    ) -> httpx.Response:
        headers = {"X-Forwarded-For": forwarded} if forwarded else {}
        if edge is not None:
            headers["X-Acme-Edge"] = edge
        transport = ASGITransport(app=self.app, client=(peer, 40000), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/auth/callback",
                json={"code": "a-code-nobody-issued", "code_verifier": "v" * 43},
                headers=headers,
            )


async def serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: ApiSettings
) -> AsyncIterator[Served]:
    roots = build_container(tmp_path)
    container = AppContainer.for_tests(roots.storage, roots.infra, settings)
    app = create_app(container)
    config = uvicorn.Config(app, **server_options(settings))
    config.load()
    served = Served(config.loaded_app)
    cache = container.infra.get_cache(CacheScope.RATE_LIMIT)
    original = cache.increment

    async def spy(org_id: Any, key: str, ttl: Any) -> Any:
        served.keys.append(key)
        return await original(org_id, key, ttl)

    monkeypatch.setattr(cache, "increment", spy)
    async with app.router.lifespan_context(app):
        yield served


@pytest.fixture
async def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Served]:
    settings = ApiSettings.model_validate(
        {"environment": "test", "trusted_proxies": ["10.10.0.0/16"]}
    )
    async for s in serve(tmp_path, monkeypatch, settings):
        yield s


@pytest.fixture
async def behind_edge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Served]:
    settings = ApiSettings.model_validate(
        {"environment": "test", "trusted_proxies": ["10.10.0.0/16"], "edge_secret": EDGE_SECRET}
    )
    async for s in serve(tmp_path, monkeypatch, settings):
        yield s


async def test_a_trusted_proxy_names_the_client(served: Served) -> None:
    # No provider in this process: the callback answers 503, after it counted.
    assert (await served.login_from(PROXY, CLIENT)).status_code == 503
    assert served.keys == [f"login:addr:{CLIENT}"]


async def test_an_untrusted_peer_cannot_choose_its_address(served: Served) -> None:
    assert (await served.login_from(OUTSIDER, CLIENT)).status_code == 503
    assert served.keys == [f"login:addr:{OUTSIDER}"]


def test_no_proxy_means_the_peer_is_the_client() -> None:
    options = server_options(ApiSettings.model_validate({"_env_file": None, "environment": "test"}))
    assert options["proxy_headers"] is False
    uvicorn.Config(create_app, factory=True, **options)  # loads as `serve` runs it


@pytest.mark.parametrize("entry", ["*", "10.10.3.7/16", "load-balancer", ""])
def test_a_proxy_is_named_by_address_or_block_and_never_by_wildcard(entry: str) -> None:
    """A wildcard trusts every peer, and a trusted peer's X-Forwarded-For names
    the client: any caller could then choose its own rate-limit subject. A
    name that is not an address or a CIDR block is refused too, so a typo
    cannot pass as a trusted host."""
    with pytest.raises(ValidationError, match="trusted_proxies"):
        ApiSettings.model_validate(
            {"_env_file": None, "environment": "test", "trusted_proxies": [entry]}
        )


def test_addresses_and_blocks_are_trusted_proxies() -> None:
    proxies = ["10.10.3.7", "10.10.0.0/16", "fd00::/8"]
    settings = ApiSettings.model_validate(
        {"_env_file": None, "environment": "test", "trusted_proxies": proxies}
    )
    assert server_options(settings)["forwarded_allow_ips"] == proxies


async def test_the_edge_secret_names_the_viewer_the_edge_appended(behind_edge: Served) -> None:
    """The edge appends the viewer, the load balancer appends the edge."""
    assert (
        await behind_edge.login_from(PROXY, f"{CLIENT}, {EDGE}", EDGE_SECRET)
    ).status_code == 503
    assert behind_edge.keys == [f"login:addr:{CLIENT}"]


async def test_through_the_edge_a_viewer_cannot_choose_its_address(behind_edge: Served) -> None:
    """What the viewer sent sits left of what the edge appended."""
    forwarded = f"{OUTSIDER}, {CLIENT}, {EDGE}"
    assert (await behind_edge.login_from(PROXY, forwarded, EDGE_SECRET)).status_code == 503
    assert behind_edge.keys == [f"login:addr:{CLIENT}"]


@pytest.mark.parametrize("presented", [None, "", "not-the-secret", EDGE_SECRET + "x"])
async def test_without_the_secret_the_edge_is_the_client(
    behind_edge: Served, presented: str | None
) -> None:
    """Another distribution appends an address too, and its owner writes the
    header before it: without this edge's secret the hop is not trusted."""
    forwarded = f"{OUTSIDER}, {EDGE}"
    assert (await behind_edge.login_from(PROXY, forwarded, presented)).status_code == 503
    assert behind_edge.keys == [f"login:addr:{EDGE}"]


async def test_the_secret_from_an_untrusted_peer_moves_nothing_past_it(behind_edge: Served) -> None:
    """A caller that reaches the process around the load balancer is its own
    client whatever it sends."""
    forwarded = f"{CLIENT}, {EDGE}"
    assert (await behind_edge.login_from(OUTSIDER, forwarded, EDGE_SECRET)).status_code == 503
    assert behind_edge.keys == [f"login:addr:{OUTSIDER}"]


def test_an_edge_secret_needs_a_trusted_proxy_and_its_length() -> None:
    with pytest.raises(ValidationError, match="trusted_proxies"):
        ApiSettings.model_validate(
            {"_env_file": None, "environment": "test", "edge_secret": EDGE_SECRET}
        )
    with pytest.raises(ValidationError, match="32"):
        ApiSettings.model_validate(
            {
                "_env_file": None,
                "environment": "test",
                "trusted_proxies": ["10.10.0.0/16"],
                "edge_secret": "short",
            }
        )


async def test_the_edge_secret_never_reaches_the_app() -> None:
    """Removed from the scope with or without a match, so nothing further in
    reads, logs, or echoes it."""
    seen: list[Any] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append((dict(scope["headers"]), scope["client"]))

    middleware = EdgeClientMiddleware(app, EDGE_SECRET, ["10.10.0.0/16"])
    for presented in (EDGE_SECRET, "wrong"):
        scope = {
            "type": "websocket",
            "client": (EDGE, 0),
            "headers": [
                (b"x-forwarded-for", f"{CLIENT}, {EDGE}".encode()),
                (b"x-acme-edge", presented.encode()),
            ],
        }
        await middleware(scope, None, None)  # type: ignore[arg-type]
    assert [b"x-acme-edge" in headers for headers, _ in seen] == [False, False]
    assert [client for _, client in seen] == [(CLIENT, 0), (EDGE, 0)]
