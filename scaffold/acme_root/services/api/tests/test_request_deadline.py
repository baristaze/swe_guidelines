"""A request's deadline (ADR 0069). Admission gives an admitted request one,
counted from when it takes its slot, and every call the request makes to a
provider or to AWS shares it. So a provider that hangs costs the request its
deadline, not its timeouts times its retries: a sign-in through a WorkOS
that never answers is unavailable by then; the three WorkOS calls of an
invitation, each well inside its timeout, share one deadline; and an upload
whose store did not take the bytes in time is unavailable and stays
pending. A scan then holds every manager to handing each such call its
context's deadline."""

import ast
import asyncio
import inspect
import json
import time
from annotationlib import Format
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from api_support import build_container, sign_in

from acme.infra.base import utcnow
from acme.infra.buckets import BucketsInterface
from acme.infra.deadline import PASSED
from acme.infra.exceptions import BackendUnreachable
from acme.infra.queues import QueuesInterface
from acme.infra.secrets import SecretsInterface
from acme.integrations.identity import IdentityProviderInterface
from acme.integrations.identity.workos import CREDENTIAL_CHECK_CODE, IdentityProviderWorkOSImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.gateway.auth import Rctx

NOW = "2026-09-26T10:00:00.000Z"


@asynccontextmanager
async def serving(container: AppContainer) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_an_admitted_request_carries_its_deadline(tmp_path: Path) -> None:
    """The request stage carries the instant its time runs out, counted from
    admission, which the calls it makes are handed."""
    container = build_container(tmp_path, request_deadline_seconds=7)
    app = create_app(container)
    seen: list[datetime | None] = []

    async def probe(rctx: Rctx) -> dict[str, bool]:
        seen.append(rctx.deadline)
        return {"seen": True}

    app.get("/v1/deadline-probe", include_in_schema=False)(probe)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            before = utcnow()
            answer = await client.get("/v1/deadline-probe")
            after = utcnow()
    assert answer.status_code == 200, answer.text
    [deadline] = seen
    assert deadline is not None
    assert before + timedelta(seconds=7) <= deadline <= after + timedelta(seconds=7)


# WorkOS, as the transport the real client sends through.


def organization(external_id: str) -> dict[str, Any]:
    return {
        "object": "organization",
        "id": "org_workos_1",
        "name": "Ajax",
        "external_id": external_id,
        "metadata": {},
        "created_at": NOW,
        "updated_at": NOW,
        "domains": [],
    }


def invitation(email: str) -> dict[str, Any]:
    return {
        "object": "invitation",
        "id": "invitation_1",
        "email": email,
        "state": "pending",
        "accepted_at": None,
        "revoked_at": None,
        "expires_at": "2026-10-03T10:00:00.000Z",
        "organization_id": "org_workos_1",
        "inviter_user_id": None,
        "accepted_user_id": None,
        "role_slug": None,
        "created_at": NOW,
        "updated_at": NOW,
        "token": "tok",
        "accept_invitation_url": "https://auth.example/invite?token=tok",
    }


class WorkOS:
    """WorkOS as a transport: the credential check at start answers as it
    does for the application's own key, at once; every other call is
    answered by `answer`, which may take its time."""

    def __init__(self, answer: Callable[[httpx.Request], Awaitable[httpx.Response]]) -> None:
        self.calls: list[str] = []
        self._answer = answer

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if json.loads(request.content or b"{}").get("code") == CREDENTIAL_CHECK_CODE:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.calls.append(f"{request.method} {request.url.path}")
        return await self._answer(request)


def over(tmp_path: Path, workos: WorkOS, deadline: float) -> AppContainer:
    """The API with the real WorkOS client, as a deployed process builds it:
    a ten second timeout and the SDK's three retries."""
    provider = IdentityProviderWorkOSImpl(
        client_id="client_test",
        api_key="sk_test_not_a_key",
        timeout=timedelta(seconds=10),
        transport=httpx.MockTransport(workos),
    )
    return build_container(
        tmp_path,
        integrations=IntegrationsOverImpl(provider),
        request_deadline_seconds=deadline,
    )


