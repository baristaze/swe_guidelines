"""The Valkey connection an infra root shares between its cache and topics
impls. GLIDE clients are created asynchronously, so constructing this object
parses the URL and connects to nothing; the shared client is created by
start() or by the first command, whichever comes first, and close() ends it."""

import asyncio
from datetime import timedelta
from urllib.parse import unquote, urlsplit

from glide import GlideClient, GlideClientConfiguration, NodeAddress, ServerCredentials

_TLS_BY_SCHEME = {"valkey": False, "valkeys": True}


class ValkeyConnection:
    def __init__(self, url: str, timeout: timedelta) -> None:
        parts = urlsplit(url)
        if parts.scheme not in _TLS_BY_SCHEME or not parts.hostname:
            raise ValueError(
                f"not a valkey:// or valkeys:// URL: {parts.scheme}://{parts.hostname}"
            )
        database = parts.path.lstrip("/")
        self._address = NodeAddress(parts.hostname, parts.port or 6379)
        self._use_tls = _TLS_BY_SCHEME[parts.scheme]
        self._database_id = int(database) if database else 0
        self._credentials = (
            ServerCredentials(unquote(parts.password), unquote(parts.username or "") or None)
            if parts.password
            else None
        )
        self._request_timeout_ms = int(timeout.total_seconds() * 1000)
        self._client: GlideClient | None = None
        self._creating = asyncio.Lock()
        self._closed = False

    def configuration(
        self, pubsub: GlideClientConfiguration.PubSubSubscriptions | None = None
    ) -> GlideClientConfiguration:
        """The shared client connects lazily, so a server that is down costs the
        first command, not the boot. A subscriber connects at once: a lazy client
        never subscribes until something else sends it a command. Every request
        is bounded by the timeout from settings."""
        return GlideClientConfiguration(
            [self._address],
            use_tls=self._use_tls,
            credentials=self._credentials,
            request_timeout=self._request_timeout_ms,
            database_id=self._database_id,
            pubsub_subscriptions=pubsub,
            lazy_connect=pubsub is None,
        )

    async def client(self) -> GlideClient | None:
        """The shared client, created on first use; None once closed."""
        if self._client is None and not self._closed:
            async with self._creating:
                if self._client is None and not self._closed:
                    self._client = await GlideClient.create(self.configuration())
        return self._client

    def describe(self) -> str:
        scheme = "valkeys" if self._use_tls else "valkey"
        return f"{scheme}://{self._address.host}:{self._address.port}/{self._database_id}"

    async def start(self) -> None:
        await self.client()

    async def close(self) -> None:
        self._closed = True
        if self._client is not None:
            await self._client.close()
            self._client = None
