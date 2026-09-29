"""The real client: the Slack app over the Web API and aiohttp.

It holds the app's three credentials (the client id, the client secret, the
signing secret) for the life of the process, and never a workspace's token:
each Web API call names the token it is made under, and the caller resolved
it for that one call. One HTTP session for the life of the process, opened at
start and shared by every call and every command reply, each under the
timeout from settings.

The session carries that timeout, to the fraction of a second, and it is the
one every call rides. The SDK's own timeout, in whole seconds, bounds only a
session the SDK opens itself, when it is handed none or a closed one; it is
rounded up and never zero, since aiohttp takes zero as no timeout at all.
The SDK tries a call again once when its connection drops, never when it
times out, so a Slack that hangs costs one timeout.

A call a request makes carries the request's deadline (ADR 0069), and is cut
there, the SDK's retry of a dropped connection included: `SlackFailed`, as a
call that times out is. The session's timeout is the process's, so the cut
is what bounds the attempt in flight."""

import logging
import math
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import aiohttp
from pydantic import SecretStr
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.webhook.async_client import AsyncWebhookClient

from acme.infra.base import utcnow
from acme.infra.deadline import bounded
from acme.integrations.slack import (
    BOT_SCOPES,
    SlackError,
    SlackFailed,
    SlackGrant,
    SlackInterface,
    SlackTokenRevoked,
    SlackTokens,
    error_for,
    is_response_url,
)
from acme.integrations.slack.requests import verify

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"


def tokens_of(answer: Any) -> SlackTokens:
    """The bot token of an `oauth.v2.access` answer, and, with token rotation
    on, its refresh token and when it expires."""
    expires_in = answer.get("expires_in")
    refresh = answer.get("refresh_token")
    return SlackTokens(
        access_token=SecretStr(str(answer["access_token"])),
        refresh_token=SecretStr(str(refresh)) if refresh else None,
        expires_at=None if not expires_in else utcnow() + timedelta(seconds=int(expires_in)),
    )


class SlackWebImpl(SlackInterface):
    def __init__(
        self, client_id: str, client_secret: str, signing_secret: str, timeout: timedelta
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._signing_secret = signing_secret
        self._timeout = timeout
        self._whole_seconds = max(1, math.ceil(timeout.total_seconds()))
        self._session: aiohttp.ClientSession | None = None

    def _web(self, token: str | None = None) -> AsyncWebClient:
        if self._session is None:
            raise RuntimeError("the Slack client is used before start()")
        return AsyncWebClient(token=token, session=self._session, timeout=self._whole_seconds)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "scope": ",".join(BOT_SCOPES),
                "state": state,
                "redirect_uri": redirect_uri,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    async def exchange_code(
        self, code: str, redirect_uri: str, *, deadline: datetime | None = None
    ) -> SlackGrant:
        answer = await self._call(
            None,
            "oauth.v2.access",
            deadline=deadline,
            client_id=self._client_id,
            client_secret=self._client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
        team = answer.get("team") or {}
        return SlackGrant(
            team_id=str(team.get("id", "")),
            team_name=str(team.get("name", "")),
            app_id=str(answer.get("app_id", "")),
            bot_user_id=str(answer.get("bot_user_id", "")),
            scopes=tuple(s for s in str(answer.get("scope", "")).split(",") if s),
            installer_user_id=str((answer.get("authed_user") or {}).get("id", "")),
            tokens=tokens_of(answer),
        )

    async def refresh(self, refresh_token: str) -> SlackTokens:
        answer = await self._call(
            None,
            "oauth.v2.access",
            client_id=self._client_id,
            client_secret=self._client_secret,
            grant_type="refresh_token",
            refresh_token=refresh_token,
        )
        return tokens_of(answer)

    async def uninstall(self, token: str, *, deadline: datetime | None = None) -> None:
        try:
            await self._call(
                token,
                "apps.uninstall",
                deadline=deadline,
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
        except SlackTokenRevoked:
            return

    async def revoke(self, token: str, *, deadline: datetime | None = None) -> None:
        try:
            await self._call(token, "auth.revoke", deadline=deadline)
        except SlackTokenRevoked:
            return

    def verify_request(self, body: bytes, timestamp: str | None, signature: str | None) -> None:
        verify(body, timestamp, signature, self._signing_secret)

    async def post_message(
        self, token: str, channel_id: str, text: str, thread_ts: str | None = None
    ) -> str:
        params: dict[str, Any] = {"channel": channel_id, "text": text}
        if thread_ts:
            params["thread_ts"] = thread_ts
        answer = await self._call(token, "chat.postMessage", json=True, **params)
        return str(answer["ts"])

    async def user_email(self, token: str, user_id: str) -> str | None:
        answer = await self._call(token, "users.info", user=user_id)
        user = answer.get("user") or {}
        if user.get("deleted") or user.get("is_bot"):
            return None
        email = (user.get("profile") or {}).get("email")
        return str(email) if email else None

    async def publish_home(self, token: str, user_id: str, view: dict[str, Any]) -> None:
        await self._call(token, "views.publish", json=True, user_id=user_id, view=view)

    async def respond(self, response_url: str, text: str) -> None:
        if not is_response_url(response_url):
            raise SlackFailed("invalid_response_url", "a response_url that is not Slack's")
        hook = AsyncWebhookClient(response_url, session=self._session, timeout=self._whole_seconds)
        try:
            answer = await hook.send(text=text, response_type="ephemeral")
        except Exception as error:
            raise SlackFailed(type(error).__name__, str(error)) from error
        if answer.status_code >= 400:
            raise SlackFailed(f"http_{answer.status_code}", answer.body)

    async def _call(
        self,
        token: str | None,
        method: str,
        *,
        json: bool = False,
        deadline: datetime | None = None,
        **params: Any,
    ) -> Any:
        """One Web API call, its refusals translated into the errors a caller
        decides on; no token or secret appears in what is raised or logged.
        A client used before start() is a programming error, raised as one and
        never as a failure a retry could fix. Under a request's deadline, the
        call is cut there."""
        web = self._web(token)
        try:
            async with bounded(deadline, lambda reason: SlackFailed("deadline", reason)):
                if json:
                    return await web.api_call(method, json=params)
                return await web.api_call(method, data=params)
        except SlackApiError as error:
            response = error.response
            code = str(response.get("error") or f"http_{response.status_code}")
            retry_after: timedelta | None = None
            if response.status_code == 429:
                header = response.headers.get("Retry-After") or response.headers.get("retry-after")
                retry_after = timedelta(seconds=int(header or 30))
            raise error_for(code, retry_after) from None
        except SlackError:
            raise
        except Exception as error:
            raise SlackFailed(type(error).__name__, str(error)) from error

    def describe(self) -> str:
        return "slack=web"

    async def start(self) -> None:
        seconds = self._timeout.total_seconds()
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=seconds))

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
        self._session = None
