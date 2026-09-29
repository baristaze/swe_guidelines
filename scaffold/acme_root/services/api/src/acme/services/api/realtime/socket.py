"""The one socket route and the ticket route that opens it. The gateway
redeems the ticket; the handler subscribes to topics on the client's behalf
through the realtime service, moves frames through the bounded send buffer,
and closes the socket when its authority ends: when the credential behind
the ticket is revoked or the membership ends, which the realtime service
hears on the bus; when the handler's own recheck of the credential is
refused, which bounds a revocation the bus never delivered by minutes; and
at the credential's expiry whatever the client does. A change of the
member's role closes the socket too, with a code that asks the client to
reconnect under the rights it has now."""

import asyncio
import contextlib
import logging
import random
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from pydantic import ValidationError

from acme.infra.observability import failure_level
from acme.infra.topics import Topics
from acme.om.base import utcnow
from acme.om.exceptions import PlatformException
from acme.om.opcontext import OpContext
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.services.api.gateway.auth import CLOSE_UNAUTHENTICATED, Ctx, Principal
from acme.services.api.gateway.resolve import RealtimeService, container_of
from acme.services.api.realtime.envelopes import (
    ClientCommand,
    ErrorEnvelope,
    HelloEnvelope,
    IssuedTicketView,
    PongEnvelope,
    SubscribedEnvelope,
    UnsubscribedEnvelope,
)
from acme.services.api.realtime.send_buffer import SendBuffer
from acme.services.api.realtime.timeouts import IDLE_TIMEOUT_SECONDS
from acme.services.api.services.realtime import RIGHTS_CHANGED, RealtimeServiceInterface

log = logging.getLogger(__name__)
router = APIRouter(prefix="/realtime", tags=["realtime"])

CREDENTIAL_EXPIRED = "credential_expired"
"""The close reason when the credential behind the ticket reached its expiry."""

CLOSE_INTERNAL_ERROR = 1011
"""The close code for a failure of this handler. The gateway accepted the
socket before the route ran, so there is no HTTP response left to answer
with: an error that reaches Starlette's handler on an accepted socket is
answered with one anyway, which the server refuses as a protocol error and
drops without a close frame. Every failure below ends as this close."""

INTERNAL_ERROR = "internal_error"
"""The close reason that goes with it; the detail stays in the log."""

CLOSE_RECONNECT = 1012
"""The close code when the socket's rights changed and its credential still
holds: the registered "service restart", which every client reads as
"reconnect". 4401 would sign the person out, and nothing about their
sign-in is wrong."""


def new_send_buffer(websocket: WebSocket) -> SendBuffer:
    settings = container_of(websocket).settings
    return SendBuffer(settings.realtime_send_buffer_size, settings.realtime_control_buffer_size)


SocketSendBuffer = Annotated[SendBuffer, Depends(new_send_buffer)]


