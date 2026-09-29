"""The gateway mints the request stage once per request and asks the tenancy
manager for every stronger stage; nothing else builds a context. The
credential's prefix decides which transition accepts it, and a socket opens
on a single-use ticket rather than a credential in its URL."""

import logging
from typing import Annotated

from fastapi import Depends, Header, Query, Request, WebSocket, WebSocketException
from starlette.requests import HTTPConnection

from acme.infra.observability import current_trace_id, current_traceparent
from acme.om.context import AppContext, AppType, IdentityContext, RequestContext, TenantContext
from acme.om.exceptions import NotAuthenticated, NotFound, PlatformException, ValidationFailed
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.services.api.gateway.admission import deadline_of
from acme.services.api.gateway.observability import request_id_of
from acme.services.api.gateway.ratelimit import failures_counted, spend_credential
from acme.services.api.gateway.resolve import container_of

log = logging.getLogger(__name__)

APP_HEADER = "x-app"
APP_VERSION_HEADER = "x-app-version"
CLOSE_UNAUTHENTICATED = 4401


def bearer_of(authorization: str | None) -> str:
    if not authorization:
        raise NotAuthenticated("missing bearer credential")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credential:
        raise NotAuthenticated("expected a bearer credential")
    return credential.strip()


def app_context_of(x_app: str | None, x_app_version: str | None) -> AppContext:
    """The app header names the calling app; a machine caller without one is `api`."""
    if x_app is None:
        app_type = AppType.API
    else:
        try:
            app_type = AppType(x_app)
        except ValueError:
            raise ValidationFailed(f"unknown app {x_app!r} in {APP_HEADER}") from None
    return AppContext(type=app_type, version=x_app_version or f"{app_type.value}@unknown")


def request_context(
    connection: HTTPConnection,
    x_app: Annotated[str | None, Header()] = None,
    x_app_version: Annotated[str | None, Header()] = None,
) -> RequestContext:
    """The weakest stage, minted once at the edge: the request id the
    middleware stamped on the scope, the calling app from its headers, the
    trace id of the current span and its trace context, the one place the
    tracer is read for it, and the deadline admission gave the request
    (ADR 0069). A websocket scope reaches it the same way, with no deadline:
    its session bounds it.

    It names no causing request. A request that arrived at the edge was caused
    by nothing this system knows of, and the field is a handoff's, filled by
    the claim on the far side of the queue; the lines this request writes
    carry its own id alone."""
    return RequestContext(
        request_id=request_id_of(connection.scope),
        app=app_context_of(x_app, x_app_version),
        trace_id=current_trace_id(),
        traceparent=current_traceparent(),
        deadline=deadline_of(connection.scope),
    )


Rctx = Annotated[RequestContext, Depends(request_context)]


async def current_context(
    request: Request,
    rctx: Rctx,
    authorization: Annotated[str | None, Header()] = None,
) -> TenantContext:
    """The tenant stage: a session token or an api key, resolved to a membership.
    The lookup counts its failures against the client address, and the
    resolved credential spends its own budget (ADR 0059)."""
    tenancy = container_of(request).managers.tenancy
    credential = bearer_of(authorization)
    async with failures_counted(request):
        ctx = await tenancy.authenticate(rctx, credential)
    await spend_credential(request, ctx)
    return ctx


Ctx = Annotated[TenantContext, Depends(current_context)]


async def dev_sign_in_open(request: Request) -> None:
    """The local sign-in's door, open only where `dev_sign_in_enabled` is on,
    which a deployed environment refuses at boot. Closed, the route answers
    404 exactly as a route that does not exist does, before the body is
    read."""
    if not container_of(request).settings.dev_sign_in_enabled:
        raise NotFound("Not Found")


async def current_identity(
    request: Request,
    rctx: Rctx,
    authorization: Annotated[str | None, Header()] = None,
) -> IdentityContext:
    """The identity stage: the person's own sign-in, accepted where a tenant is
    chosen, the person's places are listed, or an operator is admitted. The
    tenant-less sign-in credential proves it, and so does a live session,
    which proves its user's identity as well as its tenant, and an operator
    token, which reaches the operator gate and nothing else. The operator
    gate then takes the sign-in credential, with its second factor, or the
    operator token. The limits are the tenant stage's."""
    tenancy = container_of(request).managers.tenancy
    credential = bearer_of(authorization)
    async with failures_counted(request):
        identity = await tenancy.authenticate_login(rctx, credential)
    await spend_credential(request, identity)
    return identity


Identity = Annotated[IdentityContext, Depends(current_identity)]


async def socket_principal(
    websocket: WebSocket, rctx: Rctx, ticket: Annotated[str | None, Query()] = None
) -> SocketPrincipal:
    """The socket's principal and the instant its authority ends: the tenancy
    manager consumes the ticket once and re-checks the credential behind it.
    The handshake is accepted first, so a refusal reaches the client as a
    close with 4401 on an open socket; a close before the accept is an HTTP
    403 handshake failure on the wire, which no client can tell from any
    other refusal. The handler receives the socket already accepted; a
    missing ticket is optional to the framework for the same reason, so it
    too is refused after the accept and not by validation before it."""
    await websocket.accept()
    if not ticket:
        log.info("socket refused: no ticket")
        raise WebSocketException(code=CLOSE_UNAUTHENTICATED, reason=NotAuthenticated.code)
    tenancy = container_of(websocket).managers.tenancy
    try:
        return await tenancy.redeem_ticket(rctx, ticket)
    except PlatformException as error:
        log.info("socket refused: %s", error.message)
        raise WebSocketException(code=CLOSE_UNAUTHENTICATED, reason=error.code) from None


Principal = Annotated[SocketPrincipal, Depends(socket_principal)]
