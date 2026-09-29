"""Slack: the one interface the platform reaches Slack through, and the errors
a caller decides on.

Acme is a distributed Slack app. Each org installs it into its own
workspace through OAuth v2, and the install hands back a bot token for that
workspace alone; every Web API call here names the token it is made under.
Slack's calls in (slash commands, events) are signed with the app's signing
secret, and `verify_request` checks one before anything reads it. The app's
client id, client secret, and signing secret are this process's; a
workspace's token is its tenant's, and the caller resolves it.

The real client speaks the Web API; the twin answers in-process, issues its
own installs and tokens, and signs its own requests with Slack's scheme. A
caller never knows which it holds.

Slack tells the app what happened in a workspace through events, and the
app subscribes to the few it acts on: a mention, the App Home opened, the
app uninstalled or its token revoked, and a member joining a channel the
bot is in, which is the bot itself when someone invites it.

The errors are the decision a caller needs, not Slack's whole vocabulary: a
rate limit says when to come back, an unusable channel says the channel is
broken until a person fixes it, a revoked token says the install is gone,
and anything else is a failure worth a retry.

A call a request makes (the install's exchange, an uninstall, a revoke)
carries the request's `deadline`, which every call it makes shares (ADR
0069), and ends by then, `SlackFailed`, as when Slack does not answer. The
rest are a worker's, bounded by the item's lease."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr

from acme.infra.exceptions import InfraException

BOT_SCOPES: tuple[str, ...] = (
    "commands",
    "chat:write",
    "app_mentions:read",
    "users:read",
    "users:read.email",
    "channels:read",
    "groups:read",
)
"""What the bot asks for at install, and nothing more: the `/acme` command,
posting in a channel it was invited to, `@acme`, the email of the person
who typed, which is how a Slack user is matched to a member, and
`member_joined_channel` in a public channel and in a private one, which is
how Acme hears that its bot was invited back to the channel it posts to.
The manifests in `deployment/slack/` name the same list; a test holds them
together."""

UNUSABLE_CHANNEL_ERRORS = frozenset({"channel_not_found", "not_in_channel", "is_archived"})
"""Slack's answers that no retry changes: the channel is gone, archived, or
the app is not in it; a private channel the app is not in answers
`channel_not_found`. A person fixes each, and the platform stops posting
until the bot joins the channel again or another channel is bound."""

REVOKED_TOKEN_ERRORS = frozenset(
    {
        "invalid_auth",
        "not_authed",
        "token_revoked",
        "account_inactive",
        "invalid_refresh_token",
        "team_not_found",
    }
)
"""Slack's answers that say the install is gone: the app was removed, the
token revoked, or the workspace deleted. Only a new install mends it."""


class SlackError(InfraException):
    """Root of what a Slack call raises: a provider is a dependency like a
    backend, so its refusals are infra's. `slack_code` is Slack's own error
    string, or the transport's reason when Slack never answered."""

    http_status = 502
    code = "slack_failed"

    def __init__(self, slack_code: str, message: str | None = None) -> None:
        super().__init__(message or slack_code)
        self.slack_code = slack_code


class SlackRateLimited(SlackError):
    """Slack asked the caller to wait (HTTP 429); `retry_after` is how long."""

    def __init__(self, retry_after: timedelta) -> None:
        super().__init__("ratelimited", f"rate limited; retry after {retry_after}")
        self.retry_after = retry_after


class SlackChannelUnusable(SlackError):
    """The channel refuses posts until a person fixes it: one of
    `UNUSABLE_CHANNEL_ERRORS`."""


class SlackTokenRevoked(SlackError):
    """The install behind the token is gone: one of `REVOKED_TOKEN_ERRORS`."""


class SlackNotConfigured(SlackError):
    """This process holds no Slack app credentials, so nothing reaches Slack
    and nothing from Slack is accepted."""

    http_status = 503
    code = "slack_unavailable"

    def __init__(self) -> None:
        super().__init__("not_configured", "the Slack app's credentials are not configured")


class SlackFailed(SlackError):
    """Any other refusal or transport failure; a retry may change it."""


