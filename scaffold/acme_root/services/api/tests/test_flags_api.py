"""`GET /v1/flags` answers each session for its own org and its own user,
with only the flags marked for clients, and `304` to a caller that holds
the current snapshot."""

import json
from pathlib import Path
from types import MappingProxyType

import httpx
import pytest
from api_support import OWNER, add_member, seed_request, sign_in_as

from acme.infra.flags import FLAGS, Flag, FlagSpec
from acme.om.context import Role
from acme.services.api.container import AppContainer

FLAG = Flag.MEDIA_UPLOADS.value


async def test_each_session_reads_its_own_audiences_flags_and_a_held_snapshot_answers_304(
    client: httpx.AsyncClient, container: AppContainer, tmp_path: Path
) -> None:
    """Org A has the flag off and one of its users has it on; org B has no
    rule. Ann (A) reads off, Bob (A) on, Eve (B) the default."""
    tenancy = container.managers.tenancy
    _, ajax = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], "Ann")
    _, other = await tenancy.bootstrap(seed_request(), "Other", "other", "eve@other.test", "Eve")
    bob = await add_member(container, ajax.id, "bob@example.test", Role.MEMBER)
    (tmp_path / "flags.json").write_text(
        json.dumps({FLAG: {"orgs": {str(ajax.id): False}, "users": {str(bob.id): True}}})
    )
    ann = await sign_in_as(client, OWNER["email"], ajax.id)
    bob_headers = await sign_in_as(client, "bob@example.test", ajax.id)
    eve = await sign_in_as(client, "eve@other.test", other.id)

    answers = {
        name: await client.get("/v1/flags", headers=headers)
        for name, headers in (("ann", ann), ("bob", bob_headers), ("eve", eve))
    }
    for answer in answers.values():
        assert answer.status_code == 200, answer.text
        assert answer.headers["Cache-Control"] == "private, no-cache"
    assert {name: answer.json() for name, answer in answers.items()} == {
        "ann": {"flags": {FLAG: False}},
        "bob": {"flags": {FLAG: True}},
        "eve": {"flags": {FLAG: FLAGS[Flag.MEDIA_UPLOADS].default}},
    }

    held = answers["ann"].headers["ETag"]
    again = await client.get("/v1/flags", headers={**ann, "If-None-Match": held})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["ETag"] == held
    # Another audience's snapshot is never "current" for a caller whose own differs.
    elsewhere = await client.get("/v1/flags", headers={**eve, "If-None-Match": held})
    assert elsewhere.status_code == 200
    assert elsewhere.json() == answers["eve"].json()


async def test_a_flag_not_marked_for_clients_never_reaches_one(
    client: httpx.AsyncClient, owner: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "acme.infra.flags.FLAGS",
        MappingProxyType({Flag.MEDIA_UPLOADS: FlagSpec(default=True, client=False)}),
    )
    answer = await client.get("/v1/flags", headers=owner)
    assert answer.status_code == 200
    assert answer.json() == {"flags": {}}


async def test_a_session_is_needed(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/flags")).status_code == 401


async def test_an_upload_the_flag_turns_off_is_refused_with_its_code(
    client: httpx.AsyncClient, container: AppContainer, tmp_path: Path
) -> None:
    """The route's answer is the typed error's: a client reads `feature_off`,
    and the org whose flag is on still uploads."""
    tenancy = container.managers.tenancy
    _, ajax = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], "Ann")
    _, other = await tenancy.bootstrap(seed_request(), "Other", "other", "eve@other.test", "Eve")
    (tmp_path / "flags.json").write_text(json.dumps({FLAG: {"orgs": {str(ajax.id): False}}}))
    body = {"name": "report.pdf", "content_type": "application/pdf", "size_bytes": 10}
    refused = await client.post(
        "/v1/media/files",
        headers={**await sign_in_as(client, OWNER["email"], ajax.id), "Idempotency-Key": "k-1"},
        json=body,
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "feature_off"
    taken = await client.post(
        "/v1/media/files",
        headers={**await sign_in_as(client, "eve@other.test", other.id), "Idempotency-Key": "k-2"},
        json=body,
    )
    assert taken.status_code == 201, taken.text
