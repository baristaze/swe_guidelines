"""What Slack sends, handled after the API acknowledged it.

The API checks each call's signature, answers Slack within its three seconds,
and puts the call on the `slack` queue as a `SlackInbound`; this module is
the other side of that queue. A call is a slash command (`/acme ...`) or an
event: an app mention, the App Home being opened, the app being uninstalled,
or the bot joining a channel, which mends an installation broken by that
channel. Each is handled here and then deleted from the queue; one whose
handling failed is left there and comes back after its visibility timeout,
and the hosted queue moves it to its dead-letter queue after a few returns.

A call names a workspace, never a tenant: the org is the one that installed
the app in that workspace. A command also names a Slack user, and the person
is the member of that org whose sign-in proved the address the Slack profile
holds. What the command does, it does as that person, with their role.

Handling twice is harmless, which is what makes Slack's retries and the
queue's redeliveries safe. A task `/acme add` creates takes an id derived
from the call's key and the time it was received (ADR 0027), so the second
create meets the row already there. The reply to a mention is recorded under
the call's key, so it is posted once. The rest are answers to the person who
typed, which say the same thing again, and reads, which change nothing. A
join mends an installation once; a second finds it well."""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from acme.infra.observability import OUTCOMES, current_traceparent
from acme.infra.queues import Queues, QueuesInterface
from acme.integrations.slack import (
    SlackChannelUnusable,
    SlackError,
    SlackFailed,
    SlackInterface,
    SlackRateLimited,
)
from acme.integrations.slack.requests import SlackInbound
from acme.om.base import derived_id, new_id, utcnow
from acme.om.exceptions import PlanLimitReached, PlatformException
from acme.om.opcontext import AppContext, OpContext, Permission, RequestContext
from acme.om.slack import SlackManagerInterface
from acme.om.tasks import TasksManagerInterface
from acme.om.tasks.types.filter import TaskFilter
from acme.om.tasks.types.task import Task, TaskScope
from acme.om.tenancy import TenancyManagerInterface
from acme.workers.maintenance.slack_posts import escaped

log = logging.getLogger(__name__)

TITLE_LIMIT = 500
"""The longest title a task takes, as the API's own limit."""

LIST_LIMIT = 10
"""How many open tasks a list shows; the rest are a count and a link."""


class Verb(StrEnum):
    MINE = "mine"
    TEAM = "team"
    ADD = "add"
    CONNECT = "connect"
    HELP = "help"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Command:
    verb: Verb
    argument: str = ""


def parse_command(text: str) -> Command:
    """`/acme [team | add <title> | connect | help]`. An empty command is
    your own open tasks, the question asked most; a verb it does not know,
    or `add` with nothing after it, is unknown, which answers with the
    usage."""
    verb, _, rest = text.strip().partition(" ")
    verb, rest = verb.lower(), rest.strip()
    if verb == "":
        return Command(Verb.MINE)
    if verb in (Verb.TEAM, Verb.CONNECT, Verb.HELP) and not rest:
        return Command(Verb(verb))
    if verb == "add" and rest:
        return Command(Verb.ADD, rest)
    return Command(Verb.UNKNOWN, text.strip())


USAGE = "\n".join(
    [
        "*Acme* keeps your team's to-do list.",
        "• `/acme` shows your ten newest open tasks, only to you: My Tasks in Acme,"
        " assigned to you or unassigned and made by you",
        "• `/acme team` shows the team's ten newest open tasks, only to you",
        "• `/acme add <title>` adds a task, made by you",
        "• `/acme connect` makes this channel the one Acme posts reminders and task"
        " updates to; an owner or an admin types it, after `/invite @acme`",
        "• `/acme help` shows this",
    ]
)

NOT_INSTALLED = (
    "Acme is not installed for this Slack workspace yet. An owner or an admin opens"
    " Settings in Acme and clicks *Add to Slack*."
)

