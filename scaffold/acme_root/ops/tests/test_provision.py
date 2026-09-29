"""The tenants of a run: created under the provisioner's token and never a
sign-in, named for the run, and removed when the run ends, a failed run
and a failed start included."""

import json
from uuid import uuid4

import httpx
import pytest

from acme.client.client import ApiError
from acme.ops.environments import Environment
from acme.ops.profiles import Profile
from acme.ops.traffic import (
    DeployedSignIn,
    TokenRefused,
    is_run_tenant,
    provision,
    run_traffic,
)

NOW = "2026-09-20T12:00:00Z"


def environment(token: str | None = "opt_write", name: str = "staging") -> Environment:
    return Environment(
        name=name,
        api_url="http://test",
        operator_token=None,
        provisioner_token=token,
        error_tracker_url=None,
        error_tracker_token=None,
        error_tracker_org="acme",
        error_tracker_project="acme",
        prometheus_url=None,
        jaeger_url=None,
        aws_profile=None,
        aws_region=None,
        seed=None,
    )


class OperatorPlane:
    """The three operator routes a run's tenants go through, and a local
    sign-in that is off, so the run signs no one in and still removes what it
    made."""

    def __init__(self, *, refuse_create_after: int | None = None, token: str = "opt_write") -> None:
        self.orgs: dict[str, str] = {}
        self.plans: dict[str, str] = {}
        self.deleted: list[str] = []
        self.requests: list[httpx.Request] = []
        self.refuse_create_after = refuse_create_after
        self.token = token

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method, path = request.method, request.url.path
        if path == "/v1/auth/dev-sign-in":
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "no"}})
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(
                401, json={"error": {"code": "not_authenticated", "message": "no"}}
            )
        assert request.headers["x-app"] == "admin"
        body = json.loads(request.content) if request.content else {}
        if (method, path) == ("POST", "/v1/admin/orgs"):
            if self.refuse_create_after is not None and len(self.orgs) >= self.refuse_create_after:
                return httpx.Response(409, json={"error": {"code": "conflict", "message": "taken"}})
            org_id = str(uuid4())
            self.orgs[org_id] = body["slug"]
            org = {
                "id": org_id,
                "name": body["name"],
                "slug": body["slug"],
                "kind": "team",
                "created_at": NOW,
            }
            return httpx.Response(201, json={**org, "deleted_at": None})
        if method == "PUT" and path.endswith("/plan"):
            org_id = path.split("/")[-2]
            self.plans[org_id] = body["plan"]
            plan = {"plan": body["plan"], "paid_plan": None, "comped_plan": body["plan"]}
            return httpx.Response(200, json={**plan, "status": None, "ends_at": None})
        if method == "POST" and path.endswith("/members"):
            assert self.plans.get(path.split("/")[-2]) == "max", "a member joins after the grant"
            user = {
                "id": str(uuid4()),
                "email": body["email"],
                "display_name": "M",
                "created_at": NOW,
            }
            return httpx.Response(201, json=user)
        if method == "DELETE" and path.startswith("/v1/admin/orgs/"):
            org_id = path.rsplit("/", 1)[1]
            self.deleted.append(org_id)
            slug = self.orgs[org_id]
            return httpx.Response(
                200,
                json={
                    "id": org_id,
                    "name": slug,
                    "slug": slug,
                    "kind": "team",
                    "created_at": NOW,
                    "deleted_at": NOW,
                },
            )
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})


PROFILE = Profile("light", 2, 2, 1, (0.0, 0.0), 60)


async def test_tenants_are_named_for_the_run_under_the_provisioner_token() -> None:
    plane = OperatorPlane()
    tenants = await provision(environment(), PROFILE, httpx.MockTransport(plane), run_id="r1")
    assert sorted(plane.orgs.values()) == ["ops-r1-1", "ops-r1-2"]
    assert all(is_run_tenant(slug) for slug in plane.orgs.values())
    assert len(tenants.people) == 4 and len(tenants.orgs_created) == 2
    assert plane.plans == dict.fromkeys(plane.orgs, "max")
    assert not any(r.url.path.startswith("/v1/auth/") for r in plane.requests)


async def test_a_start_that_fails_part_way_removes_what_it_made() -> None:
    plane = OperatorPlane(refuse_create_after=1)
    with pytest.raises(ApiError) as refused:
        await provision(environment(), PROFILE, httpx.MockTransport(plane), run_id="r2")
    assert refused.value.status == 409
    assert plane.deleted == list(plane.orgs)


async def test_a_refused_token_names_the_command_that_writes_a_fresh_one() -> None:
    plane = OperatorPlane(token="another")
    with pytest.raises(TokenRefused, match="acme-ops token --env staging --identity provisioner"):
        await provision(environment(), PROFILE, httpx.MockTransport(plane))


async def test_a_run_removes_its_tenants_when_it_ends_even_when_no_one_signed_in() -> None:
    plane = OperatorPlane()
    result = await run_traffic(
        environment(name="local"),
        PROFILE,
        duration_seconds=0.3,
        transport=httpx.MockTransport(plane),
        pause_after_failure=0.1,
    )
    assert result.report.sessions.started == 0
    assert any("was refused at sign-in: 404" in note for note in result.report.notes)
    assert sorted(plane.deleted) == sorted(plane.orgs)
    assert "removed 2 of the run's 2 org(s)" in result.report.notes


async def test_a_run_on_a_deployed_environment_is_refused_before_it_provisions() -> None:
    """A deployed environment signs people in through the identity provider,
    and the run's people sign in by the local sign-in, which it does not
    serve: nothing is created, and the refusal says why."""
    plane = OperatorPlane()
    for orgs in (0, 2):
        with pytest.raises(DeployedSignIn, match="only the local stack serves"):
            await run_traffic(
                environment(), PROFILE, orgs=orgs, transport=httpx.MockTransport(plane)
            )
    assert plane.requests == []
