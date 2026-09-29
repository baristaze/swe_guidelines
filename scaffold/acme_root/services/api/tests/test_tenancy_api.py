import asyncio
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from api_support import (
    OWNER,
    SMALL_BUDGET,
    add_member,
    bearer,
    build_container,
    client_over,
    code_at,
    dev_login,
    enrol_operator,
    run,
    seed_request,
    sign_in_as,
)
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from acme.om.idempotency.impl.manager import IdempotencyOptions
from acme.om.opcontext import OperatorRole, Role
from acme.om.work.types.work_item import WorkKind
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.settings import ApiSettings


async def test_sign_in_round_trip(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    me = await client.get("/v1/me", headers=owner)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user"]["email"] == OWNER["email"]
    assert body["role"] == "owner"
    assert body["app"] == "portal"
    assert "manage_members" in body["permissions"]

    users = await client.get("/v1/users", headers=owner, params={"limit": 1000})
    assert [u["email"] for u in users.json()["items"]] == [OWNER["email"]]
    assert users.json()["next_cursor"] is None
    org = await client.get("/v1/orgs/current", headers=owner)
    assert org.json()["slug"] == "ajax"


async def test_error_envelope_carries_the_request_id(client: httpx.AsyncClient) -> None:
    request_id = str(uuid4())
    response = await client.get("/v1/me", headers={"x-request-id": request_id})
    assert response.status_code == 401
    assert response.headers["x-request-id"] == request_id
    assert response.json() == {
        "error": {
            "code": "not_authenticated",
            "message": "missing bearer credential",
            "request_id": request_id,
        }
    }


async def test_unknown_fields_are_refused_with_the_envelope(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    response = await client.post("/v1/api-keys", headers=owner, json={"name": "x", "rol": "member"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    UUID(response.json()["error"]["request_id"])


async def test_not_found_flows_through_the_one_handler(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    response = await client.delete(f"/v1/api-keys/{uuid4()}", headers=owner)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_sign_in_is_rate_limited_per_client(tmp_path: Path) -> None:
    # The settings' budget is sized for a crowd behind one address; the
    # mechanism is the same at any size, so this process gets a small one.
    # Every sign-in route counts against it; the callback of a process with
    # no provider answers 503 and still counts.
    container = build_container(tmp_path, login_rate_limit=SMALL_BUDGET)
    budget = container.rate_limits.of("login").limit
    assert budget == SMALL_BUDGET
    async with client_over(container) as client:
        for index in range(budget):
            body = {"code": f"code-{index}", "code_verifier": "v" * 43}
            assert (await client.post("/v1/auth/callback", json=body)).status_code == 503
        rejected = await client.post("/v1/auth/dev-sign-in", json={"email": "nobody@example.test"})
    assert rejected.status_code == 429
    assert rejected.json()["error"]["code"] == "rate_limited"
    assert int(rejected.headers["Retry-After"]) >= 1


def test_the_sign_in_budget_is_generous() -> None:
    """Far above the traffic run, or a person clicking fast."""
    assert ApiSettings.model_validate({"_env_file": None}).login_rate_limit >= 1000


async def test_a_guessed_second_factor_waits_before_its_next_try(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    """Per email and in the database, beside the per-address limit: after the
    free run of wrong codes even the right one is answered 429 with the wait."""
    _, secret = await enrol_operator(client, container, "root@example.test", OperatorRole.WRITE)
    login = bearer(await dev_login(client, "root@example.test"))
    for _ in range(container.settings.sign_in_free_failures):
        wrong = await client.post(
            "/v1/auth/second-factor", headers=login, json={"totp_code": "000000"}
        )
        assert wrong.status_code == 401, wrong.text
    delayed = await client.post(
        "/v1/auth/second-factor", headers=login, json={"totp_code": code_at(secret, 2)}
    )
    assert delayed.status_code == 429
    assert delayed.json()["error"]["code"] == "sign_in_delayed"
    assert int(delayed.headers["Retry-After"]) >= 1


async def test_api_key_creation_replays_on_the_same_idempotency_key(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    headers = {**owner, "Idempotency-Key": "create-ci-key-1"}
    body = {"name": "ci", "role": "member"}
    first = await client.post("/v1/api-keys", headers=headers, json=body)
    assert first.status_code == 201, first.text
    second = await client.post("/v1/api-keys", headers=headers, json=body)
    assert second.status_code == 201
    assert second.headers["Idempotent-Replayed"] == "true"
    # The replay is the row and no secret: the key was shown once.
    assert first.json()["key"] and second.json() == {**first.json(), "key": None}

    keys = await client.get("/v1/api-keys", headers=owner)
    assert [k["id"] for k in keys.json()["items"]] == [first.json()["api_key"]["id"]]

    as_machine = await client.get(
        "/v1/me", headers={"Authorization": f"Bearer {first.json()['key']}"}
    )
    assert as_machine.status_code == 200
    assert as_machine.json()["app"] == "api"
    assert as_machine.json()["role"] == "member"

    revoked = await client.delete(f"/v1/api-keys/{first.json()['api_key']['id']}", headers=owner)
    assert revoked.status_code == 200 and revoked.json()["deleted_at"] is not None
    refused = await client.get("/v1/me", headers={"Authorization": f"Bearer {first.json()['key']}"})
    assert refused.status_code == 401


async def test_a_member_cannot_mint_a_service_key(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    await add_member(container, org_id, "bob@example.test", Role.MEMBER)
    bob = await sign_in_as(client, "bob@example.test", org_id)
    for headers in (bob, owner):
        refused = await client.post(
            "/v1/api-keys", headers=headers, json={"name": "svc", "role": "service"}
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "validation_failed"
    assert (await client.get("/v1/api-keys", headers=owner)).json()["items"] == []


async def test_a_crash_between_the_key_create_and_finish_reissues_the_secret(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The key landed, then the process died before the outcome reached the
    # marker, so the secret reached no one. The retry takes the marker over and
    # runs the create again on the same id: a fresh secret on the same key.
    manager = container.managers.idempotency
    monkeypatch.setattr(manager, "_options", IdempotencyOptions(pending_ttl=timedelta(0)))
    original_finish = manager.finish
    crashed = False

    async def crash_once(ctx, key, attempt_id, status, body):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("the process died before finish")
        return await original_finish(ctx, key, attempt_id, status, body)

    monkeypatch.setattr(manager, "finish", crash_once)
    headers = {**owner, "Idempotency-Key": "key-crash-1"}
    body = {"name": "ci", "role": "member"}
    first = await client.post("/v1/api-keys", headers=headers, json=body)
    assert first.status_code == 500 and crashed
    keys = (await client.get("/v1/api-keys", headers=owner)).json()["items"]
    assert len(keys) == 1, "the create landed before the crash"

    retry = await client.post("/v1/api-keys", headers=headers, json=body)
    assert retry.status_code == 201, retry.text
    assert "Idempotent-Replayed" not in retry.headers
    assert retry.json()["api_key"]["id"] == keys[0]["id"]
    assert (await client.get("/v1/api-keys", headers=owner)).json()["items"] == [
        retry.json()["api_key"]
    ]
    as_machine = await client.get(
        "/v1/me", headers={"Authorization": f"Bearer {retry.json()['key']}"}
    )
    assert as_machine.status_code == 200, as_machine.text
    replay = await client.post("/v1/api-keys", headers=headers, json=body)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == {**retry.json(), "key": None}


async def test_a_slow_attempt_never_re_mints_the_key_the_retry_returned(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first attempt is still running when its pending lease passes. The
    # retry takes the marker over, reruns the create on the marker's id, and
    # hands its secret to the caller. The slow attempt then finds the id
    # written and would re-mint over it: the marker no longer holds its
    # attempt, so the re-mint is refused, and the key the caller is holding
    # goes on authenticating.
    manager = container.managers.idempotency
    monkeypatch.setattr(manager, "_options", IdempotencyOptions(pending_ttl=timedelta(0)))
    original_create = container.managers.tenancy.create_api_key
    entered, release = asyncio.Event(), asyncio.Event()
    held = False

    async def slow_once(ctx, name, role, ttl=None, attempt=None):
        nonlocal held
        if not held:
            held = True
            entered.set()
            await release.wait()
        return await original_create(ctx, name, role, ttl, attempt)

    monkeypatch.setattr(container.managers.tenancy, "create_api_key", slow_once)
    headers = {**owner, "Idempotency-Key": "key-slow-1"}
    body = {"name": "ci", "role": "member"}
    slow_call = asyncio.create_task(client.post("/v1/api-keys", headers=headers, json=body))
    await asyncio.wait_for(entered.wait(), timeout=5)

    retry = await client.post("/v1/api-keys", headers=headers, json=body)
    assert retry.status_code == 201, retry.text
    assert "Idempotent-Replayed" not in retry.headers

    release.set()
    slow = await slow_call
    assert slow.status_code == 409, slow.text
    as_machine = await client.get(
        "/v1/me", headers={"Authorization": f"Bearer {retry.json()['key']}"}
    )
    assert as_machine.status_code == 200, "the secret the retry returned still verifies"
    keys = (await client.get("/v1/api-keys", headers=owner)).json()["items"]
    assert [k["id"] for k in keys] == [retry.json()["api_key"]["id"]], "one row, the retry's"
    # The slow attempt's refusal is nobody's outcome: the marker holds the
    # retry's, and a replay answers with it.
    replay = await client.post("/v1/api-keys", headers=headers, json=body)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == {**retry.json(), "key": None}


async def test_the_stored_outcome_of_a_key_create_carries_no_secret(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    # The secret exists in one place, as a digest: the idempotency record
    # holds the view with the key absent, the first response alone carries it,
    # and a replay answers with the row, key null, and the header that says so.
    headers = {**owner, "Idempotency-Key": "key-secret-1"}
    first = await client.post(
        "/v1/api-keys", headers=headers, json={"name": "ci", "role": "member"}
    )
    assert first.status_code == 201, first.text
    secret = first.json()["key"]
    assert secret and secret.startswith("key_")

    me = (await client.get("/v1/me", headers=owner)).json()
    record = await container.storage.get_idempotency_storage().read_record(
        UUID(me["org"]["id"]), UUID(me["user"]["id"]), "key-secret-1"
    )
    assert record is not None and record.body is not None
    assert secret not in record.body
    assert json.loads(record.body) == {**first.json(), "key": None}

    replay = await client.post(
        "/v1/api-keys", headers=headers, json={"name": "ci", "role": "member"}
    )
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["key"] is None
    assert replay.json()["api_key"] == first.json()["api_key"]


async def test_api_key_ttl_is_bounded_on_the_wire(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    for ttl_days in (0, 91):
        response = await client.post(
            "/v1/api-keys",
            headers=owner,
            json={"name": "x", "role": "member", "ttl_days": ttl_days},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "validation_failed"
    accepted = await client.post(
        "/v1/api-keys", headers=owner, json={"name": "x", "role": "member", "ttl_days": 7}
    )
    assert accepted.status_code == 201, accepted.text


async def test_idempotency_keys_are_per_user_inside_a_tenant(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = (await client.get("/v1/orgs/current", headers=owner)).json()["id"]
    await add_member(container, UUID(org_id), "bob@example.test", Role.MEMBER)
    bob = await sign_in_as(client, "bob@example.test", UUID(org_id))
    body = {"name": "ci", "role": "member"}

    first = await client.post(
        "/v1/api-keys", headers={**owner, "Idempotency-Key": "shared-1"}, json=body
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/v1/api-keys", headers={**bob, "Idempotency-Key": "shared-1"}, json=body
    )
    assert second.status_code == 201, second.text
    assert "Idempotent-Replayed" not in second.headers
    assert second.json()["api_key"]["id"] != first.json()["api_key"]["id"]
    assert second.json()["api_key"]["user_id"] != first.json()["api_key"]["user_id"]


async def test_members_are_promoted_and_removed_by_a_member_manager(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    bob = await add_member(container, org_id, "bob@example.test", Role.VIEWER)
    as_bob = await sign_in_as(client, "bob@example.test", org_id)

    refused = await client.patch(
        f"/v1/memberships/{bob.id}", headers=as_bob, json={"role": "admin"}
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "not_authorized"

    promoted = await client.patch(
        f"/v1/memberships/{bob.id}", headers=owner, json={"role": "admin"}
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "admin"
    assert (await client.get("/v1/me", headers=as_bob)).json()["role"] == "admin"

    missing = await client.delete(f"/v1/memberships/{uuid4()}", headers=owner)
    assert missing.status_code == 404
    removed = await client.delete(f"/v1/memberships/{bob.id}", headers=owner)
    assert removed.status_code == 200, removed.text
    assert removed.json()["id"] == str(bob.id)
    assert (await client.get("/v1/me", headers=as_bob)).status_code == 401
    users = await client.get("/v1/users", headers=owner)
    assert [u["email"] for u in users.json()["items"]] == [OWNER["email"]]
    # The membership ended with the member: not listed, not changeable.
    memberships = await client.get("/v1/memberships", headers=owner)
    assert str(bob.id) not in [m["user_id"] for m in memberships.json()["items"]]
    gone = await client.patch(f"/v1/memberships/{bob.id}", headers=owner, json={"role": "viewer"})
    assert gone.status_code == 404


async def test_me_is_renamed_and_the_identity_is_read(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    renamed = await client.patch("/v1/me", headers=owner, json={"display_name": "Ann B."})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["display_name"] == "Ann B."
    assert (await client.get("/v1/me", headers=owner)).json()["user"]["display_name"] == "Ann B."
    blank = await client.patch("/v1/me", headers=owner, json={"display_name": ""})
    assert blank.status_code == 422

    identity = await client.get("/v1/me/identity", headers=owner)
    assert identity.status_code == 200, identity.text
    assert identity.json()["email"] == OWNER["email"]
    assert identity.json()["operator_role"] is None
    assert "password_hash" not in identity.json()


async def test_sessions_are_listed_revoked_and_logged_out(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    other = await sign_in_as(client, OWNER["email"], org_id)

    listed = await client.get("/v1/sessions", headers=owner)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 2
    assert all(s["credential_kind"] == "session_token" for s in listed.json())
    assert all(s["revoked_at"] is None for s in listed.json())

    mine = (await client.post("/v1/auth/logout", headers=other)).json()["id"]
    remaining = [s["id"] for s in (await client.get("/v1/sessions", headers=owner)).json()]
    assert mine not in remaining and len(remaining) == 1
    assert (await client.get("/v1/me", headers=other)).status_code == 401

    revoked = await client.delete(f"/v1/sessions/{remaining[0]}", headers=owner)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None
    assert (await client.get("/v1/me", headers=owner)).status_code == 401


async def test_operator_routes_need_an_operator_sign_in(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    refused = await client.get("/v1/admin/orgs", headers=owner)
    assert refused.status_code == 401
    admin, _ = await enrol_operator(client, container, "root@example.test", OperatorRole.WRITE)
    admitted = await client.get("/v1/admin/orgs", headers=admin)
    assert admitted.status_code == 200
    teams = [o["slug"] for o in admitted.json()["items"] if o["kind"] == "team"]
    assert sorted(teams) == ["ajax", "root"]


async def test_operators_delete_an_org(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = (await client.get("/v1/orgs/current", headers=owner)).json()["id"]
    admin, _ = await enrol_operator(client, container, "root@example.test", OperatorRole.WRITE)

    assert (await client.delete(f"/v1/admin/orgs/{org_id}", headers=owner)).status_code == 401
    closed = await client.delete(f"/v1/admin/orgs/{org_id}", headers=admin)
    assert closed.status_code == 200, closed.text
    # Closed at once, for everyone in it; deleted by the queue.
    assert closed.json()["id"] == org_id and closed.json()["deleted_at"] is None
    assert (await client.get("/v1/me", headers=owner)).status_code == 401
    again = await client.delete(f"/v1/admin/orgs/{org_id}", headers=admin)
    assert again.status_code == 200 and again.json() == closed.json()
    # One DELETE_ORG is queued, asked for by the operator.
    operator_id = UUID((await client.get("/v1/admin/me", headers=admin)).json()["identity_id"])
    claimed = await container.managers.work.claim(
        seed_request(), "default", [WorkKind.DELETE_ORG], "test", timedelta(seconds=30)
    )
    assert claimed is not None
    work, item = claimed
    assert (item.target_id, item.created_by) == (UUID(org_id), operator_id)
    assert (
        await container.managers.work.claim(
            seed_request(), "default", [WorkKind.DELETE_ORG], "test", timedelta(seconds=30)
        )
        is None
    )
    # Its last step deletes the org; a delete after it finds nothing.
    await container.managers.tenancy.delete_closed_org(work)
    assert (await client.delete(f"/v1/admin/orgs/{org_id}", headers=admin)).status_code == 404


async def test_an_operator_never_deletes_a_personal_org(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    admin, _ = await enrol_operator(client, container, "root@example.test", OperatorRole.WRITE)
    login = await dev_login(client, OWNER["email"])
    places = await client.get("/v1/auth/memberships", headers=bearer(login))
    home = next(p["org"] for p in places.json()["items"] if p["org"]["kind"] == "personal")
    refused = await client.delete(f"/v1/admin/orgs/{home['id']}", headers=admin)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "personal_org_fixed"


def test_realtime_channel_delivers_tenant_events(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    app = create_app(container)
    with TestClient(app) as tc:
        login = tc.post("/v1/auth/dev-sign-in", json={"email": OWNER["email"]})
        session = tc.post(
            "/v1/auth/sessions",
            json={"org_id": str(org.id)},
            headers={"Authorization": f"Bearer {login.json()['token']}"},
        )
        headers = {"Authorization": f"Bearer {session.json()['token']}", "X-App": "portal"}
        ticket = tc.post("/v1/realtime/tickets", headers=headers)
        assert ticket.status_code == 201, ticket.text
        assert ticket.json()["ticket"].startswith("tkt_")
        assert 0 < ticket.json()["expires_in_seconds"] <= 60
        url = f"/v1/realtime?ticket={ticket.json()['ticket']}"
        with tc.websocket_connect(url) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["org_id"] == str(org.id)
            ws.send_json({"op": "subscribe", "topic": "entity_changed"})
            assert ws.receive_json()["type"] == "subscribed"
            ws.send_json({"op": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong" and pong["seq"] == hello["seq"]
            created = tc.post(
                "/v1/api-keys", headers=headers, json={"name": "ci", "role": "member"}
            )
            assert created.status_code == 201, created.text
            event = ws.receive_json()
            assert event["type"] == "event" and event["topic"] == "entity_changed"
            assert event["payload"]["kind"] == "tenancy.api_key.created"
            assert event["payload"]["target_id"] == created.json()["api_key"]["id"]
            assert set(event["payload"]) == {"kind", "target_id", "seq", "actor_id"}
        # A ticket is single-use: the second socket is accepted, then closed.
        with tc.websocket_connect(url) as ws:
            with pytest.raises(WebSocketDisconnect) as refused:
                ws.receive_json()
        assert refused.value.code == 4401


async def test_the_key_list_pages_so_the_oldest_key_is_still_reachable(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    """A tenant with more live keys than one page holds: every one of them is
    listed by following `next_cursor`, so the oldest can still be found and
    revoked. A fixed limit would hide it, whatever the limit was set to."""
    created = []
    for index in range(12):
        response = await client.post(
            "/v1/api-keys", headers=owner, json={"name": f"key-{index}", "role": "member"}
        )
        assert response.status_code == 201, response.text
        created.append(response.json()["api_key"]["id"])

    listed: list[str] = []
    cursor: str | None = None
    while True:
        params = {"limit": 5} | ({"cursor": cursor} if cursor else {})
        page = await client.get("/v1/api-keys", headers=owner, params=params)
        assert page.status_code == 200, page.text
        listed += [k["id"] for k in page.json()["items"]]
        cursor = page.json()["next_cursor"]
        if cursor is None:
            break
    assert listed == created[::-1]

    oldest = created[0]
    revoked = await client.delete(f"/v1/api-keys/{oldest}", headers=owner)
    assert revoked.status_code == 200 and revoked.json()["deleted_at"] is not None


async def test_the_membership_list_pages_beside_the_member_list(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    """Every membership is reached by following `next_cursor`, and each page
    holds the roles of the members on the page of `/v1/users` read with the
    same cursor and limit, so no member of a large org is shown without one."""
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    for index in range(4):
        await add_member(container, org_id, f"m{index}@example.test", Role.MEMBER)

    listed: list[str] = []
    users_cursor: str | None = None
    roles_cursor: str | None = None
    while True:
        users = await client.get(
            "/v1/users", headers=owner, params={"limit": 2} | page_at(users_cursor)
        )
        roles = await client.get(
            "/v1/memberships", headers=owner, params={"limit": 2} | page_at(roles_cursor)
        )
        assert roles.status_code == 200, roles.text
        page = [m["user_id"] for m in roles.json()["items"]]
        assert page == [u["id"] for u in users.json()["items"]]
        listed += page
        users_cursor = users.json()["next_cursor"]
        roles_cursor = roles.json()["next_cursor"]
        assert (roles_cursor is None) == (users_cursor is None)
        if roles_cursor is None:
            break
    assert len(listed) == 5 and len(set(listed)) == 5


def page_at(cursor: str | None) -> dict[str, str]:
    return {"cursor": cursor} if cursor else {}


async def test_a_cursor_from_another_list_is_refused(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    """The cursor is opaque and names the list that issued it: the member
    list's cursor is not the key list's, and neither is a made-up string."""
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    await add_member(container, org_id, "bob@example.test", Role.MEMBER)
    users = await client.get("/v1/users", headers=owner, params={"limit": 1})
    cursor = users.json()["next_cursor"]
    assert cursor is not None

    crossed = await client.get("/v1/api-keys", headers=owner, params={"cursor": cursor})
    assert crossed.status_code == 422, crossed.text
    assert crossed.json()["error"]["code"] == "validation_failed"
    made_up = await client.get("/v1/users", headers=owner, params={"cursor": "not-a-cursor"})
    assert made_up.status_code == 422, made_up.text
