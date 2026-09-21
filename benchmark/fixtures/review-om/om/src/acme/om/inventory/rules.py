from datetime import datetime, timedelta

from acme.om.base import utcnow


def free_capacity(capacity: int, stocked: int) -> int:
    return max(0, capacity - stocked)


def is_stale(last_counted_at: datetime) -> bool:
    return utcnow() - last_counted_at > timedelta(days=30)
