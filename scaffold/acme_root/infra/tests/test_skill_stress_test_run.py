"""`stress-test-run` runs a scenario and judges it against its target,
with the hops of Next a session follows after it counted."""

import pytest
from skills_support import prose_of

NAME = "stress-test-run"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "A session follows at most 2 hops of Next.",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"
