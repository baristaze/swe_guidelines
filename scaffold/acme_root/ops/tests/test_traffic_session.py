"""One session over the fake edge: the steps in order, an idempotency key
on every creating call, the socket seeing its own change, the stream read
from where it stood, and the report at the end. A session runs under the
token its seat holds and never signs in for itself; the run does that once
per person. Think time is zero and the clock is the test's."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fake_api import OWNER_ID, FakeApi, connect_to

from acme.client.client import ApiClient
from acme.ops.environments import Environment, SeedPeople
from acme.ops.main import FAILED, OK, traffic_exit_code
from acme.ops.profiles import LIGHT, Profile
from acme.ops.report import AUTH_ROUTES, Report, Sample, Sessions
from acme.ops.stress import Readback, load_scenario, verdict
from acme.ops.traffic import (
    LOGIN_WINDOW_SECONDS,
    MAX_LOGIN_WAITS,
    NO_ONE_SIGNED_IN,
    Clock,
    Person,
    RecordingTransport,
    Seat,
    Session,
    SessionOutcome,
    route_template,
    run_traffic,
    seat_order,
)

PERSON = Person("owner@example.test", "ajax")
SEAT = Seat(PERSON, "ses_1", OWNER_ID)
"""The seat the fake's sign-in hands out: `ses_1` is the token it accepts."""

HERE = Path(__file__).parent


def quick_clock(seconds: float = 30.0) -> Clock:
    loop = asyncio.get_running_loop()
    return Clock(loop.time() + seconds, (0.0, 0.0), now=loop.time, uniform=lambda a, b: 0.0)


def client_over(api: FakeApi, samples: list[Sample]) -> tuple[ApiClient, RecordingTransport]:
    recording = RecordingTransport(httpx.MockTransport(api), also=samples)
    client = ApiClient("http://test", app="portal", app_version="ops@test", transport=recording)
    return client, recording


def local_env() -> Environment:
    return Environment(
        name="local",
        api_url="http://test",
        operator_token=None,
        provisioner_token=None,
        error_tracker_url=None,
        error_tracker_token=None,
        error_tracker_org="acme",
        error_tracker_project="acme",
        prometheus_url=None,
        jaeger_url=None,
        aws_profile=None,
        aws_region=None,
        seed=SeedPeople("ajax", "owner@example.test", "owner@example.test"),
    )


def test_ids_in_a_path_read_as_a_template() -> None:
    assert (
        route_template("/v1/tasks/0199a4c0-0000-7000-8000-000000000001/move")
        == "/v1/tasks/{id}/move"
    )
    assert route_template("/v1/tasks") == "/v1/tasks"


def test_the_people_to_sign_in_are_taken_one_org_at_a_time() -> None:
    """A run signs in fewer people than a profile provisions, so it takes
    them one from each org in turn and no org is left with no traffic."""
    people = [
        Person(f"{who}@{org}", org)
        for org in ("one", "two", "three")
        for who in ("owner", "member")
    ]
    assert [p.org_slug for p in seat_order(people)[:4]] == ["one", "two", "three", "one"]


async def test_a_session_walks_every_step_in_order() -> None:
    api = FakeApi()
    samples: list[Sample] = []
    client, recording = client_over(api, samples)
    async with client:
        session = Session(
            client,
            SEAT,
            quick_clock(),
            recording=recording,
            connect=connect_to(api),
            task_count=lambda: 5,
        )
        outcome = await session.run()
    assert outcome.failure is None, outcome.failure
    assert outcome.completed and outcome.saw_own_change and not outcome.cut
    steps = [f"{r.method} {route_template(r.url.path)}" for r in api.requests]
    assert steps == [
        "POST /v1/realtime/tickets",
        "GET /v1/tasks",
        *["POST /v1/tasks"] * 5,
        "PATCH /v1/tasks/{id}",  # edit one
        "PATCH /v1/tasks/{id}",  # complete two
        "PATCH /v1/tasks/{id}",
        "PATCH /v1/tasks/{id}",  # reopen one
        "POST /v1/tasks/{id}/move",
        "GET /v1/tasks",  # the done list
        "DELETE /v1/tasks/{id}",
        "GET /v1/events",
    ]
    assert not any(r.url.path in AUTH_ROUTES for r in api.requests)
    creates = [r for r in api.requests if r.method == "POST" and r.url.path == "/v1/tasks"]
    keys = {r.headers["idempotency-key"] for r in creates}
    assert len(keys) == 5
    assert all(r.headers["x-app"] == "portal" for r in api.requests)
    assert all(r.headers["authorization"] == "Bearer ses_1" for r in api.requests)
    lists = [r for r in api.requests if r.url.path == "/v1/tasks" and r.method == "GET"]
    assert [r.url.params["status"] for r in lists] == ["open", "done"]
    events = next(r for r in api.requests if r.url.path == "/v1/events")
    assert events.url.params["after_seq"] == "10"  # where the stream stood at the hello
    assert len(outcome.write_request_ids) == 5
    assert len(samples) == len(api.requests)
    assert {s.status for s in samples} == {200, 201}


