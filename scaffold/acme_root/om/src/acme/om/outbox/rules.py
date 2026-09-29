"""Pure rules of the outbox: the relay's retry curve. Values in, values out;
no clock, no storage, no settings. The memory impl calls it and the
relational impl spells it in SQL inside the claim statement, which names
this rule."""

from datetime import timedelta

MAX_DOUBLINGS = 30
"""Past this the delay is the cap for any base worth having; it also keeps the
arithmetic in range for a row that has failed absurdly often."""


def relay_delay(attempts: int, base: timedelta, cap: timedelta) -> timedelta:
    """The delay before the next relay after `attempts` failed ones:
    base * 2^(attempts-1), capped."""
    doublings = min(max(0, attempts - 1), MAX_DOUBLINGS)
    return min(base * (2**doublings), cap)
