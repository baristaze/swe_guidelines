"""The shared retry wait: the curve, its cap at any attempt, the jitter drawn
from the source handed in, a server's ask as the floor, one loop's count,
and the reader of `Retry-After`."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from acme.timing import Retries, RetryPolicy, retry_after

NOW = datetime(2026, 10, 21, 7, 28, tzinfo=UTC)


def test_the_wait_grows_by_its_growth_from_the_base_to_the_cap() -> None:
    doubling = RetryPolicy(base=1.0, cap=30.0)
    assert [doubling.wait(a) for a in range(1, 8)] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    tripling = RetryPolicy(base=0.5, cap=20.0, growth=3.0)
    assert [tripling.wait(a) for a in range(1, 6)] == [0.5, 1.5, 4.5, 13.5, 20.0]
    # An attempt before the first waits what the first does.
    assert doubling.wait(0) == doubling.wait(-3) == 1.0
    # A base past the cap waits the cap from the first attempt.
    assert RetryPolicy(base=60.0, cap=30.0).wait(1) == 30.0


@pytest.mark.parametrize("attempt", [10_000, 10**18])
def test_the_wait_stays_at_the_cap_however_many_attempts_failed(attempt: int) -> None:
    for policy in (
        RetryPolicy(base=0.25, cap=5.0),
        RetryPolicy(base=1e-6, cap=86_400.0, growth=1.01),
        RetryPolicy(base=1.0, cap=30.0, jitter=0.5),
    ):
        assert policy.wait(attempt, draw=lambda: 1.0) == policy.cap


def test_a_zero_base_waits_nothing_at_any_attempt() -> None:
    policy = RetryPolicy(base=0.0, cap=30.0)
    assert [policy.wait(a) for a in (1, 2, 10_000)] == [0.0, 0.0, 0.0]
    assert policy.wait(3, retry_after=2.0) == 2.0


def test_the_jitter_share_is_drawn_from_the_source_handed_in() -> None:
    half = RetryPolicy(base=4.0, cap=4.0, jitter=0.5)
    assert half.wait(1, draw=lambda: 0.0) == 2.0
    assert half.wait(1, draw=lambda: 1.0) == 4.0
    assert half.wait(1, draw=lambda: 0.25) == 2.5
    full = RetryPolicy(base=4.0, cap=4.0, jitter=1.0)
    assert (full.wait(1, draw=lambda: 0.0), full.wait(1, draw=lambda: 1.0)) == (0.0, 4.0)
    none = RetryPolicy(base=4.0, cap=4.0)
    assert none.wait(1, draw=lambda: 0.0) == none.wait(1, draw=lambda: 1.0) == 4.0
    # The real source stays in by default, inside the bounds.
    assert all(2.0 <= half.wait(1) <= 4.0 for _ in range(50))


def test_a_servers_ask_above_the_curve_is_the_floor_and_the_cap_its_bound() -> None:
    policy = RetryPolicy(base=1.0, cap=10.0)
    assert policy.wait(1, retry_after=3.0) == 3.0
    assert policy.wait(3, retry_after=3.0) == 4.0, "the curve is above the ask"
    assert policy.wait(1, retry_after=600.0) == 10.0
    assert policy.wait(1, retry_after=None) == 1.0
    assert policy.bound(0.2, 1.0) == 1.0 and policy.bound(0.2, None) == 0.2


def test_the_steps_to_the_cap_are_the_powers_the_curve_ever_raises() -> None:
    assert RetryPolicy(base=1.0, cap=30.0).steps_to_cap() == 5
    assert RetryPolicy(base=1.0, cap=32.0).steps_to_cap() == 5
    assert RetryPolicy(base=30.0, cap=100.0).steps_to_cap() == 2
    assert RetryPolicy(base=0.1, cap=0.8).steps_to_cap() == 3
    for flat in (
        RetryPolicy(base=0.0, cap=5.0),
        RetryPolicy(base=5.0, cap=5.0),
        RetryPolicy(base=1.0, cap=5.0, growth=1.0),
    ):
        assert flat.steps_to_cap() == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"base": -1.0, "cap": 5.0},
        {"base": 1.0, "cap": -5.0},
        {"base": 1.0, "cap": 5.0, "growth": 0.5},
        {"base": 1.0, "cap": 5.0, "jitter": 1.5},
        {"base": 1.0, "cap": 5.0, "jitter": -0.1},
        {"base": math.nan, "cap": 5.0},
        {"base": 1.0, "cap": math.inf},
    ],
)
def test_a_policy_that_is_no_wait_is_refused(fields: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**fields)


def test_retries_count_each_failure_and_start_over_after_a_success() -> None:
    retries = Retries(RetryPolicy(base=1.0, cap=8.0))
    assert retries.failures == 0
    assert [retries.failed() for _ in range(5)] == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert retries.failures == 5
    retries.reset()
    assert retries.failures == 0
    assert retries.failed() == 1.0 and retries.failures == 1
    assert retries.failed(retry_after=6.0) == 6.0, "the ask is the floor of this wait"


def test_retries_draw_from_the_source_handed_in() -> None:
    policy = RetryPolicy(base=2.0, cap=8.0, jitter=1.0)
    assert Retries(policy, draw=lambda: 0.0).failed() == 0.0
    assert Retries(policy, draw=lambda: 1.0).failed() == 2.0


def test_retry_after_reads_seconds_and_an_http_date() -> None:
    assert retry_after("2") == 2.0
    assert retry_after(" 120 ") == 120.0
    assert retry_after("Wed, 21 Oct 2026 07:28:30 GMT", now=NOW) == 30.0
    # RFC 5322's unknown zone is UTC.
    assert retry_after("Wed, 21 Oct 2026 07:29:00 -0000", now=NOW) == 60.0
    assert retry_after("Wed, 21 Oct 2026 07:00:00 GMT", now=NOW) == 0.0, "a date past asks for none"
    later = datetime.now(UTC) + timedelta(hours=1)
    asked = retry_after(later.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    assert asked is not None and 3500 < asked <= 3600, "against the current time by default"


@pytest.mark.parametrize(
    "value",
    [None, "", "soon", "-1", "1.5", "²", "Wed, 32 Oct 2026 07:28:00 GMT", "Wed, 21 Oct 99999"],
)
def test_retry_after_gives_none_for_a_value_it_cannot_read(value: str | None) -> None:
    assert retry_after(value, now=NOW) is None