async def test_a_retried_refusal_is_two_samples_and_a_completed_session() -> None:
    """A keyed creating call the API refused with a 503 is sent again by the
    client; both attempts are requests the report counts."""
    api = FakeApi(fail_on="POST /v1/tasks")
    samples: list[Sample] = []
    client, recording = client_over(api, samples)
    async with client:
        outcome = await Session(
            client,
            SEAT,
            quick_clock(),
            recording=recording,
            connect=connect_to(api),
            task_count=lambda: 5,
        ).run()
    assert outcome.completed and outcome.failure is None
    creates = [s.status for s in samples if s.route == "/v1/tasks" and s.method == "POST"]
    assert creates == [503, 201, 201, 201, 201, 201]
    assert len(outcome.write_request_ids) == 5


async def test_a_refusal_the_client_does_not_retry_fails_the_session() -> None:
    api = FakeApi(fail_on="DELETE /v1/tasks/{id}")
    samples: list[Sample] = []
    client, recording = client_over(api, samples)
    async with client:
        outcome = await Session(
            client, SEAT, quick_clock(), recording=recording, connect=connect_to(api)
        ).run()
    assert outcome.failure == "503 unavailable on the API"
    assert not outcome.completed
    assert samples[-1].status == 503 and samples[-1].is_error


async def test_the_deadline_cuts_a_session_between_steps() -> None:
    api = FakeApi()
    samples: list[Sample] = []
    client, _ = client_over(api, samples)
    async with client:
        outcome = await Session(client, SEAT, quick_clock(0.0), connect=connect_to(api)).run()
    assert outcome.cut and not outcome.completed and outcome.failure is None
    assert api.requests == []


async def test_a_refused_sign_in_signs_no_one_in_and_the_run_drives_nothing() -> None:
    """The sign-in is the run's, once per person, so a stack whose local
    sign-in is off ends the run there instead of failing session after
    session."""
    api = FakeApi(dev_sign_in=False)
    result = await run_traffic(
        local_env(),
        Profile("light", 1, 2, 2, (0.0, 0.0), 60),
        duration_seconds=5,
        orgs=0,
        transport=httpx.MockTransport(api),
        connect=connect_to(api),
    )
    assert result.outcomes == [] and result.report.sessions.started == 0
    assert [r.url.path for r in api.requests] == ["/v1/auth/dev-sign-in"] * 2
    assert any("404 not_found" in note for note in result.report.notes)
    assert NO_ONE_SIGNED_IN in result.report.notes