async def test_a_sign_in_whose_provider_hangs_is_unavailable_by_its_deadline(
    tmp_path: Path,
) -> None:
    """A WorkOS that takes the code exchange and never answers held the
    callback for about fifty seconds, four attempts of the timeout. Under
    a half-second deadline the callback answers unavailable in half a second,
    the refusal the portal already reads as "try again"."""

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(3600)
        raise AssertionError("never answered")

    workos = WorkOS(hang)
    async with serving(over(tmp_path, workos, deadline=0.5)) as client:
        began = time.monotonic()
        answer = await asyncio.wait_for(
            client.post("/v1/auth/callback", json={"code": "c", "code_verifier": "v" * 43}), 10
        )
        waited = time.monotonic() - began
    assert answer.status_code == 503, answer.text
    assert answer.json()["error"]["code"] == "unavailable"
    assert 0.45 <= waited < 2.0, waited
    # The exchange, cut at the deadline, and no attempt after it. On a runner
    # slow enough that the request's own work takes the whole half second,
    # the exchange never starts, which is the deadline too.
    assert workos.calls in ([], ["POST /user_management/authenticate"])


INVITATION = (
    "GET /organizations/external_id/",
    "POST /organizations",
    "POST /user_management/invitations",
)
"""The WorkOS calls of an org's first invitation, in turn: find the org's
organization, make it, send the invitation."""


