"""The frames on the socket, hand-written to mirror the service's realtime
envelopes (they are not in the OpenAPI document). A client routes on `type`
and reads tolerantly: a field it does not know is ignored."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from acme.client.types import EventView


class Frame(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    sent_at: datetime | None = None


class HelloEnvelope(Frame):
    type: Literal["hello"]
    org_id: UUID
    user_id: UUID
    seq: int  # the stream position when the socket opened
    ping_interval_seconds: int


class PongEnvelope(Frame):
    type: Literal["pong"]
    seq: int  # where the tenant's stream stands: a head past the cursor is a gap


class SubscribedEnvelope(Frame):
    type: Literal["subscribed"]
    topic: str


class UnsubscribedEnvelope(Frame):
    type: Literal["unsubscribed"]
    topic: str


class EntityChanged(Frame):
    """Which record changed (`kind` is "<namespace>.<entity>.<action>"), who
    changed it, and where it sits in the tenant's stream."""

    kind: str
    target_id: UUID
    seq: int
    actor_id: UUID

    @property
    def entity(self) -> str:
        parts = self.kind.split(".")
        return parts[1] if len(parts) >= 2 else self.kind

    @property
    def action(self) -> str:
        return self.kind.rsplit(".", 1)[-1]

    @classmethod
    def of_event(cls, event: EventView) -> EntityChanged:
        """A replayed record, as the push it would have been."""
        return cls(
            kind=event.kind, target_id=event.target_id, seq=event.seq, actor_id=event.actor_id
        )


class EventEnvelope(Frame):
    type: Literal["event"]
    topic: str
    payload: EntityChanged


class ErrorEnvelope(Frame):
    type: Literal["error"]
    code: str
    message: str


Envelope = Annotated[
    HelloEnvelope
    | PongEnvelope
    | SubscribedEnvelope
    | UnsubscribedEnvelope
    | EventEnvelope
    | ErrorEnvelope,
    Field(discriminator="type"),
]

_ENVELOPES: TypeAdapter[Envelope] = TypeAdapter(Envelope)


def parse_envelope(raw: str | bytes) -> Envelope | None:
    """None for a frame the client does not understand; the channel drops it."""
    try:
        return _ENVELOPES.validate_json(raw)
    except ValidationError:
        return None


ENTITY_CHANGED = "entity_changed"


def subscribe_command(topic: str = ENTITY_CHANGED) -> str:
    return f'{{"op": "subscribe", "topic": "{topic}"}}'


PING_COMMAND = '{"op": "ping"}'