class Waits:
    """The pauses a run asked for, recorded instead of slept: a test that
    waits out a minute-long window twice takes no longer than one that does
    not wait at all."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


async def test_a_refused_sign_in_waits_out_the_window_and_asks_again() -> None:
    """The step before a run signs in from the same address, so the run can
    meet a window that is already full. It waits the window out and asks
    again for the same person, and the run drives as it would have."""
    api = FakeApi(refuse_logins=1)
    waits = Waits()
    result = await run_traffic(
        local_env(),
        Profile("light", 1, 2, 2, (0.0, 0.0), 60),
        duration_seconds=20,
        orgs=0,
        max_sessions=2,
        transport=httpx.MockTransport(api),
        connect=connect_to(api),
        login_wait=waits,
    )
    assert waits.seconds == [LOGIN_WINDOW_SECONDS]
    report = result.report
    assert [r.url.path for r in api.requests][:3] == [
        "/v1/auth/dev-sign-in",  # refused: the window was full
        "/v1/auth/dev-sign-in",  # the same person again, once it had passed
        "/v1/auth/sessions",
    ]
    assert report.sessions.completed == 2
    assert any("429 rate_limited" in note for note in report.notes)
    assert any("waited 60 s for the login window and asked again" in note for note in report.notes)
    assert any("signed in 2 of 2 people" in note for note in report.notes)
    assert traffic_exit_code(report) == OK


async def test_the_answers_retry_after_says_how_long_the_run_waits() -> None:
    api = FakeApi(refuse_logins=1, retry_after="3")
    waits = Waits()
    result = await run_traffic(
        local_env(),
        Profile("light", 1, 2, 2, (0.0, 0.0), 60),
        duration_seconds=20,
        orgs=0,
        max_sessions=1,
        transport=httpx.MockTransport(api),
        connect=connect_to(api),
        login_wait=waits,
    )
    assert waits.seconds == [3.0]
    assert any("waited 3 s for the login window" in note for note in result.report.notes)
    assert result.report.sessions.completed == 1


async def test_a_login_window_that_never_opens_is_bounded_and_the_run_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An environment that refuses every sign-in fails the run fast: the
    waits are bounded, the last refusal stands, and the exit status and the
    message name the rate limit."""
    api = FakeApi(refuse_logins=99)
    waits = Waits()
    result = await run_traffic(
        local_env(),
        Profile("light", 1, 2, 2, (0.0, 0.0), 60),
        duration_seconds=5,
        orgs=0,
        transport=httpx.MockTransport(api),
        connect=connect_to(api),
        login_wait=waits,
    )
    assert waits.seconds == [LOGIN_WINDOW_SECONDS] * MAX_LOGIN_WAITS
    assert [r.url.path for r in api.requests] == ["/v1/auth/dev-sign-in"] * (MAX_LOGIN_WAITS + 1)
    report = result.report
    assert result.outcomes == [] and report.sessions.started == 0
    assert any("429 rate_limited" in note for note in report.notes)
    assert any(f"still closed after {MAX_LOGIN_WAITS} wait(s)" in note for note in report.notes)
    assert NO_ONE_SIGNED_IN in report.notes
    assert traffic_exit_code(report) == FAILED
    assert "per-address rate limit on POST /v1/auth/dev-sign-in" in capsys.readouterr().err


async def test_a_person_signs_in_once_for_the_run_and_the_target_judges_the_rest() -> None:
    """One sign-in and one sign-out per person, however many sessions that
    person drives, and the p95 the target holds is the working requests': a
    slow sign-in is reported beside the verdict and does not decide it."""
    api = FakeApi()
    result = await run_traffic(
        local_env(),
        Profile("light", 1, 2, 2, (0.0, 0.0), 60),
        duration_seconds=20,
        orgs=0,
        max_sessions=4,
        transport=httpx.MockTransport(api),
        connect=connect_to(api),
    )
    report = result.report
    assert report.sessions.completed == 4
    paths = [r.url.path for r in api.requests]
    assert paths.count("/v1/auth/dev-sign-in") == 2  # the seeded org's two people, once each
    assert paths.count("/v1/auth/sessions") == 2
    assert paths.count("/v1/auth/logout") == 2
    assert paths[:4] == ["/v1/auth/dev-sign-in", "/v1/auth/sessions"] * 2  # at the start
    assert paths[-2:] == ["/v1/auth/logout"] * 2  # and at the end
    assert report.auth.requests == 6
    assert report.working.requests == report.requests - 6
    assert any("signed in 2 of 2 people" in note for note in report.notes)

    # The sign-ins are the slow ones, as they are on a deployed environment.
    # The verdict's p95 is the working requests', so the run still passes.
    slow = [
        replace(s, elapsed_ms=5000.0) if s.is_auth else replace(s, elapsed_ms=10.0)
        for s in result.samples
    ]
    judged = Report.of(
        slow,
        environment="local",
        profile="light",
        started_at=datetime(2026, 9, 20, tzinfo=UTC),
        duration_seconds=20,
        sessions=Sessions(completed=4),
    )
    assert judged.auth.p95_ms == 5000.0 and judged.working.p95_ms == 10.0
    scenario = load_scenario(HERE / "smoke.yaml")  # target: p95 500 ms
    outcome = verdict(scenario, judged, Readback(100, 0))
    assert outcome.passed and outcome.reasons == ()
    assert "6 sign-ins and sign-outs" in outcome.text(scenario, judged, Readback(100, 0))


