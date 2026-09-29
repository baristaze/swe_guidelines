from datetime import datetime
from uuid import UUID

from acme.om.slack.types.installation import SlackInstallationStatus
from acme.services.api.types.common import View


class SlackInstallationView(View):
    """The Slack workspace the org installed Acme into, and the channel it
    posts to. `channel_id` is null until someone types `/acme connect` in a
    channel. `status` is `broken` when Slack refused for good (`broken_reason`
    says which refusal): the channel is gone or the app is not in it, which a
    new `/acme connect` mends, or the token no longer renews, which a new
    install mends. `created_by` is the member who installed it. The bot token
    is never on the wire."""

    id: UUID
    team_id: str
    team_name: str
    channel_id: str | None
    status: SlackInstallationStatus
    broken_reason: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class SlackStatusView(View):
    """Whether the org has installed Acme in Slack, and where."""

    installation: SlackInstallationView | None


class SlackInstallStartView(View):
    """Slack's own page, where a person approves the install for their
    workspace. The link carries a one-time state, works once, until
    `expires_at`, and is shown here only: a replay under the same
    Idempotency-Key answers with `url` null, and the caller asks for another."""

    secret_fields = frozenset({"url"})

    url: str | None
    expires_at: datetime


class SlackEventAnswerView(View):
    """Slack's check of the events URL gets its `challenge` back; every other
    event is acknowledged with nothing."""

    challenge: str | None = None
