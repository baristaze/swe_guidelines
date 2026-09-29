"""Every frame on the socket is a typed envelope; the client routes on `type`.
Inbound traffic is small by design: subscribe, unsubscribe, ping."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from acme.services.api.realtime.timeouts import PING_INTERVAL_SECONDS
from acme.services.api.types.common import RequestBody, View
from acme.services.api.types.events import EntityChangedView


class Envelope(View):
    """Subclasses declare `type` as a literal; the client routes on it."""

    sent_at: datetime | None = None


class HelloEnvelope(Envelope):
    """The first frame: who the socket is, how often to ping, and where the
    tenant's stream stands, so a client replays from `seq` on a reconnect
    even when no push has reached it yet."""

    type: Literal["hello"] = "hello"
    org_id: UUID
    user_id: UUID
    seq: int
    ping_interval_seconds: int = PING_INTERVAL_SECONDS


class PongEnvelope(Envelope):
    """The keepalive's answer carries where the tenant's stream stands, so a
    client whose last push was the one dropped learns of the gap on the next
    ping rather than on the next event; a quiet socket cannot hide a loss."""

    type: Literal["pong"] = "pong"
    seq: int


class SubscribedEnvelope(Envelope):
    type: Literal["subscribed"] = "subscribed"
    topic: str


class UnsubscribedEnvelope(Envelope):
    type: Literal["unsubscribed"] = "unsubscribed"
    topic: str


class EventEnvelope(Envelope):
    """A push. The payload's `seq` is the tenant's stream position, so a
    client that sees a gap replays from storage rather than trusting the socket."""

    type: Literal["event"] = "event"
    topic: str
    payload: EntityChangedView


class ErrorEnvelope(Envelope):
    type: Literal["error"] = "error"
    code: str
    message: str


class ClientCommand(RequestBody):
    op: Literal["subscribe", "unsubscribe", "ping"]
    topic: str | None = None


class IssuedTicketView(View):
    """Carries the freshly minted socket ticket in the clear, once; redeeming
    it opens the channel and re-checks the credential behind it."""

    ticket: str
    expires_in_seconds: int
