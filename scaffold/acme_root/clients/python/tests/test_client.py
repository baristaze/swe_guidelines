"""The transport client over a mock transport: what it sends, how it turns a
refusal into a typed error, and which call it sends again."""

import json
from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest

from acme.client.client import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_BACKOFF_SECONDS,
    ApiClient,
    ApiError,
    may_retry,
    retry_after_seconds,
    retry_delay_seconds,
    retry_wait_seconds,
)
from acme.client.realtime import Channel
from acme.client.types import FileView, OrgKind, Role

ORG = {
    "id": "0199a4c0-0000-7000-8000-0000000000bb",
    "name": "Ajax",
    "slug": "ajax",
    "kind": "team",
    "created_at": "2026-09-18T12:00:00Z",
    "deleted_at": None,
}

USER = {
    "id": "0199a4c0-0000-7000-8000-0000000000cc",
    "email": "bob@example.test",
    "display_name": "Bob",
    "created_at": "2026-09-18T12:00:00Z",
}

ME = {"app": "cli", "org": ORG, "user": USER, "role": "owner", "permissions": ["read", "write"]}

FILE = {
    "id": "0199a4c0-0000-7000-8000-0000000000ee",
    "name": "spec.pdf",
    "extension": "pdf",
    "content_type": "application/pdf",
    "size_bytes": 17,
    "purpose": "upload",
    "subject_id": None,
    "status": "pending",
    "created_at": "2026-09-18T12:00:00Z",
    "created_by": USER["id"],
    "deleted_at": None,
}

EVENT = {
    "seq": 1,
    "kind": "tenancy.invitation.created",
    "target_id": "0199a4c0-0000-7000-8000-0000000000dd",
    "produced_at": "2026-09-18T12:00:00Z",
    "actor_id": USER["id"],
}


