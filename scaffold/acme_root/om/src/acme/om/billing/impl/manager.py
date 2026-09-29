import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from acme.infra.cache import CacheInterface
from acme.integrations.payments import PaymentsInterface
from acme.integrations.payments.types import ProviderDelivery, ProviderSubscription
from acme.om.base import Platform, new_id, utcnow
from acme.om.billing.impl.cache import AccountCache
from acme.om.billing.manager import BillingManagerInterface
from acme.om.billing.rules import (
    LIVE_STATUSES,
    PLAN_PRICES,
    effective_plan,
    ends_at,
    limits_of,
    paid_plan,
    payment_failed,
    plan_rose,
    seats_metered,
    should_mirror,
)
from acme.om.billing.storage import BillingStorageInterface
from acme.om.billing.types.account import BillingAccount, status_of
from acme.om.billing.types.billing import Billing, CheckoutStart, Entitlements
from acme.om.billing.types.delivery import BillingDelivery
from acme.om.billing.types.plan import Plan
from acme.om.exceptions import (
    NotAuthorized,
    NotFound,
    SubscriptionExists,
    TenantMismatch,
    ValidationFailed,
)
from acme.om.opcontext import (
    CredentialKind,
    OpContext,
    Permission,
    ProvenanceScope,
    RequestContext,
    Role,
)
from acme.om.orchestrations.types.orchestration import ParkReason
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.tenancy import TenancyManagerInterface
from acme.om.work.types.work_item import WakeParkedPayload, WorkKind, work_row_kind

log = logging.getLogger(__name__)

ACCOUNT_UPDATED = "billing.account.updated"
ACCOUNT_CREATED = "billing.account.created"


WAKE_KIND = work_row_kind(WorkKind.WAKE_PARKED)


def lifts(before: BillingAccount | None, after: BillingAccount, now: datetime) -> bool:
    """Whether an account write lifted the org's plan: the event that wakes
    the org's records parked on a plan's bound (an import stopped at the
    plan's active tasks)."""
    return plan_rose(effective_plan(before, now), effective_plan(after, now))


def wake_payload() -> dict[str, object]:
    return WakeParkedPayload(reason=ParkReason.PLAN_LIMIT).model_dump(mode="json")


def wake_rows(
    ctx: ProvenanceScope,
    org_id: UUID,
    before: BillingAccount | None,
    after: BillingAccount,
    now: datetime,
) -> tuple[OutboxRow, ...]:
    """The work row an account write lands when it lifted the plan. It rides
    the account's commit, so a plan change and its wake-up are one fact."""
    if not lifts(before, after, now):
        return ()
    return (outbox_row(ctx, WAKE_KIND, org_id, wake_payload()),)


class BillingOptions(Platform):
    retention: timedelta = timedelta(days=30)
    """A delivery's mark is kept this long: past every retry the processor
    makes of one event, which stops after three days."""
    purge_batch: int = 1000
    """Marks one purge statement deletes at most; the sweep calls again for
    the rest."""
    account_ttl: timedelta = timedelta(seconds=60)
    """How long a cached account is read before storage is asked again. A
    write orphans the cached account at once; this bounds how stale a read
    can be when that fails."""


def billing_of(account: BillingAccount | None, now: datetime) -> Billing:
    """The answer every read and every write of this namespace gives."""
    plan = effective_plan(account, now)
    paid = paid_plan(account, now)
    comped = account.comped_plan if account else None
    ending = ends_at(account, now)
    return Billing(
        plan=plan,
        limits=limits_of(plan),
        paid_plan=paid,
        comped_plan=comped,
        ends_at=ending,
        plan_after=(comped or Plan.FREE) if ending is not None else None,
        payment_failed=payment_failed(account),
        account=account,
    )


def mirrored(
    account: BillingAccount, subscription: ProviderSubscription, actor: UUID, now: datetime
) -> BillingAccount:
    """The account with the subscription as the processor holds it now."""
    return account.model_copy(
        update={
            "subscription_id": subscription.id,
            "price_lookup_key": subscription.price_lookup_key,
            "status": status_of(subscription.status),
            "current_period_end": subscription.current_period_end,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "quantity": subscription.quantity,
            "synced_at": now,
            "updated_at": now,
            "updated_by": actor,
        }
    )


