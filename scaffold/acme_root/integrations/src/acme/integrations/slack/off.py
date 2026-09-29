"""The client a deployed process holds when the Slack app's credentials are
not configured: it reaches Slack for nothing, accepts nothing from Slack,
and says why. So a missing secret is a `503` and a log line, never a queue of
retries."""

from datetime import datetime
from typing import Any

from acme.integrations.slack import (
    SlackGrant,
    SlackInterface,
    SlackNotConfigured,
    SlackTokens,
)


class SlackOffImpl(SlackInterface):
    def authorize_url(self, state: str, redirect_uri: str) -> str:
        raise SlackNotConfigured()

    async def exchange_code(
        self, code: str, redirect_uri: str, *, deadline: datetime | None = None
    ) -> SlackGrant:
        raise SlackNotConfigured()

    async def refresh(self, refresh_token: str) -> SlackTokens:
        raise SlackNotConfigured()

    async def uninstall(self, token: str, *, deadline: datetime | None = None) -> None:
        raise SlackNotConfigured()

    async def revoke(self, token: str, *, deadline: datetime | None = None) -> None:
        raise SlackNotConfigured()

    def verify_request(self, body: bytes, timestamp: str | None, signature: str | None) -> None:
        raise SlackNotConfigured()

    async def post_message(
        self, token: str, channel_id: str, text: str, thread_ts: str | None = None
    ) -> str:
        raise SlackNotConfigured()

    async def user_email(self, token: str, user_id: str) -> str | None:
        raise SlackNotConfigured()

    async def publish_home(self, token: str, user_id: str, view: dict[str, Any]) -> None:
        raise SlackNotConfigured()

    async def respond(self, response_url: str, text: str) -> None:
        raise SlackNotConfigured()

    def describe(self) -> str:
        return "slack=off"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
