"""`ops-cloud-deployment-create` takes an environment to its first
deploy, and leaves its Next to the person."""

import pytest
from skills_support import prose_of

NAME = "ops-cloud-deployment-create"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "Its Next is the person's to run, never the session's",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"
