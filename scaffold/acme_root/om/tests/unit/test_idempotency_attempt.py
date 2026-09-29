from datetime import timedelta

from contracts.idempotency_storage import attempt_minted_at

from acme.om.base import new_id, utcnow
from acme.om.idempotency.types.attempt import lease_bound


def test_the_bound_sorts_between_the_attempts_that_began_before_it_and_after() -> None:
    cutoff = utcnow()
    bound = lease_bound(cutoff)
    assert attempt_minted_at(cutoff - timedelta(minutes=2)) < bound
    assert attempt_minted_at(cutoff + timedelta(milliseconds=1)) > bound
    assert new_id() > lease_bound(cutoff - timedelta(minutes=2)), "minted well past the cut-off"


def test_an_attempt_minted_in_the_cut_offs_millisecond_has_let_the_lease_pass() -> None:
    # A token carries the millisecond it was minted in and nothing finer, so
    # the millisecond of the cut-off itself counts as past: a lease of zero
    # hands the marker on at once, and against a real lease one millisecond is
    # nothing.
    cutoff = utcnow()
    assert attempt_minted_at(cutoff) < lease_bound(cutoff)
