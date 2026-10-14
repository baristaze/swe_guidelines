"""`ops-integration-silent` follows one inbound integration's deliveries
until they stop, with each query's polls and its hops of Next counted."""

import pytest
from skills_support import prose_of

NAME = "ops-integration-silent"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "Poll `get-query-results` at most 10 times for one query, each poll after `sleep 5`",
    'as "not read: the query did not finish in 10 polls"',
    "Step 3's query is polled the same way.",
    "A run follows at most 2 hops of Next",
    "never more than 10 polls of a query",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"
