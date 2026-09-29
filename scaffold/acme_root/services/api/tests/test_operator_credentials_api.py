"""The operator plane's credentials over the live app: the second factor an
operator enrols before the plane admits them and verifies on a sign-in, the
operator token an agent presents instead of a sign-in, and the grant job's
command."""

import argparse
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest
from api_support import (
    bearer,
    code_at,
    dev_login,
    enrol_operator,
    enrolled_sign_in,
    secret_of,
    seed_request,
)

from acme.om.context import OperatorRole
from acme.om.tenancy.impl.operator import TOTP_ISSUER
from acme.om.tenancy.rules import email_digest
from acme.services.api import main as api_main
from acme.services.api.container import AppContainer
from acme.services.api.settings import ApiSettings
from acme.services.api.token_secrets import token_secret_name


async def test_an_operator_enrols_a_second_factor_before_the_plane_admits_them(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    await container.managers.tenancy.bootstrap(
        seed_request(),
        "Root",
        "root",
        "root@example.test",
        "Root",
        operator_role=OperatorRole.WRITE,
    )
    first = await client.post("/v1/auth/dev-sign-in", json={"email": "root@example.test"})
    enrolling = bearer(first.json()["token"])
    # Allowlisted, no factor yet: every route but the two enrolment ones refuses.
    for path in ("/v1/admin/me", "/v1/admin/orgs", "/v1/admin/size"):
        refused = await client.get(path, headers=enrolling)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "second_factor_not_enrolled"
    minted = await client.post("/v1/admin/me/totp", headers=enrolling)
    assert minted.status_code == 200, minted.text
    uri = minted.json()["otpauth_uri"]
    assert uri.startswith(f"otpauth://totp/{quote(TOTP_ISSUER)}%3Aroot%40example.test?secret=")
    secret = secret_of(uri)
    wrong = await client.post(
        "/v1/admin/me/totp/confirm", headers=enrolling, json={"totp_code": "000000"}
    )
    assert wrong.status_code == 422, wrong.text
    confirmed = await client.post(
        "/v1/admin/me/totp/confirm", headers=enrolling, json={"totp_code": code_at(secret, 0)}
    )
    assert confirmed.status_code == 200, confirmed.text

    # Enrolled: the sign-in that carried no code no longer admits.
    required = await client.get("/v1/admin/me", headers=enrolling)
    assert required.status_code == 401, required.text
    assert required.json()["error"]["code"] == "second_factor_required"
    # The code is verified on a sign-in, which answers with a new one and
    # ends the one it was verified on.
    fresh = bearer(await dev_login(client, "root@example.test"))
    bad = await client.post("/v1/auth/second-factor", headers=fresh, json={"totp_code": "000000"})
    assert bad.status_code == 401, bad.text
    code = code_at(secret, 1)
    login = await client.post("/v1/auth/second-factor", headers=fresh, json={"totp_code": code})
    assert login.status_code == 200, login.text
    signed_in = bearer(login.json()["token"])
    # With its code, the sign-in mints a token and reads nothing itself.
    for path in ("/v1/admin/me", "/v1/admin/orgs", "/v1/admin/me/tokens"):
        refused = await client.get(path, headers=signed_in)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "operator_token_required"
    again = await client.post("/v1/admin/me/totp", headers=signed_in)
    assert again.status_code == 409, again.text
    reused = await client.post("/v1/auth/second-factor", headers=fresh, json={"totp_code": code})
    assert reused.status_code == 401, reused.text
    # A session is no sign-in: the second factor is verified on a login only.
    other = bearer(await dev_login(client, "root@example.test"))
    org_id = (await client.get("/v1/auth/memberships", headers=other)).json()["items"][0]
    session = await client.post(
        "/v1/auth/sessions", headers=other, json={"org_id": org_id["org"]["id"]}
    )
    tenant = bearer(session.json()["token"])
    on_session = await client.post(
        "/v1/auth/second-factor", headers=tenant, json={"totp_code": code_at(secret, 2)}
    )
    assert on_session.status_code == 401, on_session.text


async def test_an_operator_token_is_minted_once_and_admits_with_its_one_permission(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    signed_in, _ = await enrolled_sign_in(
        client, container, "root@example.test", OperatorRole.WRITE
    )
    # An hour at most: a mint refused on its body ends nothing.
    long = await client.post(
        "/v1/admin/me/tokens", headers=signed_in, json={"permission": "read", "expires_in": 3601}
    )
    assert long.status_code == 422, long.text
    issued = await client.post(
        "/v1/admin/me/tokens", headers=signed_in, json={"permission": "read"}
    )
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]
    assert token.startswith("opr_") and issued.json()["permission"] == "read"
    assert issued.json()["id"], "named, so it can be revoked by itself"
    # The mint ends the sign-in: a replay or a retry after a lost answer signs
    # in again, and a leaked sign-in mints nothing more.
    replay = await client.post(
        "/v1/admin/me/tokens", headers=signed_in, json={"permission": "read"}
    )
    assert replay.status_code == 401, replay.text

    agent = bearer(token)
    me = await client.get("/v1/admin/me", headers=agent)
    assert me.status_code == 200, me.text
    assert me.json()["operator_role"] == "read" and me.json()["email"] == "root@example.test"
    assert (await client.get("/v1/admin/orgs", headers=agent)).status_code == 200
    org_id = (await client.get("/v1/orgs/current", headers=owner)).json()["id"]
    write = await client.delete(f"/v1/admin/orgs/{org_id}", headers=agent)
    assert write.status_code == 403, write.text
    # A token never mints a token, and reaches no tenant and no person's places.
    minted_by_token = await client.post(
        "/v1/admin/me/tokens", headers=agent, json={"permission": "read"}
    )
    assert minted_by_token.status_code == 403, minted_by_token.text
    assert (await client.get("/v1/me", headers=agent)).status_code == 401
    assert (await client.get("/v1/auth/memberships", headers=agent)).status_code == 401
    exchanged = await client.post("/v1/auth/sessions", headers=agent, json={"org_id": org_id})
    assert exchanged.status_code == 401, exchanged.text
    # Never wider than the entry.
    reader, _ = await enrolled_sign_in(client, container, "sup@example.test", OperatorRole.READ)
    wider = await client.post("/v1/admin/me/tokens", headers=reader, json={"permission": "write"})
    assert wider.status_code == 403, wider.text


async def test_a_read_operator_mints_with_the_key_a_client_sends(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    """The mint is an exchange of the sign-in and keeps no marker, so the
    `Idempotency-Key` a client sends is not what decides it: a read entry's
    mint lands, where the marker, a write of the plane, refused it."""
    reader, _ = await enrolled_sign_in(client, container, "sup@example.test", OperatorRole.READ)
    issued = await client.post(
        "/v1/admin/me/tokens",
        headers={**reader, "Idempotency-Key": "token-1"},
        json={"permission": "read"},
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["token"].startswith("opr_")


async def test_a_revoked_token_is_refused_on_its_next_request(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    """An operator lists their live tokens, the grant job's for their
    identity among them, and ends one by its id: that one is refused at
    once, the others stand, and a second revoke answers the first."""
    writer, _ = await enrol_operator(client, container, "root@example.test", OperatorRole.WRITE)
    tenancy = container.managers.tenancy
    spare = await tenancy.grant_operator_token(seed_request(), "root@example.test")
    listed = await client.get("/v1/admin/me/tokens", headers=writer)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert items[0]["id"] == str(spare.id) and len(items) == 2
    assert {item["permission"] for item in items} == {"write"}
    assert all("token" not in item for item in items), "never the secret"
    page = await client.get("/v1/admin/me/tokens", headers=writer, params={"limit": 1})
    assert [i["id"] for i in page.json()["items"]] == [str(spare.id)]
    rest = await client.get(
        "/v1/admin/me/tokens", headers=writer, params={"cursor": page.json()["next_cursor"]}
    )
    assert [i["id"] for i in rest.json()["items"]] == [items[1]["id"]]

    assert (await client.get("/v1/admin/me", headers=bearer(spare.token))).status_code == 200
    revoked = await client.delete(f"/v1/admin/me/tokens/{spare.id}", headers=writer)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None
    refused = await client.get("/v1/admin/me", headers=bearer(spare.token))
    assert refused.status_code == 401, refused.text
    assert refused.json()["error"]["message"] == "operator token revoked"
    assert (await client.get("/v1/admin/me", headers=writer)).status_code == 200
    again = await client.delete(f"/v1/admin/me/tokens/{spare.id}", headers=writer)
    assert again.status_code == 200 and again.json() == revoked.json()
    left = await client.get("/v1/admin/me/tokens", headers=writer)
    assert [i["id"] for i in left.json()["items"]] == [items[1]["id"]]


async def test_an_operator_never_revokes_another_operators_token(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    """Ending another operator's credentials is the grant job's disable, not
    a route of the plane: whatever the entry, another's token is not found."""
    writer, _ = await enrol_operator(client, container, "root@example.test", OperatorRole.WRITE)
    reader, _ = await enrol_operator(client, container, "sup@example.test", OperatorRole.READ)
    theirs = (await client.get("/v1/admin/me/tokens", headers=reader)).json()["items"][0]["id"]
    mine = (await client.get("/v1/admin/me/tokens", headers=writer)).json()["items"][0]["id"]
    for headers, token_id in ((writer, theirs), (reader, mine)):
        refused = await client.delete(f"/v1/admin/me/tokens/{token_id}", headers=headers)
        assert refused.status_code == 404, refused.text
    for headers in (writer, reader):
        assert (await client.get("/v1/admin/me", headers=headers)).status_code == 200
    unknown = await client.delete(f"/v1/admin/me/tokens/{uuid4()}", headers=reader)
    assert unknown.status_code == 404, unknown.text


async def test_a_sign_out_ends_the_sign_in_or_the_token_presented(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    """The one sign-out takes every credential a person holds: a sign-in at
    the picker, an operator's sign-in with its code, and an operator token.
    Each is refused after. An api key has none: its holder revokes it."""
    picker = bearer(await dev_login(client, "owner@example.test"))
    out = await client.post("/v1/auth/logout", headers=picker)
    assert out.status_code == 200, out.text
    assert out.json()["credential_kind"] == "login" and out.json()["revoked_at"]
    assert (await client.get("/v1/auth/memberships", headers=picker)).status_code == 401

    signed_in, _ = await enrolled_sign_in(
        client, container, "root@example.test", OperatorRole.WRITE
    )
    assert (await client.post("/v1/auth/logout", headers=signed_in)).status_code == 200
    minted = await client.post(
        "/v1/admin/me/tokens", headers=signed_in, json={"permission": "read"}
    )
    assert minted.status_code == 401, minted.text

    token = await container.managers.tenancy.grant_operator_token(
        seed_request(), "root@example.test"
    )
    agent = bearer(token.token)
    ended = await client.post("/v1/auth/logout", headers=agent)
    assert ended.status_code == 200, ended.text
    assert ended.json()["id"] == str(token.id)
    assert ended.json()["credential_kind"] == "operator_token"
    assert ended.json()["provider_logout_url"] is None
    assert (await client.get("/v1/admin/me", headers=agent)).status_code == 401
    assert (await client.post("/v1/auth/logout", headers=agent)).status_code == 401

    key = await client.post(
        "/v1/api-keys",
        headers={**owner, "Idempotency-Key": "key-1"},
        json={"name": "ci", "role": "member"},
    )
    assert key.status_code == 201, key.text
    by_key = await client.post("/v1/auth/logout", headers=bearer(key.json()["key"]))
    assert by_key.status_code == 401, by_key.text


def grant_args(**given: object) -> argparse.Namespace:
    return argparse.Namespace(
        **({"permission": None, "disable": False, "mint_token": None, "expires_in": None} | given)
    )


async def test_the_grant_command_grants_disables_and_mints_into_the_secret_store(
    container: AppContainer, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tenancy = container.managers.tenancy
    await tenancy.bootstrap(seed_request(), "Ajax", "ajax", "ann@example.test", "Ann")
    settings = container.settings
    await api_main.granted(
        container, settings, grant_args(email="ann@example.test", permission="read")
    )
    storage = container.storage.get_tenancy_storage()
    ann = await storage.read_identity_by_email_digest(email_digest("ann@example.test"))
    assert ann is not None and ann.operator_role is OperatorRole.READ
    await api_main.granted(container, settings, grant_args(email="ann@example.test", disable=True))
    ann = await storage.read_identity_by_email_digest(email_digest("ann@example.test"))
    assert ann is not None and ann.operator_role is None

    # The platform's own identity is made by its first grant, and its token
    # goes to the secret store in the cloud and nowhere else.
    provisioner = "provisioner@platform.acme.invalid"
    await api_main.granted(container, settings, grant_args(email=provisioner, permission="write"))
    written: list[tuple[str, str]] = []

    async def put(_: ApiSettings, name: str, token: str) -> None:
        written.append((name, token))

    monkeypatch.setattr(api_main, "put_token", put)
    cloud = settings.model_copy(update={"environment": "staging"})
    for holder in ("provisioner", "smoke"):
        await api_main.granted(
            container, cloud, grant_args(email=provisioner, mint_token=holder, expires_in=600)
        )
    assert [name for name, _ in written] == [
        "acme-staging-provisioner-token",
        "acme-staging-smoke-token",
    ]
    assert all(token.startswith("opr_") for _, token in written)
    assert "opr_" not in capsys.readouterr().out, "never printed in the cloud"
    smoke = await tenancy.admit_operator(
        await tenancy.authenticate_login(seed_request(), written[1][1])
    )
    assert {p.value for p in smoke.permissions} == {"read"}, "the smoke test only reads"
    assert token_secret_name("production", "smoke") == "acme-production-smoke-token"


async def test_the_local_read_operators_token_is_printed_on_a_local_database_only(
    container: AppContainer, capsys: pytest.CaptureFixture[str]
) -> None:
    """`make seed` grants the local read operator and mints its token here: a
    `read` token whatever the entry holds, printed on a local database. A
    cloud has no such token: an operator's there is their own, minted after a
    sign-in with a code."""
    tenancy = container.managers.tenancy
    settings = container.settings
    operator = "operator@platform.acme.invalid"
    await api_main.granted(container, settings, grant_args(email=operator, permission="write"))
    capsys.readouterr()
    assert (
        await api_main.granted(
            container, settings, grant_args(email=operator, mint_token="operator")
        )
        == 0
    )
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("opr_")
    admin = await tenancy.admit_operator(await tenancy.authenticate_login(seed_request(), printed))
    assert {p.value for p in admin.permissions} == {"read"}
    cloud = settings.model_copy(update={"environment": "staging"})
    assert (
        await api_main.granted(container, cloud, grant_args(email=operator, mint_token="operator"))
        == 2
    )
    assert "opr_" not in capsys.readouterr().out


def test_the_grant_command_names_its_identity_and_one_action() -> None:
    for argv in (
        ["grant-operator", "--permission", "read"],
        ["grant-operator", "--email", "a@example.test"],
        ["grant-operator", "--email", "a@example.test", "--permission", "read", "--disable"],
        ["grant-operator", "--email", "a@example.test", "--mint-token", "other"],
        [
            "grant-operator",
            "--email",
            "a@example.test",
            "--permission",
            "read",
            "--expires-in",
            "5",
        ],
    ):
        with pytest.raises(SystemExit):
            api_main.main(argv)
