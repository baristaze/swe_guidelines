"""Queues: work whose producer is outside the platform and cannot be told to
wait. At-least-once, no deduplication; the consumer is idempotent."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import StrEnum

from acme.infra.base import InfraModel


class Queues(StrEnum):
    WEBHOOKS = "webhooks"
    SLACK = "slack"  # what Slack sends the API, acknowledged there, handled by maintenance


class QueueMessage(InfraModel):
    id: str
    body: bytes
    receipt: str
    attempts: int


class QueueDepth(InfraModel):
    visible: int
    in_flight: int
    dead_lettered: int


class QueuesInterface(ABC):
    @abstractmethod
    async def send(self, queue: Queues, body: bytes, *, deadline: datetime | None = None) -> None:
        """Returns nothing, for the reason `publish` does: the observable id is
        the producer-set idempotency key the body carries, and a broker-assigned
        id carries no durable meaning across retries and replays. The `receipt`
        on a `QueueMessage` is not that id; it is the handle of one delivery,
        which `delete` and `change_visibility` take.

        A request sends with its deadline: the send ends by then, unreachable
        (`acme.infra.deadline`)."""
        ...

    @abstractmethod
    async def receive(
        self, queue: Queues, max_messages: int, wait: timedelta, visibility: timedelta
    ) -> list[QueueMessage]: ...

    @abstractmethod
    async def delete(self, queue: Queues, receipt: str) -> None: ...

    @abstractmethod
    async def change_visibility(
        self, queue: Queues, receipt: str, visibility: timedelta
    ) -> None: ...

    @abstractmethod
    async def depth(self, queue: Queues) -> QueueDepth: ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None:
        """Opened by the infra root at boot. An impl that holds no connection
        of its own returns None."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Closed by the infra root at shutdown, in reverse order of start."""
        ...
