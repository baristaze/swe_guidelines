"""Pure rules of the work namespace: the retry curve, the exhaustion test,
a tenant's cap on a lane, and the attempt and stagger arithmetic. Values in,
values out; no clock, no storage, no settings. The manager and both storage
impls call these; the relational impl spells the exhaustion test, the
claim's attempt count, and the cap in SQL where one statement must decide,
and each such place names the rule it mirrors."""

from datetime import timedelta

from acme.om.work.types.work_item import WorkItem

MAX_DOUBLINGS = 30
"""Past this the delay is the cap for any base worth having; it also keeps the
arithmetic in range for an item that has failed absurdly often."""


def retry_delay(attempts: int, base: timedelta, cap: timedelta) -> timedelta:
    """A growing delay after `attempts` failed runs: base * 2^(attempts-1), capped."""
    doublings = min(max(0, attempts - 1), MAX_DOUBLINGS)
    return min(base * (2**doublings), cap)


def is_exhausted(item: WorkItem) -> bool:
    """True once the item has spent every attempt it was given."""
    return item.attempts >= item.max_attempts


def attempts_after_claim(attempts: int) -> int:
    """A claim spends one attempt."""
    return attempts + 1


def attempts_after_hand_back(attempts: int) -> int:
    """A hand-back (defer, release) refunds the attempt the claim spent."""
    return max(0, attempts - 1)


def is_at_cap(held: int, cap: int) -> bool:
    """True when the claim passes a tenant over on a lane: it already holds
    `cap` items claimed there under a live lease."""
    return held >= cap


def stagger_delay(position: int, stagger: timedelta) -> timedelta:
    """The delay before the item at `position` in a sweep becomes available again,
    so a recovered dependency is not met by every stale item at once."""
    return stagger * max(0, position)