async def settle(task: asyncio.Task[None]) -> None:
    """Ends a socket's task. One that died because the peer left (a
    disconnect, a closed transport) is the normal end of a socket and not an
    error, and so is one this handler cancelled. Anything else it raised is
    logged and goes no further: `settle` runs in the teardown, and a second
    exception raised from there would skip the unsubscribes and the close
    frame that follow it, leaving the socket's handler in the dispatcher for
    the life of the process. A failure is logged at the level its shape
    calls for: a dependency that did not answer in time is a warning."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError, WebSocketDisconnect, OSError:
        return
    except Exception as error:
        log.log(failure_level(error), "%s ended with %r", task.get_name(), error)


async def close_quietly(websocket: WebSocket, code: int = 1000, reason: str | None = None) -> None:
    """Sends the close frame when there is still a peer to send it to. A peer
    that left first, or a close already sent, is the normal end of a socket:
    the server raises its disconnect error on a send after the peer is gone
    (an `OSError`, which Starlette turns into a 1006 disconnect), and
    Starlette refuses a second close with a `RuntimeError`. None of them
    is an error of this handler."""
    if WebSocketState.DISCONNECTED in (websocket.client_state, websocket.application_state):
        return
    with contextlib.suppress(RuntimeError, WebSocketDisconnect, OSError):
        await websocket.close(code=code, reason=reason)


@router.post("/tickets", response_model=IssuedTicketView, status_code=201)
async def mint_ticket(ctx: Ctx, realtime: RealtimeService) -> IssuedTicketView:
    return await realtime.issue_ticket(ctx)


async def serve_commands(
    websocket: WebSocket,
    ctx: OpContext,
    realtime: RealtimeServiceInterface,
    buffer: SendBuffer,
    subscriptions: dict[Topics, Callable[[], None]],
) -> None:
    """The inbound loop: subscribe, unsubscribe, ping. Returns when the peer
    leaves or stays silent past the idle timeout. A command is a text frame;
    any other frame is a bad command, answered and survived."""
    try:
        while True:
            message = await asyncio.wait_for(websocket.receive(), timeout=IDLE_TIMEOUT_SECONDS)
            if message["type"] == "websocket.disconnect":
                return
            raw = message.get("text")
            if raw is None:
                buffer.offer(ErrorEnvelope(code="bad_command", message="a command is a text frame"))
                continue
            try:
                command = ClientCommand.model_validate_json(raw)
                topic = Topics(command.topic) if command.topic is not None else None
            except (ValidationError, ValueError) as error:
                buffer.offer(ErrorEnvelope(code="bad_command", message=str(error)[:200]))
                continue
            if command.op == "ping":
                buffer.offer(PongEnvelope(seq=await realtime.pong_head(ctx)))
            elif topic is None:
                buffer.offer(ErrorEnvelope(code="bad_command", message="topic is required"))
            elif command.op == "subscribe":
                if topic not in subscriptions:
                    try:
                        subscriptions[topic] = realtime.subscribe(ctx, topic, buffer.offer)
                    except PlatformException as error:
                        buffer.offer(ErrorEnvelope(code=error.code, message=error.message))
                        continue
                buffer.offer(SubscribedEnvelope(topic=topic.value))
            else:
                if (unsubscribe := subscriptions.pop(topic, None)) is not None:
                    unsubscribe()
                buffer.offer(UnsubscribedEnvelope(topic=topic.value))
    except WebSocketDisconnect, TimeoutError:
        return


async def recheck_until_refused(
    realtime: RealtimeServiceInterface,
    principal: SocketPrincipal,
    end: Callable[[str], None],
    interval: float,
    phase: Callable[[float], float] = lambda interval: random.uniform(0.0, interval),
) -> None:
    """Asks the realtime service, every `interval` seconds, whether the
    socket's authority still holds, and ends the socket with the reason when
    it does not. The first check waits a phase drawn at random within one
    interval: sockets opened together (a deploy's reconnects) would
    otherwise check together forever. No two checks are further apart than
    the interval, so a revocation the bus lost holds a socket open for one
    interval at most. A check that cannot be made raises, and the socket
    closes as on any failure of its handler: a database out of reach is no
    proof that the credential still holds."""
    wait = phase(interval)
    while True:
        await asyncio.sleep(wait)
        reason = await realtime.recheck(principal)
        if reason is not None:
            log.info("socket of user %s closed on its recheck: %s", principal.ctx.user_id, reason)
            end(reason)
            return
        wait = interval


@router.websocket("")
async def channel(
    websocket: WebSocket, principal: Principal, realtime: RealtimeService, buffer: SocketSendBuffer
) -> None:
    # The gateway accepted the socket before it redeemed the ticket.
    ctx = principal.ctx
    loop = asyncio.get_running_loop()

    # The socket's authority ends with the credential behind its ticket: at
    # its expiry, or at its revocation, the socket is closed with 4401,
    # whatever the client does; a change of its rights closes it with 1012.
    # It is attached before anything is awaited: the redemption checked the
    # credential, and a revocation that landed during an await before the
    # attach would find no socket to close.
    ended: asyncio.Future[str] = loop.create_future()

    def end(reason: str) -> None:
        if not ended.done():
            ended.set_result(reason)

    detach = realtime.attach(principal, end)
    # The head is read before the drainer exists: a task created first and a
    # read that raises after it leave the drainer waiting on the buffer for
    # the life of the process, one more on every reconnect through an outage.
    try:
        head = await realtime.head(ctx)
    except Exception as error:
        detach()
        log.log(
            failure_level(error),
            "the stream head could not be read for user %s",
            ctx.user_id,
            exc_info=error,
        )
        await close_quietly(websocket, code=CLOSE_INTERNAL_ERROR, reason=INTERNAL_ERROR)
        return
    drainer = asyncio.create_task(buffer.drain(websocket), name=f"send-buffer-{ctx.user_id}")
    subscriptions: dict[Topics, Callable[[], None]] = {}
    buffer.offer(HelloEnvelope(org_id=ctx.org_id, user_id=ctx.user_id, seq=head))

    remaining = (principal.expires_at - utcnow()).total_seconds()
    expiry = loop.call_later(max(remaining, 0.0), end, CREDENTIAL_EXPIRED)
    commands = asyncio.create_task(
        serve_commands(websocket, ctx, realtime, buffer, subscriptions),
        name=f"commands-{ctx.user_id}",
    )
    recheck = asyncio.create_task(
        recheck_until_refused(
            realtime,
            principal,
            end,
            container_of(websocket).settings.realtime_recheck_seconds,
        ),
        name=f"recheck-{ctx.user_id}",
    )
    # How the socket ends, decided before the teardown and sent after it: the
    # teardown is the only place the subscriptions are dropped, so nothing
    # between here and it may raise past it.
    code: int = 1000
    reason: str | None = None
    try:
        await asyncio.wait({commands, recheck, ended}, return_when=asyncio.FIRST_COMPLETED)
        if ended.done():
            reason = ended.result()
            code = CLOSE_RECONNECT if reason == RIGHTS_CHANGED else CLOSE_UNAUTHENTICATED
        else:
            for task in (commands, recheck):
                if not task.done() or task.cancelled():
                    continue
                if (failure := task.exception()) is not None:
                    log.log(failure_level(failure), "%s failed", task.get_name(), exc_info=failure)
                    code, reason = CLOSE_INTERNAL_ERROR, INTERNAL_ERROR
                    break
    finally:
        expiry.cancel()
        detach()
        await settle(recheck)
        await settle(commands)
        for unsubscribe in subscriptions.values():
            unsubscribe()
        await settle(drainer)
        await close_quietly(websocket, code=code, reason=reason)
