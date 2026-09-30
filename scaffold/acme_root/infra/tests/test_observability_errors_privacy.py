"""The error tracker never receives a secret: no frame locals, no request
body, no personal data, and a scrubber over what an event still carries. Nor
does it receive what a caller wrote as a request's path, query, or headers.

Nor does it, or a log line, receive an exception's text, which quotes what
the exception was handed: the input a model refused, the row a constraint
refused. What an operator reads of an error is its type, its frames in the
tree's own code, the request id, and the message of a failure the platform
raised, which names a backend, an operation, and a code."""

import io
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import sentry_sdk
from asyncpg.exceptions import PostgresError, UniqueViolationError
from pydantic import BaseModel, Field, ValidationError
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import IntegrityError

from acme.infra.exceptions import BackendUnreachable
from acme.infra.observability import (
    ERROR_REPORTING_PRIVACY,
    JsonFormatter,
    RequestIdFilter,
    configure_error_reporting,
    outgoing_event,
    request_id_var,
)

EMAIL = "ada.lovelace@example.com"
NAME = "Ada Byron King, Countess of Lovelace"
"""A member's address and display name: a tenant's words."""

RID = "0199aaaa-0000-7000-8000-000000000002"
log = logging.getLogger("acme.tests.errors")


def test_the_tracker_gets_no_locals_no_body_and_no_personal_data() -> None:
    assert ERROR_REPORTING_PRIVACY["include_local_variables"] is False
    assert ERROR_REPORTING_PRIVACY["send_default_pii"] is False
    assert ERROR_REPORTING_PRIVACY["max_request_body_size"] == "never"


def test_the_scrubber_blanks_bearers_cookies_and_passwords_wherever_they_sit() -> None:
    event = {
        "request": {
            "headers": {"Authorization": "Bearer tok", "Cookie": "s=1", "Accept": "*/*"},
            "data": {"password": "pswd_1234"},
        },
        "extra": {"nested": {"token": "abc", "database_url": "postgresql://u:p@h/d"}},
    }
    ERROR_REPORTING_PRIVACY["event_scrubber"].scrub_event(event)
    headers = event["request"]["headers"]
    assert headers["Authorization"] != "Bearer tok" and headers["Cookie"] != "s=1"
    assert headers["Accept"] == "*/*"
    assert event["request"]["data"]["password"] != "pswd_1234"
    assert event["extra"]["nested"]["token"] != "abc"
    assert "p@h" not in str(event["extra"]["nested"]["database_url"])


def test_an_event_leaves_with_the_request_id_and_nothing_the_caller_wrote() -> None:
    """The SDK's web integration fills `request` from the request: the URL,
    the query string, the headers. A caller writes each, and the tracker shows
    an event to whoever looks into the request, so only the method leaves."""
    words = "WIPZ_DOWN_ALL_RULZ"
    event = {
        "transaction": f"https://api.acme.example/{words}",
        "transaction_info": {"source": "url"},
        "request": {
            "method": "GET",
            "url": f"https://api.acme.example/{words}",
            "query_string": f"say={words}",
            "headers": {"user-agent": words, "x-note": words},
        },
    }
    token = request_id_var.set("0199aaaa-0000-7000-8000-000000000001")
    try:
        sent = outgoing_event(event, {})
    finally:
        request_id_var.reset(token)
    assert sent["request"] == {"method": "GET"}
    assert sent["transaction"] == "unmatched"
    assert sent["tags"] == {"request_id": "0199aaaa-0000-7000-8000-000000000001"}
    assert words not in str(sent)


def test_an_event_of_a_matched_route_keeps_its_transaction() -> None:
    event = {"transaction": "/v1/orgs/{org_id}", "transaction_info": {"source": "route"}}
    assert outgoing_event(event, {})["transaction"] == "/v1/orgs/{org_id}"


