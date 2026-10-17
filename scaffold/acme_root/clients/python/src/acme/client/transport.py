"""The one transport: bearer, app header, the error envelope parsed into a
typed error carrying the request id, an idempotency key on every creating
call, the one retry, and the operating system's trust store. The one module in
the package that sends a request: each namespace's calls (`calls/`) go through
`Transport.request`, and `acme.client.client.ApiClient` composes them."""

import asyncio
import random
import ssl
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal, Self

import httpx
import truststore

from acme.timing import Draw, RetryPolicy, retry_after

APP_HEADER = "X-App"
APP_VERSION_HEADER = "X-App-Version"
IDEMPOTENCY_HEADER = "Idempotency-Key"
REQUEST_ID_HEADER = "x-request-id"
LIMIT_MAX = 200
DEFAULT_TIMEOUT_SECONDS = 30.0
"""Every call out carries a timeout: the caller's settings name one per
client, and a caller that names none gets this one, so no call goes out
without one."""
DEFAULT_RETRIES = 2
"""Extra attempts a retryable failure gets. The caller's settings name this
one per client too, beside the timeout, so no caller wraps this client in a
second retry; 0 sends every call exactly once."""
RETRY = RetryPolicy(base=0.25, cap=5.0, jitter=0.5)
"""The wait between attempts: 0.25s before the first extra one, doubled per
attempt, and never longer than 5s however far the doubling runs. Half of
each wait is drawn. The caller's settings may name another base."""

RETRYABLE_STATUSES = frozenset({502, 503, 504})
"""The answers that say the API could not serve this call and may serve the
next one: its own `unavailable`, and the two a proxy sends when the origin
refused the connection or did not answer in time. Every other status is a
decision, and a decision does not change because it is asked for again."""

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
"""Methods with no effect on the API, so a second attempt costs a read."""

RETRYABLE_FAILURES = (httpx.TimeoutException, httpx.NetworkError)
"""The wire failing before the API decided: the timeout, and a connection that
was refused, reset, or lost. Every other transport failure stands as it is."""

AppName = Literal["portal", "admin", "cli", "api"]


def may_retry(method: str, idempotency_key: str | None = None) -> bool:
    """Whether this request may be sent twice. A safe method may. A creating
    call under an idempotency key may, because the API records the outcome
    under the key and replays it, so the second attempt finds the row the
    first one made instead of making another. Everything else may not: a POST
    with no key, a PATCH, and a DELETE all carry an effect that a lost answer
    leaves in doubt, and a duplicate write costs more than the failure the
    caller is told about."""
    verb = method.upper()
    if verb in SAFE_METHODS:
        return True
    return verb == "POST" and bool(idempotency_key)


def retry_delay_seconds(
    attempt: int,
    base: float = RETRY.base,
    jitter: Draw = random.random,
) -> float:
    """The wait before attempt `attempt` (1 is the first retry): the base
    doubled per attempt and capped, then halved and topped up from `jitter`.
    Half the window is fixed and half is jitter, so callers that failed
    together do not return together, and the shortest wait of one attempt is
    still the longest wait of the one before it wherever the curve doubles,
    which is what makes the growth assertable. `jitter` is a function of no arguments answering
    between 0 and 1; a test hands one in and asserts both ends of the
    window."""
    return replace(RETRY, base=base).wait(attempt, draw=jitter)


class Unset:
    """Marks an argument the caller left out, where `None` is itself a value."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = Unset()


class ApiError(Exception):
    """The API refused: the status and the stable code from the error envelope,
    and the request id to quote when asking why."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        request_id: str | None,
        retry_after: float | None = None,
        stream_head: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retry_after = retry_after
        """How long the server asked a retry to wait, its `Retry-After`, when it did."""
        self.stream_head = stream_head
        """Where the stream goes on from, when a `stream_truncated` refusal named it."""

    def __str__(self) -> str:
        suffix = f" (request {self.request_id})" if self.request_id else ""
        return f"{self.code}: {self.message}{suffix}"


