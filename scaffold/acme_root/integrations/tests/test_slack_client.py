"""The Slack client's own contract: Slack's answers become the errors a
caller decides on; the real client makes each call under the token it is
handed and never carries it in what it raises; a command's answer goes to
Slack's own address alone; an install's code and a refresh become tokens;
a call in is checked against the signing secret over its raw body, in
constant time, inside five minutes; a call a request makes ends at the
request's deadline; the twin signs the way Slack does, stays local, issues
tokens that renew once, refuses a channel its bot was removed from and
hands back the event of its invite, and fails on request; and the off
client reaches Slack for nothing."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.signature import SignatureVerifier
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from acme.infra.base import utcnow
from acme.infra.deadline import PASSED
from acme.infra.exceptions import InfraException
from acme.integrations.slack import (
    BOT_SCOPES,
    SlackChannelUnusable,
    SlackFailed,
    SlackNotConfigured,
    SlackRateLimited,
    SlackRequestRefused,
    SlackTokenRevoked,
    error_for,
    is_response_url,
)
from acme.integrations.slack.off import SlackOffImpl
from acme.integrations.slack.requests import (
    command_of,
    delivery_key,
    event_of,
    inbound_command,
    inbound_event,
    sign,
    verify,
)
from acme.integrations.slack.twin import TWIN_SIGNING_SECRET, SlackTwinImpl
from acme.integrations.slack.web import SlackWebImpl

TOKEN = "xoxe.xoxb-000-000-not-a-real-token"
SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
CLIENT_SECRET = "not-a-real-client-secret"


def test_slacks_answers_become_the_errors_a_caller_decides_on() -> None:
    limited = error_for("ratelimited", timedelta(seconds=7))
    assert isinstance(limited, SlackRateLimited) and limited.retry_after == timedelta(seconds=7)
    assert isinstance(error_for("ratelimited"), SlackRateLimited)
    for code in ("channel_not_found", "not_in_channel", "is_archived"):
        unusable = error_for(code)
        assert isinstance(unusable, SlackChannelUnusable) and unusable.slack_code == code
    for code in ("invalid_auth", "token_revoked", "account_inactive", "invalid_refresh_token"):
        assert isinstance(error_for(code), SlackTokenRevoked)
    other = error_for("internal_error")
    assert type(other) is SlackFailed and other.slack_code == "internal_error"
    assert isinstance(other, InfraException)


def test_only_slacks_own_address_answers_a_command() -> None:
    assert is_response_url("https://hooks.slack.com/commands/T0/1/abc")
    assert not is_response_url("https://hooks.slack.com.example.test/commands")
    assert not is_response_url("http://169.254.169.254/latest/meta-data/")


# The signature.


def test_a_request_signed_the_way_slack_documents_passes_the_sdks_check() -> None:
    body = b"token=x&team_id=T0&command=%2Facme&text=add+milk&trigger_id=1.2.3"
    at, signature = sign(body, SECRET)
    assert signature.startswith("v0=")
    assert SignatureVerifier(SECRET).is_valid(body, at, signature)
    verify(body, at, signature, SECRET)


def test_slacks_documented_example_signs_to_its_documented_signature() -> None:
    # The example of Slack's "Verifying requests from Slack" page.
    body = (
        b"token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow&"
        b"channel_id=G8PSS9T3V&channel_name=foobar&user_id=U2CERLKJA&user_name=roadrunner&"
        b"command=%2Fwebhook-collect&text=&response_url=https%3A%2F%2Fhooks.slack.com%2F"
        b"commands%2FT1DC2JH3J%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN&"
        b"trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c"
    )
    _, signature = sign(body, SECRET, 1531420618)
    assert signature == "v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("body", "bad_signature"),
        ("secret", "bad_signature"),
        ("stale", "bad_signature"),
        ("future", "bad_signature"),
        ("unsigned", "unsigned"),
        ("garbage", "bad_timestamp"),
    ],
)
def test_a_request_that_does_not_check_out_is_refused(change: str, reason: str) -> None:
    body = b'{"type":"event_callback","event_id":"Ev1"}'
    now = int(time.time())
    at, signature = sign(body, SECRET, now)
    timestamp: str | None = at
    if change == "body":
        body = body.replace(b"Ev1", b"Ev2")
    elif change == "secret":
        _, signature = sign(body, "another-secret", now)
    elif change == "stale":
        timestamp, signature = sign(body, SECRET, now - 301)
    elif change == "future":
        timestamp, signature = sign(body, SECRET, now + 301)
    elif change == "unsigned":
        signature = ""
    elif change == "garbage":
        timestamp = "yesterday"
    with pytest.raises(SlackRequestRefused) as refused:
        verify(body, timestamp, signature, SECRET)
    assert refused.value.slack_code == reason and refused.value.http_status == 401
    assert SECRET not in str(refused.value)


def test_a_request_just_inside_the_window_passes() -> None:
    body = b"x"
    at, signature = sign(body, SECRET, int(time.time()) - 290)
    verify(body, at, signature, SECRET)


# What a call carries, and its key.


def test_a_command_is_keyed_on_its_trigger_and_an_event_on_its_id() -> None:
    fields = command_of(
        urlencode(
            {"command": "/acme", "text": "add milk", "team_id": "T0", "trigger_id": "1.2.3"}
        ).encode()
    )
    assert fields["text"] == "add milk"
    at = datetime.now(UTC)
    assert inbound_command(fields, at, 0).key == delivery_key("1.2.3")
    first = inbound_event({"type": "event_callback", "event_id": "Ev9"}, at, 0)
    retry = inbound_event({"type": "event_callback", "event_id": "Ev9"}, at, 2)
    assert first.key == retry.key and retry.retry_num == 2, "a retry keeps its key"
    with pytest.raises(SlackRequestRefused):
        command_of(b"command=%2Facme&team_id=T0")  # no trigger to key it on
    with pytest.raises(SlackRequestRefused):
        event_of(b"not json")
    with pytest.raises(SlackRequestRefused):
        inbound_event({"type": "event_callback"}, at, 0)


# The real client.


def answer_of(status: int, data: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    return AsyncSlackResponse(
        client=None,
        http_verb="POST",
        api_url="https://slack.com/api/x",
        req_args={},
        data=data,
        headers=headers or {},
        status_code=status,
    )


def refusal(status: int, error: str, headers: dict[str, str] | None = None) -> SlackApiError:
    return SlackApiError(error, answer_of(status, {"ok": False, "error": error}, headers))


async def started(
    monkeypatch: pytest.MonkeyPatch,
    answer: Any,
    seen: list[tuple[str, str | None, Any]] | None = None,
) -> SlackWebImpl:
    """The real client with its Web API call replaced: `answer` is raised when
    it is an exception and returned otherwise; each call is noted in `seen`
    with the token it was made under."""

    async def api_call(self: AsyncWebClient, method: str, **kwargs: Any) -> Any:
        if seen is not None:
            seen.append((method, self.token, kwargs.get("json") or kwargs.get("data")))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(AsyncWebClient, "api_call", api_call)
    client = SlackWebImpl("111.222", CLIENT_SECRET, SECRET, timedelta(seconds=5))
    await client.start()
    return client


async def test_each_call_is_made_under_the_token_it_is_handed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str | None, Any]] = []
    client = await started(monkeypatch, {"ok": True, "ts": "1727000000.000100"}, seen)
    try:
        assert await client.post_message(TOKEN, "C0TEAM", "hello") == "1727000000.000100"
        await client.post_message("xoxe.xoxb-other", "C0TEAM", "hi", thread_ts="1.0")
    finally:
        await client.close()
    assert [(method, token) for method, token, _ in seen] == [
        ("chat.postMessage", TOKEN),
        ("chat.postMessage", "xoxe.xoxb-other"),
    ]
    assert seen[1][2]["thread_ts"] == "1.0"


async def test_an_install_code_becomes_the_workspaces_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str | None, Any]] = []
    answer = {
        "ok": True,
        "access_token": TOKEN,
        "refresh_token": "xoxe-1-refresh",
        "expires_in": 43200,
        "token_type": "bot",
        "scope": "commands,chat:write",
        "bot_user_id": "UBOT",
        "app_id": "A0APP",
        "team": {"id": "T0AJAX", "name": "Ajax"},
        "authed_user": {"id": "U0ANN"},
    }
    client = await started(monkeypatch, answer, seen)
    try:
        grant = await client.exchange_code("the-code", "https://api.test/webhooks/slack/oauth")
        renewed = await client.refresh("xoxe-1-refresh")
    finally:
        await client.close()
    assert (grant.team_id, grant.team_name, grant.bot_user_id) == ("T0AJAX", "Ajax", "UBOT")
    assert grant.scopes == ("commands", "chat:write") and grant.installer_user_id == "U0ANN"
    assert grant.tokens.access_token.get_secret_value() == TOKEN
    assert grant.tokens.expires_at is not None
    assert TOKEN not in repr(grant), "a token never shows in a repr"
    assert seen[0][2]["code"] == "the-code" and seen[0][2]["client_secret"] == CLIENT_SECRET
    assert seen[1][2]["grant_type"] == "refresh_token"
    assert renewed.refresh_token is not None


def test_the_install_page_asks_for_the_bot_scopes_and_carries_the_state() -> None:
    client = SlackWebImpl("111.222", CLIENT_SECRET, SECRET, timedelta(seconds=5))
    url = client.authorize_url("the-state", "https://api.test/webhooks/slack/oauth")
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://slack.com/oauth/v2/authorize"
    assert query == {
        "client_id": "111.222",
        "scope": ",".join(BOT_SCOPES),
        "state": "the-state",
        "redirect_uri": "https://api.test/webhooks/slack/oauth",
    }
    assert CLIENT_SECRET not in url


async def test_the_email_is_read_from_a_persons_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    person = {"ok": True, "user": {"id": "U0ANN", "profile": {"email": "ann@ajax.test"}}}
    client = await started(monkeypatch, person)
    try:
        assert await client.user_email(TOKEN, "U0ANN") == "ann@ajax.test"
    finally:
        await client.close()
    bot = {"ok": True, "user": {"id": "UBOT", "is_bot": True, "profile": {}}}
    client = await started(monkeypatch, bot)
    try:
        assert await client.user_email(TOKEN, "UBOT") is None
    finally:
        await client.close()


async def test_a_rate_limit_carries_the_wait_slack_named(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await started(monkeypatch, refusal(429, "ratelimited", {"Retry-After": "12"}))
    try:
        with pytest.raises(SlackRateLimited) as raised:
            await client.post_message(TOKEN, "C0TEAM", "hello")
        assert raised.value.retry_after == timedelta(seconds=12)
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (200, "not_in_channel", SlackChannelUnusable),
        (200, "channel_not_found", SlackChannelUnusable),
        (200, "is_archived", SlackChannelUnusable),
        (200, "invalid_auth", SlackTokenRevoked),
        (200, "internal_error", SlackFailed),
        (500, "", SlackFailed),
    ],
)
async def test_a_refusal_is_translated_and_never_carries_the_token(
    monkeypatch: pytest.MonkeyPatch, status: int, error: str, expected: type[Exception]
) -> None:
    client = await started(monkeypatch, refusal(status, error))
    try:
        with pytest.raises(expected) as raised:
            await client.post_message(TOKEN, "C0TEAM", "hello")
        assert TOKEN not in str(raised.value) and raised.value.__cause__ is None
    finally:
        await client.close()


async def test_an_uninstall_of_an_app_already_gone_is_done(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await started(monkeypatch, refusal(200, "invalid_auth"))
    try:
        await client.uninstall(TOKEN)
        await client.revoke(TOKEN)
    finally:
        await client.close()


async def test_a_transport_failure_is_a_failure_worth_a_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = await started(monkeypatch, TimeoutError("read timed out"))
    try:
        with pytest.raises(SlackFailed) as raised:
            await client.publish_home(TOKEN, "U0ANN", {"type": "home", "blocks": []})
        assert raised.value.slack_code == "TimeoutError"
    finally:
        await client.close()


class SilentSlack:
    """A server that takes a call and never answers: Slack hanging."""

    def __init__(self) -> None:
        self.calls = 0

    async def _hold(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.calls += 1
        try:
            while await reader.read(65536):
                pass
        finally:
            writer.close()

    @asynccontextmanager
    async def serving(self) -> AsyncIterator[str]:
        server = await asyncio.start_server(self._hold, "127.0.0.1", 0)
        try:
            yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/api/"
        finally:
            server.close()


@pytest.mark.parametrize("seconds", [0.4, 1.5])
async def test_a_call_slack_never_answers_ends_at_the_timeout_from_settings(
    monkeypatch: pytest.MonkeyPatch, seconds: float
) -> None:
    """Every call rides the one session, and the session carries the timeout
    to the fraction: in whole seconds, 1.5 would end at 1, and 0.4 would be
    zero, which aiohttp takes as no timeout at all. A timeout is not tried
    again."""
    slack = SilentSlack()
    async with slack.serving() as base_url:
        made = AsyncWebClient.__init__

        def aimed(self: AsyncWebClient, *args: Any, **kwargs: Any) -> None:
            made(self, *args, base_url=base_url, **kwargs)

        monkeypatch.setattr(AsyncWebClient, "__init__", aimed)
        client = SlackWebImpl("111.222", CLIENT_SECRET, SECRET, timedelta(seconds=seconds))
        await client.start()
        began = time.monotonic()
        try:
            with pytest.raises(SlackFailed) as raised:
                await asyncio.wait_for(client.user_email(TOKEN, "U0ANN"), seconds + 5)
        finally:
            await client.close()
        waited = time.monotonic() - began
    assert raised.value.slack_code == "TimeoutError"
    assert waited >= seconds and slack.calls == 1


async def test_a_call_a_request_makes_ends_at_its_deadline_not_at_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session's timeout is the process's, ten seconds here; the request
    has four tenths of a second left, and the install's exchange ends then,
    as a failure the install's page reports."""
    slack = SilentSlack()
    async with slack.serving() as base_url:
        made = AsyncWebClient.__init__

        def aimed(self: AsyncWebClient, *args: Any, **kwargs: Any) -> None:
            made(self, *args, base_url=base_url, **kwargs)

        monkeypatch.setattr(AsyncWebClient, "__init__", aimed)
        client = SlackWebImpl("111.222", CLIENT_SECRET, SECRET, timedelta(seconds=10))
        await client.start()
        began = time.monotonic()
        try:
            with pytest.raises(SlackFailed) as raised:
                deadline = utcnow() + timedelta(seconds=0.4)
                await asyncio.wait_for(
                    client.exchange_code("code", "https://api.example/oauth", deadline=deadline),
                    5,
                )
        finally:
            await client.close()
        waited = time.monotonic() - began
    assert raised.value.slack_code == "deadline"
    assert raised.value.message == PASSED
    assert 0.35 <= waited < 1.5, waited
    # The exchange, cut at the deadline, and no attempt after it; none at all
    # on a runner slow enough that the call's own work takes the whole of it.
    assert slack.calls <= 1


