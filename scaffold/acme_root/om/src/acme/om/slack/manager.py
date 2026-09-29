"""The slack swimlane: the Slack app an org installs into its workspace, the
bot token that install holds, the channel it posts to, and the record of what
the platform posted there."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.opcontext import OpContext, RequestContext
from acme.om.slack.types.installation import (
    SlackInstallation,
    SlackInstallStart,
    SlackPost,
)


class SlackManagerInterface(ABC):
    @abstractmethod
    async def get_installation(self, ctx: OpContext) -> SlackInstallation | None:
        """The org's installation, or None when it has none."""
        ...

    @abstractmethod
    async def start_install(self, ctx: OpContext, redirect_uri: str) -> SlackInstallStart:
        """Where an owner or an admin goes to install the app for the org:
        Slack's page, carrying a fresh one-time state bound to the org and to
        them, which Slack sends back to `redirect_uri` with a code. The state
        is kept as a digest and expires after `state_lifetime`. Requires
        `MANAGE_MEMBERS`, since the workspace then speaks for the org."""
        ...

    @abstractmethod
    async def finish_install(
        self, rctx: RequestContext, state: str, code: str, redirect_uri: str
    ) -> SlackInstallation:
        """Platform-internal, on the request stage, like a webhook's lookup: the
        state names the org, since Slack's redirect carries no credential. The
        state is redeemed in one conditional write, so it works once; the code
        is traded for the workspace's bot token, which is kept as the org's
        own secret; and the installation is written, replacing one the org
        had. NotFound when the state is unknown, used, or expired;
        `SlackWorkspaceTaken` when another org holds the workspace, and the
        token Slack handed over is revoked."""
        ...

    @abstractmethod
    async def uninstall(self, ctx: OpContext) -> SlackInstallation | None:
        """Removes the app from the org's workspace, deletes the token, and
        deletes the installation, announced; None when it had none. Requires
        `MANAGE_MEMBERS`."""
        ...

    @abstractmethod
    async def forget(self, ctx: OpContext, reason: str) -> SlackInstallation | None:
        """Slack says the install is gone (the app was uninstalled in Slack,
        or its tokens revoked): the token and the installation are deleted,
        announced. None when the org had none."""
        ...

    @abstractmethod
    async def installation_for_team(
        self, rctx: RequestContext, team_id: str
    ) -> tuple[OpContext, SlackInstallation] | None:
        """Platform-internal, on the request stage: the org a call from Slack
        is for, found by its workspace, with the service context of that org
        attributed to the member who installed the app. None when no org
        holds the workspace."""
        ...

    @abstractmethod
    async def bot_token(self, ctx: OpContext) -> str:
        """The installation's bot token, for one call: renewed first when it
        expires within `refresh_margin`, by one caller at a time. NotFound
        when the org has no installation; `SlackTokenRevoked` when the token
        no longer renews, and the installation is marked broken."""
        ...

    @abstractmethod
    async def bind_channel(self, ctx: OpContext, channel_id: str) -> SlackInstallation:
        """Makes `channel_id` the channel reminders and task updates go to,
        announced. NotFound when the org has no installation. Requires
        `MANAGE_MEMBERS`."""
        ...

    @abstractmethod
    async def mark_broken(self, ctx: OpContext, reason: str) -> SlackInstallation | None:
        """Slack refused a post for good (the channel is gone, archived, or the
        app is not in it): the installation says so, announced, until a
        channel is bound again or the bot joins this one. None when the org
        has no installation."""
        ...

    @abstractmethod
    async def bot_joined(self, ctx: OpContext, channel_id: str) -> SlackInstallation | None:
        """Slack says the app's bot joined `channel_id` (`member_joined_channel`
        with the bot as its member): someone invited it. When that is the bound
        channel and Slack's refusal of the channel broke the installation, the
        installation is well again, announced, and posts resume: the bot is in
        the channel, and a channel it can join is neither gone nor archived.
        A token Slack refused stays broken. None when nothing changed."""
        ...

    @abstractmethod
    async def read_post(self, ctx: OpContext, key: UUID) -> SlackPost | None:
        """The message the work under `key` already posted, if any."""
        ...

    @abstractmethod
    async def record_post(self, ctx: OpContext, key: UUID, channel_id: str, ts: str) -> SlackPost:
        """Records the message the work under `key` posted; a second record
        under the key returns the first."""
        ...

    @abstractmethod
    async def purge_across_tenants(self) -> int:
        """Platform-internal: the sweep, across tenants, once a pass, in one
        transaction: deleted installations, spent states, and post records
        past the retention, a batch of each at most, whatever their tenant;
        returns how many rows went. It takes no context, because it runs for
        no tenant and no principal."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: OpContext) -> int:
        """The sweep, for one tenant past its own retention: every row of it, a
        batch of each kind at most a call. Any other tenant returns 0 and
        reads nothing: its rows past the retention go across tenants."""
        ...
