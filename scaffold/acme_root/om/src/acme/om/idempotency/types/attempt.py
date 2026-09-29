"""The attempt a creating request runs under, the token that names it, and
the one rule read off that token. `new_id()` mints a
`uuid_v7`, so a token carries the millisecond the attempt began and tokens
sort by that millisecond. The pending lease runs from the attempt and never
from the marker: the marker's birth time is written once, and a marker handed
from one attempt to the next is as young as the hand-over, not as old as the
marker. Both storage impls compare tokens against the bound below, so neither
reads a clock off a record and the two cannot drift apart."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from acme.om.base import Platform

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MILLISECOND = timedelta(milliseconds=1)
TOKEN_TAIL_BITS = 80  # what a uuid_v7 carries under its 48-bit millisecond


def lease_bound(moment: datetime) -> UUID:
    """The lowest token an attempt started after `moment` can carry. Every
    token below it belongs to an attempt that began in `moment`'s millisecond
    or earlier, which is the attempt whose lease has passed when `moment` is
    the cut-off."""
    return UUID(int=((moment - EPOCH) // MILLISECOND + 1) << TOKEN_TAIL_BITS)


class Attempt(Platform):
    """One attempt at a creating request: the id its create uses and the token
    of the marker holding it. The gateway mints both before the marker and
    hands them down together, so a write of a rerun that changes what is
    stored can ask whether the marker still holds this attempt."""

    target_id: UUID
    attempt_id: UUID | None = None
    """None when the request carries no key: there is no marker, the id is
    fresh, and no rerun can reach the write that a marker would fence."""
