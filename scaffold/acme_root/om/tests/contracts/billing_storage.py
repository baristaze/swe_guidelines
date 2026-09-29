"""The billing storage contract. The cases named in `CROSS_TENANT_CASES` are
the tenant fence's evidence: each one presents another tenant's identifier and
asserts that nothing is found and nothing changes."""

from datetime import timedelta
from uuid import UUID

import pytest

from contracts.racing import race
from acme.om.base import new_id, utcnow
from acme.om.billing.storage import BillingStorageInterface
from acme.om.billing.types.account import BillingAccount, SubscriptionStatus
from acme.om.billing.types.delivery import BillingDelivery
from acme.om.billing.types.plan import Plan
from acme.om.exceptions import TenantMismatch, UniqueKeyTaken
from acme.om.outbox.types.row import OutboxRow

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "create_account",
        "purge_tenant",
        "read_account",
        "read_delivery",
        "write_account",
    }
)
"""Every method of `BillingStorageInterface` that takes a tenant has a case in
this module that presents another tenant's. `test_storage_exceptions.py` holds
the two sets to each other, so a new method arrives with its case."""


def make_account(customer_id: str | None = "cus_1", **fields: object) -> BillingAccount:
    now = utcnow()
    actor = new_id()
    return BillingAccount.model_validate(
        {
            "id": new_id(),
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "updated_by": actor,
            "customer_id": customer_id,
            **fields,
        }
    )


def make_delivery(event_id: str = "evt_1", age: timedelta = timedelta(0)) -> BillingDelivery:
    return BillingDelivery(
        id=new_id(),
        created_at=utcnow() - age,
        event_id=event_id,
        event_type="customer.subscription.updated",
    )


def make_row(org_id: UUID, account: BillingAccount, action: str = "updated") -> OutboxRow:
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind=f"billing.account.{action}",
        target_id=account.id,
        actor_id=account.updated_by,
        request_id=new_id(),
        app="api",
    )


def paying(account: BillingAccount) -> BillingAccount:
    return account.model_copy(
        update={
            "subscription_id": "sub_1",
            "price_lookup_key": "acme.pro.monthly",
            "status": SubscriptionStatus.ACTIVE,
            "quantity": 1,
            "updated_at": utcnow(),
        }
    )


async def seed(storage: BillingStorageInterface, org_id: UUID, account: BillingAccount) -> None:
    assert await storage.create_account(org_id, account, (make_row(org_id, account, "created"),))


async def drained(storage: BillingStorageInterface) -> timedelta:
    """How far back a purge case stands: a century, so no other case's mark is
    past its cut, with whatever an earlier run of these cases left behind it
    purged first. The purge reaches across tenants, so a case owns the marks
    behind its cut."""
    back = timedelta(days=36500)
    while await storage.purge_deliveries(utcnow() - back - timedelta(days=30), 1000):
        pass
    return back


