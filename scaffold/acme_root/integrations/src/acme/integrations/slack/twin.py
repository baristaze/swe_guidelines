"""The deterministic twin: Slack's side of an app, in-process.

It keeps workspaces, their people, and the channels its bot was removed
from; issues install codes and tokens the way OAuth v2 with token rotation
does (an access token that expires, a refresh token that works once);
records every post, reply, and App Home; hands back the event Slack sends
when the bot is invited to a channel; signs requests with Slack's scheme
under a signing secret of its own; and fails on request, so a test can
drive the rate limit, the unusable channel, and the revoked install the
real service would. It refuses to run outside a
local environment, and every token and message it makes says it came from
the twin."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from pydantic import SecretStr

from acme.infra.base import utcnow
from acme.integrations.slack import (
    BOT_SCOPES,
    SlackChannelUnusable,
    SlackError,
    SlackFailed,
    SlackGrant,
    SlackInterface,
    SlackTokenRevoked,
    SlackTokens,
    is_response_url,
)
from acme.integrations.slack.requests import sign, verify

log = logging.getLogger(__name__)

TWIN_ENVIRONMENTS = frozenset({"local", "test"})

TWIN_SIGNING_SECRET = "twin-signing-secret-not-a-secret"
"""What the twin signs with and checks against. It is in the source because
the twin runs on a laptop and in tests only."""

TOKEN_LIFETIME = timedelta(hours=12)
"""What Slack gives a rotated access token."""

ACCESS_PREFIX = "xoxe.xoxb-twin-"
REFRESH_PREFIX = "xoxe-1-twin-"

TWIN_TEAM = "TTWIN0001"
TWIN_TEAM_NAME = "Twin Workspace"
TWIN_INSTALLER = "UTWIN0001"
"""The workspace, and the person in it, the twin's own install page approves
for: what "Add to Slack" installs into on a laptop."""

TWIN_APP = "ATWIN"


def bot_user_of(team_id: str) -> str:
    """The bot user an install of the twin's app has in a workspace."""
    return f"UBOT{team_id}"


@dataclass(frozen=True)
class TwinPost:
    team_id: str
    channel_id: str
    text: str
    ts: str
    thread_ts: str | None = None


