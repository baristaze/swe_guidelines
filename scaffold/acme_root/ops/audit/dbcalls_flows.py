"""The built-in flows `dbcalls.py run` drives, in order: sign-in and the
per-request baseline, the members, the invitations, the API keys, a session
switch, a file uploaded, the event stream, an org deleted, an account
deleted (each with the worker's item it starts), and the sweep. Each call is
named the way the report names it: the route and what makes this call
differ (a new identity, a keyed replay, a refusal). A run covers every area
once; an audit that needs a path these do not reach adds a flows file of its
own (`--flows`), written the same way.

Flows run once per audit database: the people and orgs they make are
unique within one run, not across two.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "api" / "tests"))

from api_support import add_member, seed_request

from acme.om.base import new_id
from acme.om.context import AppContext, AppType, RequestContext, Role

CALLBACK = "http://localhost:55173/auth/callback"
VERIFIER = "v" * 43
PDF = b"%PDF-1.4 audit"


def headers(token: str, app: str = "portal") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-App": app, "X-App-Version": f"{app}@audit"}


def worker_request() -> RequestContext:
    return RequestContext(
        request_id=new_id(), app=AppContext(type=AppType.WORKER, version="worker@audit")
    )


async def login(w: Any, email: str) -> str:
    r = await w.client.post("/v1/auth/dev-sign-in", json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def session(w: Any, email: str, org_id: Any) -> str:
    token = await login(w, email)
    r = await w.client.post(
        "/v1/auth/sessions", json={"org_id": str(org_id)}, headers=headers(token)
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def personal_session(w: Any, email: str) -> str:
    """A person's session in their personal org, which a first sign-in makes."""
    token = await login(w, email)
    r = await w.client.get("/v1/auth/memberships", headers=headers(token))
    assert r.status_code == 200, r.text
    personal = next(m for m in r.json()["items"] if m["org"]["kind"] == "personal")
    r = await w.client.post(
        "/v1/auth/sessions", json={"org_id": personal["org"]["id"]}, headers=headers(token)
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---------------------------------------------------------------- seed


async def seed(w: Any) -> None:
    """An org with its owner and one member, both signed in."""
    s = new_id().hex[-8:]
    st = w.state
    st["s"] = s
    st["owner_email"] = f"owner-{s}@example.test"
    tenancy = w.container.managers.tenancy
    made = await w.measure(
        "cli",
        "bootstrap (acme-api bootstrap)",
        lambda: tenancy.bootstrap(seed_request(), "Ajax", f"ajax-{s}", st["owner_email"], "Owner"),
    )
    assert made is not None
    st["org"] = made[1]
    st["bob"] = await add_member(w.container, st["org"].id, f"bob-{s}@example.test", Role.MEMBER)
    st["H"] = headers(await session(w, st["owner_email"], st["org"].id))
    st["bobH"] = headers(await session(w, f"bob-{s}@example.test", st["org"].id))


# ---------------------------------------------------------------- sign-in and the baseline


async def sign_in(w: Any) -> None:
    st, s = w.state, w.state["s"]
    area = "auth"
    for label, email in (
        ("existing identity", st["owner_email"]),
        ("new identity: personal org", f"new-{s}@example.test"),
    ):
        await w.http(
            area,
            f"POST /v1/auth/dev-sign-in ({label})",
            "POST",
            "/v1/auth/dev-sign-in",
            json={"email": email},
        )
    token = await login(w, st["owner_email"])
    await w.http(
        area,
        "GET /v1/auth/memberships (sign-in credential)",
        "GET",
        "/v1/auth/memberships",
        headers=headers(token),
    )
    r = await w.http(
        area,
        "POST /v1/auth/sessions (exchange)",
        "POST",
        "/v1/auth/sessions",
        json={"org_id": str(st["org"].id)},
        headers=headers(token),
    )
    fresh = r.json()["token"]
    base = "baseline"
    await w.http(
        base, "GET /v1/me (session, first use: touch)", "GET", "/v1/me", headers=headers(fresh)
    )
    await w.http(base, "GET /v1/me (session, warm)", "GET", "/v1/me", headers=headers(fresh))
    await w.http(base, "GET /v1/me (unknown token)", "GET", "/v1/me", headers=headers("ses_nope"))
    await w.http(base, "GET /v1/me (no header)", "GET", "/v1/me")
    await w.http(
        area,
        "POST /v1/auth/sign-in",
        "POST",
        "/v1/auth/sign-in",
        json={"redirect_uri": CALLBACK, "state": "s" * 20},
    )
    code = w.idp.issue_code(st["owner_email"])
    await w.http(
        area,
        "POST /v1/auth/callback (existing identity)",
        "POST",
        "/v1/auth/callback",
        json={"code": code, "code_verifier": VERIFIER},
    )
    code = w.idp.issue_code(f"fresh-{s}@example.test")
    await w.http(
        area,
        "POST /v1/auth/callback (new identity)",
        "POST",
        "/v1/auth/callback",
        json={"code": code, "code_verifier": VERIFIER},
    )
    other = await session(w, st["owner_email"], st["org"].id)
    await w.http(
        area,
        "POST /v1/auth/logout",
        "POST",
        "/v1/auth/logout",
        headers=headers(other),
        json={"return_to": "http://localhost:55173/signed-out"},
    )
    r = await w.client.post(
        "/v1/api-keys", headers=st["H"], json={"name": "audit", "role": "member"}
    )
    key = r.json()["key"]
    st["keyH"] = headers(key, "api")
    await w.http(base, "GET /v1/me (api key)", "GET", "/v1/me", headers=st["keyH"])


# ---------------------------------------------------------------- members


async def members(w: Any) -> None:
    st, s, h = w.state, w.state["s"], w.state["H"]
    area = "members"
    await w.http(area, "GET /v1/me", "GET", "/v1/me", headers=h)
    await w.http(
        area, "PATCH /v1/me", "PATCH", "/v1/me", headers=h, json={"display_name": "Owner base."}
    )
    await w.http(area, "GET /v1/orgs/current", "GET", "/v1/orgs/current", headers=h)
    await w.http(area, "GET /v1/users", "GET", "/v1/users", headers=h)
    await w.http(area, "GET /v1/memberships", "GET", "/v1/memberships", headers=h)
    tenancy = w.container.managers.tenancy
    slug = st["org"].slug
    added = await w.measure(
        area,
        "add_member (a new person: identity, personal org, user, membership)",
        lambda: tenancy.add_member(
            seed_request(), slug, f"dana-{s}@example.test", "Dana", Role.MEMBER
        ),
        note="acme-api add-member and the operator plane's POST /v1/admin/orgs/{id}/members",
    )
    await w.measure(
        area,
        "add_member (already a member)",
        lambda: tenancy.add_member(
            seed_request(), slug, f"dana-{s}@example.test", "Dana", Role.MEMBER
        ),
    )
    bob = st["bob"].id
    await w.http(
        area,
        "PATCH /v1/memberships/{user} role",
        "PATCH",
        f"/v1/memberships/{bob}",
        headers=h,
        json={"role": "admin"},
    )
    await w.http(
        area,
        "PATCH /v1/memberships/{user} (own role: refused)",
        "PATCH",
        f"/v1/memberships/{bob}",
        headers=st["bobH"],
        json={"role": "admin"},
    )
    if added is not None:
        dana = added[1].id
        await w.http(
            area,
            "DELETE /v1/memberships/{user}",
            "DELETE",
            f"/v1/memberships/{dana}",
            headers=h,
        )


# ---------------------------------------------------------------- invitations


async def invitations(w: Any) -> None:
    s, h = w.state["s"], w.state["H"]
    area = "invitations"
    r = await w.http(
        area,
        "POST /v1/invitations",
        "POST",
        "/v1/invitations",
        headers=h,
        json={"email": f"inv-{s}@example.test", "role": "member"},
    )
    invitation = r.json()["id"]
    keyed = {**h, "Idempotency-Key": f"inv-{s}"}
    body = {"email": f"inv2-{s}@example.test", "role": "member"}
    await w.http(
        area, "POST /v1/invitations (keyed)", "POST", "/v1/invitations", headers=keyed, json=body
    )
    await w.http(
        area,
        "POST /v1/invitations (keyed replay)",
        "POST",
        "/v1/invitations",
        headers=keyed,
        json=body,
    )
    await w.http(area, "GET /v1/invitations", "GET", "/v1/invitations", headers=h)
    await w.http(
        area,
        "POST /v1/invitations/{id}/resend",
        "POST",
        f"/v1/invitations/{invitation}/resend",
        headers=h,
    )
    await w.http(
        area,
        "DELETE /v1/invitations/{id}",
        "DELETE",
        f"/v1/invitations/{invitation}",
        headers=h,
    )


# ---------------------------------------------------------------- api keys


async def api_keys(w: Any) -> None:
    s, h = w.state["s"], w.state["H"]
    area = "api-keys"
    await w.http(area, "GET /v1/api-keys", "GET", "/v1/api-keys", headers=h)
    r = await w.http(
        area,
        "POST /v1/api-keys",
        "POST",
        "/v1/api-keys",
        headers=h,
        json={"name": "k1", "role": "member"},
    )
    key_id = r.json()["api_key"]["id"]
    keyed = {**h, "Idempotency-Key": f"key-{s}"}
    body = {"name": "k2", "role": "member", "ttl_days": 1}
    await w.http(
        area, "POST /v1/api-keys (keyed)", "POST", "/v1/api-keys", headers=keyed, json=body
    )
    await w.http(
        area, "POST /v1/api-keys (keyed replay)", "POST", "/v1/api-keys", headers=keyed, json=body
    )
    await w.http(area, "DELETE /v1/api-keys/{id}", "DELETE", f"/v1/api-keys/{key_id}", headers=h)


# ---------------------------------------------------------------- a session switch


async def switch(w: Any) -> None:
    st, s = w.state, w.state["s"]
    area = "switch"
    h = headers(await session(w, st["owner_email"], st["org"].id))
    r = await w.http(
        area, "POST /v1/orgs", "POST", "/v1/orgs", headers=h, json={"name": f"Team {s}"}
    )
    team = r.json()["org"]["id"]
    await w.http(
        area,
        "GET /v1/auth/memberships (session)",
        "GET",
        "/v1/auth/memberships",
        headers=h,
    )
    r = await w.http(
        area,
        "POST /v1/auth/sessions (switch: presented with a session)",
        "POST",
        "/v1/auth/sessions",
        json={"org_id": team},
        headers=h,
    )
    switched = headers(r.json()["token"])
    r = await w.http(area, "GET /v1/sessions", "GET", "/v1/sessions", headers=switched)
    known = {row["id"] for row in r.json()}
    await session(w, st["owner_email"], team)
    listed = await w.client.get("/v1/sessions", headers=switched)
    other = next(row["id"] for row in listed.json() if row["id"] not in known)
    await w.http(
        area, "DELETE /v1/sessions/{id}", "DELETE", f"/v1/sessions/{other}", headers=switched
    )
    st["team"] = {"id": team, "name": f"Team {s}", "H": switched}


# ---------------------------------------------------------------- a file uploaded


async def files(w: Any) -> None:
    h = w.state["H"]
    area = "media"
    body = {"name": "notes.pdf", "content_type": "application/pdf", "size_bytes": len(PDF)}
    r = await w.http(area, "POST /v1/media/files", "POST", "/v1/media/files", headers=h, json=body)
    fid = r.json()["id"]
    await w.http(
        area, "POST /v1/media/files/{id}/upload", "POST", f"/v1/media/files/{fid}/upload", headers=h
    )
    await w.http(
        area,
        "PUT /v1/media/files/{id}/content",
        "PUT",
        f"/v1/media/files/{fid}/content",
        headers=h,
        content=PDF,
    )
    await w.http(
        area,
        "POST /v1/media/files/{id}/confirm",
        "POST",
        f"/v1/media/files/{fid}/confirm",
        headers=h,
    )
    await w.http(area, "GET /v1/media/files", "GET", "/v1/media/files", headers=h)
    await w.http(area, "GET /v1/media/files/{id}", "GET", f"/v1/media/files/{fid}", headers=h)
    await w.http(
        area,
        "GET /v1/media/files/{id}/download",
        "GET",
        f"/v1/media/files/{fid}/download",
        headers=h,
    )
    await w.http(area, "GET /v1/media/usage", "GET", "/v1/media/usage", headers=h)
    await w.http(area, "DELETE /v1/media/files/{id}", "DELETE", f"/v1/media/files/{fid}", headers=h)


# ---------------------------------------------------------------- events


async def events(w: Any) -> None:
    h = w.state["H"]
    area = "realtime"
    await w.http(
        area, "GET /v1/events?after_seq=0", "GET", "/v1/events", headers=h, params={"after_seq": 0}
    )
    await w.http(
        area,
        "GET /v1/events?limit=200",
        "GET",
        "/v1/events",
        headers=h,
        params={"after_seq": 0, "limit": 200},
    )
    await w.http(
        area,
        "GET /v1/events (past the head)",
        "GET",
        "/v1/events",
        headers=h,
        params={"after_seq": 10**9},
    )
    r = await w.http(area, "POST /v1/realtime/tickets", "POST", "/v1/realtime/tickets", headers=h)
    ticket = r.json()["ticket"]
    tenancy = w.container.managers.tenancy
    principal = await w.measure(
        area,
        "WS /v1/realtime: redeem the ticket",
        lambda: tenancy.redeem_ticket(seed_request(), ticket),
        note="the handshake's manager call",
    )
    if principal is None:
        return
    realtime = w.container.services.get_realtime_service()
    detach = realtime.attach(principal, lambda reason: None)
    try:
        await w.measure(
            area,
            "WS /v1/realtime: the socket's recheck",
            lambda: realtime.recheck(principal),
            note="once per socket per ACME_REALTIME_RECHECK_SECONDS",
        )
        ctx = principal.ctx
        await w.measure(
            area,
            "WS /v1/realtime: the head, read",
            lambda: realtime.head(ctx),
            note="the hello; a ping when the head heard is older than the bound",
        )
        await w.client.patch("/v1/me", headers=h, json={"display_name": "A hint for the pong"})
        await w.measure(
            area,
            "WS /v1/realtime: ping, head heard on the bus",
            lambda: realtime.pong_head(ctx),
            note="a ping within the bound of the last hint or read",
        )
    finally:
        detach()
    r = await w.client.post("/v1/realtime/tickets", headers=w.state["keyH"])
    principal = await w.measure(
        area,
        "WS /v1/realtime: redeem the ticket (api key)",
        lambda: tenancy.redeem_ticket(seed_request(), r.json()["ticket"]),
        note="the handshake's manager call",
    )
    if principal is not None:
        await w.measure(
            area,
            "WS /v1/realtime: the socket's recheck (api key)",
            lambda: realtime.recheck(principal),
            note="once per socket per ACME_REALTIME_RECHECK_SECONDS",
        )


# ---------------------------------------------------------------- the worker


async def drain(w: Any, label: str = "", most: int = 100) -> None:
    """Claims and runs every item ready now, one at a time, each counted from
    its claim to its settle."""
    loop = w.loop
    for _ in range(most):
        mark = w.mark()
        claimed = await loop._try_claim()
        if not claimed:
            w.record("worker", f"claim (nothing ready){label}", "none", w.since(mark))
            return
        ((running, (_, item)),) = list(loop._running.items())
        try:
            await running
        except Exception:
            pass
        rows = await w.sql(f"SELECT status FROM queue.work_items WHERE id = '{item.id}'")
        w.record(
            "worker",
            f"{item.kind.value}{label}",
            rows[0][0] if rows else "?",
            w.since(mark),
            note="claim, handle, and settle",
        )


# ---------------------------------------------------------------- an org deleted


async def team_org(w: Any) -> dict[str, Any]:
    """A team org the owner makes and switches to, uncounted: what `switch`
    leaves behind, for a run that names `org_deleted` without it."""
    st, name = w.state, f"Doomed {w.state['s']}"
    h = headers(await session(w, st["owner_email"], st["org"].id))
    r = await w.client.post("/v1/orgs", headers=h, json={"name": name})
    team = r.json()["org"]["id"]
    r = await w.client.post("/v1/auth/sessions", json={"org_id": team}, headers=h)
    return {"id": team, "name": name, "H": headers(r.json()["token"])}


async def org_deleted(w: Any) -> None:
    """The team org the switch made, deleted by its owner, and the DELETE_ORG
    item that closes it at the provider and removes it."""
    st = w.state
    team = st.get("team") or await team_org(w)
    area = "deletion"
    left = headers(await session(w, st["owner_email"], team["id"]))
    await w.http(
        area,
        "POST /v1/orgs/current/deletion (not the owner: refused)",
        "POST",
        "/v1/orgs/current/deletion",
        headers=w.state["bobH"],
        json={"name": "Ajax"},
    )
    await w.http(
        area,
        "POST /v1/orgs/current/deletion",
        "POST",
        "/v1/orgs/current/deletion",
        headers=team["H"],
        json={"name": team["name"]},
    )
    await w.http(area, "GET /v1/me (a session of the deleted org)", "GET", "/v1/me", headers=left)
    await drain(w, " (an org deleted)")


# ---------------------------------------------------------------- an account deleted


async def account_deleted(w: Any) -> None:
    """A person with a personal org alone deletes their account, and the
    DELETE_ACCOUNT item that ends them at the provider and removes the org."""
    s = w.state["s"]
    area = "deletion"
    email = f"leaving-{s}@example.test"
    h = headers(await personal_session(w, email))
    await w.http(
        area,
        "POST /v1/me/deletion (the wrong address: refused)",
        "POST",
        "/v1/me/deletion",
        headers=h,
        json={"email": f"other-{s}@example.test"},
    )
    await w.http(
        area, "POST /v1/me/deletion", "POST", "/v1/me/deletion", headers=h, json={"email": email}
    )
    await drain(w, " (an account deleted)")


# ---------------------------------------------------------------- sweep


async def sweep(w: Any) -> None:
    m = w.worker.managers
    rctx = worker_request()
    await w.measure(
        "sweep", "requeue_stale (nothing stale)", lambda: m.work.requeue_stale(rctx, 100)
    )
    await w.measure("sweep", "maintenance_contexts", lambda: m.work.maintenance_contexts(rctx))
    await w.measure("sweep", "gauges (the four reads)", lambda: w.loop._gauges())
    # A pass at two tenant counts, so its cost per tenant is measured, not guessed.
    tenancy, s = w.container.managers.tenancy, w.state["s"]
    for more in (0, 20):
        for i in range(more):
            await tenancy.bootstrap(
                seed_request(), f"More {i}", f"more-{s}-{i}", f"more-{s}-{i}@example.test", "M"
            )
        contexts = await m.work.maintenance_contexts(rctx)
        n = len(contexts or [])
        await w.measure(
            "sweep",
            f"a whole pass over {n} tenants",
            lambda: w.loop._sweep_once(),
            note=f"{n} tenant contexts",
        )


async def health(w: Any) -> None:
    for path in ("/healthz", "/readyz"):
        await w.http("health", f"GET {path}", "GET", path)


FLOWS = [
    seed,
    sign_in,
    members,
    invitations,
    api_keys,
    switch,
    files,
    events,
    org_deleted,
    account_deleted,
    sweep,
    health,
]
