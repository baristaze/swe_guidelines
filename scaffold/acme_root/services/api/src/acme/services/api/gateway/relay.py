"""The outbox relay after the answer: a request's writes commit their outbox
rows before it answers, and the relay of those rows (the event, the push, the
queued work) runs once the answer's last byte is sent. Nothing in an answer
comes from the relay, so the caller never waits for it.

The relay holds the rows that name this request's id (`hold`), and the
middleware relays them once the app below returns (`release`). It still runs
inside the request: under its request id, which every log line it writes
carries, and under its server span, as a child span of its own. Each row
carries the request's id and trace context besides, as a handoff does. A
relay that fails is logged and counted and never reaches the caller; the
maintenance sweep relays the rows past its grace. A process stopped between
the answer and the relay leaves the rows to the sweep too, and says how
many."""

from uuid import UUID

from acme.om.outbox import OutboxRelayInterface
from acme.services.api.gateway.observability import (
    ASGIApp,
    Receive,
    Scope,
    Send,
    request_id_of,
    tracer,
)

RELAY_SPAN = "outbox relay after the answer"
RELAY_ROWS_ATTRIBUTE = "acme.outbox.rows"


class RelayAfterAnswerMiddleware:
    """Holds the rows a request lands, and relays them once the app below
    returns, which is once the answer is sent. It sits inside admission, so
    the relay still holds the request's slot and the process never has more
    relays in flight than its bound of requests. A socket holds nothing: it
    answers for as long as it is open, so a relay it asks for runs at once."""

    def __init__(self, app: ASGIApp, relay: OutboxRelayInterface) -> None:
        self.app = app
        self.relay = relay

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_of(scope)
        self.relay.hold(request_id)
        try:
            await self.app(scope, receive, send)
        except Exception:
            # The rows committed before whatever failed after them.
            await self.release(request_id)
            raise
        except BaseException:
            self.relay.abandon(request_id)
            raise
        await self.release(request_id)

    async def release(self, request_id: UUID) -> None:
        rows = self.relay.held(request_id)
        if not rows:
            await self.relay.release(request_id)  # ends the hold; nothing to relay
            return
        with tracer.start_as_current_span(RELAY_SPAN, attributes={RELAY_ROWS_ATTRIBUTE: rows}):
            await self.relay.release(request_id)
