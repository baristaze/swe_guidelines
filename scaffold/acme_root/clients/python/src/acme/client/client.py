"""One transport client: bearer, app header, the error envelope parsed into a
typed error carrying the request id, an idempotency key on every creating
call, the one retry, and the operating system's trust store. The one module in
the package that sends a request; every operation is a method that returns a
typed view."""

import asyncio
import random
import ssl
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import httpx
import truststore

from acme.client.types import (
    BulkAction,
    BulkTasksView,
    DeviceSignInView,
    EventView,
    FilePageView,
    FileView,
    ImportPageView,
    ImportView,
    InvitationPageView,
    InvitationView,
    IssuedDownloadView,
    IssuedLoginView,
    IssuedSessionView,
    IssuedTicketView,
    IssuedUploadView,
    MembershipChoicePageView,
    MembershipChoiceView,
    MeView,
    OperatorEventView,
    OperatorTokenPageView,
    OperatorTokenView,
    OperatorView,
    OperatorWorkItemView,
    OrgView,
    PlatformSizeView,
    Role,
    SignedOutView,
    SignInStartView,
    SsoLinkView,
    StorageUsageView,
    TaskCountView,
    TaskPageView,
    TaskScope,
    TaskStatus,
    TaskView,
    UserPageView,
    UserView,
)

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
DEFAULT_BACKOFF_SECONDS = 0.25
"""The wait before the first extra attempt. It doubles per attempt."""
MAX_BACKOFF_SECONDS = 5.0
"""However far the doubling runs, no wait between attempts is longer."""

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
    base: float = DEFAULT_BACKOFF_SECONDS,
    jitter: Callable[[], float] = random.random,
) -> float:
    """The wait before attempt `attempt` (1 is the first retry): the base
    doubled per attempt and capped, then halved and topped up from `jitter`.
    Half the window is fixed and half is jitter, so callers that failed
    together do not return together, and the shortest wait of one attempt is
    still the longest wait of the one before it wherever the curve doubles,
    which is what makes the growth assertable. `jitter` is a function of no arguments answering
    between 0 and 1; a test hands one in and asserts both ends of the
    window."""
    full = min(base * 2 ** max(0, attempt - 1), MAX_BACKOFF_SECONDS)
    return full / 2 + (full / 2) * jitter()


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


def retry_after_seconds(header: str | None) -> float | None:
    """A `Retry-After` in seconds, as the gateway sends it; an HTTP date or
    anything unreadable is no ask at all."""
    if header is None or not header.strip().isdigit():
        return None
    return float(header.strip())


def retry_wait_seconds(curve: float, server_asked: float | None) -> float:
    """The wait before a retry: the curve's, or longer when the server asked
    for longer, and never past the cap."""
    return min(max(curve, server_asked or 0.0), MAX_BACKOFF_SECONDS)


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


