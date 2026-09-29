"""Slack's wire. Its calls in are checked against the signing secret over
the raw body before anything is queued: a good signature is queued and
answered at once, a bad, stale, or missing one is refused, and a replay
queues the same key again. The events URL answers Slack's challenge. An
install starts from the org's settings, carries a one-time state to Slack
and back, and ends on the portal's settings page saying how it went; any
member reads the installation, and only an owner or an admin installs or
removes it."""

import json
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import httpx
import pytest
from api_support import add_member, build_container, seed_request, sign_in_as

from acme.infra.queues import Queues
from acme.integrations.slack.requests import SlackInbound, delivery_key, sign
from acme.integrations.slack.twin import SlackTwinImpl
from acme.om.opcontext import Role
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer

PORTAL = "http://localhost:55173"


def twin_of(container: AppContainer) -> SlackTwinImpl:
    slack = container.integrations.get_slack()
    assert isinstance(slack, SlackTwinImpl)
    return slack


async def queued(container: AppContainer) -> list[SlackInbound]:
    queues = container.infra.get_queues()
    found: list[SlackInbound] = []
    for message in await queues.receive(Queues.SLACK, 10, timedelta(0), timedelta(30)):
        found.append(SlackInbound.model_validate_json(message.body))
        await queues.delete(Queues.SLACK, message.receipt)
    return found


def command_body(text: str = "add Buy milk", trigger: str = "1.2.3") -> bytes:
    return urlencode(
        {
            "command": "/acme",
            "text": text,
            "team_id": "T0AJAX",
            "channel_id": "C0TEAM",
            "user_id": "U0ANN",
            "trigger_id": trigger,
            "response_url": "https://hooks.slack.com/commands/T0AJAX/1/abc",
        }
    ).encode()


