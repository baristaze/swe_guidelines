"""What a replay reads: a page of the stream past a seq, with the stream's
floor and head read after it, in one transaction. The floor says whether the
page may have a hole below it, and the head is what a caller replays to when
it has."""

from acme.om.base import Platform
from acme.om.events.types.event import Event


class StreamPage(Platform):
    events: tuple[Event, ...]
    floor: int  # the highest seq the trim removed; 0 while it removed none
    head: int  # the last seq assigned; 0 before the first append
