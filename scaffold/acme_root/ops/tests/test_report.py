"""The report is arithmetic over samples: nearest-rank percentiles, the error
ratio over wire failures and 5xx only, one line per route and status, and the
working requests kept apart from the sign-ins that carry them."""

import json
from datetime import UTC, datetime

from acme.ops.report import Report, Sample, Sessions, percentile

AT = datetime(2026, 9, 20, tzinfo=UTC)


def test_percentiles_are_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile(values, 99) == 99.0
    assert percentile([7.0], 99) == 7.0
    assert percentile([], 50) == 0.0
    assert percentile([3.0, 1.0, 2.0], 50) == 2.0


def test_the_report_groups_by_route_and_status_and_counts_errors() -> None:
    samples = [
        Sample("/v1/api-keys", "POST", 201, 10.0, request_id="r1"),
        Sample("/v1/api-keys", "POST", 201, 30.0, request_id="r2"),
        Sample("/v1/api-keys/{id}", "DELETE", 404, 5.0),
        Sample("/v1/users", "GET", 503, 100.0),
        Sample("/v1/users", "GET", 0, 1000.0, failure="ConnectError"),
    ]
    report = Report.of(
        samples,
        environment="local",
        profile="light",
        started_at=AT,
        duration_seconds=3.0,
        sessions=Sessions(completed=1, failed=1),
    )
    assert [(r.method, r.route, r.status, r.count) for r in report.routes] == [
        ("DELETE", "/v1/api-keys/{id}", 404, 1),
        ("GET", "/v1/users", 0, 1),
        ("GET", "/v1/users", 503, 1),
        ("POST", "/v1/api-keys", 201, 2),
    ]
    assert report.requests == 5
    assert report.errors == 2  # the 503 and the wire failure; the 404 is a decision
    assert report.error_ratio == 0.4
    posts = next(r for r in report.routes if r.method == "POST")
    assert (posts.p50_ms, posts.p95_ms, posts.p99_ms) == (10.0, 30.0, 30.0)
    assert report.working.requests == 5 and report.working.p99_ms == 1000.0
    assert report.auth.requests == 0  # none of these is a sign-in


def test_the_sign_ins_are_totalled_beside_the_working_requests() -> None:
    """A target's p95 is the working requests', so the report splits them:
    the sign-in and the sign-out of a run are few and not what it measures."""
    samples = [
        Sample("/v1/auth/dev-sign-in", "POST", 200, 900.0),
        Sample("/v1/auth/sessions", "POST", 200, 40.0),
        Sample("/v1/users", "GET", 200, 10.0),
        Sample("/v1/api-keys", "POST", 201, 20.0),
        Sample("/v1/auth/logout", "POST", 200, 30.0),
    ]
    report = Report.of(
        samples,
        environment="local",
        profile="light",
        started_at=AT,
        duration_seconds=3.0,
        sessions=Sessions(completed=1),
    )
    assert report.requests == 5
    assert (report.auth.requests, report.auth.p95_ms) == (3, 900.0)
    assert (report.working.requests, report.working.p95_ms) == (2, 20.0)
    table = report.table()
    assert "working, the requests a target judges: 2 requests" in table
    assert "sign-in and sign-out, reported beside them: 3 requests" in table


def test_the_table_and_the_json_say_the_same() -> None:
    report = Report.of(
        [Sample("/v1/me", "GET", 200, 2.5, request_id="r")],
        environment="local",
        profile="light",
        started_at=AT,
        duration_seconds=1.0,
        sessions=Sessions(completed=1),
        notes=["orgs 0"],
    )
    table = report.table()
    assert "GET     /v1/me" in table and "requests 1, errors 0 (0.00%)" in table
    assert "note: orgs 0" in table
    data = json.loads(report.to_json())
    assert data["requests"] == 1 and data["started_at"] == AT.isoformat()
    assert data["working"]["requests"] == 1 and data["auth"]["requests"] == 0
    assert data["routes"][0]["route"] == "/v1/me"
    assert data["sessions"] == {"completed": 1, "failed": 0, "cut": 0}