def trust_store() -> ssl.SSLContext:
    """The operating system's certificates, for HTTPS and for the socket."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def retry_after_seconds(header: str | None, now: datetime | None = None) -> float | None:
    """A `Retry-After` in seconds: whole seconds, as the gateway sends it, or
    an HTTP date, read against `now`; anything unreadable is no ask at all."""
    return retry_after(header, now=now)


def retry_wait_seconds(curve: float, server_asked: float | None) -> float:
    """The wait before a retry: the curve's, or longer when the server asked
    for longer, and never past the cap."""
    return RETRY.bound(curve, server_asked)


def _error_of(response: httpx.Response) -> ApiError:
    request_id = response.headers.get(REQUEST_ID_HEADER)
    retry_after = retry_after_seconds(response.headers.get("retry-after"))
    try:
        body = response.json()
    except ValueError:
        body = None
    envelope = body.get("error") if isinstance(body, dict) else None
    if isinstance(envelope, dict) and "code" in envelope and "message" in envelope:
        stream = envelope.get("stream")
        head = stream.get("head") if isinstance(stream, dict) else None
        return ApiError(
            response.status_code,
            str(envelope["code"]),
            str(envelope["message"]),
            str(envelope.get("request_id") or request_id)
            if (envelope.get("request_id") or request_id)
            else None,
            retry_after,
            head if isinstance(head, int) else None,
        )
    return ApiError(
        response.status_code, "unknown_error", response.reason_phrase, request_id, retry_after
    )


class Transport:
    """The connection, its headers, its timeout, and its retry: what every
    namespace's calls share. `token` is the bearer every request carries; an
    operation that needs another credential (the sign-in flow) takes it as an
    argument."""

    def __init__(
        self,
        base_url: str,
        *,
        app: AppName,
        app_version: str,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = RETRY.base,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app = app
        self.app_version = app_version
        self.token = token
        self.timeout = timeout  # seconds, per request; the socket's open shares it
        # The retry arrives with the rest, so it is one client's policy and no
        # caller adds a second round of attempts on top of it.
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            timeout=timeout,
            # An injected transport is a test double; the trust store is for the network.
            verify=True if transport is not None else trust_store(),
            headers={
                "Accept": "application/json",
                APP_HEADER: app,
                APP_VERSION_HEADER: app_version,
            },
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def headers(self) -> dict[str, str]:
        """The app headers, for the socket handshake."""
        return {APP_HEADER: self.app, APP_VERSION_HEADER: self.app_version}

    def websocket_url(self, path: str) -> str:
        scheme = "wss" if self.base_url.startswith("https") else "ws"
        return f"{scheme}://{self.base_url.partition('://')[2]}{path}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        token: str | Unset | None = UNSET,
        idempotency_key: str | None = None,
        if_match: int | None = None,
        content: bytes | None = None,
        raw: bool = False,
    ) -> Any:
        """The one call every operation goes through, and the one place a
        request is sent again. Raises `ApiError` on any non-2xx; a 401 clears
        the client's token, since it will not work again.

        A request the API may see twice, and a failure that can differ on a
        second attempt, is retried up to `retries` times, spaced by a delay
        that grows and carries jitter. Anything else is raised as it is: a
        refusal is a decision the API made, and a write the API records no
        outcome for would be a second write."""
        bound = self.retries if may_retry(method, idempotency_key) else 0
        for attempt in range(bound + 1):
            server_asked: float | None = None
            try:
                return await self._attempt(
                    method,
                    path,
                    json=json,
                    params=params,
                    token=token,
                    idempotency_key=idempotency_key,
                    if_match=if_match,
                    content=content,
                    raw=raw,
                )
            except ApiError as error:
                if attempt == bound or error.status not in RETRYABLE_STATUSES:
                    raise
                server_asked = error.retry_after
            except RETRYABLE_FAILURES:
                if attempt == bound:
                    raise
            curve = retry_delay_seconds(attempt + 1, self.backoff_seconds)
            await asyncio.sleep(retry_wait_seconds(curve, server_asked))
        raise AssertionError("the loop returns or raises on its last attempt")

    async def _attempt(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        token: str | Unset | None = UNSET,
        idempotency_key: str | None = None,
        if_match: int | None = None,
        content: bytes | None = None,
        raw: bool = False,
    ) -> Any:
        """One attempt: the headers this client puts on every call, the send,
        and the answer turned into a view or a typed error. `if_match` is the
        version a write names, sent as the entity tag `If-Match` carries.
        `content` is a body of bytes rather than JSON, and `raw` answers the
        body's bytes rather than parsing it: a file's content, both ways."""
        headers: dict[str, str] = {}
        bearer = self.token if isinstance(token, Unset) else token
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        if if_match is not None:
            headers["If-Match"] = f'"{if_match}"'
        if content is not None:
            headers["Content-Type"] = "application/octet-stream"
        response = await self._http.request(
            method, path, json=json, params=params, headers=headers, content=content
        )
        # A delayed refusal belongs to the bearer sent, never a newer sign-in.
        if response.status_code == 401 and isinstance(token, Unset) and self.token == bearer:
            self.token = None
        if response.is_error:
            raise _error_of(response)
        if raw:
            return response.content
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            # Something that is not the API answered (a proxy's page, the
            # portal's index.html): said as a refusal, not a traceback.
            raise ApiError(
                response.status_code,
                "not_json",
                f"{method} {path} answered with something other than JSON",
                response.headers.get(REQUEST_ID_HEADER),
            ) from None
