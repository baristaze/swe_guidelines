"""Helpers the reminder and Slack tests share: a container over the memory
roots and the Slack twin, orgs to act in, the app installed and a channel
bound, the calls Slack makes, and the work a write queued."""

from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from worker_support import request

from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.slack.requests import SlackInbound, inbound_command, inbound_event
from acme.integrations.slack.twin import SlackTwinImpl
from acme.om.base import new_id, utcnow
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import AppContext, AppType, OpContext, Role
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tasks.types.task import Task
from acme.om.tenancy.impl.manager import TenancyManagerImpl, TenancyOptions
from acme.om.work.types.work_item import WorkItem, WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.slack_inbound import SlackInboundHandler

TEAM = "T0AJAX"
OWNER_SLACK = "U0OWNER"
PORTAL = "https://app.acme.test"
REDIRECT = "https://api.acme.test/webhooks/slack/oauth"
LEASE = timedelta(seconds=30)


def build(tmp_path: Path) -> tuple[WorkerContainer, SlackTwinImpl]:
    twin = SlackTwinImpl("test")
    container = WorkerContainer.for_tests(StorageMemoryImpl(), InfraLocalImpl(tmp_path), slack=twin)
    return container, twin


async def owner_of(container: WorkerContainer, slug: str) -> OpContext:
    ctx, _ = await container.managers.tenancy.bootstrap(
        request(), slug.title(), slug, f"owner@{slug}.test", "Owner"
    )
    return ctx


async def on_team(container: WorkerContainer, owner: OpContext) -> None:
    """Puts the org of a fresh owner on Team, with the seed's grant: a test
    of a list longer than Free's ten active tasks is about the list."""
    await container.managers.billing.grant_seeded_plan(owner, Plan.TEAM)


async def member_of(container: WorkerContainer, slug: str, email: str) -> OpContext:
    tenancy = container.managers.tenancy
    await tenancy.add_member(request(), slug, email, "Member", Role.MEMBER)
    # The worker signs nobody in, so the sign-in runs through a manager over
    # the same storage with the local sign-in on.
    signing = TenancyManagerImpl(
        container.storage.get_tenancy_storage(),
        container.managers.outbox,
        container.infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
        entitlements=container.managers.billing,
    )
    login = await signing.dev_sign_in(request(), email)
    identity = await tenancy.authenticate_login(request(), login.token)
    memberships = await tenancy.get_identity_memberships(identity, None, 10)
    issued = await tenancy.exchange_login(identity, memberships.items[0].org.id)
    return await tenancy.authenticate(request(), issued.token)


def make_task(ctx: OpContext, title: str = "Water the plants", **fields: object) -> Task:
    now = utcnow()
    return Task(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        title=title,
        **fields,  # type: ignore[arg-type]
    )


async def queued(
    container: WorkerContainer, ctx: OpContext, kind: WorkKind, target_id: UUID | None = None
) -> list[WorkItem]:
    """The items of a kind the tenant's writes queued, oldest first, read off
    the memory queue without claiming anything."""
    storage = container.storage.get_work_storage()
    found: list[WorkItem] = []
    for _, (org_id, item) in sorted(storage._items.items()):  # type: ignore[attr-defined]
        if org_id == ctx.org_id and item.kind is kind:
            if target_id is None or item.target_id == target_id:
                found.append(item)
    return found


async def claim(container: WorkerContainer, kind: WorkKind) -> tuple[OpContext, WorkItem] | None:
    return await container.managers.work.claim(request(), "default", [kind], "test", LEASE)


def command(
    text: str, channel: str = "C0SLACK", user: str = OWNER_SLACK, team: str = TEAM
) -> SlackInbound:
    """A `/acme` command as the API queues it once its signature checked out."""
    return inbound_command(
        {
            "command": "/acme",
            "text": text,
            "team_id": team,
            "channel_id": channel,
            "user_id": user,
            "trigger_id": f"trigger.{new_id()}",
            "response_url": "https://hooks.slack.com/commands/T/1/abc",
        },
        utcnow(),
        0,
    )


def event(body: dict[str, object], team: str = TEAM) -> SlackInbound:
    """An Events API callback as the API queues it."""
    return inbound_event(
        {"type": "event_callback", "team_id": team, "event_id": f"Ev{new_id().hex}", "event": body},
        utcnow(),
        0,
    )


def inbound(container: WorkerContainer, twin: SlackTwinImpl) -> SlackInboundHandler:
    return SlackInboundHandler(
        container.managers.slack,
        container.managers.tasks,
        container.managers.tenancy,
        twin,
        AppContext(type=AppType.SLACK, version="slack@test"),
        PORTAL,
    )


async def install(
    container: WorkerContainer, twin: SlackTwinImpl, owner: OpContext, team: str = TEAM
) -> None:
    """Installs the app for the owner's org the way a person does: Add to
    Slack in the portal, Allow on Slack's page, and back. The owner is in the
    workspace as `OWNER_SLACK`, with the address they sign in with."""
    slack = container.managers.slack
    start = await slack.start_install(owner, REDIRECT)
    state = parse_qs(urlsplit(start.url).query)["state"][0]
    await slack.finish_install(request(), state, twin.approve(team, OWNER_SLACK), REDIRECT)
    identity = await container.managers.tenancy.get_identity(owner)
    twin.add_user(team, OWNER_SLACK, identity.email)


async def connect(
    container: WorkerContainer, twin: SlackTwinImpl, owner: OpContext, channel: str = "C0SLACK"
) -> None:
    """Installs the app and binds a channel, as the owner types `/acme
    connect` in it."""
    await install(container, twin, owner)
    await inbound(container, twin).handle(command("connect", channel))
