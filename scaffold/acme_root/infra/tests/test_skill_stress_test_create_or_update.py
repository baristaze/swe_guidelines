"""`stress-test-create-or-update` writes a stress scenario with its
target, with no cloud and no credential, and leaves its Next to the
person."""

import pytest
from skills_support import REFERENCE, prose_of

NAME = "stress-test-create-or-update"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "Its Next is the person's to run, never the session's",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"


def test_the_scenario_writer_holds_no_credential_and_needs_no_preamble() -> None:
    text = prose_of(NAME)
    assert "No cloud, no credential, no secret." in text
    assert REFERENCE not in text
