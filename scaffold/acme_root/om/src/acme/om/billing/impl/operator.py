import logging
from datetime import datetime
from uuid import UUID

from acme.infra.cache import CacheInterface
from acme.om.base import new_id, utcnow
from acme.om.billing.impl.cache import account_changed
from acme.om.billing.impl.manager import (
    ACCOUNT_CREATED,
    ACCOUNT_UPDATED,
    WAKE_KIND,
    billing_of,
    lifts,
    wake_payload,
)
from acme.om.billing.manager import BillingOperatorManagerInterface
from acme.om.billing.storage import BillingStorageInterface
from acme.om.billing.types.account import BillingAccount
from acme.om.billing.types.billing import Billing
from acme.om.billing.types.plan import Plan
from acme.om.exceptions import NotFound
from acme.om.opcontext import OperatorContext, OperatorPermission
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.storage import TenancyStorageInterface

log = logging.getLogger(__name__)


class BillingOperatorManagerImpl(BillingOperatorManagerInterface):
    """Reads and writes one named org's account through the billing storage,
    under the tenant the operator named, and reads the org through the
    tenancy storage, as the tenancy operator plane does: no `OpContext`
    exists on this plane, so no tenant manager is asked.

    `cache` is the billing account's scope, the one the tenant plane reads
    through. This plane reads storage, and a grant bumps the org's
    generation after its commit, so the tenant plane's next read is fresh."""

    def __init__(
        self,
        storage: BillingStorageInterface,
        tenancy: TenancyStorageInterface,
        relay: OutboxRelayInterface,
        cache: CacheInterface,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._relay = relay
        self._cache = cache

    async def get_billing(self, admin: OperatorContext, org_id: UUID) -> Billing:
        admin.require(OperatorPermission.READ)
        await self._org(org_id)
        log.info("operator %s read billing of org %s", admin.identity_id, org_id)
        return billing_of(await self._storage.read_account(org_id), utcnow())

    async def comp_plan(self, admin: OperatorContext, org_id: UUID, plan: Plan | None) -> Billing:
        admin.require(OperatorPermission.WRITE)
        await self._org(org_id)
        now = utcnow()
        account = await self._storage.read_account(org_id)
        grant = None if plan is Plan.FREE else plan
        if account is None:
            account = BillingAccount(
                id=new_id(),
                created_at=now,
                updated_at=now,
                created_by=admin.identity_id,
                updated_by=admin.identity_id,
                comped_plan=grant,
            )
            rows = (
                self._row(admin, org_id, ACCOUNT_CREATED, account.id),
                *self._wake(admin, org_id, None, account, now),
            )
            if not await self._storage.create_account(org_id, account, rows):
                # Another write made the account first; the grant goes on it.
                return await self.comp_plan(admin, org_id, plan)
        else:
            before = account
            account = account.model_copy(
                update={"comped_plan": grant, "updated_at": now, "updated_by": admin.identity_id}
            )
            rows = (
                self._row(admin, org_id, ACCOUNT_UPDATED, account.id),
                *self._wake(admin, org_id, before, account, now),
            )
            await self._storage.write_account(org_id, account, rows)
        await account_changed(self._cache, org_id)
        await self._relay.relay_all(org_id, rows)
        log.info(
            "operator %s granted org %s %s",
            admin.identity_id,
            org_id,
            "no plan" if grant is None else grant.value,
        )
        return billing_of(account, now)

    async def _org(self, org_id: UUID) -> None:
        org = await self._tenancy.read_org(org_id)
        if org is None or org.deleted_at is not None:
            raise NotFound(f"org {org_id} not found")

    def _wake(
        self,
        admin: OperatorContext,
        org_id: UUID,
        before: BillingAccount | None,
        after: BillingAccount,
        now: datetime,
    ) -> tuple[OutboxRow, ...]:
        """The wake of the org's records parked on a plan's bound, when the
        grant lifted the plan (`lifts`)."""
        if not lifts(before, after, now):
            return ()
        return (self._row(admin, org_id, WAKE_KIND, org_id, wake_payload()),)

    @staticmethod
    def _row(
        admin: OperatorContext,
        org_id: UUID,
        kind: str,
        target_id: UUID,
        payload: dict[str, object] | None = None,
    ) -> OutboxRow:
        """An operator has no user in the tenant, so the row records the
        operator's identity as its actor, as the tenancy operator plane's do."""
        return OutboxRow(
            id=new_id(),
            created_at=utcnow(),
            org_id=org_id,
            kind=kind,
            target_id=target_id,
            payload=payload or {},
            actor_id=admin.identity_id,
            request_id=admin.request_id,
            app=admin.app.type.value,
            traceparent=admin.traceparent,
        )
