"""Edge idempotency: a creating POST accepts an Idempotency-Key. The outcome
is recorded per tenant and per user under the key, on the same durable
primitive the queue handlers dedupe on, and replayed on a retry. Only an
outcome a retry cannot change is recorded: a refusal is replayed, a failure,
a 429, and a 402 release the marker so the retry runs again on the same id
(a 429 and a plan's bound are answers about now, not about the request): the
release
keeps the marker with its digest and its id and clears only the attempt, so
a retry after a failure that came once the row had landed finds the row
instead of creating a second one. A key seen with a different request is
refused; a key whose first request is still running is told to wait. The
marker names its attempt: an attempt that ran past the pending lease and
lost the marker to a retry is refused when it finishes or releases, and the
refusal is logged and swallowed here, because the retry owns the marker now
and whatever the slow attempt wrote is the row the retry found. A view that
declares secret fields is stored with them absent: the secret is shown once,
on the first response, and a replay says so."""

import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import Depends, Header, Request, Response
from pydantic import BaseModel

from acme.infra.observability import OUTCOMES
from acme.om.base import new_id
from acme.om.exceptions import IdempotencyAttemptLost, IdempotencyInProgress, PlatformException
from acme.om.idempotency import IdempotencyManagerInterface
from acme.om.idempotency.types.attempt import Attempt
from acme.om.idempotency.types.record import IdempotencyRecord
from acme.om.opcontext import OpContext, OperatorContext
from acme.services.api.gateway.admin import OperatorCtx
from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.resolve import container_of
from acme.services.api.types.common import ErrorBody, ErrorResponse

log = logging.getLogger(__name__)

REPLAYED_HEADER = "Idempotent-Replayed"
JSON = "application/json"
KEY_MAX_LENGTH = 255
"""The key lands in a unique index; a longer one is refused at the edge (422),
and so is an empty one: a header present with nothing in it is a malformed
request, not an absence, and honoured as a key it would replay every
same-body request of the caller for the retention as the first one."""


def request_digest(method: str, path: str, body: bytes) -> str:
    """What makes two requests the same request: method, path, and body."""
    digest = hashlib.sha256()
    digest.update(f"{method.upper()}\n{path}\n".encode())
    digest.update(body)
    return digest.hexdigest()


class Marker(Protocol):
    """The three moves on the marker of one principal's key, bound to the
    principal: the tenant plane's under (tenant, user), the operator plane's
    under (system scope, operator). `Idempotency` below runs the same
    record-replay-finish over either."""

    async def begin(self, key: str, request_digest: str, target_id: UUID) -> IdempotencyRecord: ...

    async def finish(
        self, key: str, attempt_id: UUID, status: int, body: str
    ) -> IdempotencyRecord: ...

    async def release(self, key: str, attempt_id: UUID) -> None: ...


class TenantMarker:
    def __init__(self, manager: IdempotencyManagerInterface, ctx: OpContext) -> None:
        self._manager = manager
        self._ctx = ctx

    async def begin(self, key: str, request_digest: str, target_id: UUID) -> IdempotencyRecord:
        return await self._manager.begin(self._ctx, key, request_digest, target_id)

    async def finish(self, key: str, attempt_id: UUID, status: int, body: str) -> IdempotencyRecord:
        return await self._manager.finish(self._ctx, key, attempt_id, status, body)

    async def release(self, key: str, attempt_id: UUID) -> None:
        await self._manager.release(self._ctx, key, attempt_id)


class OperatorMarker:
    def __init__(self, manager: IdempotencyManagerInterface, admin: OperatorContext) -> None:
        self._manager = manager
        self._admin = admin

    async def begin(self, key: str, request_digest: str, target_id: UUID) -> IdempotencyRecord:
        return await self._manager.begin_for_operator(self._admin, key, request_digest, target_id)

    async def finish(self, key: str, attempt_id: UUID, status: int, body: str) -> IdempotencyRecord:
        return await self._manager.finish_for_operator(self._admin, key, attempt_id, status, body)

    async def release(self, key: str, attempt_id: UUID) -> None:
        await self._manager.release_for_operator(self._admin, key, attempt_id)


