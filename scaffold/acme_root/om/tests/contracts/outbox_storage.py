"""The outbox contract, exercised through a core-role write: the row lands
with the user it announces, the sweep claims it across tenants one attempt
at a time, the relay marks it done or records the failure, and the purge
deletes what is settled.

The cases named in `CROSS_TENANT_CASES` are the tenant fence's evidence: each
one presents another tenant's identifier and asserts that nothing is found and
nothing changes. The claim and the purge serve the sweep and take no tenant,
so they are in the enumerated exceptions instead. The negative control that
says what the cases catch is in `docs/runbooks/tenant-isolation.md`."""

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.user import User
from contracts.factories import make_user
from contracts.racing import race

IDENTITY = "0195f1a2-7b3c-7d4e-8f00-00000000d1d1"
"""The identity a user row names: a payload carries ids, never a person's field."""

NO_DELAY = timedelta(0)

CROSS_TENANT_CASES: frozenset[str] = frozenset({"mark_done", "record_failure"})
"""Every method of `OutboxStorageInterface` that takes a tenant has a case in
this module that presents another tenant's. `test_storage_exceptions.py` holds
the two sets to each other, so a new method arrives with its case."""


def make_row(org_id: UUID, target_id: UUID, *, age: timedelta = timedelta(minutes=1)) -> OutboxRow:
    return OutboxRow(
        id=new_id(),
        created_at=utcnow() - age,
        org_id=org_id,
        kind="tenancy.user.created",
        target_id=target_id,
        payload={"identity_id": IDENTITY},
        actor_id=new_id(),
        request_id=new_id(),
        app="portal",
    )


async def claim_all(
    outbox: OutboxStorageInterface,
    *,
    limit: int = 100,
    now: datetime | None = None,
    grace: timedelta = NO_DELAY,
    backoff: timedelta = NO_DELAY,
) -> list[OutboxRow]:
    """A claim with no delay after it, so the same rows are claimable again."""
    return await outbox.claim_pending(limit, now or utcnow(), grace, backoff, backoff)


def a_user() -> User:
    """A user of its own identity, so any number of them live in one tenant."""
    return make_user(new_id())


