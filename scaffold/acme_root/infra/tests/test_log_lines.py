"""What every log line carries: the service and the environment, so one query
reads across processes, the request, and the request that caused it where a
handoff named one. The filter attaches all four; no call site passes any."""

import json
import logging

from acme.infra.observability import (
    JsonFormatter,
    RequestIdFilter,
    caused_by_request_id_var,
    configure_error_reporting,
    name_process,
    request_id_var,
)


def line(record: logging.LogRecord) -> dict[str, object]:
    RequestIdFilter().filter(record)
    parsed = json.loads(JsonFormatter().format(record))
    assert isinstance(parsed, dict)
    return parsed


def record() -> logging.LogRecord:
    return logging.LogRecord("acme.test", logging.INFO, "f.py", 1, "hello", None, None)


def test_a_line_names_the_process_and_the_request() -> None:
    name_process("maintenance", "staging")
    token = request_id_var.set("11111111-1111-1111-1111-111111111111")
    try:
        written = line(record())
    finally:
        request_id_var.reset(token)
    assert written["service"] == "maintenance"
    assert written["environment"] == "staging"
    assert written["request_id"] == "11111111-1111-1111-1111-111111111111"
    assert "caused_by_request_id" not in written, "nothing caused this line"


def test_a_run_that_a_handoff_started_names_both_requests() -> None:
    name_process("maintenance", "staging")
    run = request_id_var.set("22222222-2222-2222-2222-222222222222")
    cause = caused_by_request_id_var.set("33333333-3333-3333-3333-333333333333")
    try:
        written = line(record())
    finally:
        caused_by_request_id_var.reset(cause)
        request_id_var.reset(run)
    assert written["request_id"] == "22222222-2222-2222-2222-222222222222"
    assert written["caused_by_request_id"] == "33333333-3333-3333-3333-333333333333"


def test_a_process_that_named_itself_nothing_still_writes_the_fields() -> None:
    # Before the boot step that names it, and in a test that never boots.
    name_process("unknown", "unknown")
    written = line(record())
    assert (written["service"], written["environment"]) == ("unknown", "unknown")
    assert written["request_id"] == "-"


def test_configuring_error_reporting_does_not_name_the_process() -> None:
    # Naming the process is a boot step of its own, beside configuring logging,
    # so the fields on every line do not hang off a call about error reporting.
    name_process("api", "staging")
    configure_error_reporting(None, "prod", "somebody-else")
    written = line(record())
    assert (written["service"], written["environment"]) == ("api", "staging")


def test_the_access_line_carries_its_fields_as_fields() -> None:
    access = record()
    access.http = {"method": "GET", "route": "/v1/me", "status": 200, "duration_ms": 12.5}
    assert line(access)["http"] == {
        "method": "GET",
        "route": "/v1/me",
        "status": 200,
        "duration_ms": 12.5,
    }
    assert "http" not in line(record()), "a line that is not the access line carries none"