async def test_the_web_client_refuses_a_foreign_response_url_before_any_request() -> None:
    client = SlackWebImpl("111.222", CLIENT_SECRET, SECRET, timedelta(seconds=5))
    with pytest.raises(SlackFailed) as raised:
        await client.respond("https://attacker.example.test/hook", "hi")
    assert raised.value.slack_code == "invalid_response_url"


async def test_the_web_client_is_not_used_before_start() -> None:
    client = SlackWebImpl("111.222", CLIENT_SECRET, SECRET, timedelta(seconds=5))
    with pytest.raises(RuntimeError):
        await client.post_message(TOKEN, "C0TEAM", "hello")


def test_the_web_client_checks_calls_with_its_signing_secret() -> None:
    client = SlackWebImpl("111.222", CLIENT_SECRET, SECRET, timedelta(seconds=5))
    body = b"payload"
    client.verify_request(body, *sign(body, SECRET))
    with pytest.raises(SlackRequestRefused):
        client.verify_request(body, *sign(body, TWIN_SIGNING_SECRET))


# The twin.


def test_the_twin_runs_locally_only() -> None:
    for environment in ("local", "test"):
        assert SlackTwinImpl(environment).describe() == "slack=twin"
    for environment in ("staging", "production"):
        with pytest.raises(ValueError):
            SlackTwinImpl(environment)


