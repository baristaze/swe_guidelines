"""Every push is also a record: a file's write appears on the channel with its
stream position and again on /v1/events after the last seq a client saw."""

from datetime import timedelta
from pathlib import Path

import httpx
from api_support import OWNER, build_container, run, seed_request
from starlette.testclient import TestClient

from acme.om.base import utcnow
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer

REPORT = {"name": "report.pdf", "content_type": "application/pdf", "size_bytes": 5}


async def test_events_are_paged_by_seq(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    created = await client.post("/v1/media/files", headers=owner, json=REPORT)
    file_id = created.json()["id"]
    put = await client.put(f"/v1/media/files/{file_id}/content", headers=owner, content=b"%PDF-")
    assert put.status_code == 200, put.text
    confirmed = await client.post(f"/v1/media/files/{file_id}/confirm", headers=owner)
    assert confirmed.status_code == 200, confirmed.text
    await client.delete(f"/v1/media/files/{file_id}", headers=owner)

    everything = await client.get("/v1/events", headers=owner, params={"after_seq": 0})
    assert everything.status_code == 200, everything.text
    events = everything.json()
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert [e["kind"] for e in events] == [
        "media.file.created",
        "media.file.updated",
        "media.file.deleted",
    ]
    assert all(e["target_id"] == file_id for e in events)
    assert set(events[0]) == {"seq", "kind", "target_id", "produced_at", "actor_id"}
    me = (await client.get("/v1/me", headers=owner)).json()["user"]["id"]
    assert all(e["actor_id"] == me for e in events)

    tail = await client.get("/v1/events", headers=owner, params={"after_seq": 2, "limit": 10})
    assert [e["seq"] for e in tail.json()] == [3]
    assert (
        await client.get("/v1/events", headers=owner, params={"after_seq": -1})
    ).status_code == 422
    assert (await client.get("/v1/events")).status_code == 401


async def test_a_read_below_the_floor_is_gone_and_names_the_head(
    client: httpx.AsyncClient, owner: dict[str, str], container: AppContainer
) -> None:
    """The trim took the bottom of the stream: a client whose cursor is below
    the floor cannot be caught up, and is told where the stream goes on from."""
    for name in ("one.pdf", "two.pdf", "three.pdf"):
        await client.post("/v1/media/files", headers=owner, json={**REPORT, "name": name})
    events = container.storage.get_event_storage()
    assert await events.trim(utcnow() + timedelta(seconds=1), 2) == 2

    for below in (0, 1):
        gone = await client.get("/v1/events", headers=owner, params={"after_seq": below})
        assert gone.status_code == 410, gone.text
        error = gone.json()["error"]
        assert error["code"] == "stream_truncated"
        assert error["stream"] == {"floor": 2, "head": 3}
        assert "last_owner" not in error
        assert error["request_id"] == gone.headers["x-request-id"]

    at_floor = await client.get("/v1/events", headers=owner, params={"after_seq": 2})
    assert at_floor.status_code == 200 and [e["seq"] for e in at_floor.json()] == [3]
    above = await client.get("/v1/events", headers=owner, params={"after_seq": 3})
    assert above.status_code == 200 and above.json() == []


def test_a_push_carries_the_stream_position(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    _, org = run(
        container.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    with TestClient(create_app(container)) as tc:
        login = tc.post("/v1/auth/dev-sign-in", json={"email": OWNER["email"]})
        session = tc.post(
            "/v1/auth/sessions",
            json={"org_id": str(org.id)},
            headers={"Authorization": f"Bearer {login.json()['token']}"},
        )
        headers = {"Authorization": f"Bearer {session.json()['token']}", "X-App": "portal"}
        ticket = tc.post("/v1/realtime/tickets", headers=headers).json()["ticket"]
        with tc.websocket_connect(f"/v1/realtime?ticket={ticket}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["seq"] == 0
            ws.send_json({"op": "subscribe", "topic": "entity_changed"})
            assert ws.receive_json()["type"] == "subscribed"
            ws.send_json({"op": "subscribe", "topic": "work_available"})
            refused = ws.receive_json()
            assert refused["type"] == "error" and refused["code"] == "validation_failed"

            created = tc.post("/v1/media/files", headers=headers, json=REPORT)
            assert created.status_code == 201, created.text
            event = ws.receive_json()
            assert event["type"] == "event" and event["topic"] == "entity_changed"
            assert event["payload"] == {
                "kind": "media.file.created",
                "target_id": created.json()["id"],
                "seq": 1,
                "actor_id": session.json()["user"]["id"],
            }
        replay = tc.get("/v1/events", headers=headers, params={"after_seq": 0})
        assert [e["seq"] for e in replay.json()] == [1]
        # A socket opened now starts at the stream's position.
        ticket = tc.post("/v1/realtime/tickets", headers=headers).json()["ticket"]
        with tc.websocket_connect(f"/v1/realtime?ticket={ticket}") as ws:
            assert ws.receive_json()["seq"] == 1
