"""The wire of reminders: a due date set, moved, and cleared on a task, and
never a time; a due time refused; a person's time zone recorded. Slack's own
wire is `test_slack_api.py`."""

import httpx


async def test_a_due_date_is_set_moved_and_cleared(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    created = await client.post(
        "/v1/tasks", headers=owner, json={"title": "Call the bank", "due_on": "2030-09-30"}
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["due_on"] == "2030-09-30" and task["reminded_at"] is None
    assert "remind_at" not in task, "a task carries a due date and no due time"

    async def patch(version: int, body: dict[str, object]) -> httpx.Response:
        headers = {**owner, "If-Match": f'"{version}"'}
        return await client.patch(f"/v1/tasks/{task['id']}", headers=headers, json=body)

    moved = await patch(task["version"], {"due_on": "2030-10-01"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["due_on"] == "2030-10-01"

    renamed = await patch(moved.json()["version"], {"title": "Call the bank today"})
    assert renamed.json()["due_on"] == "2030-10-01", "absent keeps it"

    cleared = await patch(renamed.json()["version"], {"due_on": None})
    assert cleared.status_code == 200 and cleared.json()["due_on"] is None


async def test_a_due_date_is_a_date_and_never_a_time(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    for wrong in ("2030-09-30T09:00:00+00:00", "Sep 30", "2030-02-30"):
        refused = await client.post(
            "/v1/tasks", headers=owner, json={"title": "When?", "due_on": wrong}
        )
        assert refused.status_code == 422, wrong


async def test_a_due_time_is_refused(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    """`remind_at`, the due time a due date replaced, is no field of a task:
    a create or an edit that sends it is refused, not read."""
    refused = await client.post(
        "/v1/tasks",
        headers=owner,
        json={"title": "Old client", "remind_at": "2030-09-30T23:30:00-05:00"},
    )
    assert refused.status_code == 422, refused.text
    created = await client.post("/v1/tasks", headers=owner, json={"title": "New client"})
    task = created.json()
    edited = await client.patch(
        f"/v1/tasks/{task['id']}",
        headers={**owner, "If-Match": f'"{task["version"]}"'},
        json={"remind_at": None},
    )
    assert edited.status_code == 422, edited.text


async def test_a_person_records_their_time_zone(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    before = await client.get("/v1/me/identity", headers=owner)
    assert before.status_code == 200 and before.json()["time_zone"] is None
    saved = await client.patch(
        "/v1/me/identity", headers=owner, json={"time_zone": "Europe/Istanbul"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["time_zone"] == "Europe/Istanbul"
    assert (await client.get("/v1/me/identity", headers=owner)).json()["time_zone"] == (
        "Europe/Istanbul"
    )
    for wrong in ("+03:00", "Mars/Olympus_Mons", "../etc/passwd", ""):
        refused = await client.patch("/v1/me/identity", headers=owner, json={"time_zone": wrong})
        assert refused.status_code == 422, wrong
