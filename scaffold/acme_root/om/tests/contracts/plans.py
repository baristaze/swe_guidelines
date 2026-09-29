"""The plan an object-model test runs its org on. Most tests are about
something else than a plan's bounds, so they run on Team: api keys, no
bound on active tasks, and no per-seat quantity to keep in step. The billing
tests name the plan they test."""

from datetime import datetime
from uuid import UUID

from acme.om.base import EMPTY_UUID, utcnow
from acme.om.billing.manager import EntitlementsInterface
from acme.om.billing.rules import limits_of
from acme.om.billing.storage.impl.memory import BillingStorageMemoryImpl
from acme.om.billing.types.account import BillingAccount
from acme.om.billing.types.billing import Entitlements
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import OpContext, Permission


class FixedPlan(EntitlementsInterface):
    """Every org on one plan."""

    def __init__(self, plan: Plan = Plan.MAX) -> None:
        self.plan = plan

    async def get_entitlements(self, ctx: OpContext) -> Entitlements:
        return self.entitlements_of(ctx, None)

    def entitlements_of(self, ctx: OpContext, account: BillingAccount | None) -> Entitlements:
        ctx.require(Permission.READ)
        return Entitlements(plan=self.plan, limits=limits_of(self.plan))


ON_TEAM = FixedPlan(Plan.TEAM)


class GrantedEverywhere(BillingStorageMemoryImpl):
    """A billing storage in which every org without an account of its own
    holds a grant of one plan, for the operator plane's tests."""

    def __init__(self, plan: Plan = Plan.MAX) -> None:
        super().__init__()
        self._plan = plan

    async def read_account(self, org_id: UUID) -> BillingAccount | None:
        found = await super().read_account(org_id)
        if found is not None:
            return found
        now: datetime = utcnow()
        return BillingAccount(
            id=org_id,
            created_at=now,
            updated_at=now,
            created_by=EMPTY_UUID,
            updated_by=EMPTY_UUID,
            comped_plan=self._plan,
        )
