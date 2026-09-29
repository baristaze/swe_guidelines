"""The Slack app an org installed, the one-time state an install carries
through Slack and back, and the record of what the platform posted."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from acme.om.base import Created, Identifiable, Platform, SoftDeletable, Trackable


class SlackInstallationStatus(StrEnum):
    OK = "ok"
    # Slack refused for good: the bound channel is gone, archived, or the app
    # is not in it; or the install's token no longer renews.
    BROKEN = "broken"


class SlackInstallation(Identifiable, Trackable, SoftDeletable):
    """The Acme app installed into one Slack workspace, for one org. An org
    has at most one living installation and a workspace belongs to at most
    one org. `created_by` is the member who installed it.

    The bot token is the tenant's secret, never a field: `credential_ref`
    names it in the secret store, under the org's own prefix, and the manager
    alone sets it. The token lives twelve hours and renews through a refresh
    token that works once, so `token_expires_at` says when it must be renewed
    and `refreshing_until` is the claim one renewal holds while it runs.

    `channel_id` is where reminders and task updates are posted: the channel
    someone typed `/acme connect` in. None until then."""

    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "credential_ref",
        "token_expires_at",
        "refreshing_until",
        "status",
        "broken_reason",
    )

    team_id: str
    team_name: str
    app_id: str
    bot_user_id: str
    scopes: str  # comma-separated, as Slack granted them
    installed_by_slack_user: str
    credential_ref: str
    token_expires_at: datetime | None = None
    refreshing_until: datetime | None = None
    channel_id: str | None = None
    status: SlackInstallationStatus = SlackInstallationStatus.OK
    broken_reason: str | None = None


class SlackInstallState(Identifiable, Created):
    """The `state` an install carries to Slack and back: it binds the install
    to the org and the member who started it. Only its digest is kept; it
    expires in minutes and redeeming it is one conditional write, so it works
    once."""

    user_id: UUID
    state_hash: str
    expires_at: datetime
    redeemed_at: datetime | None = None


class SlackInstallStart(Platform):
    """Where the browser goes to install: Slack's page, carrying the state."""

    url: str
    expires_at: datetime


class SlackPost(Identifiable, Created):
    """A message the platform posted, keyed by the work that posted it: a
    retried post finds its key here and posts nothing. Slack's timestamp
    names the message in the channel."""

    key: UUID  # the posting work item's idempotency key, or the delivery's
    channel_id: str
    ts: str