class SlackRequestRefused(SlackError):
    """A call in whose signature, timestamp, or body did not check out.
    Nothing is queued for it."""

    http_status = 401
    code = "slack_signature_invalid"


class SlackTokens(BaseModel):
    """A bot token and what renews it. With token rotation on, the access
    token lives twelve hours and the refresh token works once."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_at: datetime | None = None


class SlackGrant(BaseModel):
    """What a finished install hands back (`oauth.v2.access`): the workspace,
    the bot user in it, the scopes granted, the tokens, and who installed."""

    model_config = ConfigDict(frozen=True)

    team_id: str
    team_name: str
    app_id: str
    bot_user_id: str
    scopes: tuple[str, ...]
    installer_user_id: str
    tokens: SlackTokens


class SlackInterface(ABC):
    # The app: its install, its tokens, and its calls in.

    @abstractmethod
    def authorize_url(self, state: str, redirect_uri: str) -> str:
        """Slack's page that asks a person to install the app into their
        workspace with `BOT_SCOPES`, carrying `state` back to `redirect_uri`."""
        ...

    @abstractmethod
    async def exchange_code(
        self, code: str, redirect_uri: str, *, deadline: datetime | None = None
    ) -> SlackGrant:
        """Finishes an install: the code Slack sent to the redirect, traded
        with the app's client id and secret (`oauth.v2.access`)."""
        ...

    @abstractmethod
    async def refresh(self, refresh_token: str) -> SlackTokens:
        """A fresh bot token from a refresh token, which Slack then revokes
        after a short grace period: a refresh token works once."""
        ...

    @abstractmethod
    async def uninstall(self, token: str, *, deadline: datetime | None = None) -> None:
        """Removes the app from the workspace the token belongs to
        (`apps.uninstall`); a token already revoked is already uninstalled."""
        ...

    @abstractmethod
    async def revoke(self, token: str, *, deadline: datetime | None = None) -> None:
        """Revokes one token (`auth.revoke`) and leaves the app installed: for
        a token an install handed over that the platform will not keep."""
        ...

    @abstractmethod
    def verify_request(self, body: bytes, timestamp: str | None, signature: str | None) -> None:
        """Checks a call in: `v0=` HMAC-SHA256 of `v0:<timestamp>:<body>`
        under the signing secret, compared in constant time, with a timestamp
        no more than five minutes from now. `SlackRequestRefused` otherwise;
        `SlackNotConfigured` when this process holds no signing secret."""
        ...

    # The Web API, under one install's bot token.

    @abstractmethod
    async def post_message(
        self, token: str, channel_id: str, text: str, thread_ts: str | None = None
    ) -> str:
        """Posts `text` to the channel as the app (`chat.postMessage`), in the
        thread of `thread_ts` when given; returns the message's timestamp."""
        ...

    @abstractmethod
    async def user_email(self, token: str, user_id: str) -> str | None:
        """The email a Slack user's profile holds (`users.info`), or None for
        a bot, a deleted user, or a profile without one."""
        ...

    @abstractmethod
    async def publish_home(self, token: str, user_id: str, view: dict[str, Any]) -> None:
        """Publishes the App Home view for one person (`views.publish`)."""
        ...

    @abstractmethod
    async def respond(self, response_url: str, text: str) -> None:
        """Answers a slash command through the `response_url` Slack sent with
        it, visible to the person who typed it alone. The URL must be one of
        Slack's own; any other is refused before a request is made."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


def error_for(code: str, retry_after: timedelta | None = None) -> SlackError:
    """The error a caller decides on, from Slack's own answer."""
    if retry_after is not None or code == "ratelimited":
        return SlackRateLimited(retry_after or timedelta(seconds=30))
    if code in UNUSABLE_CHANNEL_ERRORS:
        return SlackChannelUnusable(code)
    if code in REVOKED_TOKEN_ERRORS:
        return SlackTokenRevoked(code)
    return SlackFailed(code)


RESPONSE_URL_PREFIX = "https://hooks.slack.com/"
"""Where Slack's `response_url` points. A command's answer goes nowhere else."""


def is_response_url(url: str) -> bool:
    return url.startswith(RESPONSE_URL_PREFIX)
