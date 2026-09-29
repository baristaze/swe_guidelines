"""Which reader an environment gets. A deployed environment reads its logs,
metrics, and traces out of its own account, so it needs no error tracker; the
local stack ships one and is refused without it."""

import pytest

from acme.ops.environments import Environment
from acme.ops.main import readback_text, signals_for
from acme.ops.signals import ErrorEventFound, Readback
from acme.ops.signals.cloud import SignalsCloudImpl
from acme.ops.signals.local import SignalsLocalImpl

RID = "11111111-2222-7333-8444-555555555555"


def environment(name: str, *, tracker: bool) -> Environment:
    return Environment(
        name=name,
        api_url="https://api.example.test",
        operator_token=None,
        provisioner_token=None,
        error_tracker_url="https://sentry.example.test" if tracker else None,
        error_tracker_token="stok" if tracker else None,
        error_tracker_org="ajax",
        error_tracker_project="acme",
        prometheus_url="http://prom" if name == "local" else None,
        jaeger_url="http://jaeger" if name == "local" else None,
        aws_profile=None,
        aws_region="us-west-2",
        seed=None,
    )


def test_a_deployed_environment_with_a_tracker_reads_error_events() -> None:
    signals = signals_for(environment("staging", tracker=True))
    assert isinstance(signals, SignalsCloudImpl) and signals.reads_error_events
    assert (
        "errors: Sentry https://sentry.example.test org ajax project acme "
        "by environment staging and tag request_id"
    ) in signals.describe()


def test_every_environment_reads_the_same_project_under_its_own_name() -> None:
    """One project for the product; the environment is what separates the
    events in it, so the reader carries the environment's name and no part of
    the read is named per environment."""
    staging = signals_for(environment("staging", tracker=True))
    production = signals_for(environment("production", tracker=True))
    assert isinstance(staging, SignalsCloudImpl) and isinstance(production, SignalsCloudImpl)
    assert staging.sentry_project == production.sentry_project == "acme"
    assert (staging.environment, production.environment) == ("staging", "production")


def test_a_deployed_environment_without_a_tracker_still_gets_a_reader() -> None:
    """Nothing provisions a tracker for a deployed environment, so a stress
    run must not stop on one being absent."""
    signals = signals_for(environment("staging", tracker=False))
    assert isinstance(signals, SignalsCloudImpl) and not signals.reads_error_events
    assert "errors: not read, the environment names no error tracker" in signals.describe()


def test_a_local_environment_still_needs_the_whole_stack() -> None:
    with pytest.raises(ValueError, match="names no Prometheus, Jaeger, and error tracker"):
        signals_for(environment("local", tracker=False))
    assert isinstance(signals_for(environment("local", tracker=True)), SignalsLocalImpl)


def test_the_error_event_line_tells_not_read_from_not_found() -> None:
    metric = "acme_http_requests_total"
    found = Readback(RID, error_event=ErrorEventFound("e1", "99", "boom"))
    assert "error event: e1 in issue 99 (boom)" in readback_text(found, metric)
    read_and_empty = Readback(RID)
    assert "error event: not found" in readback_text(read_and_empty, metric)
    unread = Readback(RID, error_events_read=False)
    assert "error event: not read, the environment names no error tracker" in readback_text(
        unread, metric
    )
