"""`acme-ops size`: the operator's token reads `/v1/admin/size`, the
tenants the traffic generator created are left out of it, and how long ago
the worker counted it is printed beside it."""

import argparse
from pathlib import Path

import httpx
import pytest

from acme.ops.main import size_command

NOW = "2026-09-20T12:00:00Z"
RUN_ORG = "0199a4c0-0000-7000-8000-00000000000b"


def org(org_id: str, slug: str) -> dict[str, object]:
    return {
        "id": org_id,
        "name": slug,
        "slug": slug,
        "kind": "team",
        "created_at": NOW,
        "deleted_at": None,
    }


def operator_api(request: httpx.Request) -> httpx.Response:
    assert not request.url.path.startswith("/v1/auth/"), "an agent never signs in"
    assert request.headers["authorization"] == "Bearer opt_read"
    assert request.headers["x-app"] == "admin"
    if request.url.path == "/v1/admin/size":
        return httpx.Response(
            200,
            json={
                "tenants": 3,
                "users": 12,
                "tasks_last_24h": 40,
                "events_last_24h": 90,
                "since": "2026-09-19T12:00:00Z",
                "counted_at": "2026-09-20T12:00:00Z",
            },
        )
    if request.url.path == "/v1/admin/orgs":
        if request.url.params.get("cursor") is None:
            items = [org("0199a4c0-0000-7000-8000-00000000000a", "ajax")]
            return httpx.Response(200, json={"items": items, "next_cursor": "c2"})
        return httpx.Response(200, json={"items": [org(RUN_ORG, "ops-r1-1")], "next_cursor": None})
    if request.url.path == f"/v1/admin/orgs/{RUN_ORG}/members":
        person = {"email": "m@x.test", "display_name": "M", "created_at": NOW}
        items = [{**person, "id": f"0199a4c0-0000-7000-8000-00000000010{n}"} for n in range(2)]
        return httpx.Response(200, json={"items": items, "next_cursor": None})
    return httpx.Response(404, json={"error": {"code": "not_found", "message": "no"}})


def env_file(home: Path, text: str) -> None:
    file = home / ".config" / "acme" / "ops" / "staging.env"
    file.parent.mkdir(parents=True)
    file.write_text(text)
    file.chmod(0o600)


async def test_size_reads_the_operator_plane_with_the_token_and_leaves_run_tenants_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path, "ACME_API_URL=http://test\nACME_OPERATOR_TOKEN=opt_read\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ACME_API_URL", raising=False)
    monkeypatch.delenv("ACME_OPERATOR_TOKEN", raising=False)
    code = await size_command(
        argparse.Namespace(env="staging"), transport=httpx.MockTransport(operator_api)
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "tenants                  2" in out and "users                    10" in out
    assert "tasks_last_24h           40" in out
    assert "counted_at               2026-09-20 12:00:00+00:00" in out
    assert "counted                  " in out and "s ago, by the maintenance worker's sweep" in out
    assert "traffic run tenants      1 left out (2 users)" in out


async def test_size_without_a_token_names_the_command_that_writes_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path, "ACME_API_URL=http://test\nACME_OPERATOR_TOKEN=\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ACME_OPERATOR_TOKEN", raising=False)
    code = await size_command(argparse.Namespace(env="staging"))
    assert code == 2
    assert "acme-ops token --env staging --identity operator" in capsys.readouterr().err