async def test_the_calls_of_one_request_share_its_deadline(tmp_path: Path) -> None:
    """An org's first invitation makes three WorkOS calls in turn. Each
    answers in three tenths of a second, a thirtieth of its timeout, and
    each fits in the request's eight tenths; the three together do not. So
    the deadline ends one of them, where a budget per call would end none:
    the calls before it answered in full, it was cut, and none came after
    it. Which call that is depends on how long the request's own work
    around the calls takes on the runner: the third on a quick one, an
    earlier one on a slow one. The test holds whichever it is."""
    answered: list[str] = []

    async def slowly(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        answered.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return httpx.Response(404, json={"message": "not found"})
        if request.url.path == "/organizations":
            return httpx.Response(
                201, json=organization(json.loads(request.content)["external_id"])
            )
        return httpx.Response(201, json=invitation(json.loads(request.content)["email"]))

    workos = WorkOS(slowly)
    container = over(tmp_path, workos, deadline=0.8)
    async with serving(container) as client:
        owner = await sign_in(client, container)
        workos.calls.clear()
        answered.clear()
        began = time.monotonic()
        answer = await asyncio.wait_for(
            client.post(
                "/v1/invitations",
                headers={**owner, "Idempotency-Key": "inv-1"},
                json={"email": "bob@example.test", "role": "member"},
            ),
            10,
        )
        waited = time.monotonic() - began
    assert answer.status_code == 503, answer.text
    assert answer.json()["error"]["code"] == "unavailable"
    # At the deadline, not before it, and far short of one call's ten second
    # timeout. How far past it depends on the runner too.
    assert 0.75 <= waited < 5, waited
    made = workos.calls
    assert len(made) <= len(INVITATION), made
    assert all(call.startswith(step) for call, step in zip(made, INVITATION, strict=False)), made
    # Every call before the last answered in full. The last was cut waiting
    # for its answer, or answered as the deadline passed.
    assert answered in (made, made[:-1]), (made, answered)


# An upload, whose bytes go to the store on the request.


async def test_an_upload_whose_store_did_not_answer_in_time_is_unavailable(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The store did not take the bytes by the request's deadline. The
    answer is the refusal a client reads as "try again", the file is still
    pending, and nothing confirms it until the bytes arrive."""

    async def unreachable(*args: Any, **kwargs: Any) -> None:
        raise BackendUnreachable("s3", "put", PASSED)

    started = await client.post(
        "/v1/media/files",
        headers=owner,
        json={"name": "report.pdf", "content_type": "application/pdf", "size_bytes": 5},
    )
    assert started.status_code == 201, started.text
    file_id = started.json()["id"]
    monkeypatch.setattr(container.infra.get_buckets(), "put", unreachable)
    put = await client.put(f"/v1/media/files/{file_id}/content", headers=owner, content=b"%PDF-")
    assert put.status_code == 503, put.text
    assert put.json()["error"]["code"] == "unavailable"
    kept = await client.get(f"/v1/media/files/{file_id}", headers=owner)
    assert kept.json()["status"] == "pending"
    confirmed = await client.post(f"/v1/media/files/{file_id}/confirm", headers=owner)
    assert confirmed.status_code == 422, confirmed.text


# Every call handed its deadline.

DEADLINED: tuple[type, ...] = (
    IdentityProviderInterface,
    SecretsInterface,
    BucketsInterface,
    QueuesInterface,
)
"""The doors to a provider and to AWS whose calls a request makes."""

SCANNED = ("om/src", "services/api/src")
"""Where a request's calls are made: the managers and the API's services."""


def repository_root() -> Path:
    """The checkout this test runs in: the nearest folder above it with an
    `.env.example`, so a copy that is not yet a repository reads its own."""
    here = Path(__file__).resolve().parent
    return next(p for p in (here, *here.parents) if (p / ".env.example").is_file())


def deadlined_methods() -> dict[str, set[str]]:
    """For each door, by its name, the calls that take a deadline."""
    return {
        door.__name__: {
            name
            for name, member in vars(door).items()
            if inspect.iscoroutinefunction(member)
            and "deadline" in inspect.signature(member, annotation_format=Format.STRING).parameters
        }
        for door in DEADLINED
    }


def doors_of(cls: ast.ClassDef) -> dict[str, str]:
    """The attributes a class keeps a door in, by the annotation of the
    `__init__` argument it keeps: `self._queues = queues`, `queues: QueuesInterface`."""
    init = next(
        (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None
    )
    if init is None:
        return {}
    annotated = {
        arg.arg: ast.unparse(arg.annotation)
        for arg in (*init.args.args, *init.args.kwonlyargs)
        if arg.annotation is not None
    }
    kept: dict[str, str] = {}
    for node in ast.walk(init):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and node.value.id in annotated
                ):
                    kept[target.attr] = annotated[node.value.id]
    return kept


def calls_to_doors() -> list[tuple[str, str, bool]]:
    """Every call a class makes through a door it keeps, to a method that
    takes a deadline: (site, method, handed one)."""
    methods = deadlined_methods()
    root = repository_root()
    found: list[tuple[str, str, bool]] = []
    for scanned in SCANNED:
        for path in sorted((root / scanned).rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
                doors = doors_of(cls)
                for call in ast.walk(cls):
                    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                        continue
                    receiver = call.func.value
                    if not (
                        isinstance(receiver, ast.Attribute)
                        and isinstance(receiver.value, ast.Name)
                        and receiver.value.id == "self"
                    ):
                        continue
                    door = doors.get(receiver.attr)
                    if door is None or call.func.attr not in methods.get(door, set()):
                        continue
                    handed = any(keyword.arg == "deadline" for keyword in call.keywords)
                    site = f"{path.relative_to(root)}:{call.lineno}"
                    found.append((site, f"{door}.{call.func.attr}", handed))
    return found


def test_every_call_a_manager_makes_to_a_provider_or_aws_is_handed_its_deadline() -> None:
    """A manager hands every call that takes a deadline its context's: the
    request's on a request, none on a worker's item. A call left without one
    waits for its timeouts and retries while the request holds its slot."""
    found = calls_to_doors()
    assert {method for _, method, _ in found} >= {
        "IdentityProviderInterface.authenticate_code",
        "IdentityProviderInterface.start_device",
        "IdentityProviderInterface.authenticate_device",
        "IdentityProviderInterface.get_organization",
        "IdentityProviderInterface.accepted_invitation",
        "IdentityProviderInterface.ensure_organization",
        "IdentityProviderInterface.send_invitation",
        "IdentityProviderInterface.find_pending_invitation",
        "IdentityProviderInterface.resend_invitation",
        "IdentityProviderInterface.revoke_invitation",
        "IdentityProviderInterface.portal_link",
        "BucketsInterface.put",
        "BucketsInterface.get",
        "BucketsInterface.exists",
        "QueuesInterface.send",
    }, "the scan no longer sees a call it used to; widen it before trusting it"
    missing = [f"{site} {method}" for site, method, handed in found if not handed]
    assert not missing, f"calls not handed their deadline: {missing}"
