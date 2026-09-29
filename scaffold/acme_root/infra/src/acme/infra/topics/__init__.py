"""Topics: wake-ups and live updates. Names are fixed by enum, payloads by
the payload map. A topic is best effort: a published event reaches every
process that was subscribed at the time, at most once, and a bus hiccup may
lose it. Durable work never rides a topic; it is a row in the work queue,
and a missed wake-up degrades to polling latency, never to lost work.
`publish` says whether the bus took the message, so a producer that must not
lose one keeps it and sends it again."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from acme.infra.base import SYSTEM_SCOPE

NO_ACTOR = SYSTEM_SCOPE
"""The actor of a frame that names none: the reserved UUID, which no person's
id ever equals, so a client never mistakes such a change for its own."""


class TopicPayload(BaseModel):
    """The frozen base every payload extends, declared here so the object
    model never has to be imported by the bus. A tolerant reader: a field
    the consumer does not know is ignored, so producers and consumers roll
    out in either order."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    idempotency_key: UUID  # uuid_v7, set by the producer
    produced_at: datetime
    org_id: UUID
    # Set by a bus that trims a payload; the consumer re-reads the record. No
    # bus here trims yet, and a consumer treats every frame as a hint anyway.
    truncated: bool = False


class Topics(StrEnum):
    WORK_AVAILABLE = "work_available"
    ENTITY_CHANGED = "entity_changed"  # kind, target_id, seq, version: the realtime producer


class WorkAvailablePayload(TopicPayload):
    lane: str
    kind: str


class EntityChangedPayload(TopicPayload):
    """A record of `kind` changed; the socket is a hint and the record, at
    `seq` in the tenant's event stream, is the truth."""

    kind: str  # "<namespace>.<entity>.<created|updated|deleted>"
    target_id: UUID
    seq: int
    # The user whose request produced it. Defaulted, not required: a payload
    # gains only optional, defaulted fields, so a frame from a replica one
    # release behind parses here instead of being dropped as malformed, and
    # the two sides roll out in either order. Every producer sets it.
    actor_id: UUID = NO_ACTOR
    # The version the change wrote, where the record carries one and the
    # writer knew it. A client that already holds the record at this version
    # has nothing to read. None: read it.
    version: int | None = None


TOPIC_PAYLOADS: dict[Topics, type[TopicPayload]] = {
    Topics.WORK_AVAILABLE: WorkAvailablePayload,
    Topics.ENTITY_CHANGED: EntityChangedPayload,
}

TopicHandler = Callable[[TopicPayload], Awaitable[None]]


class TopicsInterface(ABC):
    @abstractmethod
    async def publish(self, topic: Topics, payload: TopicPayload) -> bool:
        """True once the bus took the message. False when it was dropped: the
        bus refused it, which the impl logs and counts, or a breaker declined
        to pay for it. Never raises for the bus; a payload of the wrong type
        raises. A caller that only wakes someone ignores the answer; the
        outbox relay keeps a row whose message was dropped and sends it
        again."""
        ...

    @abstractmethod
    def subscribe(
        self,
        topic: Topics,
        consumer: str,
        handler: TopicHandler,
    ) -> Callable[[], None]:
        """Returns an unsubscribe callable."""
        ...

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