async def test_a_run_over_the_seeded_org_bounds_sessions_and_reports() -> None:
    api = FakeApi()
    profile = Profile("light", 1, 2, 2, (0.0, 0.0), 60)
    result = await run_traffic(
        local_env(),
        profile,
        duration_seconds=20,
        orgs=0,
        max_sessions=3,
        transport=httpx.MockTransport(api),
        connect=connect_to(api),
    )
    report = result.report
    assert report.sessions.completed == 3 and report.sessions.failed == 0
    assert report.profile == "light" and report.environment == "local"
    assert report.errors == 0 and report.requests == len(api.requests)
    assert any(
        r.route == "/v1/tasks" and r.method == "POST" and r.status == 201 for r in report.routes
    )
    assert report.notes[0] == "orgs 0: the seeded org 'ajax' and its two people"
    # The run says what the profile did and hands out one request id to read
    # the signals back by; the fake transport answers no x-request-id, so it
    # says so instead of inventing one.
    assert report.notes[-2].startswith("profile light: 2 at once, think time")
    assert report.notes[-1].startswith("sample request id: ")
    assert len(result.outcomes) == 3 and all(o.write_request_ids for o in result.outcomes)


class RefusesTheWork(FakeApi):
    """Signs a person in, opens their socket, and refuses every other route:
    a session that fails as soon as it starts working."""

    def answer(self, request: httpx.Request) -> httpx.Response:
        if request.url.path in AUTH_ROUTES or request.url.path == "/v1/realtime/tickets":
            return super().answer(request)
        return httpx.Response(403, json={"error": {"code": "forbidden", "message": "no"}})


async def test_a_failed_session_pauses_the_worker_before_the_next_one() -> None:
    """A session that fails must not loop: the worker waits before starting
    another, so a refusal is not a flood."""
    api = RefusesTheWork()
    result = await run_traffic(
        local_env(),
        Profile("light", 1, 1, 1, (0.0, 0.0), 60),
        duration_seconds=0.6,
        orgs=0,
        transport=httpx.MockTransport(api),
        connect=connect_to(api),
        pause_after_failure=0.25,
    )
    assert result.report.sessions.failed in (2, 3)  # not hundreds
    assert all(o.failure == "403 forbidden on the API" for o in result.outcomes)


async def test_a_run_needs_seeded_people_or_a_provisioner() -> None:
    env = Environment(
        name="local",
        api_url="http://test",
        operator_token=None,
        provisioner_token=None,
        error_tracker_url=None,
        error_tracker_token=None,
        error_tracker_org="t",
        error_tracker_project="t",
        prometheus_url=None,
        jaeger_url=None,
        aws_profile=None,
        aws_region=None,
        seed=None,
    )
    with pytest.raises(ValueError, match="seeded people"):
        await run_traffic(
            env, LIGHT, duration_seconds=1, orgs=0, transport=httpx.MockTransport(FakeApi())
        )
    with pytest.raises(ValueError, match="provisioner"):
        await run_traffic(
            env, LIGHT, duration_seconds=1, orgs=1, transport=httpx.MockTransport(FakeApi())
        )


# Shared tasks: the 412 and the 404 a session meets when another session
# wrote first. The fake's `interfere` is that other session.


async def session_over(api: FakeApi) -> tuple[SessionOutcome, list[str]]:
    samples: list[Sample] = []
    client, recording = client_over(api, samples)
    async with client:
        outcome = await Session(
            client,
            SEAT,
            quick_clock(),
            recording=recording,
            connect=connect_to(api),
            task_count=lambda: 5,
        ).run()
    steps = [f"{r.method} {route_template(r.url.path)}" for r in api.requests]
    return outcome, steps


async def test_a_412_on_a_move_reads_the_task_afresh_and_moves_it_on_the_fresh_version() -> None:
    """Another writer changed the task, so the version this session holds is
    stale: the API says 412, and the session reads the task and moves it once
    more. A conflict, counted, and not a failure."""
    api = FakeApi(interfere={"POST /v1/tasks/{id}/move": ["bump"]})
    outcome, steps = await session_over(api)
    assert outcome.completed and outcome.failure is None, outcome.failure
    assert (outcome.conflicts, outcome.gone) == (1, 0)
    at = steps.index("POST /v1/tasks/{id}/move")
    assert steps[at : at + 3] == [
        "POST /v1/tasks/{id}/move",  # 412: the task moved on
        "GET /v1/tasks/{id}",  # the fresh read
        "POST /v1/tasks/{id}/move",  # on the fresh version, and it lands
    ]
    moves = [r for r in api.requests if r.url.path.endswith("/move")]
    sent = [json.loads(r.content)["expected_version"] for r in moves]
    assert sent[1] == sent[0] + 1


