"""`ops-watch` is a live tail with a cap: a sub-agent polls one closed
interval at a time, reads each minute of a metric once, and never writes
a read of nothing as a number."""

import pytest
from skills_support import PREAMBLE, ROOT, prose_of

NAME = "ops-watch"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "at least 30 seconds",
    "A shorter interval is raised to 30 seconds",
    "A watch runs at most 30 batches.",
    "the interval is widened to `--for` divided by 30, up to five minutes",
    "an hour asked at 10 seconds runs 30 batches of two minutes",
    "its report names the part of the window it did not watch",
    "A batch makes at most 20 tool calls.",
    "A read that would be the 21st call is not made",
    "with `k` from 0 to at most 29",
    "The period of each `get-metric-data` query, its `Period` and the last argument "
    "of a `SEARCH` expression, is a whole minute",
    "The sub-agent names its Next and never runs it",
    "A session follows at most 2 hops of Next.",
    "never more than 30 batches, never a batch shorter than 30 seconds, "
    "never more than 20 tool calls in a batch",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"


# A watch's batches start at its own second, and a metric's datapoint is a
# whole minute: a batch that passed its own bounds would read the minute it
# shares with the next batch twice, and every count would be about double.
ONE_READ_A_MINUTE = [
    "the two bounds a batch passes are its start and its end, each rounded down to a whole minute",
    "no minute is read twice",
    "makes no metric read; the next batch reads that minute",
    "The period of each `get-metric-data` query, its `Period` and the last argument of a "
    "`SEARCH` expression, is a whole minute, 60 seconds, whatever the batch interval",
    "a count is `increase(<metric>[1m])`",
    "each a range query from the rounded start plus 60 seconds to the rounded end, "
    "at a 60-second `step`",
]


@pytest.mark.parametrize("sentence", ONE_READ_A_MINUTE)
def test_a_watch_reads_each_minute_of_a_metric_once(sentence: str) -> None:
    assert sentence in prose_of(NAME), f"ops-watch no longer says: {sentence}"


# A cloud read that returned nothing sums to zero, and a p95 no request fed is
# `null`. Neither is a number of the batch: a zero reads as traffic stopping.
NOTHING_READ_IS_NOT_A_ZERO = [
    "in the cloud a batch whose `requests` is 0 read nothing",
    'Such a batch writes "metrics not read" in place of its numbers, never a zero',
    "no later batch reads those minutes",
    "the batch line's p95 is that number times 1,000, in milliseconds, "
    "and a `null` writes `p95 none`",
    "p95 <ms, or none>, <failures> worker failures "
    "| metrics read in the next batch | metrics not read>",
]


@pytest.mark.parametrize("sentence", NOTHING_READ_IS_NOT_A_ZERO)
def test_a_watch_never_writes_a_read_of_nothing_as_a_number(sentence: str) -> None:
    assert sentence in prose_of(NAME), f"ops-watch no longer says: {sentence}"


def test_the_watch_hands_its_sub_agent_the_preamble_from_the_repository_root() -> None:
    """A sub-agent gets the skill's text without its folder, so a path from
    that folder does not resolve for it; the path from the root does."""
    root_path = PREAMBLE.relative_to(ROOT).as_posix()
    assert root_path == ".agents/skills/_shared/ops-preamble.md"
    assert f"`{root_path}`" in prose_of(NAME)
