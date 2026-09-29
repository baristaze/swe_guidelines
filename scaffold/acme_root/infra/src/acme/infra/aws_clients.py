"""The one place botocore's client configuration is named. Every AWS impl
opens its clients with the configuration built here, so every call out to
AWS carries the timeout from settings and none goes out without one. The
holder gives each impl its lifecycle: one client, opened at start() and
closed at close(), never one per call."""

from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from botocore.config import Config


def client_config(timeout: timedelta) -> Config:
    """Connect and read bounded by the same timeout, per attempt. botocore's
    default retries stay, and they retry a timeout too, so a call to an
    endpoint that keeps hanging costs a few attempts of this, not one."""
    seconds = timeout.total_seconds()
    return Config(connect_timeout=seconds, read_timeout=seconds)


class AwsClientHolder:
    """Holds one client through an exit stack. `open()` enters the client's
    context, `client()` hands it out, `close()` leaves the context."""

    def __init__(self, service: str, create: Callable[[], Any]) -> None:
        self._service = service
        self._create = create
        self._stack = AsyncExitStack()
        self._client: Any = None

    async def open(self) -> None:
        if self._client is None:
            self._client = await self._stack.enter_async_context(self._create())

    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError(f"the {self._service} client is used before start()")
        return self._client

    async def close(self) -> None:
        await self._stack.aclose()
        self._client = None