@dataclass
class SlackTwinImpl(SlackInterface):
    environment: str
    posts: list[TwinPost] = field(default_factory=list)
    responses: list[tuple[str, str]] = field(default_factory=list)
    homes: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    uninstalled: list[str] = field(default_factory=list)
    failures: list[tuple[SlackError, str | None]] = field(default_factory=list)
    """Errors the next calls raise, first in first out, each for any call or
    for one method alone: a test's way of making Slack refuse."""
    _codes: dict[str, SlackGrant] = field(default_factory=dict)
    _emails: dict[tuple[str, str], str] = field(default_factory=dict)
    _revoked: set[str] = field(default_factory=set)  # teams, and tokens refreshed away
    _removed: set[tuple[str, str]] = field(default_factory=set)  # (team, channel) the bot left
    _minted: int = 0

    def __post_init__(self) -> None:
        if self.environment not in TWIN_ENVIRONMENTS:
            raise ValueError(f"the Slack twin runs locally only, not in {self.environment!r}")

    # What a test arranges.

    def fail_next(self, error: SlackError, method: str | None = None) -> None:
        """The next call raises `error`; with `method` (`chat.postMessage`,
        `users.info`, ...), the next call of that method alone."""
        self.failures.append((error, method))

    def add_user(self, team_id: str, user_id: str, email: str) -> None:
        """A person in a workspace, with the email their profile holds."""
        self._emails[(team_id, user_id)] = email

    def approve(
        self,
        team_id: str,
        installer_user_id: str,
        team_name: str = TWIN_TEAM_NAME,
        scopes: tuple[str, ...] = BOT_SCOPES,
    ) -> str:
        """What a person clicking Allow on Slack's page does: a code the
        redirect carries, good for one exchange."""
        self._minted += 1
        code = f"twin-code-{self._minted}"
        self._codes[code] = SlackGrant(
            team_id=team_id,
            team_name=team_name,
            app_id=TWIN_APP,
            bot_user_id=bot_user_of(team_id),
            scopes=scopes,
            installer_user_id=installer_user_id,
            tokens=self._mint(team_id),
        )
        return code

    def remove_bot(self, team_id: str, channel_id: str) -> None:
        """Someone removed the bot from a channel: a post there answers
        `not_in_channel` until the bot is invited again."""
        self._removed.add((team_id, channel_id))

    def invite_bot(
        self, team_id: str, channel_id: str, inviter: str = TWIN_INSTALLER
    ) -> dict[str, Any]:
        """`/invite @acme` in a channel: posts there work again, and the
        answer is the Events API body Slack then sends the app, a
        `member_joined_channel` whose member is the bot."""
        self._removed.discard((team_id, channel_id))
        self._minted += 1
        return {
            "type": "event_callback",
            "team_id": team_id,
            "api_app_id": TWIN_APP,
            "event_id": f"EvTWIN{self._minted:06d}",
            "event_time": int(utcnow().timestamp()),
            "event": {
                "type": "member_joined_channel",
                "user": bot_user_of(team_id),
                "channel": channel_id,
                "channel_type": "C",
                "team": team_id,
                "inviter": inviter,
            },
        }

    def revoke_team(self, team_id: str) -> None:
        """The workspace removed the app: every token it held stops working."""
        self._revoked.add(team_id)

    def team_of(self, token: str) -> str | None:
        """The workspace a live token of the twin's belongs to. A token names
        its workspace, so a token one process's twin issued works in
        another's: the local API installs, the local worker posts."""
        for prefix in (ACCESS_PREFIX, REFRESH_PREFIX):
            if token.startswith(prefix):
                team = token.removeprefix(prefix).rpartition("-")[0]
                if team and team not in self._revoked and token not in self._revoked:
                    return team
        return None

    def signed(self, body: bytes, timestamp: int | None = None) -> dict[str, str]:
        """The two headers Slack would send with `body`."""
        at, signature = sign(body, TWIN_SIGNING_SECRET, timestamp)
        return {"X-Slack-Request-Timestamp": at, "X-Slack-Signature": signature}

    # The app.

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        """Slack's page would ask a person to approve; the twin approves at
        once, for its own workspace, and sends the browser straight back to
        the redirect with the code, as Slack does after Allow. So "Add to
        Slack" completes on a laptop with no Slack at all."""
        code = self.approve(TWIN_TEAM, TWIN_INSTALLER, TWIN_TEAM_NAME)
        return f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"

    async def exchange_code(
        self, code: str, redirect_uri: str, *, deadline: datetime | None = None
    ) -> SlackGrant:
        self._maybe_fail("oauth.v2.access")
        grant = self._codes.pop(code, None)
        if grant is None:
            raise SlackFailed("invalid_code")
        return grant

    async def refresh(self, refresh_token: str) -> SlackTokens:
        self._maybe_fail("oauth.v2.access")
        team = self.team_of(refresh_token) if refresh_token.startswith(REFRESH_PREFIX) else None
        if team is None:
            raise SlackTokenRevoked("invalid_refresh_token")
        self._revoked.add(refresh_token)  # a refresh token works once
        return self._mint(team)

    async def uninstall(self, token: str, *, deadline: datetime | None = None) -> None:
        self._maybe_fail("apps.uninstall")
        team = self.team_of(token)
        if team is None:
            return
        self.uninstalled.append(team)
        self.revoke_team(team)

    async def revoke(self, token: str, *, deadline: datetime | None = None) -> None:
        self._maybe_fail("auth.revoke")
        self._revoked.add(token)

    def verify_request(self, body: bytes, timestamp: str | None, signature: str | None) -> None:
        verify(body, timestamp, signature, TWIN_SIGNING_SECRET)

    # The Web API.

    async def post_message(
        self, token: str, channel_id: str, text: str, thread_ts: str | None = None
    ) -> str:
        team = self._authorized(token, "chat.postMessage")
        if (team, channel_id) in self._removed:
            raise SlackChannelUnusable("not_in_channel")
        ts = f"twin.{len(self.posts) + 1:06d}"
        self.posts.append(TwinPost(team, channel_id, text, ts, thread_ts))
        log.info("slack twin posted %s to %s: %s", ts, channel_id, text)
        return ts

    async def user_email(self, token: str, user_id: str) -> str | None:
        team = self._authorized(token, "users.info")
        return self._emails.get((team, user_id))

    async def publish_home(self, token: str, user_id: str, view: dict[str, Any]) -> None:
        self._authorized(token, "views.publish")
        self.homes.append((user_id, view))

    async def respond(self, response_url: str, text: str) -> None:
        if not is_response_url(response_url):
            raise SlackFailed("invalid_response_url", "a response_url that is not Slack's")
        self._maybe_fail("response_url")
        self.responses.append((response_url, text))
        log.info("slack twin answered a command: %s", text)

    def _authorized(self, token: str, method: str) -> str:
        self._maybe_fail(method)
        team = self.team_of(token) if token.startswith(ACCESS_PREFIX) else None
        if team is None:
            raise SlackTokenRevoked("invalid_auth")
        return team

    def _mint(self, team_id: str) -> SlackTokens:
        self._minted += 1
        access = f"{ACCESS_PREFIX}{team_id}-{self._minted}"
        refresh = f"{REFRESH_PREFIX}{team_id}-{self._minted}"
        return SlackTokens(
            access_token=SecretStr(access),
            refresh_token=SecretStr(refresh),
            expires_at=utcnow() + TOKEN_LIFETIME,
        )

    def _maybe_fail(self, method: str) -> None:
        for n, (error, only) in enumerate(self.failures):
            if only is None or only == method:
                del self.failures[n]
                raise error

    def describe(self) -> str:
        return "slack=twin"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
