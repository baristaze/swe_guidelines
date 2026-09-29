from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from acme.om.base import EMPTY_UUID
from acme.om.billing.storage import BillingStorageInterface
from acme.om.billing.storage.tables.billing_accounts import BillingAccounts
from acme.om.billing.storage.tables.billing_deliveries import BillingDeliveries
from acme.om.billing.types.account import BillingAccount
from acme.om.billing.types.delivery import BillingDelivery
from acme.om.exceptions import RowDeleted, TenantMismatch, UniqueKeyTaken
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import (
    PLAN_WITH_VALUES,
    PgStorageBase,
    delete_batch,
    deleted,
    violated_constraint,
)
from acme.om.storage.utils.translation import apply_row, to_model, to_row, to_values, undeletes

ORG_KEY = "uq_billing_accounts_org_id"
PRIMARY_KEY = "pk_billing_accounts"


class BillingStoragePostgresImpl(PgStorageBase, BillingStorageInterface):
    async def read_account(self, org_id: UUID) -> BillingAccount | None:
        stmt = select(BillingAccounts).where(BillingAccounts.org_id == org_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, BillingAccount)

    async def create_account(
        self, org_id: UUID, account: BillingAccount, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        try:
            return await self._insert(BillingAccounts, org_id, account, outbox_rows)
        except UniqueKeyTaken as taken:
            # The org is the unique key: another create for it won, and the
            # caller reads the account that did.
            if ORG_KEY in taken.message:
                return False
            raise

    async def write_account(
        self,
        org_id: UUID,
        account: BillingAccount,
        outbox_rows: tuple[OutboxRow, ...],
        delivery: BillingDelivery | None = None,
    ) -> bool:
        # One transaction: the mark first, so a copy already applied stops
        # here with nothing written; then the account and its outbox rows.
        async with self._session_for(BillingAccounts, org_id=org_id) as session:
            if delivery is not None:
                marked = (
                    insert(BillingDeliveries)
                    .values(org_id=org_id, **to_values(delivery, BillingDeliveries))
                    .on_conflict_do_nothing(index_elements=["id"])
                    .returning(BillingDeliveries.id)
                )
                if (await session.execute(marked)).scalar_one_or_none() is None:
                    await session.rollback()
                    return False
            row = await session.get(BillingAccounts, account.id)
            if row is None:
                session.add(to_row(account, BillingAccounts, org_id=org_id))
            else:
                if row.org_id != org_id:
                    raise TenantMismatch(f"billing_accounts {account.id} is not in {org_id}")
                if undeletes(row, account):
                    raise RowDeleted(f"billing_accounts {account.id} was deleted")
                apply_row(row, account)
            for outbox_row in outbox_rows:
                session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            try:
                await session.commit()
            except IntegrityError as error:
                constraint = violated_constraint(error)
                if row is None and constraint == PRIMARY_KEY:
                    raise TenantMismatch(
                        f"billing_accounts {account.id} is not in {org_id}"
                    ) from error
                raise UniqueKeyTaken(
                    f"billing_accounts {account.id}: {constraint or 'a unique key'} is taken"
                ) from error
            return True

    async def read_delivery(self, org_id: UUID, delivery_id: UUID) -> BillingDelivery | None:
        stmt = select(BillingDeliveries).where(
            BillingDeliveries.org_id == org_id, BillingDeliveries.id == delivery_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, BillingDelivery)

    async def purge_deliveries(self, before: datetime, limit: int) -> int:
        stmt = delete_batch(BillingDeliveries, BillingDeliveries.created_at < before, limit=limit)
        # Every tenant's marks past the retention, so the system scope, spelled
        # here; planned with its values, so the index serves an idle pass too.
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            await session.execute(PLAN_WITH_VALUES)
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        deliveries = delete_batch(
            BillingDeliveries, BillingDeliveries.org_id == org_id, limit=limit
        )
        accounts = delete_batch(BillingAccounts, BillingAccounts.org_id == org_id, limit=limit)
        async with self._session_for(accounts, org_id=org_id) as session:
            purged = deleted(await session.execute(deliveries))
            purged += deleted(await session.execute(accounts))
            await session.commit()
            return purged