async def test_the_twin_installs_renews_once_and_revokes() -> None:
    twin = SlackTwinImpl("test")
    grant = await twin.exchange_code(twin.approve("T0AJAX", "U0ANN"), "https://r")
    with pytest.raises(SlackFailed):
        await twin.exchange_code("twin-code-unknown", "https://r")
    token = grant.tokens.access_token.get_secret_value()
    refresh = grant.tokens.refresh_token
    assert refresh is not None
    assert twin.team_of(token) == "T0AJAX"
    renewed = await twin.refresh(refresh.get_secret_value())
    with pytest.raises(SlackTokenRevoked):
        await twin.refresh(refresh.get_secret_value())
    fresh = renewed.access_token.get_secret_value()
    assert await twin.post_message(fresh, "C0TEAM", "hello")
    await twin.uninstall(fresh)
    assert twin.uninstalled == ["T0AJAX"]
    with pytest.raises(SlackTokenRevoked):
        await twin.post_message(fresh, "C0TEAM", "after")


async def test_the_twins_bot_removed_from_a_channel_is_invited_back_by_an_event() -> None:
    twin = SlackTwinImpl("test")
    grant = await twin.exchange_code(twin.approve("T0AJAX", "U0ANN"), "https://r")
    token = grant.tokens.access_token.get_secret_value()
    twin.remove_bot("T0AJAX", "C0TEAM")
    with pytest.raises(SlackChannelUnusable) as refused:
        await twin.post_message(token, "C0TEAM", "while it is out")
    assert refused.value.slack_code == "not_in_channel"
    assert await twin.post_message(token, "C0OTHER", "another channel still works")
    joined = twin.invite_bot("T0AJAX", "C0TEAM")
    assert joined["event"] == {
        "type": "member_joined_channel",
        "user": grant.bot_user_id,
        "channel": "C0TEAM",
        "channel_type": "C",
        "team": "T0AJAX",
        "inviter": "UTWIN0001",
    }
    delivery = inbound_event(event_of(json.dumps(joined).encode()), utcnow(), 0)
    assert delivery.key == delivery_key(joined["event_id"])
    assert await twin.post_message(token, "C0TEAM", "back in")