async def post_signed(
    client: httpx.AsyncClient,
    twin: SlackTwinImpl,
    path: str,
    body: bytes,
    *,
    at: int | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    signed = {**twin.signed(body, at), **(headers or {})}
    return await client.post(path, content=body, headers=signed)


# The calls in.


async def test_a_signed_command_is_queued_and_answered_at_once(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    twin = twin_of(container)
    answer = await post_signed(client, twin, "/webhooks/slack/commands", command_body())
    assert answer.status_code == 200 and answer.content == b""
    [delivery] = await queued(container)
    assert delivery.kind == "command" and delivery.key == delivery_key("1.2.3")
    assert delivery.payload["text"] == "add Buy milk" and delivery.retry_num == 0


@pytest.mark.parametrize("change", ["tampered", "stale", "unsigned", "foreign"])
async def test_a_call_that_does_not_check_out_is_refused_and_queues_nothing(
    client: httpx.AsyncClient, container: AppContainer, change: str
) -> None:
    twin = twin_of(container)
    body = command_body()
    if change == "tampered":
        signed = twin.signed(body)
        answer = await client.post(
            "/webhooks/slack/commands", content=body.replace(b"milk", b"beer"), headers=signed
        )
    elif change == "stale":
        answer = await post_signed(
            client, twin, "/webhooks/slack/commands", body, at=int(time.time()) - 600
        )
    elif change == "unsigned":
        answer = await client.post("/webhooks/slack/commands", content=body)
    else:
        at, signature = sign(body, "another-apps-secret")
        answer = await client.post(
            "/webhooks/slack/commands",
            content=body,
            headers={"X-Slack-Request-Timestamp": at, "X-Slack-Signature": signature},
        )
    assert answer.status_code == 401, answer.text
    assert answer.json()["error"]["code"] == "slack_signature_invalid"
    assert await queued(container) == []


async def test_a_replayed_call_queues_the_same_key(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    twin = twin_of(container)
    body = command_body()
    for _ in range(2):
        answer = await post_signed(client, twin, "/webhooks/slack/commands", body)
        assert answer.status_code == 200
    first, second = await queued(container)
    assert first.key == second.key, "the worker's handling dedupes on it"


async def test_the_events_url_answers_slacks_challenge(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    twin = twin_of(container)
    body = json.dumps({"type": "url_verification", "challenge": "3eZbrw1aB"}).encode()
    answer = await post_signed(client, twin, "/webhooks/slack/events", body)
    assert answer.status_code == 200 and answer.json() == {"challenge": "3eZbrw1aB"}
    refused = await client.post("/webhooks/slack/events", content=body)
    assert refused.status_code == 401
    assert await queued(container) == []


async def test_an_event_is_queued_on_its_id_and_a_retry_keeps_it(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    twin = twin_of(container)
    body = json.dumps(
        {
            "type": "event_callback",
            "team_id": "T0AJAX",
            "event_id": "Ev08MFMKH6",
            "event": {"type": "app_mention", "channel": "C0TEAM", "ts": "1.0"},
        }
    ).encode()
    first = await post_signed(client, twin, "/webhooks/slack/events", body)
    retry = await post_signed(
        client,
        twin,
        "/webhooks/slack/events",
        body,
        headers={"X-Slack-Retry-Num": "1", "X-Slack-Retry-Reason": "http_timeout"},
    )
    assert first.status_code == retry.status_code == 200 and first.json() == {}
    one, two = await queued(container)
    assert one.key == two.key == delivery_key("Ev08MFMKH6")
    assert (one.retry_num, two.retry_num) == (0, 1)


async def test_the_bot_invited_to_a_channel_is_queued_for_the_worker(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    twin = twin_of(container)
    joined = twin.invite_bot("T0AJAX", "C0TEAM")
    answer = await post_signed(client, twin, "/webhooks/slack/events", json.dumps(joined).encode())
    assert answer.status_code == 200 and answer.json() == {}
    [delivery] = await queued(container)
    assert delivery.kind == "event" and delivery.key == delivery_key(joined["event_id"])
    assert delivery.payload["event"]["type"] == "member_joined_channel"
    assert delivery.payload["event"]["user"] == "UBOTT0AJAX"


async def test_without_the_apps_credentials_slack_is_turned_away(tmp_path: Path) -> None:
    container = build_container(tmp_path, slack_backend="slack")
    app = create_app(container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            answer = await client.post("/webhooks/slack/commands", content=command_body())
            assert answer.status_code == 503
            assert answer.json()["error"]["code"] == "slack_unavailable"


# The install.


async def start(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    started = await client.post("/v1/slack/installation", headers=headers)
    assert started.status_code == 201, started.text
    return started.json()["url"]


async def back_from_slack(client: httpx.AsyncClient, url: str) -> str:
    """Follows the twin's install page back to the API's callback, as the
    browser does; answers where the callback sends it."""
    parts = urlsplit(url)
    back = await client.get(f"{parts.path}?{parts.query}")
    assert back.status_code == 302, back.text
    return back.headers["location"]


async def test_an_owner_installs_and_a_member_reads(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    status = await client.get("/v1/slack/installation", headers=owner)
    assert status.status_code == 200 and status.json() == {"installation": None}
    location = await back_from_slack(client, await start(client, owner))
    assert location == f"{PORTAL}/settings?slack=installed"
    installed = (await client.get("/v1/slack/installation", headers=owner)).json()
    assert installed["installation"]["team_id"] == "TTWIN0001"
    assert installed["installation"]["channel_id"] is None
    assert "token" not in json.dumps(installed) and "credential" not in json.dumps(installed)

    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    await add_member(container, org_id, "bob@example.test", Role.MEMBER)
    bob = await sign_in_as(client, "bob@example.test", org_id)
    assert (await client.get("/v1/slack/installation", headers=bob)).status_code == 200
    refused = await client.post("/v1/slack/installation", headers=bob)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_authorized"
    assert (await client.delete("/v1/slack/installation", headers=bob)).status_code == 403

    gone = await client.delete("/v1/slack/installation", headers=owner)
    assert gone.status_code == 200 and gone.json() == {"installation": None}
    assert twin_of(container).uninstalled == ["TTWIN0001"]


async def test_a_state_that_is_forged_reused_or_missing_installs_nothing(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    url = await start(client, owner)
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    forged = await client.get(parts.path, params={"code": query["code"], "state": "made-up"})
    assert forged.headers["location"] == f"{PORTAL}/settings?slack=expired"
    assert (await back_from_slack(client, url)).endswith("slack=installed")
    assert (await back_from_slack(client, url)).endswith("slack=expired"), "a state works once"
    missing = await client.get(parts.path, params={"code": query["code"]})
    assert missing.headers["location"].endswith("slack=expired")


async def test_a_person_who_cancels_on_slacks_page_comes_back_told_so(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    url = await start(client, owner)
    parts = urlsplit(url)
    state = parse_qs(parts.query)["state"][0]
    back = await client.get(parts.path, params={"error": "access_denied", "state": state})
    assert back.headers["location"] == f"{PORTAL}/settings?slack=cancelled"
    status = await client.get("/v1/slack/installation", headers=owner)
    assert status.json() == {"installation": None}


async def test_a_workspace_another_org_holds_is_refused(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    assert (await back_from_slack(client, await start(client, owner))).endswith("installed")
    fabrikam, _ = await container.managers.tenancy.bootstrap(
        seed_request(), "Fabrikam", "fabrikam", "zoe@fab.test", "Zoe"
    )
    zoe = await sign_in_as(client, "zoe@fab.test", fabrikam.org_id)
    taken = await back_from_slack(client, await start(client, zoe))
    assert taken == f"{PORTAL}/settings?slack=taken"
    assert (await client.get("/v1/slack/installation", headers=zoe)).json() == {
        "installation": None
    }
