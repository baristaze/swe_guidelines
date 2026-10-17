"""Pure rules of the outbox: the relay's retry curve. Values in, values out;
no clock, no storage, no settings. The memory impl calls it and the
relational impl spells it in SQL inside the claim statement, which names
this rule."""

from datetime import timedelta

from acme.timing import RetryPolicy


def relay_policy(base: timedelta, cap: timedelta) -> RetryPolicy:
    """The relay's curve: `base`, doubling per failed relay, up to `cap`,
    with nothing drawn, so the claim statement can spell it."""
    return RetryPolicy(base=base.total_seconds(), cap=cap.total_seconds())


def relay_delay(attempts: int, base: timedelta, cap: timedelta) -> timedelta:
    """The delay before the next relay after `attempts` failed ones:
    base * 2^(attempts-1), capped."""
    return timedelta(seconds=relay_policy(base, cap).wait(attempts))
