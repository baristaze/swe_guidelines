"""What Slack sends, after the API checked and acknowledged it: the command
parser; the workspace names the org and the Slack profile's address names
the person; `/acme` alone, `/acme team`, `/acme add`, `/acme connect`,
and the help; the mention, once; the App Home; the uninstall; the bot
invited back to its channel; the tenant fence of the workspace; and the
queue consumer's delete-or-retry."""

import asyncio
from datetime import date, timedelta
from pathlib import Path

import pytest
from slack_support import (
    OWNER_SLACK,
    PORTAL,
    TEAM,
    build,
    claim,
    command,
    connect,
    event,
    inbound,
    install,
    make_task,
    member_of,
    on_team,
    owner_of,
    queued,
)

from acme.infra.queues import Queues
from acme.infra.queues.memory import QueueMemoryImpl
from acme.integrations.slack import SlackFailed, error_for
from acme.integrations.slack.requests import inbound_event
from acme.integrations.slack.twin import SlackTwinImpl, bot_user_of
from acme.om.base import derived_id, utcnow
from acme.om.opcontext import OpContext
from acme.om.slack.types.installation import SlackInstallationStatus
from acme.om.tasks.types.filter import TaskFilter
from acme.om.tasks.types.task import Task, TaskScope, TaskStatus
from acme.om.work.types.work_item import WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.slack_inbound import (
    CONNECTED,
    NOT_INSTALLED,
    USAGE,
    Command,
    InboundOptions,
    SlackInboundConsumer,
    SlackInboundHandler,
    Verb,
    home_view,
    parse_command,
)
from acme.workers.maintenance.slack_posts import SlackPostHandlerImpl

BOB_SLACK = "U0BOB"


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("", Command(Verb.MINE)),
        ("   ", Command(Verb.MINE)),
        ("team", Command(Verb.TEAM)),
        ("  TEAM ", Command(Verb.TEAM)),
        ("help", Command(Verb.HELP)),
        ("connect", Command(Verb.CONNECT)),
        ("add Buy milk", Command(Verb.ADD, "Buy milk")),
        ("Add   Buy   milk ", Command(Verb.ADD, "Buy   milk")),
        ("add", Command(Verb.UNKNOWN, "add")),
        ("me", Command(Verb.UNKNOWN, "me")),
        ("list", Command(Verb.UNKNOWN, "list")),
        ("team everyone", Command(Verb.UNKNOWN, "team everyone")),
        ("delete 3", Command(Verb.UNKNOWN, "delete 3")),
        ("edit Buy milk", Command(Verb.UNKNOWN, "edit Buy milk")),
    ],
)
def test_the_command_parser(text: str, parsed: Command) -> None:
    assert parse_command(text) == parsed


def answers(twin: SlackTwinImpl) -> list[str]:
    return [text for _, text in twin.responses]


async def with_bob(container: WorkerContainer, twin: SlackTwinImpl) -> OpContext:
    """Bob, a member of ajax, in the workspace as `BOB_SLACK`."""
    bob = await member_of(container, "ajax", "bob@ajax.test")
    twin.add_user(TEAM, BOB_SLACK, "bob@ajax.test")
    return bob


