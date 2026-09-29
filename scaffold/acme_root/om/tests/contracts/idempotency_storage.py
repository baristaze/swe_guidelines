"""The idempotency storage contract. The cases named in `CROSS_TENANT_CASES`
are the tenant fence's evidence: each one presents another tenant's identifier
and asserts that nothing is found and nothing changes. The negative control
that says what they catch is in `docs/runbooks/tenant-isolation.md`."""

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.exceptions import DuplicateIdempotencyKey, TenantMismatch
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.types.attempt import lease_bound
from acme.om.idempotency.types.record import IdempotencyRecord
from contracts.racing import race

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "finish_pending",
        "read_record",
        "rearm_released",
        "release_pending",
        "take_over_pending",
        "write_record",
    }
)
"""Every method of `IdempotencyStorageInterface` that takes a tenant has a case
in this module that presents another tenant's. `test_storage_exceptions.py`
holds the two sets to each other, so a new method arrives with its case."""


def make_record(
    user_id: UUID | None = None, key: str = "req-1", created_at: datetime | None = None
) -> IdempotencyRecord:
    """A marker on its first attempt: the attempt began when the marker was
    born, as begin mints both in the one breath. A marker handed on since is
    written with an attempt of its own."""
    born = created_at or utcnow()
    return IdempotencyRecord(
        id=new_id(),
        user_id=user_id or new_id(),
        key=key,
        request_digest="sha256:abc",
        target_id=new_id(),
        attempt_id=attempt_minted_at(born),
        created_at=born,
    )


async def drained(storage: IdempotencyStorageInterface) -> datetime:
    """The moment a purge case stands at: ten years back, so no other case's
    record is past its cuts, with whatever an earlier run of these cases left
    behind them purged first. The purge reaches across tenants, so a case
    owns the records behind its cuts. A token carries its moment since 1970,
    so the moment is not a century back."""
    now = utcnow() - timedelta(days=3650)
    cut, attempts_before = now - timedelta(days=1), lease_bound(now - timedelta(minutes=20))
    while await storage.purge_records(cut, attempts_before, 1000):
        pass
    return now


def attempt_minted_at(moment: datetime) -> UUID:
    """The token of an attempt that began at `moment`: a uuid_v7 carrying that
    millisecond, as `new_id()` mints one at the moment it is called."""
    tail = new_id().int & ((1 << 80) - 1)
    return UUID(int=(int(moment.timestamp() * 1000) << 80) | tail, version=7)


def attempt_of(record: IdempotencyRecord) -> UUID:
    """The attempt that holds the record; a released record has none."""
    assert record.attempt_id is not None, "the record is released"
    return record.attempt_id