class Idempotency:
    """Wraps the one creating call of a route so the outcome cannot escape
    without being recorded: `run` begins the record, calls the handler with the
    attempt the request runs under, and finishes the record with whatever the
    handler produced. The id on that attempt is minted here, before the marker,
    and travels on it, so a retry that re-arms a released marker or takes over
    an abandoned one creates on the same id, and neither a failure after the
    create nor a crash between the create and `finish` can end in two rows. The
    attempt token travels with it, so a write of a rerun that changes what is
    stored, the re-mint of a secret, can be made conditional on the marker
    still holding this attempt, the way `finish` and the release are."""

    def __init__(self, marker: Marker, request_id: UUID, key: str | None, digest: str) -> None:
        self._marker = marker
        self._request_id = request_id
        self._key = key
        self._digest = digest
        self.target_id: UUID = new_id()

    async def run(
        self, status: int, handler: Callable[[Attempt], Awaitable[BaseModel]]
    ) -> Response:
        if self._key is None:
            # No key, no marker: the id is fresh, so nothing reruns and there is
            # no attempt for a write to be conditional on.
            return _json(await handler(Attempt(target_id=self.target_id)), status)
        try:
            record = await self._marker.begin(self._key, self._digest, self.target_id)
        except IdempotencyInProgress:
            OUTCOMES.labels(subsystem="idempotency", outcome="in_progress").inc()
            raise
        if record.status is not None and record.body is not None:
            OUTCOMES.labels(subsystem="idempotency", outcome="replayed").inc()
            body = record.body
            if record.status >= 400:
                # A replayed refusal names this request, as its header does,
                # not the attempt that first produced it.
                body = self._with_request_id(body)
            return Response(
                content=body,
                status_code=record.status,
                media_type=JSON,
                headers={REPLAYED_HEADER: "true"},
            )
        attempt_id = record.attempt_id
        assert attempt_id is not None, "begin hands back a marker it armed"
        try:
            view = await handler(Attempt(target_id=record.target_id, attempt_id=attempt_id))
        except PlatformException as error:
            # A 429 is a refusal only for now: replayed, it would be the
            # answer for good, and the client's only exit a new key. So is a
            # 402, a plan's bound: the org may be on another plan a moment
            # later, and the retry of the same create should then land.
            if error.http_status >= 500 or error.http_status in (402, 429):
                await self._release(attempt_id)
            else:
                # A refusal is an outcome a retry cannot change: stored and replayed.
                await self._finish(
                    attempt_id, error.http_status, self._error_body(error.code, error.message)
                )
            raise
        except Exception:
            # A failure is not an outcome: the attempt goes, the marker stays,
            # and the retry runs the request again on the same id instead of
            # replaying the failure for good.
            await self._release(attempt_id)
            raise
        body = view.model_dump_json()
        if await self._finish(attempt_id, status, stored_body(view)):
            OUTCOMES.labels(subsystem="idempotency", outcome="recorded").inc()
        return Response(content=body, status_code=status, media_type=JSON)

    async def _finish(self, attempt_id: UUID, status: int, body: str) -> bool:
        assert self._key is not None
        try:
            await self._marker.finish(self._key, attempt_id, status, body)
        except IdempotencyAttemptLost:
            self._lost("finish")
            return False
        return True

    async def _release(self, attempt_id: UUID) -> None:
        assert self._key is not None
        try:
            await self._marker.release(self._key, attempt_id)
        except IdempotencyAttemptLost:
            self._lost("release")
            return
        OUTCOMES.labels(subsystem="idempotency", outcome="released").inc()

    def _lost(self, action: str) -> None:
        """The attempt ran past the pending lease and a retry took the marker
        over; its outcome is the retry's to record, so the refusal ends here."""
        log.warning(
            "idempotency key %r: %s refused, a retry holds the marker now", self._key, action
        )
        OUTCOMES.labels(subsystem="idempotency", outcome="attempt_lost").inc()

    def _error_body(self, code: str, message: str) -> str:
        """The error envelope as the handler would have sent it, so a retry sees
        the same refusal the first attempt saw."""
        error = ErrorBody(code=code, message=message, request_id=self._request_id)
        return ErrorResponse(error=error).model_dump_json(exclude_none=True)

    def _with_request_id(self, body: str) -> str:
        """The stored refusal with this request's id in its envelope; a body that
        is not an envelope is replayed as stored."""
        try:
            stored = ErrorResponse.model_validate_json(body)
        except ValueError:
            return body
        return self._error_body(stored.error.code, stored.error.message)


def stored_body(view: BaseModel) -> str:
    """The outcome as the marker stores it: the view with every field it
    declares a secret absent (`View.secret_fields`), so the secret exists in one
    place, as a digest, and a replay answers with the row and no secret. The
    first response carries the view whole; this is what the retry sees."""
    secrets: frozenset[str] = getattr(type(view), "secret_fields", frozenset())
    if not secrets:
        return view.model_dump_json()
    return view.model_copy(update=dict.fromkeys(secrets)).model_dump_json()


def _json(view: BaseModel, status: int) -> Response:
    return Response(content=view.model_dump_json(), status_code=status, media_type=JSON)


IdempotencyKey = Annotated[str | None, Header(min_length=1, max_length=KEY_MAX_LENGTH)]


async def idempotency(
    request: Request, ctx: Ctx, idempotency_key: IdempotencyKey = None
) -> Idempotency:
    digest = request_digest(request.method, request.url.path, await request.body())
    marker = TenantMarker(container_of(request).managers.idempotency, ctx)
    return Idempotency(marker, ctx.request_id, idempotency_key, digest)


async def operator_idempotency(
    request: Request, admin: OperatorCtx, idempotency_key: IdempotencyKey = None
) -> Idempotency:
    """The operator plane's creating routes carry a key like every other: the
    marker is the operator's, in the system scope, and the run is the same."""
    digest = request_digest(request.method, request.url.path, await request.body())
    marker = OperatorMarker(container_of(request).managers.idempotency, admin)
    return Idempotency(marker, admin.request_id, idempotency_key, digest)


Idem = Annotated[Idempotency, Depends(idempotency)]
OperatorIdem = Annotated[Idempotency, Depends(operator_idempotency)]
