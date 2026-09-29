import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from acme.infra.base import new_id, utcnow
from acme.infra.observability import OUTCOMES
from acme.infra.queues import QueueDepth, QueueMessage, Queues, QueuesInterface

log = logging.getLogger(__name__)


@dataclass
class _Message:
    id: str
    body: bytes
    attempts: int = 0
    visible_at: datetime | None = None
    receipt: str = ""


class QueueMemoryImpl(QueuesInterface):
    """A faithful twin of the hosted queue: visibility timeouts, redelivery,
    and a dead-letter list after `max_receives` attempts. Like the hosted
    queue it does not deduplicate, and the interface offers no knob that
    says otherwise; the consumer is idempotent."""

    def __init__(self, max_receives: int = 5) -> None:
        self._max_receives = max_receives
        self._messages: dict[Queues, list[_Message]] = {q: [] for q in Queues}
        self._dead: dict[Queues, list[_Message]] = {q: [] for q in Queues}

    async def send(self, queue: Queues, body: bytes, *, deadline: datetime | None = None) -> None:
        self._messages[queue].append(_Message(id=str(new_id()), body=body))
        OUTCOMES.labels(subsystem="queue", outcome="sent").inc()

    async def receive(
        self, queue: Queues, max_messages: int, wait: timedelta, visibility: timedelta
    ) -> list[QueueMessage]:
        deadline = utcnow() + wait
        while True:
            received = self._take(queue, max_messages, visibility)
            if received or utcnow() >= deadline:
                OUTCOMES.labels(subsystem="queue", outcome="received").inc(len(received))
                return received
            await asyncio.sleep(0.01)

    def _take(self, queue: Queues, max_messages: int, visibility: timedelta) -> list[QueueMessage]:
        now = utcnow()
        received: list[QueueMessage] = []
        for message in list(self._messages[queue]):
            if len(received) >= max_messages:
                break
            if message.visible_at is not None and message.visible_at > now:
                continue
            message.attempts += 1
            if message.attempts > self._max_receives:
                self._dead_letter(queue, message)
                continue
            message.visible_at = now + visibility
            message.receipt = secrets.token_hex(8)
            received.append(
                QueueMessage(
                    id=message.id,
                    body=message.body,
                    receipt=message.receipt,
                    attempts=message.attempts,
                )
            )
        return received

    def _dead_letter(self, queue: Queues, message: _Message) -> None:
        """Dead letters are visible, not silent: a log line names the message
        and a metric counts it."""
        self._messages[queue].remove(message)
        self._dead[queue].append(message)
        log.warning(
            "message %s on queue %s dead-lettered after %d attempts",
            message.id,
            queue.value,
            self._max_receives,
        )
        OUTCOMES.labels(subsystem="queue", outcome="dead_lettered").inc()

    async def delete(self, queue: Queues, receipt: str) -> None:
        self._messages[queue] = [m for m in self._messages[queue] if m.receipt != receipt]
        OUTCOMES.labels(subsystem="queue", outcome="deleted").inc()

    async def change_visibility(self, queue: Queues, receipt: str, visibility: timedelta) -> None:
        for message in self._messages[queue]:
            if message.receipt == receipt:
                message.visible_at = utcnow() + visibility

    async def depth(self, queue: Queues) -> QueueDepth:
        now = utcnow()
        in_flight = sum(
            1 for m in self._messages[queue] if m.visible_at is not None and m.visible_at > now
        )
        return QueueDepth(
            visible=len(self._messages[queue]) - in_flight,
            in_flight=in_flight,
            dead_lettered=len(self._dead[queue]),
        )

    def describe(self) -> str:
        return "queues=memory"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