INVITE = "@acme is not in this channel yet, so it cannot post here: type `/invite @acme`."

CONNECTED = ":link: Acme posts reminders and task updates in this channel."


def unknown_person(org_name: str, email: str | None) -> str:
    """The answer to a person Acme cannot match to a member: how to join."""
    if email is None:
        return (
            "Acme could not read the email of your Slack profile, which is how it knows"
            f" you in {escaped(org_name)}. Ask a workspace admin to show it, then try again."
        )
    return (
        f"You are not a member of {escaped(org_name)} in Acme yet. Acme knows you by the"
        f" email of your Slack profile, {escaped(email)}. Ask an owner or an admin to invite"
        " that address in Acme, under Settings, then sign in once with it."
    )


def due_text(task: Task) -> str:
    """The due date as Slack shows it: the date itself, which reads the same
    in every time zone."""
    return "" if task.due_on is None else task.due_on.isoformat()


def task_line(task: Task) -> str:
    """One task of the list: its title, escaped, and its due date when set."""
    line = f"• {escaped(task.title)}"
    due = due_text(task)
    return f"{line}  _due {due}_" if due else line


def list_answer(heading: str, tasks: tuple[Task, ...], more: int, portal_url: str) -> str:
    """The reply to a list: the newest tasks first, and how many more there
    are with a link to the rest."""
    link = f"<{portal_url.rstrip('/')}/|Acme>"
    if not tasks:
        return f"No open tasks. Add one with `/acme add <title>`, or open {link}."
    lines = [f"*{heading}*", *(task_line(task) for task in tasks)]
    if more > 0:
        lines.append(f"…and {more} more in {link}")
    else:
        lines.append(f"Open {link} to work on them.")
    return "\n".join(lines)


def home_view() -> dict[str, Any]:
    """The App Home: what the commands do and how a channel gets posts."""
    return {
        "type": "home",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Acme"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": USAGE}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Get reminders in a channel*\nIn the channel, type"
                    " `/invite @acme`, then `/acme connect`.",
                },
            },
        ],
    }


def over_the_plan(refused: PlanLimitReached, portal_url: str) -> str:
    """The answer to an add the org's plan has no room for: what the plan
    allows, and a link to where the org is upgraded. Every feature stays; a
    bound is lifted in Acme, by an owner or an admin."""
    plan = refused.plan.title()
    said = refused.message.replace(f"the {refused.plan} plan", f"the {plan} plan")
    lifts = f" {refused.suggested_plan.title()} lifts it." if refused.suggested_plan else ""
    billing = f"<{portal_url.rstrip('/')}/settings/billing|Settings, Billing>"
    return f"Not added: {said}.{lifts} An owner or an admin can upgrade in Acme, under {billing}."