async def test_a_token_one_twin_issued_works_in_another_process() -> None:
    api, worker = SlackTwinImpl("local"), SlackTwinImpl("local")
    grant = await api.exchange_code(api.approve("T0AJAX", "U0ANN"), "https://r")
    token = grant.tokens.access_token.get_secret_value()
    assert await worker.post_message(token, "C0TEAM", "hello")
    assert worker.posts[-1].team_id == "T0AJAX"


def test_the_twins_install_page_approves_at_once_and_sends_the_browser_back() -> None:
    url = SlackTwinImpl("local").authorize_url("the-state", "http://127.0.0.1:8000/cb")
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "http://127.0.0.1:8000/cb"
    assert query["state"] == "the-state" and query["code"].startswith("twin-code-")


async def test_the_twin_signs_as_slack_does_and_fails_on_request() -> None:
    twin = SlackTwinImpl("test")
    body = b'{"type":"url_verification","challenge":"c"}'
    headers = twin.signed(body)
    twin.verify_request(body, headers["X-Slack-Request-Timestamp"], headers["X-Slack-Signature"])
    assert SignatureVerifier(TWIN_SIGNING_SECRET).is_valid_request(body, headers)
    grant = await twin.exchange_code(twin.approve("T0AJAX", "U0ANN"), "https://r")
    token = grant.tokens.access_token.get_secret_value()
    twin.fail_next(error_for("not_in_channel"), "chat.postMessage")
    twin.add_user("T0AJAX", "U0ANN", "ann@ajax.test")
    assert await twin.user_email(token, "U0ANN") == "ann@ajax.test", "only the post fails"
    with pytest.raises(SlackChannelUnusable):
        await twin.post_message(token, "C0TEAM", "three")
    await twin.post_message(token, "C0TEAM", "four")
    with pytest.raises(SlackFailed):
        await twin.respond("https://attacker.example.test/hook", "hi")
    await twin.respond("https://hooks.slack.com/commands/T0/1/abc", "hi")
    assert twin.responses == [("https://hooks.slack.com/commands/T0/1/abc", "hi")]


async def test_the_off_client_reaches_slack_for_nothing_and_says_why() -> None:
    off = SlackOffImpl()
    assert off.describe() == "slack=off"
    with pytest.raises(SlackNotConfigured) as refused:
        off.verify_request(b"x", "1", "v0=0")
    assert refused.value.http_status == 503
    with pytest.raises(SlackNotConfigured):
        off.authorize_url("s", "https://r")
    with pytest.raises(SlackNotConfigured):
        await off.post_message(TOKEN, "C0TEAM", "hello")
    with pytest.raises(SlackNotConfigured):
        await off.respond("https://hooks.slack.com/commands/T0/1/abc", "hi")