class IdempotencyStorageContract:
    @pytest.fixture
    def storage(self) -> IdempotencyStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip_is_user_scoped_within_the_tenant(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        org, other_org = new_id(), new_id()
        record = make_record()
        await storage.write_record(org, record)
        assert await storage.read_record(org, record.user_id, record.key) == record
        assert await storage.read_record(org, new_id(), record.key) is None
        assert await storage.read_record(org, record.user_id, "other") is None
        assert await storage.read_record(other_org, record.user_id, record.key) is None
        finished = record.model_copy(update={"status": 201, "body": '{"id":"x"}'})
        await storage.write_record(org, finished)
        assert await storage.read_record(org, record.user_id, record.key) == finished
        assert not finished.pending and record.pending

    async def test_key_is_unique_per_tenant_and_user(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        org = new_id()
        record = make_record()
        await storage.write_record(org, record)
        with pytest.raises(DuplicateIdempotencyKey):
            await storage.write_record(org, make_record(record.user_id, record.key))
        await storage.write_record(org, make_record(new_id(), record.key))
        await storage.write_record(new_id(), make_record(record.user_id, record.key))
        assert await storage.read_record(org, record.user_id, record.key) == record

    async def test_write_refuses_another_tenant(self, storage: IdempotencyStorageInterface) -> None:
        org_a, org_b = new_id(), new_id()
        record = make_record()
        await storage.write_record(org_a, record)
        with pytest.raises(TenantMismatch):
            await storage.write_record(org_b, record.model_copy(update={"status": 200}))
        assert await storage.read_record(org_a, record.user_id, record.key) == record

    async def test_finish_is_conditional_on_the_attempt(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        org = new_id()
        record = make_record()
        await storage.write_record(org, record)

        async def finish(org_id: UUID, attempt_id: UUID, body: str) -> IdempotencyRecord | None:
            return await storage.finish_pending(
                org_id, record.user_id, record.key, attempt_id, 201, body
            )

        # A stale attempt (one that lost the marker) and another tenant change nothing.
        assert await finish(org, new_id(), "stale") is None
        assert await finish(new_id(), attempt_of(record), "elsewhere") is None
        assert await storage.read_record(org, record.user_id, record.key) == record
        # The holder finishes it once; the outcome is then nobody's to rewrite.
        finished = await finish(org, attempt_of(record), '{"id":"x"}')
        assert finished is not None and finished.status == 201 and finished.body == '{"id":"x"}'
        assert finished.attempt_id == record.attempt_id
        assert await storage.read_record(org, record.user_id, record.key) == finished
        assert await finish(org, attempt_of(record), "again") is None
        assert await storage.read_record(org, record.user_id, record.key) == finished

    async def test_release_keeps_the_record_and_clears_only_the_attempt(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        org_id, user_id = new_id(), new_id()
        pending = make_record(user_id, "k-pending")
        await storage.write_record(org_id, pending)

        async def release(org: UUID, key: str, attempt_id: UUID) -> bool:
            return await storage.release_pending(org, user_id, key, attempt_id)

        # A stale attempt and another tenant release nothing.
        assert pending.attempt_id is not None
        assert not await release(org_id, "k-pending", new_id())
        assert not await release(new_id(), "k-pending", pending.attempt_id)
        assert await storage.read_record(org_id, user_id, "k-pending") == pending
        # The holder releases it: the record stays with its digest and its
        # target id, and only the attempt goes.
        assert await release(org_id, "k-pending", pending.attempt_id)
        released = await storage.read_record(org_id, user_id, "k-pending")
        assert released == pending.model_copy(update={"attempt_id": None})
        assert released is not None and released.released and released.pending
        # Released, it is nobody's: the same attempt cannot release it again
        # or finish it, and it stays as it is.
        assert not await release(org_id, "k-pending", pending.attempt_id)
        assert (
            await storage.finish_pending(
                org_id, user_id, "k-pending", pending.attempt_id, 201, "{}"
            )
            is None
        )
        assert await storage.read_record(org_id, user_id, "k-pending") == released
        # A finished record is never released.
        finished = make_record(user_id, "k-done").model_copy(update={"status": 201, "body": "{}"})
        await storage.write_record(org_id, finished)
        assert finished.attempt_id is not None
        assert not await release(org_id, "k-done", finished.attempt_id)
        assert await storage.read_record(org_id, user_id, "k-done") == finished

    async def test_rearm_is_one_conditional_write_on_a_released_marker(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        org = new_id()
        record = make_record()
        await storage.write_record(org, record)

        async def rearm(org_id: UUID, attempt_id: UUID | None = None) -> IdempotencyRecord | None:
            return await storage.rearm_released(
                org_id, record.user_id, record.key, attempt_id or new_id()
            )

        # Held: a marker with an attempt is not re-armed, whatever its age.
        assert await rearm(org) is None
        assert await storage.read_record(org, record.user_id, record.key) == record
        assert record.attempt_id is not None
        assert await storage.release_pending(org, record.user_id, record.key, record.attempt_id)
        # Released: the re-arm stamps its own attempt, which starts the lease,
        # in the one write, and keeps the digest, the target id and the birth
        # time; another tenant's is never matched.
        assert await rearm(new_id()) is None
        second_attempt = new_id()
        armed = await rearm(org, second_attempt)
        assert armed is not None and armed.pending and not armed.released
        assert armed.attempt_id == second_attempt and armed.created_at == record.created_at
        assert armed.target_id == record.target_id
        assert armed.request_digest == record.request_digest
        assert await storage.read_record(org, record.user_id, record.key) == armed
        # Re-armed, it is held again: not re-armed twice, and the attempt that
        # released it can neither finish nor release it.
        assert await rearm(org) is None
        assert (
            await storage.finish_pending(
                org, record.user_id, record.key, record.attempt_id, 201, "{}"
            )
            is None
        )
        assert not await storage.release_pending(org, record.user_id, record.key, record.attempt_id)
        assert await storage.read_record(org, record.user_id, record.key) == armed
        # The re-armed attempt finishes it; a finished record is never re-armed.
        finished = await storage.finish_pending(
            org, record.user_id, record.key, second_attempt, 201, "{}"
        )
        assert finished is not None and finished.status == 201
        assert await rearm(org) is None
        assert await storage.read_record(org, record.user_id, record.key) == finished

    async def test_two_rearms_admit_exactly_one(self, storage: IdempotencyStorageInterface) -> None:
        # Two retries reach the same released marker. The re-arm is one
        # conditional write, so exactly one of them holds the marker
        # afterwards, and the record names that one's attempt. See
        # contracts/racing.py for what each impl's run of this proves.
        org = new_id()
        record = make_record().model_copy(update={"attempt_id": None})
        await storage.write_record(org, record)
        attempts = [new_id(), new_id()]
        run = await race(
            *(
                storage.rearm_released(org, record.user_id, record.key, attempt_id)
                for attempt_id in attempts
            )
        )
        assert len(run.admitted) == 1, run.summary()
        stored = await storage.read_record(org, record.user_id, record.key)
        assert stored is not None and stored == run.admitted[0]
        assert stored.attempt_id in attempts
        assert stored.pending and stored.created_at == record.created_at
        assert stored.target_id == record.target_id
        # The refusal the conditional write gives whoever arrives after it:
        # the marker is armed, so there is no released marker to re-arm.
        assert await storage.rearm_released(org, record.user_id, record.key, new_id()) is None

    async def test_take_over_is_one_conditional_write(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        org = new_id()
        record = make_record(created_at=utcnow() - timedelta(minutes=10))
        await storage.write_record(org, record)

        async def take_over(
            org_id: UUID, cutoff: datetime, attempt_id: UUID | None = None
        ) -> IdempotencyRecord | None:
            return await storage.take_over_pending(
                org_id, record.user_id, record.key, lease_bound(cutoff), attempt_id or new_id()
            )

        # Not abandoned yet: the attempt began after the cut-off.
        assert await take_over(org, record.created_at - timedelta(seconds=1)) is None
        # Abandoned: the take-over stamps its own attempt in the one write, and
        # that attempt, minted now, holds the marker past the same cut-off.
        cutoff = record.created_at + timedelta(seconds=1)
        second_attempt = new_id()
        taken = await take_over(org, cutoff, second_attempt)
        assert taken is not None and taken.created_at == record.created_at and taken.pending
        assert taken.attempt_id == second_attempt and taken.target_id == record.target_id
        assert await take_over(org, cutoff) is None
        assert await storage.read_record(org, record.user_id, record.key) == taken
        # The first attempt lost the marker: its finish and its release are refused.
        assert (
            await storage.finish_pending(
                org, record.user_id, record.key, attempt_of(record), 201, "{}"
            )
            is None
        )
        assert not await storage.release_pending(
            org, record.user_id, record.key, attempt_of(record)
        )
        assert await storage.read_record(org, record.user_id, record.key) == taken
        # A released marker is re-armed, never taken over, however old it is.
        assert taken.attempt_id is not None
        assert await storage.release_pending(org, record.user_id, record.key, taken.attempt_id)
        later = utcnow() + timedelta(minutes=5)
        assert await take_over(org, later) is None
        released = await storage.read_record(org, record.user_id, record.key)
        assert released is not None and released.released
        # A finished record is never taken over, and another tenant's is never matched.
        await storage.write_record(org, taken.model_copy(update={"status": 200, "body": "{}"}))
        assert await take_over(org, later) is None
        assert await take_over(new_id(), later) is None

    async def test_two_take_overs_admit_exactly_one(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        # Two retries reach the same abandoned marker. The take-over is one
        # conditional write, so exactly one of them holds the marker
        # afterwards, and the record names that one's attempt. See
        # contracts/racing.py for what each impl's run of this proves.
        org = new_id()
        record = make_record(created_at=utcnow() - timedelta(minutes=10))
        await storage.write_record(org, record)
        cutoff = lease_bound(utcnow() - timedelta(minutes=2))
        attempts = [new_id(), new_id()]
        run = await race(
            *(
                storage.take_over_pending(org, record.user_id, record.key, cutoff, attempt_id)
                for attempt_id in attempts
            )
        )
        assert len(run.admitted) == 1, run.summary()
        stored = await storage.read_record(org, record.user_id, record.key)
        assert stored is not None and stored == run.admitted[0]
        assert stored.attempt_id in attempts
        assert stored.pending and stored.created_at == record.created_at
        # The refusal the conditional write gives whoever arrives after it:
        # the admitted attempt's token restarted the lease, so the same
        # cut-off matches nothing.
        assert (
            await storage.take_over_pending(org, record.user_id, record.key, cutoff, new_id())
            is None
        )

    async def test_a_hand_over_starts_the_lease_again_and_keeps_the_birth_time(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        # The lease runs from the attempt. A marker born an hour ago and taken
        # over just now is held by a token minted just now, so the cut-off that
        # freed it no longer matches it and a third attempt cannot take it over
        # at once; and the marker still carries the birth time it was written
        # with, which no write here touches.
        org = new_id()
        born = utcnow() - timedelta(hours=1)
        record = make_record(created_at=born)
        await storage.write_record(org, record)
        cutoff = lease_bound(utcnow() - timedelta(minutes=2))
        second_attempt = new_id()
        taken = await storage.take_over_pending(
            org, record.user_id, record.key, cutoff, second_attempt
        )
        assert taken is not None and taken.attempt_id == second_attempt
        assert taken.created_at == born, "the birth time is written once"
        third = await storage.take_over_pending(org, record.user_id, record.key, cutoff, new_id())
        assert third is None, "the attempt it was handed to is within its lease"
        assert await storage.read_record(org, record.user_id, record.key) == taken

    async def test_purge_counts_finished_and_released_past_the_cut_and_pending_past_theirs(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        """The purge runs across tenants: every tenant's records past their cut
        go in one call."""
        org, other_org = new_id(), new_id()
        now = await drained(storage)
        old_finished = make_record(key="old-done", created_at=now - timedelta(days=2)).model_copy(
            update={"status": 201, "body": "{}"}
        )
        fresh_finished = make_record(key="new-done").model_copy(
            update={"status": 201, "body": "{}"}
        )
        abandoned = make_record(key="abandoned", created_at=now - timedelta(hours=1))
        live_pending = make_record(key="live")
        # Born an hour ago and handed on a moment ago: the sweep measures the
        # attempt, like the lease does, so this one is still somebody's.
        handed_on = make_record(key="handed-on", created_at=now - timedelta(hours=1)).model_copy(
            update={"attempt_id": attempt_minted_at(now)}
        )
        # A released marker lives as long as a finished one: past the
        # retention a retry begins afresh, within it the retry re-arms it.
        old_released = make_record(key="old-released", created_at=now - timedelta(days=2))
        old_released = old_released.model_copy(update={"attempt_id": None})
        fresh_released = make_record(key="new-released", created_at=now - timedelta(hours=1))
        fresh_released = fresh_released.model_copy(update={"attempt_id": None})
        elsewhere = make_record(key="old-done", created_at=now - timedelta(days=2)).model_copy(
            update={"status": 201, "body": "{}"}
        )
        mine = (
            old_finished,
            fresh_finished,
            abandoned,
            live_pending,
            handed_on,
            old_released,
            fresh_released,
        )
        for record in mine:
            await storage.write_record(org, record)
        await storage.write_record(other_org, elsewhere)
        attempts_before = lease_bound(now - timedelta(minutes=20))
        purged = await storage.purge_records(now - timedelta(days=1), attempts_before, 10)
        assert purged == 4, "three of this tenant's and the other's one"
        kept = [await storage.read_record(org, r.user_id, r.key) for r in mine]
        assert kept == [
            None,
            fresh_finished,
            None,
            live_pending,
            handed_on,
            None,
            fresh_released,
        ]
        assert await storage.read_record(other_org, elsewhere.user_id, elsewhere.key) is None
        assert await storage.purge_records(now - timedelta(days=1), attempts_before, 10) == 0

    async def test_a_backlog_past_a_batch_goes_a_batch_at_a_time(
        self, storage: IdempotencyStorageInterface
    ) -> None:
        org = new_id()
        now = await drained(storage)
        for i in range(3):
            old = make_record(key=f"old-{i}", created_at=now - timedelta(days=2))
            await storage.write_record(org, old.model_copy(update={"status": 201, "body": "{}"}))
        cut, attempts_before = now - timedelta(days=1), lease_bound(now - timedelta(minutes=20))
        assert await storage.purge_records(cut, attempts_before, 2) == 2
        assert await storage.purge_records(cut, attempts_before, 2) == 1
        assert await storage.purge_records(cut, attempts_before, 2) == 0
