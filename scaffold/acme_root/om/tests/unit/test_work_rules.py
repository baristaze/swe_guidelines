"""The work rules are pure: values in, values out."""

from datetime import timedelta

from contracts.work_storage import make_item

from acme.om.work.rules import (
    attempts_after_claim,
    attempts_after_hand_back,
    is_exhausted,
    retry_delay,
    stagger_delay,
)

BASE = timedelta(seconds=30)
CAP = timedelta(minutes=15)


def test_retry_delay_doubles_from_the_base_and_is_capped() -> None:
    assert retry_delay(0, BASE, CAP) == BASE
    assert retry_delay(1, BASE, CAP) == BASE
    assert retry_delay(2, BASE, CAP) == BASE * 2
    assert retry_delay(3, BASE, CAP) == BASE * 4
    assert retry_delay(5, BASE, CAP) == BASE * 16
    assert retry_delay(6, BASE, CAP) == CAP
    assert retry_delay(60, BASE, CAP) == CAP


def test_exhaustion_is_attempts_against_max_attempts() -> None:
    item = make_item().model_copy(update={"max_attempts": 2})
    assert not is_exhausted(item)
    assert not is_exhausted(item.model_copy(update={"attempts": 1}))
    assert is_exhausted(item.model_copy(update={"attempts": 2}))
    assert is_exhausted(item.model_copy(update={"attempts": 3}))


def test_a_claim_spends_an_attempt_and_a_hand_back_refunds_it() -> None:
    assert attempts_after_claim(0) == 1
    assert attempts_after_hand_back(attempts_after_claim(0)) == 0
    assert attempts_after_hand_back(attempts_after_claim(4)) == 4
    assert attempts_after_hand_back(0) == 0


def test_stagger_grows_with_position_from_zero() -> None:
    stagger = timedelta(seconds=5)
    assert stagger_delay(0, stagger) == timedelta(0)
    assert stagger_delay(1, stagger) == stagger
    assert stagger_delay(3, stagger) == stagger * 3
    assert stagger_delay(-1, stagger) == timedelta(0)
