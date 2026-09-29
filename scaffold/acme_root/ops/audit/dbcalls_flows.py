"""The built-in flows `dbcalls.py run` drives, in order: the per-request
baseline, sign-in, the tenancy routes, tasks, attachments, the event stream,
billing, the worker's items, and the sweep. Each call is named the way the
report names it: the route and what makes this call differ (an empty list,
a full page, a keyed replay). area run covers every area once; an audit that
needs a path these do not reach adds a flows file of its own (`--flows`),
written the same way.

Flows run once per audit database: the people and orgs they make are
unique within one run, not across two.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "api" / "tests"))

from api_support import add_member, on_plan, seed_request

from acme.om.base import new_id
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import AppContext, AppType, RequestContext, Role

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


async def make_tasks(w: Any, h: dict[str, str], n: int, prefix: str) -> list[str]:
    ids = []
    for i in range(n):
        r = await w.client.post("/v1/tasks", headers=h, json={"title": f"{prefix} {i}"})
        ids.append(r.json()["id"])
    return ids


# ---------------------------------------------------------------- seed


async def seed(w: Any) -> None:
    """An org on Team with its owner and one member, both signed in."""
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
    await on_plan(w.container, st["org"].id, Plan.TEAM)
    st["bob"] = await add_member(w.container, st["org"].id, f"bob-{s}@example.test", Role.MEMBER)
    st["H"] = headers(await session(w, st["owner_email"], st["org"].id))
    st["bobH"] = headers(await session(w, f"bob-{s}@example.test", st["org"].id))


# ---------------------------------------------------------------- auth and the baseline


async def auth(w: Any) -> None:
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
    await w.http(base, "GET /v1/me (unknown token)", "GET", "/v1/me", headers=headers("tds_s_nope"))
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


# ---------------------------------------------------------------- tenancy


async def tenancy(w: Any) -> None:
    st, s, h = w.state, w.state["s"], w.state["H"]
    area = "tenancy"
    await w.http(area, "GET /v1/me", "GET", "/v1/me", headers=h)
    await w.http(
        area, "PATCH /v1/me", "PATCH", "/v1/me", headers=h, json={"display_name": "Owner base."}
    )
    await w.http(area, "GET /v1/orgs/current", "GET", "/v1/orgs/current", headers=h)
    await w.http(area, "GET /v1/users", "GET", "/v1/users", headers=h)
    await w.http(area, "GET /v1/memberships", "GET", "/v1/memberships", headers=h)
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
    await w.http(
        area,
        "POST /v1/invitations",
        "POST",
        "/v1/invitations",
        headers=h,
        json={"email": f"inv-{s}@example.test", "role": "member"},
    )
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
    await w.http(area, "GET /v1/sessions", "GET", "/v1/sessions", headers=h)
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
    await w.http(area, "DELETE /v1/api-keys/{id}", "DELETE", f"/v1/api-keys/{key_id}", headers=h)
    await w.http(area, "POST /v1/orgs", "POST", "/v1/orgs", headers=h, json={"name": f"Team {s}"})
    carl = await add_member(w.container, st["org"].id, f"carl-{s}@example.test", Role.MEMBER)
    await w.http(
        area, "DELETE /v1/memberships/{user}", "DELETE", f"/v1/memberships/{carl.id}", headers=h
    )


# ---------------------------------------------------------------- tasks


async def tasks(w: Any) -> None:
    st, s, h = w.state, w.state["s"], w.state["H"]
    area = "tasks"
    await w.http(area, "GET /v1/tasks (empty)", "GET", "/v1/tasks", headers=h)
    r = await w.http(area, "POST /v1/tasks", "POST", "/v1/tasks", headers=h, json={"title": "one"})
    one = r.json()
    await w.http(
        area,
        "POST /v1/tasks (due date, assignee)",
        "POST",
        "/v1/tasks",
        headers=h,
        json={"title": "two", "due_on": "2030-09-30", "assignee_id": str(st["bob"].id)},
    )
    keyed = {**h, "Idempotency-Key": f"t-{s}"}
    await w.http(
        area, "POST /v1/tasks (keyed)", "POST", "/v1/tasks", headers=keyed, json={"title": "three"}
    )
    await w.http(
        area,
        "POST /v1/tasks (keyed replay)",
        "POST",
        "/v1/tasks",
        headers=keyed,
        json={"title": "three"},
    )
    await w.http(area, "GET /v1/tasks/{id}", "GET", f"/v1/tasks/{one['id']}", headers=h)
    await w.http(area, "GET /v1/tasks/{id} (unknown)", "GET", f"/v1/tasks/{new_id()}", headers=h)
    r = await w.http(
        area,
        "PATCH /v1/tasks/{id} title",
        "PATCH",
        f"/v1/tasks/{one['id']}",
        headers={**h, "If-Match": f'"{one["version"]}"'},
        json={"title": "one!"},
    )
    version = r.json()["version"]
    await w.http(
        area,
        "PATCH /v1/tasks/{id} done",
        "PATCH",
        f"/v1/tasks/{one['id']}",
        headers={**h, "If-Match": f'"{version}"'},
        json={"status": "done"},
    )
    await w.http(
        area,
        "PATCH /v1/tasks/{id} (stale If-Match: 412)",
        "PATCH",
        f"/v1/tasks/{one['id']}",
        headers={**h, "If-Match": '"1"'},
        json={"title": "x"},
    )
    ids = await make_tasks(w, h, 5, "m")
    await w.http(
        area,
        "POST /v1/tasks/{id}/move (to the top)",
        "POST",
        f"/v1/tasks/{ids[3]}/move",
        headers=h,
        json={"after_id": None, "expected_version": 1},
    )
    await w.http(
        area,
        "POST /v1/tasks/{id}/move (after another)",
        "POST",
        f"/v1/tasks/{ids[0]}/move",
        headers=h,
        json={"after_id": ids[4], "expected_version": 1},
    )
    current = (await w.client.get(f"/v1/tasks/{ids[1]}", headers=h)).json()
    await w.http(
        area,
        "DELETE /v1/tasks/{id}",
        "DELETE",
        f"/v1/tasks/{ids[1]}",
        headers={**h, "If-Match": f'"{current["version"]}"'},
    )
    await w.http(area, "GET /v1/tasks/count", "GET", "/v1/tasks/count", headers=h)
    many = await make_tasks(w, h, 210, "p")
    await w.http(area, "GET /v1/tasks (a page of 50)", "GET", "/v1/tasks", headers=h)
    await w.http(
        area,
        "GET /v1/tasks?limit=200 (a full page)",
        "GET",
        "/v1/tasks",
        headers=h,
        params={"limit": 200},
    )
    await w.http(
        area, "GET /v1/tasks?scope=mine", "GET", "/v1/tasks", headers=h, params={"scope": "mine"}
    )
    await w.http(
        area, "GET /v1/tasks?status=done", "GET", "/v1/tasks", headers=h, params={"status": "done"}
    )
    await w.http(
        area,
        "POST /v1/tasks/bulk complete 1 id",
        "POST",
        "/v1/tasks/bulk",
        headers=h,
        json={"action": "complete", "ids": many[:1]},
    )
    await w.http(
        area,
        "POST /v1/tasks/bulk complete 100 ids",
        "POST",
        "/v1/tasks/bulk",
        headers=h,
        json={"action": "complete", "ids": many[1:101]},
    )
    await w.http(
        area,
        "POST /v1/tasks/bulk reopen all",
        "POST",
        "/v1/tasks/bulk",
        headers=h,
        json={"action": "reopen", "all": {"scope": "team", "status": "done"}},
    )


# ---------------------------------------------------------------- attachments


async def attachments(w: Any) -> None:
    h = w.state["H"]
    area = "attachments"
    task = (await w.client.post("/v1/tasks", headers=h, json={"title": "with files"})).json()
    tid = task["id"]
    body = {"name": "plan.pdf", "content_type": "application/pdf", "size_bytes": len(PDF)}
    r = await w.http(
        area,
        "POST /v1/tasks/{id}/attachments",
        "POST",
        f"/v1/tasks/{tid}/attachments",
        headers=h,
        json=body,
    )
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
    await w.http(
        area, "GET /v1/tasks/{id}/attachments", "GET", f"/v1/tasks/{tid}/attachments", headers=h
    )
    await w.http(area, "GET /v1/media/usage", "GET", "/v1/media/usage", headers=h)
    current = (await w.client.get(f"/v1/tasks/{tid}", headers=h)).json()
    await w.http(
        "tasks",
        "DELETE /v1/tasks/{id} (with an attachment)",
        "DELETE",
        f"/v1/tasks/{tid}",
        headers={**h, "If-Match": f'"{current["version"]}"'},
    )


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
        await w.client.post("/v1/tasks", headers=h, json={"title": "a hint for the pong"})
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


# ---------------------------------------------------------------- billing


async def billing(w: Any) -> None:
    h = w.state["H"]
    await w.http("billing", "GET /v1/billing", "GET", "/v1/billing", headers=h)
    await w.http(
        "billing", "GET /v1/billing (a member)", "GET", "/v1/billing", headers=w.state["bobH"]
    )


# ---------------------------------------------------------------- worker


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
        ((task, (_, item)),) = list(loop._running.items())
        try:
            await task
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


async def worker(w: Any) -> None:
    h = w.state["H"]
    r = await w.client.post(
        "/v1/tasks", headers=h, json={"title": "remind me", "due_on": "2030-01-02"}
    )
    task_id = r.json()["id"]
    await w.sql(f"UPDATE core.tasks SET due_on = current_date - 1 WHERE id = '{task_id}'")
    # The reminder waits for nine in the morning of its day; its day is past.
    await w.sql(
        "UPDATE queue.work_items SET available_at = now()"
        " WHERE status = 'queued' AND available_at > now()"
    )
    await drain(w)


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


FLOWS = [seed, auth, tenancy, tasks, attachments, events, billing, worker, sweep, health]