async def test_help_and_an_unknown_command_answer_with_the_usage(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    handler = inbound(container, twin)
    await handler.handle(command("help"))  # needs no install
    await handler.handle(command("rename 3 to 4"))
    await handler.handle(command("list"))
    assert answers(twin) == [USAGE, USAGE, USAGE]


async def test_a_workspace_no_org_installed_says_how_to_install(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    handler = inbound(container, twin)
    for text in ("", "team", "add Buy milk", "connect"):
        await handler.handle(command(text))
    assert answers(twin) == [NOT_INSTALLED] * 4


async def test_a_person_acme_does_not_know_is_told_how_to_join(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    twin.add_user(TEAM, "U0STRANGER", "stranger@elsewhere.test")
    handler = inbound(container, twin)
    await handler.handle(command("add Sneak in", user="U0STRANGER"))
    assert answers(twin)[-1] == (
        "You are not a member of Ajax in Acme yet. Acme knows you by the email of your"
        " Slack profile, stranger@elsewhere.test. Ask an owner or an admin to invite that"
        " address in Acme, under Settings, then sign in once with it."
    )
    await handler.handle(command("", user="U0NOEMAIL"))
    assert answers(twin)[-1].startswith("Acme could not read the email of your Slack profile")
    count = await container.managers.tasks.count_open_tasks(
        ann, TaskFilter(scope=TaskScope.TEAM, user_id=ann.user_id)
    )
    assert count == 0, "nothing was added"


async def test_add_is_made_by_the_person_who_typed_and_once(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    bob = await with_bob(container, twin)
    typed = command("add Order more coffee", user=BOB_SLACK)
    handler = inbound(container, twin)
    await handler.handle(typed)
    await handler.handle(typed)  # Slack's retry, or the queue handing it back
    page = await container.managers.tasks.get_open_tasks(
        ann, TaskFilter(scope=TaskScope.TEAM, user_id=ann.user_id), None, 50
    )
    [task] = page.items
    assert task.title == "Order more coffee" and task.created_by == bob.user_id
    assert task.id == derived_id(typed.key, typed.received_at)
    assert answers(twin)[-1] == "Added: *Order more coffee*"


async def test_add_lands_in_the_workspaces_org_alone(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    zoe = await owner_of(container, "zenith")
    await install(container, twin, ann, "T0AJAX")
    await install(container, twin, zoe, "T0ZENITH")
    await inbound(container, twin).handle(command("add Ajax's own", team="T0AJAX"))
    tasks = container.managers.tasks
    ajax = await tasks.count_open_tasks(ann, TaskFilter(scope=TaskScope.TEAM, user_id=ann.user_id))
    zenith = await tasks.count_open_tasks(
        zoe, TaskFilter(scope=TaskScope.TEAM, user_id=zoe.user_id)
    )
    assert (ajax, zenith) == (1, 0)


async def test_add_past_the_plans_bound_says_so_and_where_to_upgrade(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")  # a new org is on Free: ten active tasks
    await install(container, twin, ann)
    for n in range(10):
        await container.managers.tasks.create_task(ann, make_task(ann, f"task {n}"))
    await inbound(container, twin).handle(command("add One too many"))
    assert answers(twin)[-1] == (
        "Not added: the Free plan allows 10 active tasks. Pro lifts it. An owner or an "
        f"admin can upgrade in Acme, under <{PORTAL}/settings/billing|Settings, Billing>."
    )
    assert await container.managers.tasks.count_active_tasks(ann) == 10


async def test_connect_binds_the_channel_an_owner_typed_it_in(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await connect(container, twin, ann, "C0TEAM")
    installation = await container.managers.slack.get_installation(ann)
    assert installation is not None and installation.channel_id == "C0TEAM"
    assert twin.posts[-1].channel_id == "C0TEAM" and twin.posts[-1].text == CONNECTED
    assert answers(twin)[-1] == (
        "Connected. Reminders and task updates will appear in this channel."
    )


async def test_connect_is_an_owners_or_an_admins(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    await with_bob(container, twin)
    await inbound(container, twin).handle(command("connect", "C0TEAM", BOB_SLACK))
    assert answers(twin)[-1].startswith("Only an owner or an admin")
    installation = await container.managers.slack.get_installation(ann)
    assert installation is not None and installation.channel_id is None


async def test_connect_in_a_channel_the_app_is_not_in_binds_nothing(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    twin.fail_next(error_for("not_in_channel"), "chat.postMessage")
    await inbound(container, twin).handle(command("connect", "C0PRIVATE"))
    assert answers(twin)[-1] == (
        "@acme is not in this channel yet, so it cannot post here: type `/invite @acme`."
        " Then type `/acme connect` again."
    )
    installation = await container.managers.slack.get_installation(ann)
    assert installation is not None and installation.channel_id is None


async def add_tasks(
    container: WorkerContainer, ctx: OpContext, count: int, prefix: str = "Task"
) -> list[Task]:
    """`count` open tasks, created oldest first, so the last one created is
    the newest."""
    return [
        await container.managers.tasks.create_task(ctx, make_task(ctx, f"{prefix} {n}"))
        for n in range(1, count + 1)
    ]


async def listed(
    handler: SlackInboundHandler, twin: SlackTwinImpl, text: str = "", user: str = OWNER_SLACK
) -> str:
    await handler.handle(command(text, user=user))
    return twin.responses[-1][1]


async def test_an_empty_list_says_so_to_the_person_alone(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    answer = await listed(inbound(container, twin), twin)
    assert answer == (
        f"No open tasks. Add one with `/acme add <title>`, or open <{PORTAL}/|Acme>."
    )
    assert twin.responses[-1][0] == "https://hooks.slack.com/commands/T/1/abc", "for one person"


async def test_acme_alone_is_my_open_tasks_newest_first(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await on_team(container, ann)
    await install(container, twin, ann)
    bob = await with_bob(container, twin)
    await add_tasks(container, ann, 3, "Ann's")
    await add_tasks(container, bob, 2, "Bob's")
    handler = inbound(container, twin)
    assert await listed(handler, twin) == "\n".join(
        [
            "*Your open tasks*",
            "• Ann's 3",
            "• Ann's 2",
            "• Ann's 1",
            f"Open <{PORTAL}/|Acme> to work on them.",
        ]
    )
    assert (await listed(handler, twin, user=BOB_SLACK)).split("\n")[1:3] == [
        "• Bob's 2",
        "• Bob's 1",
    ]
    assert await listed(handler, twin, "   ") == await listed(handler, twin), (
        "spaces are still alone"
    )


async def test_team_is_every_open_task_newest_first(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await on_team(container, ann)
    await install(container, twin, ann)
    bob = await with_bob(container, twin)
    await add_tasks(container, ann, 1, "Ann's")
    await add_tasks(container, bob, 1, "Bob's")
    answer = await listed(inbound(container, twin), twin, "team", BOB_SLACK)
    assert answer.split("\n")[:3] == ["*The team's open tasks*", "• Bob's 1", "• Ann's 1"]


async def test_a_long_list_shows_the_ten_newest_and_counts_the_rest(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await on_team(container, ann)
    await install(container, twin, ann)
    await add_tasks(container, ann, 12)
    for text in ("", "team"):
        lines = (await listed(inbound(container, twin), twin, text)).split("\n")
        assert lines[1:11] == [f"• Task {n}" for n in range(12, 2, -1)]
        assert lines[-1] == f"…and 2 more in <{PORTAL}/|Acme>"
        assert len(lines) == 12


async def test_the_list_leaves_out_done_and_deleted_tasks(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    done, gone, kept = await add_tasks(container, ann, 3)
    tasks = container.managers.tasks
    await tasks.update_task(ann, done.model_copy(update={"status": TaskStatus.DONE}), done.version)
    await tasks.delete_task(ann, gone.id, gone.version)
    answer = await listed(inbound(container, twin), twin, "team")
    assert answer.split("\n")[1:-1] == [f"• {kept.title}"]


async def test_a_title_cannot_ping_or_fake_a_link_and_the_due_date_is_shown(
    tmp_path: Path,
) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    await container.managers.tasks.create_task(
        ann, make_task(ann, "Ship <!channel> & <https://evil.test|this>", due_on=date(2030, 5, 17))
    )
    answer = await listed(inbound(container, twin), twin)
    assert answer.split("\n")[1] == (
        "• Ship &lt;!channel&gt; &amp; &lt;https://evil.test|this&gt;  _due 2030-05-17_"
    )
    assert "<!channel>" not in answer


async def test_a_mention_is_answered_in_its_thread_once(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    mention = event({"type": "app_mention", "channel": "C0ANY", "ts": "1700.01", "text": "hi"})
    handler = inbound(container, twin)
    await handler.handle(mention)
    await handler.handle(mention)  # Slack's retry of the same event
    replies = [post for post in twin.posts if post.thread_ts == "1700.01"]
    assert [post.text for post in replies] == [USAGE]
    assert replies[0].team_id == TEAM


async def test_the_home_is_published_with_the_orgs_token(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    await inbound(container, twin).handle(
        event({"type": "app_home_opened", "user": "U0ANN", "tab": "home"})
    )
    [(user, view)] = twin.homes
    assert user == "U0ANN" and view["type"] == "home"


@pytest.mark.parametrize(
    "body",
    [
        {"type": "app_uninstalled"},
        {"type": "tokens_revoked", "tokens": {"oauth": [], "bot": ["UBOTT0AJAX"]}},
    ],
)
async def test_an_uninstall_in_slack_removes_the_installation(
    tmp_path: Path, body: dict[str, object]
) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    installation = await container.managers.slack.get_installation(ann)
    assert installation is not None
    await inbound(container, twin).handle(event(body))
    assert await container.managers.slack.get_installation(ann) is None
    secrets = container.infra.get_secrets()
    assert not await secrets.has(ann.org_id, installation.credential_ref)
    await inbound(container, twin).handle(command("add After"))
    assert answers(twin)[-1] == NOT_INSTALLED


async def test_a_user_token_revoked_leaves_the_installation(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await install(container, twin, ann)
    await inbound(container, twin).handle(
        event({"type": "tokens_revoked", "tokens": {"oauth": ["U0ANN"], "bot": []}})
    )
    assert await container.managers.slack.get_installation(ann) is not None


async def post_what_is_queued(container: WorkerContainer, twin: SlackTwinImpl) -> None:
    """Runs every Slack post the org's writes queued, as the worker's loop does."""
    posting = SlackPostHandlerImpl(container.managers.tasks, container.managers.slack, twin)
    while (claimed := await claim(container, WorkKind.SLACK_POST)) is not None:
        await posting.handle(*claimed)
        await container.managers.work.complete(*claimed)


async def status_of(container: WorkerContainer, ctx: OpContext) -> tuple[str, str | None]:
    installation = await container.managers.slack.get_installation(ctx)
    assert installation is not None
    return installation.status, installation.broken_reason


async def test_the_bot_invited_back_mends_the_install_and_posts_resume(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await connect(container, twin, ann, "C0TEAM")
    tasks = container.managers.tasks
    twin.remove_bot(TEAM, "C0TEAM")  # someone removed @acme from the channel
    await tasks.create_task(ann, make_task(ann, "While it was out"))
    await post_what_is_queued(container, twin)
    assert await status_of(container, ann) == (SlackInstallationStatus.BROKEN, "not_in_channel")
    before = len(await queued(container, ann, WorkKind.SLACK_POST))
    await tasks.create_task(ann, make_task(ann, "Still out"))
    assert len(await queued(container, ann, WorkKind.SLACK_POST)) == before, "posting stopped"
    joined = inbound_event(twin.invite_bot(TEAM, "C0TEAM"), utcnow(), 0)
    handler = inbound(container, twin)
    await handler.handle(joined)
    await handler.handle(joined)  # Slack's retry of the same event
    assert await status_of(container, ann) == (SlackInstallationStatus.OK, None)
    await tasks.create_task(ann, make_task(ann, "Back again"))
    await post_what_is_queued(container, twin)
    assert twin.posts[-1].channel_id == "C0TEAM"
    assert twin.posts[-1].text == ":memo: New task: *Back again*", "no relink, no new install"


@pytest.mark.parametrize(
    ("who", "channel"),
    [("U0SOMEONE", "C0TEAM"), (bot_user_of(TEAM), "C0ELSEWHERE")],
    ids=["a person joins the channel", "the bot joins another channel"],
)
async def test_only_the_bot_joining_its_own_channel_mends_it(
    tmp_path: Path, who: str, channel: str
) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await connect(container, twin, ann, "C0TEAM")
    await container.managers.slack.mark_broken(ann, "not_in_channel")
    await inbound(container, twin).handle(
        event({"type": "member_joined_channel", "user": who, "channel": channel, "team": TEAM})
    )
    assert await status_of(container, ann) == (SlackInstallationStatus.BROKEN, "not_in_channel")


async def test_an_event_for_a_workspace_no_org_holds_does_nothing(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    await inbound(container, twin).handle(
        event({"type": "app_mention", "channel": "C0ANY", "ts": "1.0"}, team="T0NOBODY")
    )
    assert twin.posts == []


async def test_the_consumer_deletes_what_it_handled_and_leaves_what_failed(
    tmp_path: Path,
) -> None:
    container, twin = build(tmp_path)
    queues = QueueMemoryImpl()
    consumer = SlackInboundConsumer(
        queues, inbound(container, twin), InboundOptions(wait=timedelta(0))
    )
    await queues.send(Queues.SLACK, b"not json")
    await queues.send(Queues.SLACK, command("help").model_dump_json().encode())
    assert await consumer.poll_once() == 2
    depth = await queues.depth(Queues.SLACK)
    assert depth.visible + depth.in_flight == 0
    twin.fail_next(SlackFailed("timeout"))
    await queues.send(Queues.SLACK, command("help").model_dump_json().encode())
    assert await consumer.poll_once() == 1
    assert (await queues.depth(Queues.SLACK)).in_flight == 1, "left for the queue to hand back"


async def test_the_consumer_stops_when_asked(tmp_path: Path) -> None:
    container, twin = build(tmp_path)
    consumer = SlackInboundConsumer(
        QueueMemoryImpl(), inbound(container, twin), InboundOptions(wait=timedelta(0))
    )
    running = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.01)
    consumer.stop()
    await asyncio.wait_for(running, 1)


def test_the_usage_and_the_home_name_every_command() -> None:
    for named in ("`/acme`", "`/acme team`", "`/acme add <title>`", "`/acme connect`"):
        assert named in USAGE
    assert "`/acme list`" not in USAGE and "`/acme me`" not in USAGE
    [section] = [b for b in home_view()["blocks"] if b.get("text", {}).get("text") == USAGE]
    assert section["type"] == "section"
