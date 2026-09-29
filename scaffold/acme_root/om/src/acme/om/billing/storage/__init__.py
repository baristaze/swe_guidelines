"""Storage of the billing swimlane: one account per org, and the marks of the
processor's deliveries already applied. Every operation takes org_id first."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.om.billing.types.account import BillingAccount
from acme.om.billing.types.delivery import BillingDelivery
from acme.om.outbox.types.row import OutboxRow


class BillingStorageInterface(ABC):
    @abstractmethod
    async def read_account(self, org_id: UUID) -> BillingAccount | None:
        """The org's one account, or None before it has one."""
        ...

    @abstractmethod
    async def create_account(
        self, org_id: UUID, account: BillingAccount, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create, with the rows that announce it in one commit; False,
        with nothing landed, when the org already has an account (under this
        id or another: the org is the unique key), and the caller reads it."""
        ...

    @abstractmethod
    async def write_account(
        self,
        org_id: UUID,
        account: BillingAccount,
        outbox_rows: tuple[OutboxRow, ...],
        delivery: BillingDelivery | None = None,
    ) -> bool:
        """The update of the org's account, with the rows that announce it and,
        when a delivery caused it, the delivery's mark, all in one commit. A
        delivery already marked lands nothing and answers False: the second
        copy of an event changes nothing. Otherwise True."""
        ...

    @abstractmethod
    async def read_delivery(self, org_id: UUID, delivery_id: UUID) -> BillingDelivery | None:
        """The mark of one delivery, when it was applied under this org."""
        ...

    @abstractmethod
    async def purge_deliveries(self, before: datetime, limit: int) -> int:
        """Cross-tenant, for the sweep, in the system scope, once a pass for
        every tenant: removes at most `limit` marks older than `before`, past
        any retry the processor still makes, whatever their tenant, skipping
        rows another transaction holds; returns how many."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """The hard delete of a deleted tenant's account and marks once the
        retention has passed, at most `limit` of each per call; returns how
        many rows went."""
        ...