class ApiClient:
    """Async. `token` is the bearer every request carries; an operation that
    needs another credential (the sign-in flow) takes it as an argument."""

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
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
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

    async def __aenter__(self) -> ApiClient:
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

    # Tenancy

    async def start_sign_in(
        self,
        redirect_uri: str,
        state: str,
        *,
        invitation_token: str | None = None,
        sign_up: bool = False,
    ) -> SignInStartView:
        """Where a browser goes to sign in at the identity provider; it comes
        back to `redirect_uri` with a code and `state`."""
        body: dict[str, object] = {"redirect_uri": redirect_uri, "state": state, "sign_up": sign_up}
        if invitation_token is not None:
            body["invitation_token"] = invitation_token
        answer = await self.request("POST", "/v1/auth/sign-in", json=body, token=None)
        return SignInStartView.model_validate(answer)

    async def finish_sign_in(
        self, code: str, code_verifier: str, invitation_token: str | None = None
    ) -> IssuedLoginView:
        """The code the browser brought back and the verifier `start_sign_in`
        answered with, exchanged by the API; answered as every sign-in is. A
        person nobody knew is signed up by it."""
        body: dict[str, object] = {"code": code, "code_verifier": code_verifier}
        if invitation_token is not None:
            body["invitation_token"] = invitation_token
        answer = await self.request("POST", "/v1/auth/callback", json=body, token=None)
        return IssuedLoginView.model_validate(answer)

    async def start_device_sign_in(self) -> DeviceSignInView:
        """A sign-in for a program with no browser of its own: the person
        confirms the code at the address the answer names, in any browser."""
        answer = await self.request("POST", "/v1/auth/device", token=None)
        return DeviceSignInView.model_validate(answer)

    async def finish_device_sign_in(self, device_code: str) -> IssuedLoginView:
        """Asks once whether the person confirmed the device sign-in. Before
        they do, an `ApiError` with status 400 and code `sign_in_pending` (or
        `sign_in_slow_down`); the caller asks again after the interval."""
        answer = await self.request(
            "POST", "/v1/auth/device/token", json={"device_code": device_code}, token=None
        )
        return IssuedLoginView.model_validate(answer)

    async def dev_sign_in(self, email: str, display_name: str = "") -> IssuedLoginView:
        """Local and test only: a sign-in by address alone, which a deployed
        environment answers with 404. A person nobody knew is made, with
        their personal org."""
        answer = await self.request(
            "POST",
            "/v1/auth/dev-sign-in",
            json={"email": email, "display_name": display_name},
            token=None,
        )
        return IssuedLoginView.model_validate(answer)

    async def verify_second_factor(self, login_token: str, totp_code: str) -> IssuedLoginView:
        """A sign-in's second factor: a new sign-in that records the verified
        code, which the operator plane asks of an enrolled operator."""
        answer = await self.request(
            "POST", "/v1/auth/second-factor", json={"totp_code": totp_code}, token=login_token
        )
        return IssuedLoginView.model_validate(answer)

    async def exchange_session(self, login_token: str, org_id: UUID) -> IssuedSessionView:
        body = await self.request(
            "POST", "/v1/auth/sessions", json={"org_id": str(org_id)}, token=login_token
        )
        return IssuedSessionView.model_validate(body)

    async def create_org(
        self, name: str, slug: str | None = None, *, idempotency_key: str | None = None
    ) -> MembershipChoiceView:
        """A team org the signed-in person makes and owns; the answer is their
        place in it, which `switch` takes. A creating call, so it always
        carries an idempotency key."""
        body: dict[str, object] = {"name": name}
        if slug is not None:
            body["slug"] = slug
        answer = await self.request(
            "POST", "/v1/orgs", json=body, idempotency_key=idempotency_key or str(uuid4())
        )
        return MembershipChoiceView.model_validate(answer)

    async def memberships(
        self, *, cursor: str | None = None, limit: int = LIMIT_MAX
    ) -> MembershipChoicePageView:
        """The signed-in person's places, with the session the client holds."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self.request("GET", "/v1/auth/memberships", params=params)
        return MembershipChoicePageView.model_validate(body)

    async def every_membership(self, limit: int = LIMIT_MAX) -> list[MembershipChoiceView]:
        """Every place, page after page, as `every_user` reads the members."""
        found: list[MembershipChoiceView] = []
        cursor: str | None = None
        while True:
            page = await self.memberships(cursor=cursor, limit=limit)
            found += page.items
            if page.next_cursor is None:
                return found
            cursor = page.next_cursor

    async def switch_session(self, org_id: UUID) -> IssuedSessionView:
        """The exchange presented with the session the client holds: the API
        ends that session in the same write, and the client carries the new
        one from here on, so it never holds two."""
        issued = await self.exchange_session(self._require_token(), org_id)
        self.token = issued.token
        return issued

    def _require_token(self) -> str:
        if not self.token:
            raise ApiError(401, "not_authenticated", "no session to switch from", None)
        return self.token

    async def invitations(
        self, *, cursor: str | None = None, limit: int = LIMIT_MAX
    ) -> InvitationPageView:
        params: dict[str, object] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        answer = await self.request("GET", "/v1/invitations", params=params)
        return InvitationPageView.model_validate(answer)

    async def invite_member(
        self, email: str, role: Role = Role.member, *, idempotency_key: str | None = None
    ) -> InvitationView:
        """The identity provider sends the person the email with the link; a
        creating call, so it always carries an idempotency key."""
        answer = await self.request(
            "POST",
            "/v1/invitations",
            json={"email": email, "role": role.value},
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return InvitationView.model_validate(answer)

    async def resend_invitation(self, invitation_id: UUID) -> InvitationView:
        answer = await self.request("POST", f"/v1/invitations/{invitation_id}/resend")
        return InvitationView.model_validate(answer)

    async def revoke_invitation(self, invitation_id: UUID) -> InvitationView:
        answer = await self.request("DELETE", f"/v1/invitations/{invitation_id}")
        return InvitationView.model_validate(answer)

    async def sso_link(
        self,
        return_url: str,
        intent: Literal["sso", "domain_verification"] = "sso",
    ) -> SsoLinkView:
        answer = await self.request(
            "POST",
            "/v1/orgs/current/sso-link",
            json={"intent": intent, "return_url": return_url},
        )
        return SsoLinkView.model_validate(answer)

    async def logout(self) -> SignedOutView:
        """Ends the credential this client holds: a session, a sign-in, or an
        operator token. The answer's `provider_logout_url` is a browser's
        next stop; a sign-in without a browser (the device sign-in, the local
        one) has none, and neither has an operator token."""
        return SignedOutView.model_validate(await self.request("POST", "/v1/auth/logout"))

    async def me(self) -> MeView:
        return MeView.model_validate(await self.request("GET", "/v1/me"))

    async def users(self, *, cursor: str | None = None, limit: int = LIMIT_MAX) -> UserPageView:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self.request("GET", "/v1/users", params=params)
        return UserPageView.model_validate(body)

    async def every_user(self, limit: int = LIMIT_MAX) -> list[UserView]:
        """Every member, page after page until the API says there is no next
        one: a caller that names people needs the whole list, and a fixed
        limit would silently leave the rest unnamed."""
        users: list[UserView] = []
        cursor: str | None = None
        while True:
            page = await self.users(cursor=cursor, limit=limit)
            users += page.items
            cursor = page.next_cursor
            if cursor is None:
                return users

    # Tasks

    async def tasks(
        self,
        status: TaskStatus = TaskStatus.open,
        scope: TaskScope = TaskScope.team,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> TaskPageView:
        params: dict[str, Any] = {"status": status.value, "scope": scope.value, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return TaskPageView.model_validate(await self.request("GET", "/v1/tasks", params=params))

    async def count_tasks(
        self, status: TaskStatus = TaskStatus.open, scope: TaskScope = TaskScope.team
    ) -> TaskCountView:
        """How many tasks one list shows, the done one without the archive."""
        params = {"status": status.value, "scope": scope.value}
        return TaskCountView.model_validate(
            await self.request("GET", "/v1/tasks/count", params=params)
        )

    async def change_tasks(
        self,
        action: BulkAction,
        *,
        ids: Sequence[UUID] | None = None,
        scope: TaskScope | None = None,
        status: TaskStatus | None = None,
        idempotency_key: str | None = None,
    ) -> BulkTasksView:
        """Completes or reopens many tasks in one call: the ones named in `ids`,
        or with `scope` and `status` every task of that list, as the server
        reads it. Always under an idempotency key, so a retry answers what the
        first call did. The answer names what changed, which the other action
        over those ids undoes."""
        body: dict[str, Any] = {"action": action.value}
        if ids is not None:
            body["ids"] = [str(task_id) for task_id in ids]
        if scope is not None or status is not None:
            body["all"] = {
                "scope": (scope or TaskScope.team).value,
                "status": (status or TaskStatus.open).value,
            }
        changed = await self.request(
            "POST", "/v1/tasks/bulk", json=body, idempotency_key=idempotency_key or str(uuid4())
        )
        return BulkTasksView.model_validate(changed)

    async def task(self, task_id: UUID) -> TaskView:
        return TaskView.model_validate(await self.request("GET", f"/v1/tasks/{task_id}"))

    async def create_task(
        self,
        title: str,
        *,
        notes: str = "",
        assignee_id: UUID | None = None,
        due_on: date | None = None,
        idempotency_key: str | None = None,
    ) -> TaskView:
        """A creating call always carries an idempotency key; a retry with the
        same key returns the task the first call created. `due_on` is a date,
        never a time: the reminder goes out at nine in the morning of it, in
        the time zone of the person the task is for."""
        body: dict[str, Any] = {"title": title, "notes": notes}
        if assignee_id is not None:
            body["assignee_id"] = str(assignee_id)
        if due_on is not None:
            body["due_on"] = due_on.isoformat()
        created = await self.request(
            "POST", "/v1/tasks", json=body, idempotency_key=idempotency_key or str(uuid4())
        )
        return TaskView.model_validate(created)

    async def update_task(
        self,
        task_id: UUID,
        *,
        version: int,
        title: str | None = None,
        notes: str | None = None,
        status: TaskStatus | None = None,
        assignee_id: UUID | Unset | None = UNSET,
        due_on: date | Unset | None = UNSET,
    ) -> TaskView:
        """A partial update: only what the caller passes is sent. `assignee_id=None`
        unassigns; leaving it out keeps the assignee. `due_on` is the same:
        None clears the due date, and leaving it out keeps it. `version` is the task's as
        the caller read it, sent in `If-Match`; the API refuses the update with
        412 `precondition_failed` when the task changed since, and the caller
        reads again."""
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if notes is not None:
            body["notes"] = notes
        if status is not None:
            body["status"] = status.value
        if not isinstance(assignee_id, Unset):
            body["assignee_id"] = None if assignee_id is None else str(assignee_id)
        if not isinstance(due_on, Unset):
            body["due_on"] = None if due_on is None else due_on.isoformat()
        return TaskView.model_validate(
            await self.request("PATCH", f"/v1/tasks/{task_id}", json=body, if_match=version)
        )

    async def move_task(self, task_id: UUID, after_id: UUID | None, version: int) -> TaskView:
        """`version` is the moved task's, as on `update_task`; a move is a POST,
        so it rides the body as `expected_version`."""
        body = {
            "after_id": None if after_id is None else str(after_id),
            "expected_version": version,
        }
        return TaskView.model_validate(
            await self.request("POST", f"/v1/tasks/{task_id}/move", json=body)
        )

    async def delete_task(self, task_id: UUID, version: int) -> TaskView:
        """`version` is the task's, as on `update_task`, in `If-Match`."""
        return TaskView.model_validate(
            await self.request("DELETE", f"/v1/tasks/{task_id}", if_match=version)
        )

    # A task's attachments, and the files behind them

    async def attachments(
        self, task_id: UUID, *, cursor: str | None = None, limit: int = LIMIT_MAX
    ) -> FilePageView:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        page = await self.request("GET", f"/v1/tasks/{task_id}/attachments", params=params)
        return FilePageView.model_validate(page)

    async def every_attachment(self, task_id: UUID, limit: int = LIMIT_MAX) -> list[FileView]:
        """A task's stored attachments, oldest first, following the cursor to the end."""
        files: list[FileView] = []
        cursor: str | None = None
        while True:
            page = await self.attachments(task_id, cursor=cursor, limit=limit)
            files.extend(page.items)
            if page.next_cursor is None:
                return files
            cursor = page.next_cursor

    async def start_attachment(
        self,
        task_id: UUID,
        name: str,
        content_type: str,
        size_bytes: int,
        *,
        idempotency_key: str | None = None,
    ) -> FileView:
        """A pending attachment: the upload may begin. Always under an
        idempotency key, so a retry lands one file."""
        body = {"name": name, "content_type": content_type, "size_bytes": size_bytes}
        started = await self.request(
            "POST",
            f"/v1/tasks/{task_id}/attachments",
            json=body,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return FileView.model_validate(started)

    async def issue_upload(self, file_id: UUID) -> IssuedUploadView:
        form = await self.request("POST", f"/v1/media/files/{file_id}/upload")
        return IssuedUploadView.model_validate(form)

    async def put_content(self, file_id: UUID, data: bytes) -> FileView:
        """The bytes through the API, for a store that cannot take a form post."""
        put = await self.request("PUT", f"/v1/media/files/{file_id}/content", content=data)
        return FileView.model_validate(put)

    async def confirm_file(self, file_id: UUID) -> FileView:
        return FileView.model_validate(
            await self.request("POST", f"/v1/media/files/{file_id}/confirm")
        )

    async def issue_download(self, file_id: UUID) -> IssuedDownloadView:
        link = await self.request("GET", f"/v1/media/files/{file_id}/download")
        return IssuedDownloadView.model_validate(link)

    async def content(self, file_id: UUID) -> bytes:
        return cast(
            bytes, await self.request("GET", f"/v1/media/files/{file_id}/content", raw=True)
        )

    async def remove_attachment(self, task_id: UUID, file_id: UUID) -> FileView:
        removed = await self.request("DELETE", f"/v1/tasks/{task_id}/attachments/{file_id}")
        return FileView.model_validate(removed)

    async def storage_usage(self) -> StorageUsageView:
        return StorageUsageView.model_validate(await self.request("GET", "/v1/media/usage"))

    async def attach(self, task_id: UUID, name: str, content_type: str, data: bytes) -> FileView:
        """The whole upload of a task's attachment: start it, then `upload`."""
        started = await self.start_attachment(task_id, name, content_type, len(data))
        return await self.upload(started, data)

    async def upload(self, started: FileView, data: bytes) -> FileView:
        """The bytes of a started upload, then its confirm. They go straight to
        the store with the form the API signed, or through the API when the
        store cannot take a post. The post to the store carries no credential
        of ours: the form is the credential, and it holds the post to this
        file's type and size."""
        form = await self.issue_upload(started.id)
        if form.url is None:
            await self.put_content(started.id, data)
        else:
            async with httpx.AsyncClient(timeout=self.timeout, verify=trust_store()) as store:
                posted = await store.post(
                    form.url,
                    data={field.name: field.value for field in form.fields},
                    files={"file": (started.name, data, started.content_type)},
                )
            if posted.is_error:
                raise ApiError(posted.status_code, "upload_refused", posted.text[:200], None)
        return await self.confirm_file(started.id)

    # Imports

    async def start_import_file(
        self, name: str, size_bytes: int, *, idempotency_key: str | None = None
    ) -> FileView:
        """A pending CSV file to import: the upload may begin. Always under an
        idempotency key, so a retry lands one file."""
        body = {"name": name, "content_type": "text/csv", "size_bytes": size_bytes}
        started = await self.request(
            "POST",
            "/v1/tasks/imports/files",
            json=body,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return FileView.model_validate(started)

    async def start_import(
        self, file_id: UUID, *, idempotency_key: str | None = None
    ) -> ImportView:
        """Starts the import of a stored file; the worker reads it."""
        started = await self.request(
            "POST",
            "/v1/tasks/imports",
            json={"file_id": str(file_id)},
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return ImportView.model_validate(started)

    async def import_tasks(self, name: str, data: bytes) -> ImportView:
        """The whole start: the file's upload, then its import."""
        started = await self.start_import_file(name, len(data))
        stored = await self.upload(started, data)
        return await self.start_import(stored.id)

    async def task_import(self, import_id: UUID) -> ImportView:
        return ImportView.model_validate(
            await self.request("GET", f"/v1/tasks/imports/{import_id}")
        )

    async def task_imports(self, limit: int = 10) -> ImportPageView:
        page = await self.request("GET", "/v1/tasks/imports", params={"limit": limit})
        return ImportPageView.model_validate(page)

    async def resume_import(self, import_id: UUID) -> ImportView:
        resumed = await self.request("POST", f"/v1/tasks/imports/{import_id}/resume")
        return ImportView.model_validate(resumed)

    async def download(self, file_id: UUID) -> bytes:
        """A stored file's bytes: by the signed link, or through the API when
        the store cannot sign one."""
        link = await self.issue_download(file_id)
        if link.url is None:
            return await self.content(file_id)
        async with httpx.AsyncClient(timeout=self.timeout, verify=trust_store()) as store:
            fetched = await store.get(link.url)
        if fetched.is_error:
            raise ApiError(fetched.status_code, "download_refused", fetched.text[:200], None)
        return fetched.content

    # Events and the channel

    async def events_after(self, after_seq: int, limit: int = LIMIT_MAX) -> list[EventView]:
        body = await self.request(
            "GET", "/v1/events", params={"after_seq": after_seq, "limit": limit}
        )
        return [EventView.model_validate(e) for e in cast(list[Any], body)]

    async def ticket(self) -> IssuedTicketView:
        return IssuedTicketView.model_validate(await self.request("POST", "/v1/realtime/tickets"))

    # The operator plane. The bearer is an operator token, never a tenant
    # session; a caller sets `token` to it. The operator's own sign-in, with
    # its second factor, mints one token and does nothing else.

    async def admin_me(self) -> OperatorView:
        """Who the operator plane admitted and what the entry grants; the check a
        skill makes before its first read."""
        return OperatorView.model_validate(await self.request("GET", "/v1/admin/me"))

    async def admin_tokens(
        self, *, cursor: str | None = None, limit: int = LIMIT_MAX
    ) -> OperatorTokenPageView:
        """The operator's own live tokens, newest first: the ids a revoke names."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self.request("GET", "/v1/admin/me/tokens", params=params)
        return OperatorTokenPageView.model_validate(body)

    async def admin_revoke_token(self, token_id: UUID) -> OperatorTokenView:
        """Ends one of the operator's own tokens at once; one ended already is
        answered as it is."""
        body = await self.request("DELETE", f"/v1/admin/me/tokens/{token_id}")
        return OperatorTokenView.model_validate(body)

    async def admin_size(self) -> PlatformSizeView:
        return PlatformSizeView.model_validate(await self.request("GET", "/v1/admin/size"))

    async def admin_create_org(
        self,
        name: str,
        slug: str,
        *,
        owner_email: str,
        owner_name: str,
        idempotency_key: str | None = None,
    ) -> OrgView:
        """An org with its owner, as `bootstrap` seeds one; a creating call, so
        it always carries an idempotency key."""
        body = {
            "name": name,
            "slug": slug,
            "owner_email": owner_email,
            "owner_name": owner_name,
        }
        created = await self.request(
            "POST", "/v1/admin/orgs", json=body, idempotency_key=idempotency_key or str(uuid4())
        )
        return OrgView.model_validate(created)

    async def admin_add_member(
        self,
        org_id: UUID,
        email: str,
        *,
        display_name: str,
        role: Role = Role.member,
        idempotency_key: str | None = None,
    ) -> UserView:
        """A person in the org, as `add-member` seeds one; a creating call, so
        it always carries an idempotency key."""
        body = {
            "email": email,
            "display_name": display_name,
            "role": role.value,
        }
        added = await self.request(
            "POST",
            f"/v1/admin/orgs/{org_id}/members",
            json=body,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return UserView.model_validate(added)

    async def admin_org(self, org_id: UUID) -> OrgView:
        return OrgView.model_validate(await self.request("GET", f"/v1/admin/orgs/{org_id}"))

    async def admin_members(
        self, org_id: UUID, *, cursor: str | None = None, limit: int = LIMIT_MAX
    ) -> UserPageView:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self.request("GET", f"/v1/admin/orgs/{org_id}/members", params=params)
        return UserPageView.model_validate(body)

    async def admin_tasks(
        self,
        org_id: UUID,
        status: TaskStatus = TaskStatus.open,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> TaskPageView:
        params: dict[str, Any] = {"status": status.value, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self.request("GET", f"/v1/admin/orgs/{org_id}/tasks", params=params)
        return TaskPageView.model_validate(body)

    async def admin_events(
        self, org_id: UUID, after_seq: int = 0, limit: int = LIMIT_MAX
    ) -> list[OperatorEventView]:
        body = await self.request(
            "GET",
            f"/v1/admin/orgs/{org_id}/events",
            params={"after_seq": after_seq, "limit": limit},
        )
        return [OperatorEventView.model_validate(e) for e in cast(list[Any], body)]

    async def admin_requeue_work(self, org_id: UUID, item_id: UUID) -> OperatorWorkItemView:
        """One failed work item back to the queue, with its attempts reset; a
        write, refused with `work_not_failed` for an item that is not failed."""
        body = await self.request("POST", f"/v1/admin/orgs/{org_id}/work/{item_id}/requeue")
        return OperatorWorkItemView.model_validate(body)