class Recorder:
    def __init__(self, respond: dict[str, httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.respond = respond or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond.get(request.url.path, httpx.Response(200, json=ME))


def client_over(recorder: Recorder, token: str | None = "ses_1") -> ApiClient:
    return ApiClient(
        "http://test/",
        app="cli",
        app_version="cli@test",
        token=token,
        transport=httpx.MockTransport(recorder),
    )


async def test_every_request_carries_bearer_and_app_headers() -> None:
    recorder = Recorder()
    async with client_over(recorder) as client:
        await client.me()
    sent = recorder.requests[0]
    assert sent.headers["authorization"] == "Bearer ses_1"
    assert sent.headers["x-app"] == "cli" and sent.headers["x-app-version"] == "cli@test"
    assert sent.headers["accept"] == "application/json"
    assert client.base_url == "http://test"


async def test_a_creating_call_always_sends_an_idempotency_key() -> None:
    recorder = Recorder({"/v1/media/files": httpx.Response(201, json=FILE)})
    async with client_over(recorder) as client:
        started = await client.start_upload("spec.pdf", "application/pdf", 17)
        await client.start_upload("spec.pdf", "application/pdf", 17, idempotency_key="given")
    minted, given = recorder.requests
    UUID(minted.headers["idempotency-key"])  # a fresh uuid when none is given
    assert given.headers["idempotency-key"] == "given"
    assert minted.method == "POST" and minted.url.path == "/v1/media/files"
    assert json.loads(minted.read()) == {
        "name": "spec.pdf",
        "content_type": "application/pdf",
        "size_bytes": 17,
    }
    assert started.id == UUID(FILE["id"]) and started.status.value == "pending"


async def test_a_versioned_write_names_its_version_in_if_match() -> None:
    recorder = Recorder()
    async with client_over(recorder) as client:
        await client.request("PATCH", "/v1/me", json={"display_name": "Ann"}, if_match=3)
        await client.me()
    versioned, plain = recorder.requests
    assert versioned.headers["if-match"] == '"3"'
    assert "if-match" not in plain.headers


async def test_the_sign_in_flow_uses_the_credential_it_is_given() -> None:
    login = {
        "token": "lgn_1",
        "expires_at": "2026-09-18T13:00:00Z",
        "memberships": [
            {
                "org": {
                    "id": str(uuid4()),
                    "name": "Ajax",
                    "slug": "ajax",
                    "kind": "team",
                    "created_at": "2026-09-18T12:00:00Z",
                    "deleted_at": None,
                },
                "user": {
                    "id": str(uuid4()),
                    "email": "ann@example.test",
                    "display_name": "Ann",
                    "created_at": "2026-09-18T12:00:00Z",
                },
                "role": "owner",
            }
        ],
    }
    session = {
        "token": "ses_2",
        "expires_at": "2026-09-19T12:00:00Z",
        "org": login["memberships"][0]["org"],
        "user": login["memberships"][0]["user"],
        "role": "owner",
    }
    recorder = Recorder(
        {
            "/v1/auth/dev-sign-in": httpx.Response(200, json=login),
            "/v1/auth/sessions": httpx.Response(200, json=session),
        }
    )
    async with client_over(recorder, token=None) as client:
        issued = await client.dev_sign_in("ann@example.test")
        exchanged = await client.exchange_session(issued.token, issued.memberships[0].org.id)
    assert exchanged.token == "ses_2" and exchanged.org.slug == "ajax"
    assert "authorization" not in recorder.requests[0].headers
    assert recorder.requests[1].headers["authorization"] == "Bearer lgn_1"


CHOICE = {"org": ORG, "user": USER, "role": "owner"}
LOGIN = {"token": "lgn_1", "expires_at": "2026-09-18T13:00:00Z", "memberships": [CHOICE]}


async def test_the_sign_in_calls_carry_no_bearer_and_no_key() -> None:
    """Before a sign-in there is no principal: no bearer, and no idempotency
    key, since the marker is a principal's."""
    device = {
        "device_code": "dc-1",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://auth.example.test/device",
        "verification_uri_complete": "https://auth.example.test/device?user_code=ABCD-EFGH",
        "expires_in": 300,
        "interval": 5,
    }
    recorder = Recorder(
        {
            "/v1/auth/sign-in": httpx.Response(
                200,
                json={
                    "authorization_url": "https://auth.example.test/authorize",
                    "code_verifier": "v" * 43,
                },
            ),
            "/v1/auth/callback": httpx.Response(200, json=LOGIN),
            "/v1/auth/device": httpx.Response(200, json=device),
            "/v1/auth/device/token": httpx.Response(200, json=LOGIN),
            "/v1/auth/dev-sign-in": httpx.Response(200, json=LOGIN),
        }
    )
    async with client_over(recorder, token="ses_stale") as client:
        started = await client.start_sign_in(
            "http://localhost:55173/auth/callback", "state-of-the-tab", invitation_token="inv"
        )
        called_back = await client.finish_sign_in("code-1", "v" * 43)
        begun = await client.start_device_sign_in()
        confirmed = await client.finish_device_sign_in(begun.device_code)
        local = await client.dev_sign_in("dee@example.test", "Dee")
    assert started.authorization_url == "https://auth.example.test/authorize"
    assert begun.user_code == "ABCD-EFGH" and begun.interval == 5
    assert called_back.token == confirmed.token == local.token == "lgn_1"
    for sent in recorder.requests:
        assert "authorization" not in sent.headers and "idempotency-key" not in sent.headers
    bodies = [json.loads(r.content) if r.content else None for r in recorder.requests]
    assert bodies == [
        {
            "redirect_uri": "http://localhost:55173/auth/callback",
            "state": "state-of-the-tab",
            "sign_up": False,
            "invitation_token": "inv",
        },
        {"code": "code-1", "code_verifier": "v" * 43},
        None,
        {"device_code": "dc-1"},
        {"email": "dee@example.test", "display_name": "Dee"},
    ]


async def test_a_pending_device_sign_in_is_a_typed_refusal_sent_once() -> None:
    pending = {"error": {"code": "sign_in_pending", "message": "not yet", "request_id": "r1"}}
    recorder = Recorder({"/v1/auth/device/token": httpx.Response(400, json=pending)})
    async with client_over(recorder, token=None) as client:
        with pytest.raises(ApiError) as refused:
            await client.finish_device_sign_in("dc-1")
    assert (refused.value.status, refused.value.code) == (400, "sign_in_pending")
    assert len(recorder.requests) == 1


async def test_the_second_factor_is_presented_with_the_sign_in() -> None:
    recorder = Recorder({"/v1/auth/second-factor": httpx.Response(200, json=LOGIN)})
    async with client_over(recorder, token=None) as client:
        verified = await client.verify_second_factor("lgn_0", "123456")
    [sent] = recorder.requests
    assert sent.headers["authorization"] == "Bearer lgn_0"
    assert json.loads(sent.content) == {"totp_code": "123456"}
    assert verified.token == "lgn_1"


INVITATION = {
    "id": "0199a4c0-0000-7000-8000-0000000000dd",
    "email": "bob@example.test",
    "role": "member",
    "state": "pending",
    "expires_at": "2026-09-25T12:00:00Z",
    "created_at": "2026-09-18T12:00:00Z",
    "created_by": USER["id"],
}


async def test_invitations_are_sent_under_a_key_and_managed_by_id() -> None:
    invitation_id = UUID(INVITATION["id"])
    recorder = Recorder(
        {
            "/v1/invitations": httpx.Response(201, json=INVITATION),
            f"/v1/invitations/{invitation_id}/resend": httpx.Response(200, json=INVITATION),
            f"/v1/invitations/{invitation_id}": httpx.Response(
                200, json=INVITATION | {"state": "revoked"}
            ),
            "/v1/orgs/current/sso-link": httpx.Response(
                200, json={"url": "https://auth.example.test/portal"}
            ),
        }
    )
    async with client_over(recorder) as client:
        sent = await client.invite_member("bob@example.test", Role.admin)
        await client.resend_invitation(invitation_id)
        revoked = await client.revoke_invitation(invitation_id)
        link = await client.sso_link("http://localhost:55173/settings")
    assert sent.email == "bob@example.test" and revoked.state.value == "revoked"
    assert link.url == "https://auth.example.test/portal"
    invite, resend, revoke, sso = recorder.requests
    UUID(invite.headers["idempotency-key"])
    assert json.loads(invite.content) == {"email": "bob@example.test", "role": "admin"}
    assert (resend.method, revoke.method) == ("POST", "DELETE")
    assert json.loads(sso.content) == {
        "intent": "sso",
        "return_url": "http://localhost:55173/settings",
    }


async def test_create_org_carries_a_key_and_the_slug_only_when_given() -> None:
    recorder = Recorder({"/v1/orgs": httpx.Response(201, json=CHOICE)})
    async with client_over(recorder, token="ses_1") as client:
        place = await client.create_org("Bakery")
        await client.create_org("Cafe", "cafe", idempotency_key="k-2")
    first, second = recorder.requests
    assert json.loads(first.content) == {"name": "Bakery"}
    assert first.headers["idempotency-key"]
    assert json.loads(second.content) == {"name": "Cafe", "slug": "cafe"}
    assert second.headers["idempotency-key"] == "k-2"
    assert place.org.kind is OrgKind.team and place.role.value == "owner"


async def test_the_memberships_are_read_page_after_page_with_the_session() -> None:
    pages = iter(
        [
            httpx.Response(200, json={"items": [CHOICE], "next_cursor": "c1"}),
            httpx.Response(200, json={"items": [CHOICE], "next_cursor": None}),
        ]
    )
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return next(pages)

    client = ApiClient(
        "http://test/",
        app="cli",
        app_version="cli@test",
        token="ses_1",
        transport=httpx.MockTransport(answer),
    )
    async with client:
        every = await client.every_membership(limit=1)
    assert len(every) == 2
    assert [r.url.params.get("cursor") for r in sent] == [None, "c1"]
    assert all(r.headers["authorization"] == "Bearer ses_1" for r in sent)


async def test_a_switch_presents_the_session_and_carries_the_new_one() -> None:
    switched = {
        "token": "ses_2",
        "expires_at": "2026-09-19T12:00:00Z",
        "org": ORG,
        "user": USER,
        "role": "member",
    }
    recorder = Recorder({"/v1/auth/sessions": httpx.Response(200, json=switched)})
    async with client_over(recorder, token="ses_1") as client:
        issued = await client.switch_session(UUID(ORG["id"]))
        assert client.token == "ses_2" and issued.role.value == "member"
        await client.me()
    assert recorder.requests[0].headers["authorization"] == "Bearer ses_1"
    assert recorder.requests[1].headers["authorization"] == "Bearer ses_2"
    async with client_over(recorder, token=None) as client:
        with pytest.raises(ApiError) as refused:
            await client.switch_session(UUID(ORG["id"]))
    assert refused.value.status == 401


async def test_a_refusal_is_a_typed_error_with_the_request_id() -> None:
    refusal = httpx.Response(
        404,
        json={"error": {"code": "not_found", "message": "file x not found", "request_id": "r-1"}},
        headers={"x-request-id": "r-1"},
    )
    recorder = Recorder({"/v1/media/files/" + FILE["id"]: refusal})
    async with client_over(recorder) as client:
        with pytest.raises(ApiError) as raised:
            await client.file(UUID(FILE["id"]))
    error = raised.value
    assert (error.status, error.code, error.request_id) == (404, "not_found", "r-1")
    assert str(error) == "not_found: file x not found (request r-1)"


async def test_a_401_clears_the_token_and_a_bare_error_still_types() -> None:
    recorder = Recorder({"/v1/me": httpx.Response(401, text="nope")})
    async with client_over(recorder) as client:
        with pytest.raises(ApiError) as raised:
            await client.me()
        assert client.token is None
    assert raised.value.code == "unknown_error" and raised.value.status == 401


def test_websocket_url_follows_the_scheme() -> None:
    plain = ApiClient("http://127.0.0.1:8000", app="cli", app_version="v")
    secure = ApiClient("https://api.acme.example/", app="cli", app_version="v")
    assert plain.websocket_url("/v1/realtime") == "ws://127.0.0.1:8000/v1/realtime"
    assert secure.websocket_url("/v1/realtime") == "wss://api.acme.example/v1/realtime"
    assert plain.headers == {"X-App": "cli", "X-App-Version": "v"}


def test_every_call_and_the_socket_open_carry_the_timeout() -> None:
    given = ApiClient("http://test", app="cli", app_version="v", timeout=4.5)
    assert given.timeout == 4.5 and given._http.timeout == httpx.Timeout(4.5)
    opening = cast(Any, Channel(given)._connect_default("ws://127.0.0.1:1/v1/realtime", {}))
    assert opening.open_timeout == 4.5  # built, not entered: nothing connects
    default = ApiClient("http://test", app="cli", app_version="v")
    assert default.timeout == DEFAULT_TIMEOUT_SECONDS
    assert default._http.timeout == httpx.Timeout(DEFAULT_TIMEOUT_SECONDS)


async def test_a_401_from_an_old_request_keeps_the_replacement_token() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer ses_old"
        client.token = "ses_new"  # another sign-in completed while this request was in flight
        return httpx.Response(401, text="nope")

    async with ApiClient(
        "http://test",
        app="cli",
        app_version="cli@test",
        token="ses_old",
        transport=httpx.MockTransport(respond),
    ) as client:
        with pytest.raises(ApiError):
            await client.me()
        assert client.token == "ses_new"


async def test_an_answer_that_is_not_json_is_a_typed_error() -> None:
    """Pointed at the portal's nginx, every path answers index.html with a
    200: that is said as `not_json`, never raised as a decoding traceback."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!doctype html>", headers={"x-request-id": "r-1"})

    async with ApiClient(
        "http://test", app="cli", app_version="cli@test", transport=httpx.MockTransport(respond)
    ) as client:
        with pytest.raises(ApiError) as refused:
            await client.me()
    assert refused.value.code == "not_json"
    assert refused.value.status == 200 and refused.value.request_id == "r-1"


# The retry. The waits are zero in the cases below: the curve is asserted on
# its own function, with the jitter handed in, so no test waits for a delay.


def retrying(handler: Any, **overrides: Any) -> ApiClient:
    settings: dict[str, Any] = {"retries": 2, "backoff_seconds": 0.0} | overrides
    return ApiClient(
        "http://test",
        app="cli",
        app_version="cli@test",
        token="ses_1",
        transport=httpx.MockTransport(handler),
        **settings,
    )


def answering(*responses: httpx.Response) -> tuple[Any, list[httpx.Request]]:
    """A handler that answers each call from the list, repeating the last one,
    and the requests it saw."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    return handle, seen


def test_which_request_may_be_sent_twice() -> None:
    assert may_retry("GET") and may_retry("get") and may_retry("HEAD")
    # A creating call only under the key the API records the outcome under.
    assert may_retry("POST", "key_1")
    assert not may_retry("POST") and not may_retry("POST", "")
    # Neither carries such a key, so a lost answer leaves the write in doubt.
    assert not may_retry("PATCH", "key_1") and not may_retry("DELETE", "key_1")


def test_the_delay_grows_and_carries_jitter() -> None:
    base = DEFAULT_BACKOFF_SECONDS
    assert retry_delay_seconds(1, base, lambda: 0.0) == 0.125
    assert retry_delay_seconds(1, base, lambda: 1.0) == 0.25
    # The shortest wait of an attempt is the longest wait of the one before it.
    assert retry_delay_seconds(2, base, lambda: 0.0) == 0.25
    assert retry_delay_seconds(2, base, lambda: 1.0) == 0.5
    assert retry_delay_seconds(3, base, lambda: 0.0) == 0.5
    spread = {retry_delay_seconds(2, base, lambda r=r: r) for r in (0.0, 0.25, 0.5, 0.75)}
    assert len(spread) == 4 and all(0.25 <= delay <= 0.5 for delay in spread)
    # The doubling stops at the cap, and the real source of randomness stays in.
    assert retry_delay_seconds(20, base, lambda: 1.0) == MAX_BACKOFF_SECONDS
    assert all(0.125 <= retry_delay_seconds(1, base) <= 0.25 for _ in range(50))


async def test_an_unavailable_answer_is_retried_to_the_bound_and_then_surfaces() -> None:
    handler, seen = answering(
        httpx.Response(503, json={"error": {"code": "unavailable", "message": "no"}})
    )
    async with retrying(handler) as client:
        with pytest.raises(ApiError) as raised:
            await client.me()
    assert len(seen) == 3  # the first attempt and the two the bound allows
    assert raised.value.status == 503 and raised.value.code == "unavailable"


async def test_the_retry_stops_as_soon_as_an_attempt_answers() -> None:
    handler, seen = answering(httpx.Response(503), httpx.Response(200, json=ME))
    async with retrying(handler) as client:
        me = await client.me()
    assert me.user.email == "bob@example.test" and len(seen) == 2


def raising(failure: httpx.TransportError) -> tuple[Any, list[httpx.Request]]:
    """A handler that never answers, and the requests it saw."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise failure

    return handle, seen


@pytest.mark.parametrize(
    ("failure", "attempts"),
    [
        (httpx.ReadTimeout("slow"), 3),
        (httpx.ConnectError("refused"), 3),
        (httpx.UnsupportedProtocol("gopher"), 1),
    ],
    ids=["timed out", "refused", "a scheme that will never work"],
)
async def test_the_wire_failures_that_can_differ_are_retried_and_no_others(
    failure: httpx.TransportError, attempts: int
) -> None:
    handler, seen = raising(failure)
    async with retrying(handler) as client:
        with pytest.raises(httpx.TransportError):
            await client.me()
    assert len(seen) == attempts


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429, 500])
async def test_a_refusal_is_sent_once(status: int) -> None:
    """A decision does not change because it is asked for again."""
    handler, seen = answering(
        httpx.Response(status, json={"error": {"code": "no", "message": "no"}})
    )
    async with retrying(handler) as client:
        with pytest.raises(ApiError):
            await client.me()
    assert len(seen) == 1


async def test_a_creating_call_is_retried_under_the_key_the_first_attempt_carried() -> None:
    handler, seen = answering(httpx.Response(503), httpx.Response(201, json=CHOICE))
    async with retrying(handler) as client:
        await client.create_org("Bakery")
    assert len(seen) == 2
    assert len({request.headers["idempotency-key"] for request in seen}) == 1


async def test_a_write_the_api_records_no_outcome_for_is_sent_once() -> None:
    """No key, no record of the outcome: a second attempt could write twice,
    so the failure is told to the caller instead."""
    handler, seen = answering(httpx.Response(503))
    async with retrying(handler) as client:
        file_id = UUID(FILE["id"])
        for call in (
            client.request("PATCH", "/v1/me", json={"display_name": "Ann"}, if_match=1),
            client.delete_file(file_id),
            client.confirm_file(file_id),
            client.logout(),
        ):
            with pytest.raises(ApiError):
                await call
    assert len(seen) == 4


async def test_the_count_and_the_delay_arrive_through_the_constructor() -> None:
    default = ApiClient("http://test", app="cli", app_version="v")
    assert default.retries == DEFAULT_RETRIES
    assert default.backoff_seconds == DEFAULT_BACKOFF_SECONDS
    handler, seen = answering(httpx.Response(503))
    async with retrying(handler, retries=4) as client:
        with pytest.raises(ApiError):
            await client.me()
    assert len(seen) == 5
    handler, seen = answering(httpx.Response(503))
    async with retrying(handler, retries=0) as client:
        with pytest.raises(ApiError):
            await client.me()
    assert len(seen) == 1


# Media: the org's files. The in-process store cannot sign a form or a link,
# so the bytes go through the API's content route both ways.


async def test_the_files_are_paged_with_the_cursor_they_are_given() -> None:
    stored = FILE | {"status": "stored"}
    page = {"items": [stored], "next_cursor": "c-2"}
    recorder = Recorder({"/v1/media/files": httpx.Response(200, json=page)})
    async with client_over(recorder) as client:
        first = await client.files()
        await client.files(cursor=first.next_cursor, limit=5)
    assert [f.name for f in first.items] == ["spec.pdf"] and first.next_cursor == "c-2"
    listed, following = recorder.requests
    assert listed.method == "GET" and listed.url.path == "/v1/media/files"
    assert dict(listed.url.params) == {"limit": "200"}
    assert dict(following.url.params) == {"limit": "5", "cursor": "c-2"}


async def test_a_file_is_read_and_deleted_by_its_id_and_the_usage_read() -> None:
    file_id = UUID(FILE["id"])
    usage = {"total_count": 0, "total_size_bytes": 0, "pending_size_bytes": 0, "purposes": []}
    recorder = Recorder(
        {
            f"/v1/media/files/{file_id}": httpx.Response(200, json=FILE),
            "/v1/media/usage": httpx.Response(200, json=usage),
        }
    )
    async with client_over(recorder) as client:
        read = await client.file(file_id)
        removed = await client.delete_file(file_id)
        counted = await client.storage_usage()
    assert read.id == removed.id == file_id and counted.total_count == 0
    assert [(r.method, r.url.path) for r in recorder.requests] == [
        ("GET", f"/v1/media/files/{file_id}"),
        ("DELETE", f"/v1/media/files/{file_id}"),
        ("GET", "/v1/media/usage"),
    ]


async def test_the_bytes_go_through_the_api_when_the_store_cannot_sign() -> None:
    file_id = UUID(FILE["id"])
    base = f"/v1/media/files/{file_id}"
    stored = FILE | {"status": "stored"}
    no_form = {"url": None, "fields": [], "expires_at": "2026-09-18T12:15:00Z"}
    no_link = {"url": None, "expires_at": "2026-09-18T12:15:00Z"}
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        match (request.method, request.url.path):
            case ("POST", path) if path == f"{base}/upload":
                return httpx.Response(200, json=no_form)
            case ("PUT", path) if path == f"{base}/content":
                return httpx.Response(200, json=FILE)
            case ("POST", path) if path == f"{base}/confirm":
                return httpx.Response(200, json=stored)
            case ("GET", path) if path == f"{base}/download":
                return httpx.Response(200, json=no_link)
            case ("GET", path) if path == f"{base}/content":
                return httpx.Response(200, content=b"%PDF-1.7 the spec")
        raise AssertionError(request.url)

    async with ApiClient(
        "http://test",
        app="cli",
        app_version="cli@test",
        token="ses_1",
        transport=httpx.MockTransport(answer),
    ) as client:
        confirmed = await client.upload(FileView.model_validate(FILE), b"%PDF-1.7 the spec")
        fetched = await client.download(file_id)
    assert confirmed.status.value == "stored" and fetched == b"%PDF-1.7 the spec"
    put = seen[1]
    assert put.headers["content-type"] == "application/octet-stream"
    assert put.read() == b"%PDF-1.7 the spec"
    assert [(r.method, r.url.path.removeprefix(base)) for r in seen] == [
        ("POST", "/upload"),
        ("PUT", "/content"),
        ("POST", "/confirm"),
        ("GET", "/download"),
        ("GET", "/content"),
    ]


# The operator plane: each method sends the route it stands for, under the
# bearer the client holds (an operator's own sign-in), and types the answer.


async def test_admin_size_reads_the_platforms_size() -> None:
    size = {
        "tenants": 2,
        "users": 3,
        "events_last_24h": 5,
        "since": "2026-09-17T12:00:00Z",
        "counted_at": "2026-09-18T12:00:00Z",
    }
    recorder = Recorder({"/v1/admin/size": httpx.Response(200, json=size)})
    async with client_over(recorder, token="lgn_1") as client:
        read = await client.admin_size()
    assert (read.tenants, read.users, read.events_last_24h) == (2, 3, 5)
    assert read.counted_at - read.since == timedelta(hours=24)
    sent = recorder.requests[0]
    assert sent.method == "GET" and sent.url.path == "/v1/admin/size"
    assert sent.headers["authorization"] == "Bearer lgn_1"


async def test_admin_create_org_is_a_creating_call_under_a_key() -> None:
    recorder = Recorder({"/v1/admin/orgs": httpx.Response(201, json=ORG)})
    async with client_over(recorder, token="lgn_1") as client:
        created = await client.admin_create_org(
            "Ajax", "ajax", owner_email="ann@example.test", owner_name="Ann"
        )
        again = await client.admin_create_org(
            "Ajax",
            "ajax",
            owner_email="ann@example.test",
            owner_name="Ann",
            idempotency_key="given",
        )
    assert created.slug == "ajax" and again.id == created.id
    minted, given = recorder.requests
    assert minted.method == "POST" and minted.url.path == "/v1/admin/orgs"
    UUID(minted.headers["idempotency-key"])
    assert given.headers["idempotency-key"] == "given"
    assert minted.read() == (
        b'{"name":"Ajax","slug":"ajax","owner_email":"ann@example.test","owner_name":"Ann"}'
    )


async def test_admin_add_member_is_a_creating_call_under_a_key() -> None:
    org_id = UUID(ORG["id"])
    path = f"/v1/admin/orgs/{org_id}/members"
    recorder = Recorder({path: httpx.Response(201, json=USER)})
    async with client_over(recorder, token="lgn_1") as client:
        added = await client.admin_add_member(org_id, "bob@example.test", display_name="Bob")
        as_admin = await client.admin_add_member(
            org_id,
            "bob@example.test",
            display_name="Bob",
            role=Role.admin,
            idempotency_key="given",
        )
    assert added.email == "bob@example.test" and as_admin.id == added.id
    minted, given = recorder.requests
    assert minted.method == "POST" and minted.url.path == path
    UUID(minted.headers["idempotency-key"])
    assert given.headers["idempotency-key"] == "given"
    assert minted.read() == (b'{"email":"bob@example.test","display_name":"Bob","role":"member"}')
    assert given.read().endswith(b'"role":"admin"}')


async def test_admin_org_reads_one_tenant() -> None:
    org_id = UUID(ORG["id"])
    recorder = Recorder({f"/v1/admin/orgs/{org_id}": httpx.Response(200, json=ORG)})
    async with client_over(recorder, token="lgn_1") as client:
        org = await client.admin_org(org_id)
    assert org.id == org_id and org.deleted_at is None
    sent = recorder.requests[0]
    assert sent.method == "GET" and sent.url.path == f"/v1/admin/orgs/{org_id}"


async def test_admin_members_pages_with_the_cursor_it_is_given() -> None:
    org_id = UUID(ORG["id"])
    path = f"/v1/admin/orgs/{org_id}/members"
    page = {"items": [USER], "next_cursor": "c-2"}
    recorder = Recorder({path: httpx.Response(200, json=page)})
    async with client_over(recorder, token="lgn_1") as client:
        first = await client.admin_members(org_id)
        second = await client.admin_members(org_id, cursor=first.next_cursor, limit=5)
    assert [u.email for u in first.items] == ["bob@example.test"] and second.next_cursor == "c-2"
    assert recorder.requests[0].url.params["limit"] == "200"
    assert "cursor" not in recorder.requests[0].url.params
    assert dict(recorder.requests[1].url.params) == {"limit": "5", "cursor": "c-2"}


async def test_admin_events_replays_one_tenants_stream() -> None:
    org_id = UUID(ORG["id"])
    path = f"/v1/admin/orgs/{org_id}/events"
    operator_event = {**EVENT, "request_id": str(uuid4()), "app": "portal"}
    recorder = Recorder({path: httpx.Response(200, json=[operator_event])})
    async with client_over(recorder, token="lgn_1") as client:
        events = await client.admin_events(org_id, after_seq=3, limit=10)
    assert [e.seq for e in events] == [1] and events[0].kind == "tenancy.invitation.created"
    assert str(events[0].request_id) == operator_event["request_id"] and events[0].app == "portal"
    sent = recorder.requests[0]
    assert sent.method == "GET" and sent.url.path == path
    assert dict(sent.url.params) == {"after_seq": "3", "limit": "10"}


async def test_admin_me_reads_the_operators_own_entry() -> None:
    me = {"identity_id": str(uuid4()), "email": "sup@example.test", "operator_role": "read"}
    recorder = Recorder({"/v1/admin/me": httpx.Response(200, json=me)})
    async with client_over(recorder, token="lgn_1") as client:
        view = await client.admin_me()
    assert view.email == "sup@example.test" and view.operator_role.value == "read"
    assert recorder.requests[0].headers["authorization"] == "Bearer lgn_1"


OPERATOR_TOKEN = {
    "id": str(uuid4()),
    "permission": "read",
    "created_at": "2026-09-26T10:00:00Z",
    "expires_at": "2026-09-26T11:00:00Z",
    "revoked_at": None,
}


async def test_admin_tokens_pages_the_operators_own_tokens() -> None:
    page = {"items": [OPERATOR_TOKEN], "next_cursor": "c-2"}
    recorder = Recorder({"/v1/admin/me/tokens": httpx.Response(200, json=page)})
    async with client_over(recorder, token="opr_1") as client:
        first = await client.admin_tokens()
        await client.admin_tokens(cursor=first.next_cursor, limit=5)
    assert [str(t.id) for t in first.items] == [OPERATOR_TOKEN["id"]]
    assert first.items[0].permission.value == "read" and first.items[0].revoked_at is None
    assert dict(recorder.requests[0].url.params) == {"limit": "200"}
    assert dict(recorder.requests[1].url.params) == {"limit": "5", "cursor": "c-2"}


async def test_admin_revoke_token_deletes_one_by_its_id() -> None:
    token_id = UUID(OPERATOR_TOKEN["id"])
    revoked = {**OPERATOR_TOKEN, "revoked_at": "2026-09-26T10:30:00Z"}
    path = f"/v1/admin/me/tokens/{token_id}"
    recorder = Recorder({path: httpx.Response(200, json=revoked)})
    async with client_over(recorder, token="opr_1") as client:
        view = await client.admin_revoke_token(token_id)
    assert view.id == token_id and view.revoked_at is not None
    sent = recorder.requests[0]
    assert sent.method == "DELETE" and sent.url.path == path


def test_the_servers_retry_after_is_read_in_whole_seconds() -> None:
    assert retry_after_seconds("2") == 2.0
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT") is None


def test_a_retry_waits_at_least_what_the_server_asked_up_to_the_cap() -> None:
    assert retry_wait_seconds(0.2, 1.0) == 1.0
    assert retry_wait_seconds(1.5, 1.0) == 1.5
    assert retry_wait_seconds(0.2, None) == 0.2
    assert retry_wait_seconds(0.2, 60.0) == MAX_BACKOFF_SECONDS


async def test_a_truncated_stream_names_its_head_on_the_error() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        error = {"code": "stream_truncated", "message": "gone", "request_id": "r"}
        return httpx.Response(410, json={"error": {**error, "stream": {"floor": 3, "head": 9}}})

    async with ApiClient(
        "http://test", app="cli", app_version="cli@test", transport=httpx.MockTransport(respond)
    ) as client:
        with pytest.raises(ApiError) as refused:
            await client.events_after(1)
    assert (refused.value.status, refused.value.code) == (410, "stream_truncated")
    assert refused.value.stream_head == 9


async def test_the_retry_after_rides_on_the_error() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": "unavailable", "message": "busy", "request_id": "r"}}
        return httpx.Response(503, json=body, headers={"retry-after": "1"})

    async with ApiClient(
        "http://test", app="cli", app_version="cli@test", transport=httpx.MockTransport(respond)
    ) as client:
        with pytest.raises(ApiError) as refused:
            await client.me()
    assert refused.value.retry_after == 1.0