class Captured(Transport):
    """Where the SDK sends an event in place of a tracker: each one as it
    leaves `outgoing_event`."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(dict(event))


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> Iterator[Captured]:
    """The SDK as `configure_error_reporting` sets it up, with the logging
    integration alone and every event kept here."""
    captured = Captured()
    init = sentry_sdk.init

    def kept_here(*args: Any, **options: Any) -> Any:
        return init(
            *args,
            **options,
            transport=captured,
            default_integrations=False,
            auto_enabling_integrations=False,
            integrations=[LoggingIntegration()],
        )

    monkeypatch.setattr(sentry_sdk, "init", kept_here)
    with sentry_sdk.isolation_scope():
        configure_error_reporting("https://key@tracker.example.test/1", "test", "api")
        try:
            yield captured
        finally:
            sentry_sdk.get_client().close()
            sentry_sdk.get_global_scope().set_client(None)


@pytest.fixture
def lines() -> Iterator[io.StringIO]:
    """The JSON lines a deployed process writes, as `configure_logging` sets
    them up."""
    written = io.StringIO()
    handler = logging.StreamHandler(written)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.propagate = False
    try:
        yield written
    finally:
        log.removeHandler(handler)
        log.propagate = True


class Profile(BaseModel):
    email: str = Field(max_length=16)
    display_name: str = Field(max_length=16)


def update_profile() -> None:
    """A `PATCH /v1/me` whose body the model refuses: the error quotes each
    value it refused."""
    Profile.model_validate({"email": EMAIL, "display_name": NAME})


def insert_user() -> None:
    """An insert a unique key refuses, as SQLAlchemy raises it over asyncpg:
    its error over the adapter's over the driver's, each naming the row's
    value in the DETAIL line, with the bound values hidden. The driver builds
    its error from the fields the server answered with."""
    answered = {
        "C": "23505",
        "M": 'duplicate key value violates unique constraint "users_email"',
        "D": f"Key (email)=({EMAIL}) already exists.",
    }
    try:
        try:
            raise PostgresError.new(answered)
        except UniqueViolationError as error:
            raise AsyncAdapt_asyncpg_dbapi.IntegrityError(f"{type(error)}: {error}") from error
    except AsyncAdapt_asyncpg_dbapi.IntegrityError as error:
        statement = "INSERT INTO core.users (email, display_name) VALUES ($1, $2)"
        raise IntegrityError(statement, (EMAIL, NAME), error, hide_parameters=True) from error


def unhandled(raising: Callable[[], None]) -> BaseException:
    """What the API's middleware does with an exception no handler took."""
    token = request_id_var.set(RID)
    try:
        raising()
    except Exception as error:
        log.exception("unhandled error on PATCH /v1/me")
        return error
    finally:
        request_id_var.reset(token)
    raise AssertionError("nothing was raised")


@pytest.mark.parametrize(
    ("raising", "raised", "quoted"),
    [(update_profile, ValidationError, (EMAIL, NAME)), (insert_user, IntegrityError, (EMAIL,))],
    ids=["validation", "integrity"],
)
def test_an_exception_leaves_its_type_and_frames_and_none_of_its_text(
    tracker: Captured,
    lines: io.StringIO,
    raising: Callable[[], None],
    raised: type[BaseException],
    quoted: tuple[str, ...],
) -> None:
    error = unhandled(raising)
    assert all(word in str(error) for word in quoted), "its own text quotes the tenant"

    (event,) = tracker.events
    assert EMAIL not in json.dumps(event) and NAME not in json.dumps(event)
    exception = event["exception"]["values"][-1]
    assert exception["type"] == raised.__name__
    assert all("value" not in value for value in event["exception"]["values"])
    in_app = [frame["function"] for frame in exception["stacktrace"]["frames"] if frame["in_app"]]
    assert raising.__name__ in in_app
    assert event["tags"]["request_id"] == RID
    assert event["logentry"]["formatted"] == "unhandled error on PATCH /v1/me"

    written = lines.getvalue()
    assert EMAIL not in written and NAME not in written
    line = json.loads(written)
    assert line["request_id"] == RID
    assert line["exception"].endswith(f"\n{raised.__module__}.{raised.__qualname__}")
    assert f", in {raising.__name__}\n" in line["exception"]


def test_a_failure_the_platform_raised_keeps_its_message(
    tracker: Captured, lines: io.StringIO
) -> None:
    """A failure's message is the platform's: a backend, an operation, and
    what went wrong. The library error it was raised from keeps its type."""

    def select() -> None:
        try:
            raise OSError(f"no route for {EMAIL}")
        except OSError as error:
            raise BackendUnreachable("postgres", "select", type(error).__name__) from error

    unhandled(select)
    message = "postgres select could not reach the backend: OSError"
    (event,) = tracker.events
    cause, failure = event["exception"]["values"]
    assert (cause["type"], "value" in cause) == ("OSError", False)
    assert (failure["type"], failure["value"]) == ("BackendUnreachable", message)

    written = lines.getvalue()
    assert EMAIL not in written and EMAIL not in json.dumps(event)
    kind = f"{BackendUnreachable.__module__}.BackendUnreachable"
    assert json.loads(written)["exception"].endswith(f"\n{kind}: {message}")
    assert "\nOSError\n" in json.loads(written)["exception"]


def test_an_exception_a_line_names_is_its_type(tracker: Captured, lines: io.StringIO) -> None:
    """A line may name an exception in its message, and one that is not an
    event rides on the next event as a breadcrumb: each says its type."""
    log.warning("lease renewal failed on %s: %r", "item-1", ValueError(EMAIL))
    log.error("work task %s ended with %s", "work-1", KeyError(NAME))

    renewal, task = (json.loads(line) for line in lines.getvalue().splitlines())
    assert renewal["message"] == "lease renewal failed on item-1: 'ValueError'"
    assert task["message"] == "work task work-1 ended with KeyError"
    (event,) = tracker.events
    assert event["logentry"]["formatted"] == "work task work-1 ended with KeyError"
    crumbs = [crumb["message"] for crumb in event["breadcrumbs"]["values"]]
    assert crumbs == ["lease renewal failed on item-1: 'ValueError'"]
    assert EMAIL not in json.dumps(event) and NAME not in json.dumps(event)