async def test_a_second_412_leaves_the_step_undone_and_the_session_goes_on() -> None:
    """One retry, as a client makes: a task that keeps moving under the
    session is left as it is, and the session goes on to its next step."""
    api = FakeApi(interfere={"PATCH /v1/tasks/{id}": ["bump", "bump"]})
    outcome, steps = await session_over(api)
    assert outcome.completed and outcome.failure is None, outcome.failure
    assert (outcome.conflicts, outcome.gone) == (2, 0)
    assert steps[7:10] == ["PATCH /v1/tasks/{id}", "GET /v1/tasks/{id}", "PATCH /v1/tasks/{id}"]
    first = next(t for t in api.tasks.values() if t["title"].endswith("task 1"))
    assert not first["title"].endswith("(edited)")


async def test_a_task_deleted_under_the_session_is_dropped_and_its_later_steps_left_out() -> None:
    """Another session deleted a task this one was about to complete: the
    write answers 404, the fresh read says it is gone, and the reopen that
    would have followed is left out."""
    api = FakeApi(interfere={"PATCH /v1/tasks/{id}": ["pass", "delete"]})
    outcome, steps = await session_over(api)
    assert outcome.completed and outcome.failure is None, outcome.failure
    assert (outcome.conflicts, outcome.gone) == (0, 1)
    assert steps[7:] == [
        "PATCH /v1/tasks/{id}",  # edit one
        "PATCH /v1/tasks/{id}",  # complete it: 404, gone
        "GET /v1/tasks/{id}",  # 404: gone indeed
        "PATCH /v1/tasks/{id}",  # complete the other; no reopen of the gone one
        "POST /v1/tasks/{id}/move",
        "GET /v1/tasks",
        "DELETE /v1/tasks/{id}",
        "GET /v1/events",
    ]


async def test_a_412_on_a_delete_then_a_404_on_its_retry_is_a_conflict_and_a_task_gone() -> None:
    """The ticket's race: one session deletes a task while another moves
    it. The delete meets 412, the fresh read finds the task, and by the
    retry the other session has deleted it: 404, and the session is done."""
    api = FakeApi(interfere={"DELETE /v1/tasks/{id}": ["bump", "delete"]})
    outcome, steps = await session_over(api)
    assert outcome.completed and outcome.failure is None, outcome.failure
    assert (outcome.conflicts, outcome.gone) == (1, 1)
    at = steps.index("DELETE /v1/tasks/{id}")
    assert steps[at:] == [
        "DELETE /v1/tasks/{id}",  # 412
        "GET /v1/tasks/{id}",  # still there, at a later version
        "DELETE /v1/tasks/{id}",  # 404: deleted by the other session meanwhile
        "GET /v1/tasks/{id}",  # 404: gone
        "GET /v1/events",
    ]


class NotFoundOnDelete(FakeApi):
    """Answers a delete with 404 while the task is still there to read."""

    def answer(self, request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "no"}})
        return super().answer(request)


async def test_a_404_on_a_task_that_is_still_there_fails_the_session() -> None:
    """A 404 is taken as a task gone only when the fresh read agrees; when
    the task is there, something else was not found, and that is a failure."""
    outcome, steps = await session_over(NotFoundOnDelete())
    assert outcome.failure == "404 not_found on the API"
    assert (outcome.conflicts, outcome.gone) == (0, 0)
    assert steps[-2:] == ["DELETE /v1/tasks/{id}", "GET /v1/tasks/{id}"]


async def test_a_run_counts_conflicts_apart_from_errors_and_failures() -> None:
    """A run whose sessions met conflicts completes them, exits 0, and says
    how many it met: a line in the table and two numbers in the JSON."""
    api = FakeApi(
        interfere={
            "POST /v1/tasks/{id}/move": ["bump"],
            "DELETE /v1/tasks/{id}": ["delete"],
        }
    )
    result = await run_traffic(
        local_env(),
        Profile("light", 1, 2, 2, (0.0, 0.0), 60),
        duration_seconds=20,
        orgs=0,
        max_sessions=2,
        transport=httpx.MockTransport(api),
        connect=connect_to(api),
    )
    report = result.report
    s = report.sessions
    assert (s.completed, s.failed, s.conflicts, s.gone) == (2, 0, 1, 1)
    assert report.errors == 0
    assert any(r.status == 412 for r in report.routes)
    assert "conflicts: 1 re-read, 1 gone" in report.table()
    assert json.loads(report.to_json())["sessions"]["conflicts"] == 1
    assert traffic_exit_code(report) == OK
    # The stress run drives the same sessions and says the same, beside its verdict.
    scenario = load_scenario(HERE / "smoke.yaml")
    text = verdict(scenario, report, Readback(100, 0)).text(scenario, report, Readback(100, 0))
    assert "conflicts: 1 answered by a fresh read, 1 on a task found gone" in text
