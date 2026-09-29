"""The Slack post handler: one message to the channel the org bound, for a
task created, completed, or reminded of, under the org's own bot token.

Idempotent on the item's key: the post is recorded under it, and a run that
finds the record posts nothing, so a retried item never posts twice (a crash
between Slack's answer and the record is the one window left, and it costs a
duplicate line, never a lost one). The message is composed from the task as
it is when the item runs; the item carries no field of it.

Slack's answers decide the outcome. A rate limit parks the item until the
time Slack named, spending no attempt. A channel that is gone, archived, or
that the app is not in, and an install whose token Slack revoked, break the
installation: it is marked broken, which the portal shows, and the item
completes, since no retry fixes either. The bot's join to the channel mends
a broken channel (`slack_inbound`), and posts resume. A process with no Slack app
configured says so in the log and posts nothing. Anything else fails the
item, which retries with a growing delay."""

import logging
from typing import ClassVar

from acme.integrations.slack import (
    SlackChannelUnusable,
    SlackInterface,
    SlackNotConfigured,
    SlackRateLimited,
    SlackTokenRevoked,
)
from acme.om.exceptions import NotFound
from acme.om.opcontext import OpContext, Permission
from acme.om.slack import SlackManagerInterface
from acme.om.slack.types.installation import SlackInstallationStatus
from acme.om.tasks import TasksManagerInterface
from acme.om.tasks.types.task import Task
from acme.om.work.types.handler import WorkHandlerInterface, WorkParked
from acme.om.work.types.work_item import SlackPostEvent, SlackPostPayload, WorkItem

log = logging.getLogger(__name__)

LEADS: dict[SlackPostEvent, str] = {
    SlackPostEvent.CREATED: ":memo: New task",
    SlackPostEvent.COMPLETED: ":white_check_mark: Done",
    SlackPostEvent.REMINDED: ":alarm_clock: Reminder",
}


def escaped(text: str) -> str:
    """Slack's three control characters, escaped: a title is what a person
    typed, and `<!channel>` in one must not ping a channel."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def message_for(event: SlackPostEvent, task: Task) -> str:
    """The line the channel reads."""
    return f"{LEADS[event]}: *{escaped(task.title)}*"


class SlackPostHandlerImpl(WorkHandlerInterface):
    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.READ, Permission.WRITE)
    """The installation, its token, the task, and the post are read; a post
    is recorded and a broken installation marked."""

    def __init__(
        self,
        tasks: TasksManagerInterface,
        slack: SlackManagerInterface,
        client: SlackInterface,
    ) -> None:
        self._tasks = tasks
        self._slack = slack
        self._client = client

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        payload = SlackPostPayload.model_validate(item.payload)
        if await self._slack.read_post(ctx, item.idempotency_key) is not None:
            return  # posted by an earlier run of this item
        installation = await self._slack.get_installation(ctx)
        if (
            installation is None
            or installation.channel_id is None
            or installation.status is not SlackInstallationStatus.OK
        ):
            log.info("slack post %s dropped: the org has no working channel", item.id)
            return
        channel = installation.channel_id
        try:
            task = await self._tasks.get_task(ctx, item.target_id)
        except NotFound:
            log.info("slack post %s dropped: task %s is gone", item.id, item.target_id)
            return
        try:
            token = await self._slack.bot_token(ctx)
            ts = await self._client.post_message(token, channel, message_for(payload.event, task))
        except SlackRateLimited as error:
            raise WorkParked("slack rate limited the post", error.retry_after) from None
        except SlackChannelUnusable as error:
            log.warning("slack channel of org %s is unusable: %s", ctx.org_id, error.slack_code)
            await self._slack.mark_broken(ctx, error.slack_code)
            return
        except SlackTokenRevoked as error:
            log.warning("slack token of org %s is revoked: %s", ctx.org_id, error.slack_code)
            await self._slack.mark_broken(ctx, error.slack_code)
            return
        except SlackNotConfigured:
            log.warning("slack post %s dropped: no Slack app is configured", item.id)
            return
        await self._slack.record_post(ctx, item.idempotency_key, channel, ts)
