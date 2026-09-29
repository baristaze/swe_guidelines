"""The event storage contract. The cases named in `CROSS_TENANT_CASES` are the
tenant fence's evidence: each one presents another tenant's identifier and
asserts that nothing is found and nothing changes. The negative control that
says what they catch is in `docs/runbooks/tenant-isolation.md`."""

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from contracts.racing import race
from acme.om.base import new_id, utcnow
from acme.om.events.storage import EventStorageInterface
from acme.om.events.types.event import Event
from acme.om.exceptions import TenantMismatch

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {"append_events", "purge_tenant", "read_after", "read_floor", "read_head", "read_page"}
)
"""Every method of `EventStorageInterface` that takes a tenant has a case in
this module that presents another tenant's. `test_storage_exceptions.py` holds
the two sets to each other, so a new method arrives with its case."""


async def drained(storage: EventStorageInterface) -> datetime:
    """The moment a trim case stands at: a century back, so no other case's
    event is past its cut, with every event an earlier run of these cases
    left in that past trimmed first. The trim reaches across tenants, so a
    case owns the events behind its cut; the drain's own cut is later than
    any case's events, so an old event a case left above a young one goes
    too, and never holds a place in a later case's batch."""
    now = utcnow() - timedelta(days=36500)
    while await storage.trim(now + timedelta(days=1000), 1000):
        pass
    return now


def make_event(
    org_id: UUID, kind: str = "tasks.task.created", produced_at: datetime | None = None
) -> Event:
    return Event(
        id=new_id(),
        org_id=org_id,
        kind=kind,
        target_id=new_id(),
        payload={"title": "t"},
        produced_at=produced_at or utcnow(),
        actor_id=new_id(),
        request_id=new_id(),
        app="portal",
    )


async def append_one(storage: EventStorageInterface, org_id: UUID, event: Event) -> Event:
    """One event appended: a batch of one."""
    (appended,) = await storage.append_events(org_id, [event])
    return appended