class OutboxStorageContract:
    @pytest.fixture
    def tenancy(self) -> TenancyStorageInterface:
        raise NotImplementedError("the concrete test class provides the tenancy storage")

    @pytest.fixture
    def outbox(self) -> OutboxStorageInterface:
        raise NotImplementedError("the concrete test class provides the outbox storage")

    async def test_the_row_lands_with_the_core_row_and_is_claimed_across_tenants(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        first, second = a_user(), a_user()
        row_a, row_b = make_row(org_a, first.id), make_row(org_b, second.id)
        await tenancy.write_user(org_a, first, (row_a,))
        await tenancy.write_user(org_b, second, (row_b,))
        claimed = await claim_all(outbox)
        mine = [row for row in claimed if row.id in (row_a.id, row_b.id)]
        assert [(row.org_id, row.id) for row in mine] == [(org_a, row_a.id), (org_b, row_b.id)]
        assert [row.payload for row in mine] == [
            {"identity_id": IDENTITY},
            {"identity_id": IDENTITY},
        ]
        assert [row.attempts for row in mine] == [1, 1]
        assert (await tenancy.read_user(org_a, first.id)) == first

    async def test_a_claimed_row_names_its_own_tenant(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """The relay runs with no context, so the tenant is on the row and not
        beside it: what the claim hands back is enough to relay, to mark done,
        and to publish under. A row that names another tenant is landed under
        the one the write names."""
        org = new_id()
        user = a_user()
        row = make_row(new_id(), user.id)
        await tenancy.write_user(org, user, (row,))
        claimed = [r for r in await claim_all(outbox) if r.id == row.id]
        assert [r.org_id for r in claimed] == [org]
        # The tenant the row names is the one the rest of the namespace takes.
        await outbox.mark_done(claimed[0].org_id, [claimed[0].id])
        assert row.id not in {r.id for r in await claim_all(outbox)}

    async def test_a_claim_spends_an_attempt_and_sets_the_next_with_a_growing_delay(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org = new_id()
        user = a_user()
        row = make_row(org, user.id)
        await tenancy.write_user(org, user, (row,))
        base, cap = timedelta(seconds=30), timedelta(seconds=100)
        now = utcnow()
        for attempt, delay in enumerate((30, 60, 100, 100), start=1):
            claimed = await outbox.claim_pending(100, now, NO_DELAY, base, cap)
            mine = [r for r in claimed if r.id == row.id]
            assert len(mine) == 1
            assert mine[0].attempts == attempt
            assert mine[0].next_attempt_at == now + timedelta(seconds=delay)
            # Not due yet: the same clock claims nothing, a later one does.
            assert row.id not in {r.id for r in await claim_all(outbox, now=now)}
            now = now + timedelta(seconds=delay)
        assert row.id in {r.id for r in await claim_all(outbox, now=now)}

    async def test_a_poison_row_does_not_block_the_rows_behind_it(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org = new_id()
        poison, fine = a_user(), a_user()
        poison_row, fine_row = make_row(org, poison.id), make_row(org, fine.id)
        await tenancy.write_user(org, poison, (poison_row,))
        await tenancy.write_user(org, fine, (fine_row,))
        claimed = await claim_all(outbox, backoff=timedelta(hours=1))
        assert {r.id for r in claimed} >= {poison_row.id, fine_row.id}
        # The poison row's relay failed; the fine one was done. A newer row
        # lands, and the next sweep claims it alone: the poison row waits out
        # its delay and the new row is not behind it.
        await outbox.record_failure(org, poison_row.id, "bus down", None)
        await outbox.mark_done(org, [fine_row.id])
        newer = a_user()
        newer_row = make_row(org, newer.id)
        await tenancy.write_user(org, newer, (newer_row,))
        claimed = await claim_all(outbox)
        ids = {r.id for r in claimed}
        assert newer_row.id in ids and poison_row.id not in ids and fine_row.id not in ids
        # Once its delay is out the poison row is claimed again, error kept.
        later = utcnow() + timedelta(hours=2)
        again = [r for r in await claim_all(outbox, now=later) if r.id == poison_row.id]
        assert len(again) == 1 and again[0].attempts == 2 and again[0].last_error == "bus down"

    async def test_two_sweeps_claim_disjoint_sets(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        # Two relays sweep the same four rows, two apiece. The claim spends an
        # attempt and sets the next one in the one write, so no row is in both
        # sets. See contracts/racing.py for what each impl's run of this proves.
        org = new_id()
        rows: list[OutboxRow] = []
        for _ in range(4):
            user = a_user()
            row = make_row(org, user.id)
            await tenancy.write_user(org, user, (row,))
            rows.append(row)
        now = utcnow()
        run = await race(
            outbox.claim_pending(2, now, NO_DELAY, timedelta(hours=1), timedelta(hours=1)),
            outbox.claim_pending(2, now, NO_DELAY, timedelta(hours=1), timedelta(hours=1)),
        )
        first = {r.id for r in run.outcomes[0]}
        second = {r.id for r in run.outcomes[1]}
        assert first and second and not (first & second), run.summary()
        assert (first | second) >= {row.id for row in rows}
        # The refusal the claim gives whoever sweeps after it: every row is
        # spent and waiting out its delay, so a third sweep claims nothing.
        assert await claim_all(outbox, now=now) == []

    async def test_a_row_younger_than_the_grace_is_left_to_the_request_path(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org = new_id()
        young, old = a_user(), a_user()
        young_row = make_row(org, young.id, age=timedelta(0))
        old_row = make_row(org, old.id, age=timedelta(minutes=5))
        await tenancy.write_user(org, young, (young_row,))
        await tenancy.write_user(org, old, (old_row,))
        ids = {r.id for r in await claim_all(outbox, grace=timedelta(minutes=1))}
        assert old_row.id in ids and young_row.id not in ids
        assert young_row.id in {r.id for r in await claim_all(outbox)}

    async def test_mark_done_is_per_tenant_and_idempotent(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org = new_id()
        user = a_user()
        row = make_row(org, user.id)
        await tenancy.write_user(org, user, (row,))
        await outbox.mark_done(new_id(), [row.id])  # another tenant: no effect
        assert row.id in {r.id for r in await claim_all(outbox)}
        await outbox.mark_done(org, [row.id])
        await outbox.mark_done(org, [row.id])
        assert row.id not in {r.id for r in await claim_all(outbox)}

    async def test_mark_done_takes_a_batch_and_only_the_tenants_rows_of_it(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        mine: list[OutboxRow] = []
        for _ in range(3):
            user = a_user()
            mine.append(make_row(org, user.id))
            await tenancy.write_user(org, user, (mine[-1],))
        user = a_user()
        theirs = make_row(other, user.id)
        await tenancy.write_user(other, user, (theirs,))
        await outbox.mark_done(org, [])  # nothing to mark: no effect
        await outbox.mark_done(org, [*(row.id for row in mine), theirs.id, new_id()])
        pending = {r.id for r in await claim_all(outbox)}
        assert not pending & {row.id for row in mine}
        assert theirs.id in pending, "another tenant's row in the batch is left as is"

    async def test_a_failed_row_is_a_dead_letter_never_claimed_again(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org = new_id()
        user = a_user()
        row = make_row(org, user.id)
        await tenancy.write_user(org, user, (row,))
        await outbox.record_failure(new_id(), row.id, "elsewhere", utcnow())  # another tenant
        assert row.id in {r.id for r in await claim_all(outbox)}
        failed_at = utcnow()
        await outbox.record_failure(org, row.id, "for good", failed_at)
        assert row.id not in {r.id for r in await claim_all(outbox)}
        # Done rows stay done: a failure recorded afterwards changes nothing.
        done = a_user()
        done_row = make_row(org, done.id)
        await tenancy.write_user(org, done, (done_row,))
        await outbox.mark_done(org, [done_row.id])
        await outbox.record_failure(org, done_row.id, "too late", utcnow())
        # The purge is cross-tenant, so only what it takes of these two rows
        # is asserted: nothing before they settled, both once the cut passes.
        await outbox.purge_done(failed_at - timedelta(seconds=1), 1000)
        assert await outbox.purge_done(failed_at + timedelta(seconds=1), 1000) >= 2

    async def test_a_backlog_past_a_batch_goes_a_batch_at_a_time(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """Done rows and dead letters are two statements, a batch of each per
        call; a count below the batch says both are drained."""
        org = new_id()
        for i in range(6):
            user = a_user()
            row = make_row(org, user.id)
            await tenancy.write_user(org, user, (row,))
            if i % 2:
                await outbox.record_failure(org, row.id, "for good", utcnow())
            else:
                await outbox.mark_done(org, [row.id])
        later = utcnow() + timedelta(seconds=1)
        assert await outbox.purge_done(later, 2) == 4, "two done, two failed"
        assert await outbox.purge_done(later, 2) == 2
        assert await outbox.purge_done(later, 2) == 0

    async def test_purge_deletes_only_rows_settled_before_the_cut(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org = new_id()
        done, pending = a_user(), a_user()
        done_row, pending_row = make_row(org, done.id), make_row(org, pending.id)
        await tenancy.write_user(org, done, (done_row,))
        await tenancy.write_user(org, pending, (pending_row,))
        await outbox.mark_done(org, [done_row.id])
        assert await outbox.purge_done(utcnow() - timedelta(hours=1), 1000) == 0
        assert await outbox.purge_done(utcnow() + timedelta(seconds=1), 1000) >= 1
        assert pending_row.id in {r.id for r in await claim_all(outbox)}

    async def test_the_dead_letters_are_counted_across_tenants_by_when_they_failed(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """The dead-letter gauge's read: the rows failed for good after a
        moment, in any tenant; a row that failed an attempt and waits for its
        next is not counted, nor is a done one."""
        org, other = new_id(), new_id()
        rows: dict[str, OutboxRow] = {}
        for name, owner in (
            ("an hour ago", org),
            ("a minute ago", other),
            ("retrying", org),
            ("done", other),
        ):
            user = a_user()
            rows[name] = make_row(owner, user.id)
            await tenancy.write_user(owner, user, (rows[name],))
        now = utcnow()
        await outbox.record_failure(
            org, rows["an hour ago"].id, "for good", now - timedelta(hours=1)
        )
        await outbox.record_failure(
            other, rows["a minute ago"].id, "for good", now - timedelta(minutes=1)
        )
        await outbox.record_failure(org, rows["retrying"].id, "bus down", None)
        await outbox.mark_done(other, [rows["done"].id])
        assert await outbox.count_failed_since(now - timedelta(minutes=15)) == 1
        assert await outbox.count_failed_since(now - timedelta(hours=2)) == 2
        assert await outbox.count_failed_since(now) == 0

    async def test_the_oldest_pending_row_is_read_across_tenants(
        self, tenancy: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """The relay gauge's read: the oldest row neither done nor failed, in
        any tenant, a row waiting out its delay after a failed attempt
        included; nothing once every row is settled."""
        assert await outbox.oldest_pending_at() is None
        org, other = new_id(), new_id()
        rows: dict[str, OutboxRow] = {}
        for name, owner, age in (
            ("done", org, timedelta(hours=3)),
            ("failed", org, timedelta(hours=2)),
            ("retrying", other, timedelta(hours=1)),
            ("fresh", org, timedelta(seconds=1)),
        ):
            user = a_user()
            rows[name] = make_row(owner, user.id, age=age)
            await tenancy.write_user(owner, user, (rows[name],))
        await outbox.mark_done(org, [rows["done"].id])
        await outbox.record_failure(org, rows["failed"].id, "for good", utcnow())
        # One attempt spent and failed: the row waits for its next attempt.
        await claim_all(outbox, backoff=timedelta(hours=1))
        await outbox.record_failure(other, rows["retrying"].id, "bus down", None)
        assert await outbox.oldest_pending_at() == rows["retrying"].created_at
        await outbox.mark_done(other, [rows["retrying"].id])
        assert await outbox.oldest_pending_at() == rows["fresh"].created_at
        await outbox.mark_done(org, [rows["fresh"].id])
        assert await outbox.oldest_pending_at() is None
