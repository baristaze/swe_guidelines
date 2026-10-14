"""`ops-investigate` reads every signal of one environment over one
window, asks the error tracker for that window its own way, and writes
the cloud's p95 as one number."""

import pytest
from skills_support import prose_of, text_of

NAME = "ops-investigate"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "Poll `get-query-results` at most 10 times for one query, each poll after `sleep 5`",
    'as "not read: the query did not finish in 10 polls"',
    "Step 8's query is polled the same way.",
    "A session follows at most 2 hops of Next.",
    "never more than 10 polls of a query",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"


def test_the_tracker_read_asks_for_its_window_the_trackers_way() -> None:
    """The tracker's `statsPeriod` takes `24h` and `14d` and answers 400 to
    any other window, so the read asks Sentry for the issues last seen in it.
    GlitchTip reads that term as a tag and answers with no issue, deployed or
    local, so only Sentry's own host is sent it."""
    text = text_of(NAME)
    assert 'in *sentry.io*) window=" lastSeen:-<since>" ;; *) window="" ;; esac' in text
    assert '"query=environment:<env>$window"' in text
    assert "statsPeriod=" not in text
    assert "keep the issues whose `lastSeen` field is inside the window" in prose_of(NAME)


def test_the_cloud_p95_of_an_investigation_is_one_number_in_milliseconds() -> None:
    text = prose_of(NAME)
    assert "The report's p95 is its highest minute times 1,000, in milliseconds" in text
    assert "The cloud has no p95 by route" in text
    assert "p95 <ms> by route (cloud: one p95 <ms, or none>, every route together)" in text