class BillingManagerImpl(BillingManagerInterface):
    def __init__(
        self,
        storage: BillingStorageInterface,
        payments: PaymentsInterface,
        relay: OutboxRelayInterface,
        tenancy: Callable[[], TenancyManagerInterface],
        cache: CacheInterface,
        options: BillingOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        """`tenancy` is a provider and not the manager itself: the tenancy
        manager asks this one for an org's entitlements, and this one asks it
        whether a tenant is past its retention, so the root binds the edge at
        call time.

        `cache` is the billing account's scope. The reads that answer what
        the org is on (`get_entitlements`, `get_billing`) read the account
        through it; every write of the account bumps the org's generation
        after its commit. A write reads storage, never the cache."""
        self._storage = storage
        self._payments = payments
        self._relay = relay
        self._tenancy = tenancy
        self._options = options
        self._clock = clock
        self._accounts = AccountCache(cache, options.account_ttl)

    async def get_entitlements(self, ctx: OpContext) -> Entitlements:
        ctx.require(Permission.READ)
        return self.entitlements_of(ctx, await self._cached_account(ctx))

    def entitlements_of(self, ctx: OpContext, account: BillingAccount | None) -> Entitlements:
        ctx.require(Permission.READ)
        plan = effective_plan(account, self._clock())
        return Entitlements(plan=plan, limits=limits_of(plan))

    async def get_billing(self, ctx: OpContext) -> Billing:
        ctx.require(Permission.READ)
        return billing_of(await self._cached_account(ctx), self._clock())

    async def start_checkout(
        self,
        ctx: OpContext,
        plan: Plan,
        seats: int,
        org_name: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutStart:
        ctx.require(Permission.MANAGE_BILLING)
        lookup_key = PLAN_PRICES[plan].lookup_key
        if lookup_key is None:
            raise ValidationFailed(f"the {plan.value} plan is not bought")
        account = await self._storage.read_account(ctx.org_id)
        paid = paid_plan(account, self._clock())
        if paid is not None:
            raise SubscriptionExists(
                f"the org pays for {paid.value}; change the plan in the billing portal"
            )
        if account is None or account.customer_id is None:
            customer_id = await self._payments.create_customer(
                ctx.org_id, org_name, deadline=ctx.deadline
            )
            account = await self._with_customer(ctx, account, customer_id)
        assert account.customer_id is not None
        url = await self._payments.create_checkout(
            customer_id=account.customer_id,
            org_id=ctx.org_id,
            lookup_key=lookup_key,
            quantity=max(1, seats) if seats_metered(plan) else 1,
            success_url=success_url,
            cancel_url=cancel_url,
            deadline=ctx.deadline,
        )
        log.info("org %s started a checkout for %s", ctx.org_id, plan.value)
        return CheckoutStart(url=url)

    async def open_portal(
        self, ctx: OpContext, return_url: str, update_payment_method: bool = False
    ) -> str:
        ctx.require(Permission.MANAGE_BILLING)
        account = await self._storage.read_account(ctx.org_id)
        if account is None or account.customer_id is None:
            raise NotFound("the org has no billing account yet; choose a plan first")
        return await self._payments.create_portal_session(
            account.customer_id, return_url, update_payment_method, deadline=ctx.deadline
        )

    async def cancel(self, ctx: OpContext) -> Billing:
        return await self._set_cancel(ctx, True)

    async def resume(self, ctx: OpContext) -> Billing:
        return await self._set_cancel(ctx, False)

    async def _set_cancel(self, ctx: OpContext, cancel: bool) -> Billing:
        ctx.require(Permission.MANAGE_BILLING)
        account = await self._storage.read_account(ctx.org_id)
        now = self._clock()
        if account is None or account.subscription_id is None or paid_plan(account, now) is None:
            raise NotFound("the org has no paid plan to change")
        subscription = await self._payments.set_cancel_at_period_end(
            account.subscription_id, cancel, deadline=ctx.deadline
        )
        updated = mirrored(account, subscription, ctx.user_id, now)
        await self._write(ctx, updated)
        log.info("org %s set its plan to %s", ctx.org_id, "end" if cancel else "renew")
        return billing_of(updated, now)

    async def close_account(self, ctx: OpContext) -> Billing:
        # The work of an account's deletion, as a seat count's is: the
        # service role runs it, and it manages members, not a plan.
        ctx.require(Permission.MANAGE_MEMBERS)
        account = await self._storage.read_account(ctx.org_id)
        now = self._clock()
        if account is None or (account.customer_id is None and account.subscription_id is None):
            return billing_of(account, now)
        # The subscription first: the customer's deletion would end it too,
        # but a customer that will not go must not leave it billing.
        if account.subscription_id is not None:
            await self._payments.cancel_subscription(account.subscription_id)
        if account.customer_id is not None:
            await self._payments.delete_customer(account.customer_id)
        closed = account.model_copy(
            update={
                "customer_id": None,
                "subscription_id": None,
                "status": None,
                "cancel_at_period_end": False,
                "synced_at": now,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        await self._write(ctx, closed)
        log.info("org %s closed its account at the processor", ctx.org_id)
        return billing_of(closed, now)

    async def org_of_delivery(
        self, rctx: RequestContext, delivery: ProviderDelivery
    ) -> UUID | None:
        if delivery.org_hint is not None:
            return delivery.org_hint
        if delivery.customer_id is not None:
            return await self._payments.read_customer_org(delivery.customer_id)
        return None

    async def apply_delivery(self, ctx: OpContext, delivery: ProviderDelivery) -> bool:
        ctx.require(Permission.WRITE)
        mark = BillingDelivery(
            id=delivery.idempotency_key,
            created_at=self._clock(),
            event_id=delivery.event_id,
            event_type=delivery.event_type,
        )
        if await self._storage.read_delivery(ctx.org_id, mark.id) is not None:
            return False
        account = await self._storage.read_account(ctx.org_id)
        now = self._clock()
        if account is None or account.customer_id is None:
            log.warning(
                "delivery %s names org %s, which has no customer here; nothing to mirror",
                delivery.event_id,
                ctx.org_id,
            )
            return False
        if delivery.customer_id is not None and delivery.customer_id != account.customer_id:
            log.warning(
                "delivery %s names customer %s, not org %s's; nothing to mirror",
                delivery.event_id,
                delivery.customer_id,
                ctx.org_id,
            )
            return await self._storage.write_account(ctx.org_id, account, (), mark)
        updated = account
        if delivery.subscription_id is not None:
            subscription = await self._payments.read_subscription(delivery.subscription_id)
            if subscription is not None and self._follows(account, subscription):
                updated = mirrored(account, subscription, ctx.user_id, now)
        rows = (
            (*self._rows(ctx, updated), *wake_rows(ctx, ctx.org_id, account, updated, now))
            if updated is not account
            else ()
        )
        applied = await self._storage.write_account(ctx.org_id, updated, rows, mark)
        if applied:
            await self._accounts.changed(ctx.org_id)
            await self._relay.relay_all(ctx.org_id, rows)
            log.info(
                "applied %s %s to org %s",
                delivery.event_type,
                delivery.event_id,
                ctx.org_id,
            )
        return applied

    @staticmethod
    def _follows(account: BillingAccount, subscription: ProviderSubscription) -> bool:
        """The subscription is the org's own, and the one the mirror follows."""
        if subscription.customer_id != account.customer_id:
            log.warning(
                "subscription %s is not customer %s's; not mirrored",
                subscription.id,
                account.customer_id,
            )
            return False
        live = status_of(subscription.status) in LIVE_STATUSES
        return should_mirror(account, subscription.id, live)

    async def sync_seats(self, ctx: OpContext, seats: int, idempotency_key: str) -> Billing:
        ctx.require(Permission.MANAGE_MEMBERS)
        account = await self._storage.read_account(ctx.org_id)
        now = self._clock()
        paid = paid_plan(account, now)
        if account is None or account.subscription_id is None or paid is None:
            return billing_of(account, now)
        if not seats_metered(paid):
            return billing_of(account, now)
        wanted = max(1, seats)
        current = await self._payments.read_subscription(account.subscription_id)
        if current is None:
            return billing_of(account, now)
        if current.quantity != wanted:
            current = await self._payments.set_quantity(current, wanted, idempotency_key)
            log.info("org %s now pays for %d seats", ctx.org_id, wanted)
        updated = mirrored(account, current, ctx.user_id, now)
        await self._write(ctx, updated)
        return billing_of(updated, now)

    async def grant_seeded_plan(self, ctx: OpContext, plan: Plan) -> Billing:
        ctx.require(Permission.MANAGE_BILLING)
        if ctx.credential_kind is not CredentialKind.INTERNAL or ctx.role is not Role.OWNER:
            raise NotAuthorized("a plan is granted by an operator; the seed grants its own org")
        now = self._clock()
        account = await self._storage.read_account(ctx.org_id)
        grant = None if plan is Plan.FREE else plan
        if account is None:
            created = BillingAccount(
                id=new_id(),
                created_at=now,
                updated_at=now,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
                comped_plan=grant,
            )
            rows = (
                outbox_row(ctx, ACCOUNT_CREATED, created.id, {}),
                *wake_rows(ctx, ctx.org_id, None, created, now),
            )
            if await self._storage.create_account(ctx.org_id, created, rows):
                await self._accounts.changed(ctx.org_id)
                await self._relay.relay_all(ctx.org_id, rows)
                return billing_of(created, now)
            account = await self._storage.read_account(ctx.org_id)
            if account is None:
                raise TenantMismatch(f"billing_accounts {created.id} is not in {ctx.org_id}")
        updated = account.model_copy(
            update={"comped_plan": grant, "updated_at": now, "updated_by": ctx.user_id}
        )
        await self._write(ctx, updated, wake_rows(ctx, ctx.org_id, account, updated, now))
        return billing_of(updated, now)

    async def purge_across_tenants(self) -> int:
        return await self._storage.purge_deliveries(
            self._clock() - self._options.retention, self._options.purge_batch
        )

    async def purge_tenant(self, ctx: OpContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy().tenant_expired(ctx):
            return 0
        purged = await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)
        await self._accounts.changed(ctx.org_id)
        return purged

    async def _with_customer(
        self, ctx: OpContext, account: BillingAccount | None, customer_id: str
    ) -> BillingAccount:
        """The org's account, now naming its customer: created the first time,
        or, when a grant made the account first, updated with it."""
        now = self._clock()
        if account is not None:
            updated = account.model_copy(
                update={"customer_id": customer_id, "updated_at": now, "updated_by": ctx.user_id}
            )
            await self._write(ctx, updated)
            return updated
        created = BillingAccount(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            customer_id=customer_id,
        )
        rows = (outbox_row(ctx, ACCOUNT_CREATED, created.id, {}),)
        if await self._storage.create_account(ctx.org_id, created, rows):
            await self._accounts.changed(ctx.org_id)
            await self._relay.relay_all(ctx.org_id, rows)
            return created
        # Another start raced this one and made the account first; the
        # processor's idempotency key made them one customer.
        stored = await self._storage.read_account(ctx.org_id)
        if stored is None:
            raise TenantMismatch(f"billing_accounts {created.id} is not in {ctx.org_id}")
        if stored.customer_id is None:
            return await self._with_customer(ctx, stored, customer_id)
        return stored

    @staticmethod
    def _rows(ctx: ProvenanceScope, account: BillingAccount) -> tuple[OutboxRow, ...]:
        return (outbox_row(ctx, ACCOUNT_UPDATED, account.id, {}),)

    async def _write(
        self, ctx: OpContext, account: BillingAccount, work: tuple[OutboxRow, ...] = ()
    ) -> None:
        rows = (*self._rows(ctx, account), *work)
        await self._storage.write_account(ctx.org_id, account, rows)
        await self._accounts.changed(ctx.org_id)
        await self._relay.relay_all(ctx.org_id, rows)

    async def _cached_account(self, ctx: OpContext) -> BillingAccount | None:
        """The org's account for a read, from the cache when it holds it.
        The caller has checked its permission first."""
        return await self._accounts.read(ctx.org_id, lambda: self._storage.read_account(ctx.org_id))
