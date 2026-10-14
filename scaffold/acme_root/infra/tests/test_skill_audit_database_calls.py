"""`audit-database-calls` counts the database calls of each flow, and
runs a failed flow again at most once."""

import pytest
from skills_support import prose_of

NAME = "audit-database-calls"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "the first run plus at most 1 rerun",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"