class SlackInboundHandler:
    """What each kind of call does. Holds nothing per call."""

    def __init__(
        self,
        slack: SlackManagerInterface,
        tasks: TasksManagerInterface,
        tenancy: TenancyManagerInterface,
        client: SlackInterface,
        app: AppContext,
        portal_url: str,
    ) -> None:
        self._slack = slack
        self._tasks = tasks
        self._tenancy = tenancy
        self._client = client
        self._app = app
        self._portal_url = portal_url

    async def handle(self, delivery: SlackInbound) -> None:
        if delivery.kind == "command":
            await self._command(delivery)
        else:
            await self._event(delivery)

    def _request(self) -> RequestContext:
        return RequestContext(request_id=new_id(), app=self._app, traceparent=current_traceparent())

    async def _command(self, delivery: SlackInbound) -> None:
        payload = delivery.payload
        response_url = str(payload.get("response_url", ""))
        command = parse_command(str(payload.get("text", "")))
        if command.verb in (Verb.HELP, Verb.UNKNOWN):
            await self._client.respond(response_url, USAGE)
            return
        found = await self._slack.installation_for_team(
            self._request(), str(payload.get("team_id", ""))
        )
        if found is None:
            await self._client.respond(response_url, NOT_INSTALLED)
            return
        org_ctx, _ = found
        token = await self._slack.bot_token(org_ctx)
        email = await self._client.user_email(token, str(payload.get("user_id", "")))
        person = None
        if email is not None:
            person = await self._tenancy.member_context(self._request(), org_ctx.org_id, email)
        if person is None:
            org = await self._tenancy.get_org(org_ctx)
            await self._client.respond(response_url, unknown_person(org.name, email))
            return
        if command.verb is Verb.MINE:
            answer = await self._list(person, TaskScope.MINE, "Your open tasks")
        elif command.verb is Verb.TEAM:
            answer = await self._list(person, TaskScope.TEAM, "The team's open tasks")
        elif command.verb is Verb.CONNECT:
            answer = await self._connect(person, token, str(payload.get("channel_id", "")))
        else:
            answer = await self._add(person, delivery, command.argument)
        await self._client.respond(response_url, answer)

    async def _list(self, person: OpContext, scope: TaskScope, heading: str) -> str:
        """The newest open tasks the scope shows, read as the person who
        typed: one bounded page, and a count only when more are open."""
        criterion = TaskFilter(scope=scope, user_id=person.user_id)
        page = await self._tasks.get_recent_open_tasks(person, criterion, LIST_LIMIT)
        more = 0
        if page.has_more:
            # A task closed between the two reads can make the count fall to
            # the page; a page that said more are open still says "more".
            total = await self._tasks.count_open_tasks(person, criterion)
            more = max(total - len(page.items), 1)
        return list_answer(heading, page.items, more, self._portal_url)

    async def _add(self, person: OpContext, delivery: SlackInbound, title: str) -> str:
        now = utcnow()
        task = Task(
            id=derived_id(delivery.key, delivery.received_at),
            created_at=now,
            updated_at=now,
            created_by=person.user_id,
            updated_by=person.user_id,
            title=title[:TITLE_LIMIT],
        )
        try:
            created = await self._tasks.create_task(person, task)
        except PlanLimitReached as refused:
            return over_the_plan(refused, self._portal_url)
        return f"Added: *{escaped(created.title)}*"

    async def _connect(self, person: OpContext, token: str, channel_id: str) -> str:
        """The channel the command was typed in becomes the one Acme posts to,
        once a first post there shows the app can post at all: a channel it
        was never invited to refuses, and is not bound."""
        if not person.has(Permission.MANAGE_MEMBERS):
            return "Only an owner or an admin of the org chooses the channel Acme posts to."
        try:
            await self._client.post_message(token, channel_id, CONNECTED)
        except SlackChannelUnusable:
            return f"{INVITE} Then type `/acme connect` again."
        await self._slack.bind_channel(person, channel_id)
        return "Connected. Reminders and task updates will appear in this channel."

    async def _event(self, delivery: SlackInbound) -> None:
        payload = delivery.payload
        event = payload.get("event") or {}
        kind = event.get("type")
        found = await self._slack.installation_for_team(
            self._request(), str(payload.get("team_id", ""))
        )
        if found is None:
            log.info("slack event %s for a workspace no org holds; ignored", kind)
            return
        org_ctx, installation = found
        if kind == "app_uninstalled" or (
            kind == "tokens_revoked" and (event.get("tokens") or {}).get("bot")
        ):
            await self._slack.forget(org_ctx, str(kind))
        elif kind == "app_mention":
            await self._mention(org_ctx, delivery, event)
        elif kind == "app_home_opened" and event.get("tab", "home") == "home":
            token = await self._slack.bot_token(org_ctx)
            await self._client.publish_home(token, str(event.get("user", "")), home_view())
        elif kind == "member_joined_channel" and event.get("user") == installation.bot_user_id:
            await self._slack.bot_joined(org_ctx, str(event.get("channel", "")))
        else:
            log.info("slack event %s ignored", kind)

    async def _mention(
        self, org_ctx: OpContext, delivery: SlackInbound, event: dict[str, Any]
    ) -> None:
        """`@acme` answers with the usage, in the thread, once per call."""
        if await self._slack.read_post(org_ctx, delivery.key) is not None:
            return
        channel = str(event.get("channel", ""))
        thread = str(event.get("thread_ts") or event.get("ts") or "")
        token = await self._slack.bot_token(org_ctx)
        ts = await self._client.post_message(token, channel, USAGE, thread_ts=thread)
        await self._slack.record_post(org_ctx, delivery.key, channel, ts)


class InboundOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: int = 10
    wait: timedelta = timedelta(seconds=10)
    visibility: timedelta = timedelta(seconds=60)
    error_backoff: timedelta = timedelta(seconds=5)
    idle: timedelta = timedelta(seconds=1)
    """The pause after an empty receive. The hosted queue's long poll already
    waits; a queue that answers empty at once (the in-process twin) would
    otherwise spin."""


def settled(error: SlackError | PlatformException) -> bool:
    """Whether a refusal ends the call: a platform refusal of the request (a
    4xx) and a Slack answer about the channel or the install do; a rate
    limit, a transport failure, and a platform that cannot serve now (a 5xx)
    are worth another run."""
    if isinstance(error, PlatformException):
        return error.http_status < 500
    return not isinstance(error, SlackRateLimited | SlackFailed)


class SlackInboundConsumer:
    """The loop over the `slack` queue: receive, handle, delete. A call that
    does not parse is deleted with a log line, since no retry parses it; one
    whose handling raised stays for the queue to hand back."""

    def __init__(
        self, queues: QueuesInterface, handler: SlackInboundHandler, options: InboundOptions
    ) -> None:
        self._queues = queues
        self._handler = handler
        self._options = options
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        while not self._stopping.is_set():
            try:
                received = await self.poll_once()
            except Exception:
                log.exception("slack inbound: receive failed")
                OUTCOMES.labels(subsystem="slack_inbound", outcome="receive_error").inc()
                await self._pause(self._options.error_backoff)
                continue
            if received == 0:
                await self._pause(self._options.idle)

    async def _pause(self, pause: timedelta) -> None:
        """Waits out `pause`, or less when a stop arrives meanwhile."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=pause.total_seconds())

    async def poll_once(self) -> int:
        """One receive and every call it returned; how many it received."""
        messages = await self._queues.receive(
            Queues.SLACK, self._options.batch, self._options.wait, self._options.visibility
        )
        for message in messages:
            try:
                delivery = SlackInbound.model_validate_json(message.body)
            except ValidationError:
                log.error("slack inbound: message %s does not parse; dropped", message.id)
                OUTCOMES.labels(subsystem="slack_inbound", outcome="malformed").inc()
                await self._queues.delete(Queues.SLACK, message.receipt)
                continue
            try:
                await self._handler.handle(delivery)
            except (SlackError, PlatformException) as error:
                if not settled(error):
                    log.warning(
                        "slack inbound: %s met %s; the queue hands it back", delivery.key, error
                    )
                    OUTCOMES.labels(subsystem="slack_inbound", outcome="failed").inc()
                    continue
                # A refusal no retry changes is said in the log; the call is done.
                log.warning("slack inbound: %s answered %s", delivery.key, error)
                OUTCOMES.labels(subsystem="slack_inbound", outcome="refused").inc()
            except Exception:
                log.exception("slack inbound: %s failed; the queue hands it back", delivery.key)
                OUTCOMES.labels(subsystem="slack_inbound", outcome="failed").inc()
                continue
            await self._queues.delete(Queues.SLACK, message.receipt)
            OUTCOMES.labels(subsystem="slack_inbound", outcome="handled").inc()
        return len(messages)