class EventStorageContract:
    @pytest.fixture
    def storage(self) -> EventStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_append_assigns_a_dense_sequence_per_tenant(
        self, storage: EventStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        assert await storage.read_head(org_a) == 0
        appended = [await append_one(storage, org_a, make_event(org_a)) for _ in range(3)]
        assert [e.seq for e in appended] == [1, 2, 3]
        assert await storage.read_head(org_a) == 3
        elsewhere = await append_one(storage, org_b, make_event(org_b))
        assert elsewhere.seq == 1
        assert await storage.read_head(org_b) == 1
        first = appended[0]
        assert first == make_event(org_a).model_copy(
            update={
                "id": first.id,
                "seq": 1,
                "target_id": first.target_id,
                "produced_at": first.produced_at,
                "actor_id": first.actor_id,
                "request_id": first.request_id,
            }
        )
        assert first.payload == {"title": "t"}
        assert await storage.read_after(org_a, 0, 10) == appended
        assert await storage.read_after(org_a, 2, 10) == appended[2:]
        assert await storage.read_after(org_a, 3, 10) == []
        assert await storage.read_after(org_a, 0, 2) == appended[:2]
        assert await storage.read_after(org_b, 0, 10) == [elsewhere]
        assert await storage.read_after(new_id(), 0, 10) == []

    async def test_the_count_since_a_moment_spans_every_tenant(
        self, storage: EventStorageInterface
    ) -> None:
        """The traffic figure the platform's size reads: events produced at or
        after the cut, in whichever tenant's stream."""
        cut = utcnow()
        assert await storage.count_since(cut) == 0
        org_a, org_b = new_id(), new_id()
        await append_one(storage, org_a, make_event(org_a))
        await append_one(storage, org_b, make_event(org_b))
        earlier = make_event(org_b).model_copy(update={"produced_at": cut - timedelta(hours=25)})
        await append_one(storage, org_b, earlier)
        assert await storage.count_since(cut) == 2
        assert await storage.count_since(cut - timedelta(days=2)) == 3

    async def test_a_tenant_purge_drops_its_stream_and_no_other(
        self, storage: EventStorageInterface
    ) -> None:
        """The events and the cursor go together, so the tenant's stream is
        gone whole; the other tenant's stays as it was."""
        gone, kept = new_id(), new_id()
        for _ in range(2):
            await append_one(storage, gone, make_event(gone))
        stays = await append_one(storage, kept, make_event(kept))
        assert await storage.purge_tenant(gone, 10) == 2
        assert await storage.read_after(gone, 0, 10) == []
        assert await storage.read_head(gone) == 0
        assert await storage.read_after(kept, 0, 10) == [stays]
        assert await storage.read_head(kept) == 1
        assert await storage.purge_tenant(gone, 10) == 0

    async def test_a_tenant_purge_goes_a_batch_at_a_time_and_the_cursor_last(
        self, storage: EventStorageInterface
    ) -> None:
        gone = new_id()
        for _ in range(3):
            await append_one(storage, gone, make_event(gone))
        assert await storage.purge_tenant(gone, 2) == 2
        assert await storage.read_head(gone) == 3, "the cursor stays while events do"
        assert await storage.purge_tenant(gone, 2) == 1
        assert await storage.read_head(gone) == 0, "and goes with the last of them"
        assert await storage.read_after(gone, 0, 10) == []

    async def test_an_appended_event_names_its_own_tenant(
        self, storage: EventStorageInterface
    ) -> None:
        """The relay appends with no context and a client replays records, so
        the tenant is on the event and not beside it. An event that names
        another tenant is appended under the one the caller names."""
        org = new_id()
        appended = await append_one(storage, org, make_event(new_id()))
        assert appended.org_id == org
        assert [e.org_id for e in await storage.read_after(org, 0, 10)] == [org]

    async def test_append_is_idempotent_on_the_id(self, storage: EventStorageInterface) -> None:
        # The outbox relay appends under the row's id; relaying twice appends once.
        org = new_id()
        event = make_event(org)
        first = await append_one(storage, org, event)
        again = await append_one(storage, org, event.model_copy(update={"kind": "ignored"}))
        assert again == first and first.seq == 1
        assert [e.seq for e in await storage.read_after(org, 0, 10)] == [1]

    async def test_an_id_another_tenant_appended_is_never_written_over(
        self, storage: EventStorageInterface
    ) -> None:
        """The append is idempotent on the id, so a repeat returns what is
        stored. The id is unique across tenants, and a repeat from another
        tenant is not the same event: it is refused, and the stream it named
        stays as it was."""
        org_a, org_b = new_id(), new_id()
        event = make_event(org_a)
        appended = await append_one(storage, org_a, event)
        with pytest.raises(TenantMismatch):
            await append_one(storage, org_b, event.model_copy(update={"kind": "stolen"}))
        assert await storage.read_after(org_a, 0, 10) == [appended]
        assert await storage.read_after(org_b, 0, 10) == []
        # The refusal spends nothing: an id another tenant owns never moves
        # this tenant's cursor, so the next append here is still the first.
        assert await storage.read_head(org_b) == 0

    async def test_many_appends_never_share_or_skip_a_seq(
        self, storage: EventStorageInterface
    ) -> None:
        # N appends reach one tenant's cursor and leave with 1..N: no gap, no
        # duplicate, and the head is the last of them. See contracts/racing.py
        # for what each impl's run of this proves.
        org, n = new_id(), 32
        run = await race(*(append_one(storage, org, make_event(org)) for _ in range(n)))
        appended = run.outcomes
        assert sorted(e.seq for e in appended) == list(range(1, n + 1))
        assert [e.seq for e in await storage.read_after(org, 0, n * 2)] == list(range(1, n + 1))
        assert await storage.read_head(org) == n
        # The cursor the appends left is the one the next append takes from.
        assert (await append_one(storage, org, make_event(org))).seq == n + 1

    async def test_many_appends_keep_one_cursor_per_tenant(
        self, storage: EventStorageInterface
    ) -> None:
        # Two tenants appending at once never see each other's numbers.
        org_a, org_b, n = new_id(), new_id(), 16
        run = await race(*(append_one(storage, org, make_event(org)) for org in (org_a, org_b) * n))
        appended = run.outcomes
        assert sorted(e.seq for e in appended[0::2]) == list(range(1, n + 1))
        assert sorted(e.seq for e in appended[1::2]) == list(range(1, n + 1))
        assert await storage.read_head(org_a) == n
        assert await storage.read_head(org_b) == n

    async def test_a_retried_append_consumes_no_seq(self, storage: EventStorageInterface) -> None:
        # The retry of an appended id rolls back, and the number it took goes
        # back with it: the next event is 2, not 3.
        org = new_id()
        event = make_event(org)
        await append_one(storage, org, event)
        await append_one(storage, org, event)
        assert (await append_one(storage, org, make_event(org))).seq == 2
        assert await storage.read_head(org) == 2

    async def test_a_batch_takes_contiguous_numbers_in_its_order(
        self, storage: EventStorageInterface
    ) -> None:
        """An import step's relay appends its hundred events in one call: they
        take the next run of numbers, in the order given, and come back in it."""
        org = new_id()
        first = await append_one(storage, org, make_event(org))
        batch = [make_event(org) for _ in range(5)]
        appended = await storage.append_events(org, batch)
        assert [e.id for e in appended] == [e.id for e in batch]
        assert [e.seq for e in appended] == [2, 3, 4, 5, 6]
        assert await storage.read_after(org, 0, 10) == [first, *appended]
        assert await storage.read_head(org) == 6
        assert await storage.append_events(org, []) == ()
        assert await storage.read_head(org) == 6

    async def test_a_replayed_batch_appends_only_what_is_new(
        self, storage: EventStorageInterface
    ) -> None:
        """A relay that ran twice presents events already appended beside new
        ones: the stored ones come back as stored and take no number, and the
        new ones take the next run, in the order given."""
        org = new_id()
        a, b, c, d = (make_event(org) for _ in range(4))
        stored = await storage.append_events(org, [a, b])
        again = await storage.append_events(
            org, [a.model_copy(update={"kind": "ignored"}), c, b, d]
        )
        assert again[0] == stored[0] and again[2] == stored[1]
        assert [e.seq for e in again] == [1, 3, 2, 4]
        assert [e.seq for e in await storage.read_after(org, 0, 10)] == [1, 2, 3, 4]
        assert await storage.append_events(org, [b, a]) == (stored[1], stored[0])
        assert await storage.read_head(org) == 4

    async def test_a_batch_with_another_tenants_id_is_refused_whole(
        self, storage: EventStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        taken = await append_one(storage, org_a, make_event(org_a))
        batch = [make_event(org_b), make_event(org_b).model_copy(update={"id": taken.id})]
        with pytest.raises(TenantMismatch):
            await storage.append_events(org_b, batch)
        # Nothing of the batch is written and no number is spent.
        assert await storage.read_after(org_b, 0, 10) == []
        assert await storage.read_head(org_b) == 0
        assert await storage.read_after(org_a, 0, 10) == [taken]

    async def test_a_batch_that_names_one_id_twice_is_refused(
        self, storage: EventStorageInterface
    ) -> None:
        org = new_id()
        event = make_event(org)
        with pytest.raises(ValueError, match="twice"):
            await storage.append_events(org, [event, event])
        assert await storage.read_head(org) == 0

    async def test_batches_and_single_appends_at_once_keep_the_stream_gapless(
        self, storage: EventStorageInterface
    ) -> None:
        """Batches and single appends reach one tenant's cursor at once. Every
        number from 1 to the head is taken once, and each batch holds one
        contiguous run in its own order."""
        org, batches, size, singles = new_id(), 6, 10, 12
        calls = [
            storage.append_events(org, [make_event(org) for _ in range(size)])
            for _ in range(batches)
        ] + [storage.append_events(org, [make_event(org)]) for _ in range(singles)]
        run = await race(*calls)
        total = batches * size + singles
        assert sorted(e.seq for out in run.outcomes for e in out) == list(range(1, total + 1))
        for out in run.outcomes[:batches]:
            seqs = [e.seq for e in out]
            assert seqs == list(range(seqs[0], seqs[0] + size)), seqs
        assert [e.seq for e in await storage.read_after(org, 0, total + 1)] == list(
            range(1, total + 1)
        )
        assert await storage.read_head(org) == total

    async def test_one_batch_relayed_twice_at_once_is_appended_once(
        self, storage: EventStorageInterface
    ) -> None:
        """Two relays of the same rows at once, the request's and the sweep's:
        both leave with the same events and the same numbers, and the stream
        holds each once."""
        org = new_id()
        batch = [make_event(org) for _ in range(5)]
        run = await race(*(storage.append_events(org, batch) for _ in range(3)))
        first = run.outcomes[0]
        assert all(out == first for out in run.outcomes), run.summary()
        assert [e.seq for e in first] == [1, 2, 3, 4, 5]
        assert await storage.read_head(org) == 5
        assert (await append_one(storage, org, make_event(org))).seq == 6

    async def append_aged(
        self, storage: EventStorageInterface, org: UUID, now: datetime, *days_ago: int
    ) -> list[Event]:
        """One event per age, in stream order, each produced that many days
        before `now`."""
        return [
            await append_one(storage, org, make_event(org, produced_at=now - timedelta(days=d)))
            for d in days_ago
        ]

    async def test_the_trim_takes_the_old_bottom_and_moves_the_floor_with_it(
        self, storage: EventStorageInterface
    ) -> None:
        org = new_id()
        now = await drained(storage)
        assert await storage.read_floor(org) == 0
        kept = (await self.append_aged(storage, org, now, 100, 95, 91, 10, 1))[3:]
        assert await storage.trim(now - timedelta(days=90), 1000) == 3
        assert await storage.read_floor(org) == 3
        assert await storage.read_head(org) == 5
        # Every event above the floor is there: the stream is whole from it.
        assert await storage.read_after(org, 3, 10) == kept
        assert await storage.read_after(org, 0, 10) == kept
        # The next append continues from the head, never from the floor.
        assert (await append_one(storage, org, make_event(org))).seq == 6

    async def test_a_page_carries_the_floor_and_the_head_of_its_own_tenant(
        self, storage: EventStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        now = await drained(storage)
        kept = (await self.append_aged(storage, org, now, 100, 100, 1, 1))[2:]
        await storage.trim(now - timedelta(days=90), 1000)
        page = await storage.read_page(org, 2, 10)
        assert (page.events, page.floor, page.head) == (tuple(kept), 2, 4)
        assert (await storage.read_page(org, 0, 1)).events == tuple(kept[:1])
        # Another tenant's page is its own: none of these events, no floor.
        empty = await storage.read_page(other, 0, 10)
        assert (empty.events, empty.floor, empty.head) == ((), 0, 0)

    async def test_the_trim_takes_one_batch_at_a_time(self, storage: EventStorageInterface) -> None:
        org = new_id()
        now = await drained(storage)
        await self.append_aged(storage, org, now, 100, 100, 100, 100, 100)
        before = now - timedelta(days=90)
        assert await storage.trim(before, 2) == 2
        assert await storage.read_floor(org) == 2
        assert await storage.trim(before, 2) == 2
        assert await storage.read_floor(org) == 4
        assert await storage.trim(before, 2) == 1
        assert await storage.read_floor(org) == 5 == await storage.read_head(org)
        # Nothing is left to trim, and trimming again moves nothing.
        assert await storage.trim(before, 2) == 0
        assert await storage.read_floor(org) == 5
        assert await storage.read_after(org, 5, 10) == []

    async def test_the_trim_stops_at_the_first_young_event(
        self, storage: EventStorageInterface
    ) -> None:
        """`seq` follows the relay, not the write, so an old event can sit
        above a young one. The trim never skips the young one to reach it: the
        stream above the floor stays whole."""
        org = new_id()
        now = await drained(storage)
        events = await self.append_aged(storage, org, now, 100, 1, 100)
        assert await storage.trim(now - timedelta(days=90), 1000) == 1
        assert await storage.read_floor(org) == 1
        assert await storage.read_after(org, 1, 10) == events[1:]

    async def test_a_trim_with_nothing_old_enough_moves_nothing(
        self, storage: EventStorageInterface
    ) -> None:
        org = new_id()
        now = await drained(storage)
        assert await storage.trim(now, 1000) == 0, "no stream at all"
        await self.append_aged(storage, org, now, 1, 100)
        assert await storage.trim(now - timedelta(days=90), 1000) == 0
        assert await storage.read_floor(org) == 0
        assert [e.seq for e in await storage.read_after(org, 0, 10)] == [1, 2]

    async def test_the_trim_moves_each_tenants_floor_by_its_own_events(
        self, storage: EventStorageInterface
    ) -> None:
        """One call trims every tenant at once, each one's run from its own
        bottom, and each floor moves with its own events and no other's."""
        mine, theirs, young = new_id(), new_id(), new_id()
        now = await drained(storage)
        await self.append_aged(storage, mine, now, 100, 100)
        stays = (await self.append_aged(storage, theirs, now, 100, 1))[1:]
        untouched = await self.append_aged(storage, young, now, 1)
        assert await storage.trim(now - timedelta(days=90), 1000) == 3
        assert await storage.read_floor(mine) == 2
        assert await storage.read_floor(theirs) == 1
        assert await storage.read_after(theirs, 0, 10) == stays
        assert await storage.read_floor(young) == 0
        assert await storage.read_after(young, 0, 10) == untouched
        assert await storage.read_floor(new_id()) == 0

    async def test_a_tenant_purge_drops_the_floor_with_the_stream(
        self, storage: EventStorageInterface
    ) -> None:
        org = new_id()
        now = await drained(storage)
        await self.append_aged(storage, org, now, 100, 1)
        await storage.trim(now - timedelta(days=90), 1000)
        assert await storage.purge_tenant(org, 1000) == 1
        assert await storage.read_floor(org) == 0
        assert await storage.read_head(org) == 0

    async def test_trims_and_appends_at_once_leave_the_stream_whole_above_the_floor(
        self, storage: EventStorageInterface
    ) -> None:
        """Sweeps trimming at once while a tenant's writes append: a trim that
        meets a cursor another holds skips it rather than wait, each event
        goes once, the floor only moves up, and whatever the floor ends at,
        every seq above it up to the head is stored."""
        org = new_id()
        now = await drained(storage)
        await self.append_aged(storage, org, now, *([100] * 12))
        before = now - timedelta(days=90)
        run = await race(
            storage.trim(before, 5),
            storage.trim(before, 5),
            append_one(storage, org, make_event(org)),
            storage.trim(before, 5),
            append_one(storage, org, make_event(org)),
        )
        trimmed = sum(o for o in run.outcomes if isinstance(o, int))
        while more := await storage.trim(before, 5):
            trimmed += more
        assert trimmed == 12, run.summary()
        floor, head = await storage.read_floor(org), await storage.read_head(org)
        assert (floor, head) == (12, 14)
        assert [e.seq for e in await storage.read_after(org, floor, 10)] == [13, 14]
