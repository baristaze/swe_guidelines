"""Files over the live app, on the local store: an upload started, its bytes
moved through the API (the local store cannot presign), confirmed, listed,
downloaded, and deleted; the bounds; the usage; the tenant boundary; and the
namespaces setting that mounts the media routes alone."""

from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from api_support import add_member, build_container, seed_request, sign_in_as

from acme.om.opcontext import Role
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.gateway.body import MAX_BODY_BYTES

PDF = b"%PDF-1.7 the report"


async def start(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    name: str = "report.pdf",
    content_type: str = "application/pdf",
    size: int = len(PDF),
) -> httpx.Response:
    return await client.post(
        "/v1/media/files",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"name": name, "content_type": content_type, "size_bytes": size},
    )


async def upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    data: bytes = PDF,
    name: str = "report.pdf",
) -> dict:
    """The whole flow a client makes against a store that cannot presign."""
    started = await start(client, headers, name=name, size=len(data))
    assert started.status_code == 201, started.text
    file_id = started.json()["id"]
    form = await client.post(f"/v1/media/files/{file_id}/upload", headers=headers)
    assert form.status_code == 200, form.text
    assert form.json()["url"] is None and form.json()["fields"] == []
    put = await client.put(f"/v1/media/files/{file_id}/content", headers=headers, content=data)
    assert put.status_code == 200, put.text
    confirmed = await client.post(f"/v1/media/files/{file_id}/confirm", headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


async def test_an_upload_goes_up_is_listed_comes_down_and_is_deleted(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    started = await start(client, owner)
    assert started.status_code == 201, started.text
    pending = started.json()
    assert (pending["status"], pending["purpose"], pending["subject_id"]) == (
        "pending",
        "upload",
        None,
    )
    assert "key" not in pending, "where the bytes live is never on the wire"
    listed = await client.get("/v1/media/files", headers=owner)
    assert listed.status_code == 200, listed.text
    assert listed.json() == {"items": [], "next_cursor": None}, "a pending file is not listed"
    early = await client.post(f"/v1/media/files/{pending['id']}/confirm", headers=owner)
    assert early.status_code == 422, "no object, no confirm"

    stored = await upload(client, owner)
    assert (stored["status"], stored["name"], stored["extension"]) == (
        "stored",
        "report.pdf",
        "pdf",
    )
    listed = await client.get("/v1/media/files", headers=owner)
    assert [f["id"] for f in listed.json()["items"]] == [stored["id"]]

    link = await client.get(f"/v1/media/files/{stored['id']}/download", headers=owner)
    assert link.status_code == 200 and link.json()["url"] is None, link.text
    content = await client.get(f"/v1/media/files/{stored['id']}/content", headers=owner)
    assert content.status_code == 200 and content.content == PDF
    assert content.headers["content-type"] == "application/pdf"
    assert content.headers["content-disposition"] == "attachment; filename*=UTF-8''report.pdf"
    preview = await client.get(
        f"/v1/media/files/{stored['id']}/content", headers=owner, params={"inline": "true"}
    )
    assert preview.headers["content-disposition"] == "inline"
    assert preview.headers["content-type"] == "application/pdf"

    deleted = await client.delete(f"/v1/media/files/{stored['id']}", headers=owner)
    assert deleted.status_code == 200 and deleted.json()["deleted_at"] is not None
    assert (await client.get("/v1/media/files", headers=owner)).json()["items"] == []
    gone = await client.get(f"/v1/media/files/{stored['id']}", headers=owner)
    assert gone.status_code == 404
    usage = (await client.get("/v1/media/usage", headers=owner)).json()
    assert usage["total_count"] == 0, "a deleted file stops counting at once"


async def test_the_files_are_paged_oldest_first(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    stored = [(await upload(client, owner, name=f"report-{n}.pdf"))["id"] for n in range(3)]
    first = await client.get("/v1/media/files", headers=owner, params={"limit": 2})
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 2 and page["next_cursor"] is not None
    rest = await client.get(
        "/v1/media/files", headers=owner, params={"limit": 2, "cursor": page["next_cursor"]}
    )
    assert rest.json()["next_cursor"] is None
    seen = [f["id"] for f in page["items"]] + [f["id"] for f in rest.json()["items"]]
    assert sorted(seen) == sorted(stored) and len(seen) == 3
    foreign = await client.get(
        "/v1/media/files", headers=owner, params={"cursor": "bm90LWEtY3Vyc29y"}
    )
    assert foreign.status_code == 422, foreign.text


async def test_a_retried_start_lands_one_file(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    headers = {**owner, "Idempotency-Key": str(uuid4())}
    body = {"name": "report.pdf", "content_type": "application/pdf", "size_bytes": 10}
    first = await client.post("/v1/media/files", headers=headers, json=body)
    again = await client.post("/v1/media/files", headers=headers, json=body)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert again.headers["idempotent-replayed"] == "true"
    usage = (await client.get("/v1/media/usage", headers=owner)).json()
    assert usage["pending_size_bytes"] == 10, "one pending file, not two"


@pytest.mark.parametrize(
    ("name", "content_type", "size"),
    [
        ("page.html", "text/html", 10),
        ("image.svg", "image/svg+xml", 10),
        ("report.png", "application/pdf", 10),
        ("report.pdf", "application/pdf", 100 * 1024 * 1024 + 1),
        ("a/b.pdf", "application/pdf", 10),
    ],
)
async def test_an_upload_outside_the_bounds_never_starts(
    client: httpx.AsyncClient, owner: dict[str, str], name: str, content_type: str, size: int
) -> None:
    refused = await start(client, owner, name, content_type, size)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "validation_failed"


async def test_bytes_past_the_declared_size_are_refused(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    started = await start(client, owner, size=4)
    file_id = started.json()["id"]
    await client.post(f"/v1/media/files/{file_id}/upload", headers=owner)
    over = await client.put(f"/v1/media/files/{file_id}/content", headers=owner, content=b"12345")
    assert over.status_code == 422, over.text
    huge = await client.put(
        f"/v1/media/files/{file_id}/content",
        headers={**owner, "Content-Length": str(MAX_BODY_BYTES + 1)},
        content=b"x",
    )
    assert huge.status_code == 422, huge.text


async def test_usage_is_counted_from_the_rows(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    await upload(client, owner)
    await upload(client, owner, b"12345")
    await start(client, owner, size=700)
    usage = await client.get("/v1/media/usage", headers=owner)
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert (body["total_count"], body["total_size_bytes"], body["pending_size_bytes"]) == (
        2,
        len(PDF) + 5,
        700,
    )
    by = {p["purpose"]: p for p in body["purposes"]}
    assert set(by) == {"upload"}
    assert by["upload"]["count"] == 2


async def test_a_viewer_reads_files_and_starts_or_deletes_none(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    stored = await upload(client, owner)
    org_id = UUID((await client.get("/v1/me", headers=owner)).json()["org"]["id"])
    await add_member(container, org_id, "vic@example.test", Role.VIEWER)
    viewer = await sign_in_as(client, "vic@example.test", org_id)
    listed = await client.get("/v1/media/files", headers=viewer)
    assert [f["id"] for f in listed.json()["items"]] == [stored["id"]]
    assert (await start(client, viewer)).status_code == 403
    removal = await client.delete(f"/v1/media/files/{stored['id']}", headers=viewer)
    assert removal.status_code == 403
    kept = await client.get(f"/v1/media/files/{stored['id']}", headers=owner)
    assert kept.status_code == 200 and kept.json()["deleted_at"] is None


async def test_an_upload_is_its_starters_until_it_is_stored(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    """Another member reads a pending file but signs no form for it, moves no
    bytes into it, and confirms it for nobody."""
    started = (await start(client, owner)).json()
    org_id = UUID((await client.get("/v1/me", headers=owner)).json()["org"]["id"])
    await add_member(container, org_id, "mia@example.test", Role.MEMBER)
    mia = await sign_in_as(client, "mia@example.test", org_id)
    file_id = started["id"]
    assert (await client.get(f"/v1/media/files/{file_id}", headers=mia)).status_code == 200
    moves: list[tuple[str, str, dict]] = [
        ("POST", f"/v1/media/files/{file_id}/upload", {}),
        ("PUT", f"/v1/media/files/{file_id}/content", {"content": PDF}),
        ("POST", f"/v1/media/files/{file_id}/confirm", {}),
    ]
    for method, path, extra in moves:
        refused = await client.request(method, path, headers=mia, **extra)
        assert refused.status_code == 403, f"{method} {path}: {refused.status_code}"


async def test_another_tenants_file_answers_as_a_missing_one(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    """Tenant B names A's file on every route that takes one; each answer is
    the 404 an unknown id gets, B's list and usage hold nothing of A's, and
    A's file is as it was."""
    stored = await upload(client, owner)
    _, other = await container.managers.tenancy.bootstrap(
        seed_request(), "Other", "other", "eve@other.test", "Eve"
    )
    eve = await sign_in_as(client, "eve@other.test", other.id)
    file_id = stored["id"]
    swept: list[tuple[str, str, dict]] = [
        ("GET", f"/v1/media/files/{file_id}", {}),
        ("POST", f"/v1/media/files/{file_id}/upload", {}),
        ("PUT", f"/v1/media/files/{file_id}/content", {"content": PDF}),
        ("POST", f"/v1/media/files/{file_id}/confirm", {}),
        ("GET", f"/v1/media/files/{file_id}/download", {}),
        ("GET", f"/v1/media/files/{file_id}/content", {}),
        ("DELETE", f"/v1/media/files/{file_id}", {}),
    ]
    for method, path, extra in swept:
        answered = await client.request(method, path, headers=eve, **extra)
        assert answered.status_code == 404, f"{method} {path}: {answered.status_code}"
    assert (await client.get("/v1/media/files", headers=eve)).json()["items"] == []
    assert (await client.get("/v1/media/usage", headers=eve)).json()["total_count"] == 0
    mine = await client.get(f"/v1/media/files/{file_id}", headers=owner)
    assert mine.status_code == 200 and mine.json() == stored


async def test_the_namespaces_setting_mounts_the_media_routes_alone(tmp_path: Path) -> None:
    app = create_app(build_container(tmp_path, namespaces=["media"]))
    paths = set(app.openapi()["paths"])
    assert {"/v1/media/usage", "/v1/media/files"} <= paths
    assert not any(p.startswith(("/v1/auth", "/v1/events", "/v1/admin")) for p in paths)
    whole = set(create_app(build_container(tmp_path)).openapi()["paths"])
    assert paths < whole


def test_a_namespace_the_image_does_not_host_refuses_the_boot(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="payroll"):
        create_app(build_container(tmp_path, namespaces=["media", "payroll"]))