class BillingStorageContract:
    @pytest.fixture
    def storage(self) -> BillingStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip_and_update(self, storage: BillingStorageInterface) -> None:
        org = new_id()
        account = make_account()
        assert await storage.read_account(org) is None
        await seed(storage, org, account)
        assert await storage.read_account(org) == account
        paid = paying(account)
        assert await storage.write_account(org, paid, (make_row(org, paid),)) is True
        assert await storage.read_account(org) == paid

    async def test_one_account_per_org(self, storage: BillingStorageInterface) -> None:
        """The org is the unique key: a second create for it, under the same id
        or another, lands nothing and answers False; a write of a second
        account for it is refused."""
        org = new_id()
        first = make_account("cus_first")
        await seed(storage, org, first)
        again = make_account("cus_second")
        assert await storage.create_account(org, again, (make_row(org, again, "created"),)) is False
        assert await storage.create_account(org, first, ()) is False
        with pytest.raises(UniqueKeyTaken):
            await storage.write_account(org, again, (make_row(org, again),))
        assert await storage.read_account(org) == first

    async def test_two_creates_for_one_org_admit_one(
        self, storage: BillingStorageInterface
    ) -> None:
        org = new_id()
        one, two = make_account("cus_a"), make_account("cus_b")
        run = await race(
            storage.create_account(org, one, (make_row(org, one, "created"),)),
            storage.create_account(org, two, (make_row(org, two, "created"),)),
        )
        assert run.outcomes.count(True) == 1, run.summary()
        stored = await storage.read_account(org)
        assert stored in (one, two)

    async def test_a_delivery_lands_once(self, storage: BillingStorageInterface) -> None:
        """The mark and the account share a commit: the first copy of a
        delivery writes both, the second writes neither."""
        org = new_id()
        account = make_account()
        await seed(storage, org, account)
        delivery = make_delivery()
        paid = paying(account)
        assert await storage.write_account(org, paid, (make_row(org, paid),), delivery) is True
        assert await storage.read_delivery(org, delivery.id) == delivery
        later = paid.model_copy(update={"status": SubscriptionStatus.CANCELED})
        assert await storage.write_account(org, later, (make_row(org, later),), delivery) is False
        assert await storage.read_account(org) == paid

    async def test_two_copies_of_one_delivery_at_once_admit_one(
        self, storage: BillingStorageInterface
    ) -> None:
        org = new_id()
        account = make_account()
        await seed(storage, org, account)
        delivery = make_delivery()
        paid = paying(account)
        run = await race(
            storage.write_account(org, paid, (make_row(org, paid),), delivery),
            storage.write_account(org, paid, (make_row(org, paid),), delivery),
        )
        assert run.outcomes.count(True) == 1, run.summary()

    async def test_reads_are_tenant_scoped(self, storage: BillingStorageInterface) -> None:
        org_a, org_b = new_id(), new_id()
        account = make_account()
        await seed(storage, org_a, account)
        delivery = make_delivery()
        await storage.write_account(org_a, account, (), delivery)
        assert await storage.read_account(org_b) is None
        assert await storage.read_delivery(org_b, delivery.id) is None
        assert await storage.read_delivery(org_a, delivery.id) == delivery

    async def test_writes_refuse_another_tenant(self, storage: BillingStorageInterface) -> None:
        org_a, org_b = new_id(), new_id()
        account = make_account()
        await seed(storage, org_a, account)
        # A create under another tenant of an id this one holds lands nothing.
        assert await storage.create_account(org_b, account, ()) is False
        assert await storage.read_account(org_b) is None
        with pytest.raises(TenantMismatch):
            await storage.write_account(org_b, paying(account), ())
        with pytest.raises(TenantMismatch):
            await storage.write_account(org_b, paying(account), (), make_delivery("evt_b"))
        assert await storage.read_account(org_a) == account

    async def test_purges_are_tenant_scoped(self, storage: BillingStorageInterface) -> None:
        """The purge of a tenant takes its rows and no other's; the purge of
        marks past the retention takes every tenant's, and none younger."""
        org_a, org_b = new_id(), new_id()
        back = await drained(storage)
        account, theirs = make_account(), make_account()
        await seed(storage, org_a, account)
        await seed(storage, org_b, theirs)
        old = make_delivery("evt_old", back + timedelta(days=40))
        fresh = make_delivery("evt_new")
        their_old = make_delivery("evt_their_old", back + timedelta(days=40))
        await storage.write_account(org_a, account, (), old)
        await storage.write_account(org_a, account, (), fresh)
        await storage.write_account(org_b, theirs, (), their_old)
        cutoff = utcnow() - back - timedelta(days=30)
        assert await storage.purge_tenant(new_id(), 10) == 0
        assert await storage.read_delivery(org_a, old.id) == old
        assert await storage.purge_deliveries(cutoff, 10) == 2, "every tenant's"
        assert await storage.read_delivery(org_a, old.id) is None
        assert await storage.read_delivery(org_b, their_old.id) is None
        assert await storage.read_delivery(org_a, fresh.id) == fresh
        assert await storage.purge_tenant(org_a, 10) == 2
        assert await storage.read_account(org_a) is None
        assert await storage.read_account(org_b) is not None

    async def test_a_backlog_past_a_batch_goes_a_batch_at_a_time(
        self, storage: BillingStorageInterface
    ) -> None:
        org = new_id()
        back = await drained(storage)
        account = make_account()
        await seed(storage, org, account)
        for i in range(3):
            await storage.write_account(
                org, account, (), make_delivery(f"evt_{i}", back + timedelta(days=40))
            )
        cutoff = utcnow() - back - timedelta(days=30)
        assert await storage.purge_deliveries(cutoff, 2) == 2
        assert await storage.purge_deliveries(cutoff, 2) == 1
        assert await storage.purge_deliveries(cutoff, 2) == 0

    async def test_a_grant_round_trips(self, storage: BillingStorageInterface) -> None:
        org = new_id()
        account = make_account(None, comped_plan=Plan.MAX)
        await seed(storage, org, account)
        stored = await storage.read_account(org)
        assert stored is not None and stored.comped_plan is Plan.MAX
        assert stored.customer_id is None
