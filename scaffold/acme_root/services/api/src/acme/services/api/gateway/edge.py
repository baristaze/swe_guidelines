"""The client address through the portal's CDN edge.

The portal reaches the API through its own CDN distribution, which forwards
the request to the load balancer. Each appends the address it received the
request from to X-Forwarded-For: the edge appends the viewer's, the load
balancer appends the edge's. uvicorn walks that header from the right past
the trusted proxies (the load balancer's block) and stops at the edge, so on
its own it names the edge as the client, and every viewer the edge serves
shares one rate-limit subject.

The edge is one hop further in only when the request came through this
system's distribution. Any CDN customer's distribution appends an address,
and its owner can write the header before it, so the edge's address range
proves nothing. The distribution proves itself instead: it sends a secret in
`X-Acme-Edge` on every request, set whatever the viewer sent under that
name. Beside that secret, and only beside it, this middleware takes the
address the edge appended as the client. Without it, or with a wrong one,
the client stays what uvicorn named. The header is removed from the scope
either way, so nothing further in reads, logs, or echoes it."""

import hmac
import ipaddress
from collections.abc import Sequence

from acme.services.api.gateway.observability import ASGIApp, Receive, Scope, Send

EDGE_HEADER = b"x-acme-edge"
FORWARDED_FOR_HEADER = b"x-forwarded-for"

type Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def client_through_edge(forwarded_for: str, trusted: Sequence[Network]) -> tuple[str, str] | None:
    """The edge's address and the one the edge appended: from the right, past
    every trusted proxy's hop, the edge (which is where uvicorn stops), then
    the viewer. None when the header is too short to hold both, or either
    entry is not an address."""
    hosts = [host.strip() for host in forwarded_for.split(",")]
    index = len(hosts) - 1
    while index >= 0 and _is_trusted(hosts[index], trusted):
        index -= 1
    if index < 1:
        return None
    try:
        return (
            str(ipaddress.ip_address(hosts[index])),
            str(ipaddress.ip_address(hosts[index - 1])),
        )
    except ValueError:
        return None


def _is_trusted(host: str, trusted: Sequence[Network]) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in trusted)


class EdgeClientMiddleware:
    """Names the viewer as the client of a request that carries the edge
    secret; strips the secret from every request, HTTP and socket alike.
    It runs inside uvicorn's proxy handling and outside everything of this
    app, so the rate limits, the access log, and the socket all read the
    address it settles on."""

    def __init__(self, app: ASGIApp, secret: str, trusted_proxies: Sequence[str]) -> None:
        self.app = app
        self.secret = secret.encode()
        self.trusted = [ipaddress.ip_network(proxy) for proxy in trusted_proxies]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        presented: bytes | None = None
        forwarded_for = b""
        headers: list[tuple[bytes, bytes]] = []
        for name, value in scope["headers"]:
            if name == EDGE_HEADER:
                presented = value
                continue
            if name == FORWARDED_FOR_HEADER:
                forwarded_for = forwarded_for + b"," + value if forwarded_for else value
            headers.append((name, value))
        if presented is not None:
            scope["headers"] = headers
            if hmac.compare_digest(presented, self.secret):
                self.step_past_the_edge(scope, forwarded_for.decode("latin-1"))
        await self.app(scope, receive, send)

    def step_past_the_edge(self, scope: Scope, forwarded_for: str) -> None:
        """Only when uvicorn already named the edge: that is, the peer was a
        trusted proxy and the edge is the hop it stopped at. A caller that
        reached the process around the load balancer stays its own client."""
        hop = client_through_edge(forwarded_for, self.trusted)
        client = scope.get("client")
        if hop is None or client is None:
            return
        edge, viewer = hop
        if _same_address(client[0], edge):
            scope["client"] = (viewer, 0)


def _same_address(one: str, other: str) -> bool:
    try:
        return ipaddress.ip_address(one) == ipaddress.ip_address(other)
    except ValueError:
        return False
