"""The billing swimlane: the plan an org is on, what it entitles the org to,
and the org's account at the payment processor.

The processor owns payment and the subscription; Acme owns entitlements.
The account mirrors the subscription, written only from a read of the
processor, and the plan is derived from that mirror and one pure table in
`billing.rules`."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.integrations.payments.types import ProviderDelivery
from acme.om.billing.types.account import BillingAccount
from acme.om.billing.types.billing import Billing, CheckoutStart, Entitlements
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import OpContext, OperatorContext, RequestContext


class EntitlementsInterface(ABC):
    """What every lever asks before it lets an org have one more: the plan
    the org is on now and its bounds. The tasks and tenancy managers hold
    this and nothing else of billing."""

    @abstractmethod
    async def get_entitlements(self, ctx: OpContext) -> Entitlements: ...

    @abstractmethod
    def entitlements_of(self, ctx: OpContext, account: BillingAccount | None) -> Entitlements:
        """What `get_entitlements` answers, from the org's account as a read
        of the caller's own found it. An api key's use reads the account in
        the statement that reads its principal, so the key path reads the
        database once for both; the plan is still decided here."""
        ...


class BillingManagerInterface(EntitlementsInterface):
    @abstractmethod
    async def get_billing(self, ctx: OpContext) -> Billing:
        """The plan, where it comes from, and when a paid plan set to end
        drops the org to what is left."""
        ...

    @abstractmethod
    async def start_checkout(
        self,
        ctx: OpContext,
        plan: Plan,
        seats: int,
        org_name: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutStart:
        """A hosted checkout for `plan`, for an org with no paid plan. The org's
        customer is made the first time, with the account that remembers it.
        A per-seat plan starts at `seats`, the org's active members; any other
        at one. An org that pays already changes plan in the processor's
        portal, and is refused here (`SubscriptionExists`)."""
        ...

    @abstractmethod
    async def open_portal(
        self, ctx: OpContext, return_url: str, update_payment_method: bool = False
    ) -> str:
        """The processor's page for the org's customer: payment methods,
        invoices, a change between paid plans, cancellation. With
        `update_payment_method`, the page opens on adding a payment method
        and comes back to `return_url` once one is saved: the way out of a
        payment that failed."""
        ...

    @abstractmethod
    async def cancel(self, ctx: OpContext) -> Billing:
        """Sets the paid plan to end at its period's end. The org keeps it until
        then and drops to what is left after."""
        ...

    @abstractmethod
    async def resume(self, ctx: OpContext) -> Billing:
        """Takes a cancellation back before the period ends."""
        ...

    @abstractmethod
    async def org_of_delivery(
        self, rctx: RequestContext, delivery: ProviderDelivery
    ) -> UUID | None:
        """Platform-internal: the org a verified delivery is about, before any
        stage exists for it: the org the event names itself, else the one the
        customer's metadata names. A hint the delivery's own apply then holds
        to the customer the org's account names. None when no org is named."""
        ...

    @abstractmethod
    async def apply_delivery(self, ctx: OpContext, delivery: ProviderDelivery) -> bool:
        """Platform-internal: mirrors what a delivery is about, under the org's
        service context. The subscription is read from the processor again and
        never from the delivery, so deliveries that arrive out of order
        converge on what the processor holds now. The delivery's mark lands in
        the same commit as the account, so a second copy changes nothing and
        answers False."""
        ...

    @abstractmethod
    async def sync_seats(self, ctx: OpContext, seats: int, idempotency_key: str) -> Billing:
        """Holds a per-seat subscription's quantity to `seats`, the active
        member count, read by the caller at the moment it runs. A plan not per
        seat, or a quantity already at `seats`, changes nothing."""
        ...

    @abstractmethod
    async def close_account(self, ctx: OpContext) -> Billing:
        """Platform-internal, a step of `DELETE_ACCOUNT`: the org is going with
        its person, so its subscription is canceled now and its customer at
        the processor deleted; the processor keeps the invoices. The account
        then names neither, so a rerun calls nothing. An org that never
        started a checkout calls nothing either."""
        ...

    @abstractmethod
    async def grant_seeded_plan(self, ctx: OpContext, plan: Plan) -> Billing:
        """Platform-internal: the local seed's grant, so the team a laptop is
        seeded with is not on Free. It takes the context a seeding transition
        produced (`bootstrap`), an internal credential held by the org's
        owner; a tenant's own credential is never internal, so no route
        reaches it. The operator plane's grant is `comp_plan`."""
        ...

    @abstractmethod
    async def purge_across_tenants(self) -> int:
        """Platform-internal: the sweep, across tenants, once a pass: the marks
        of deliveries past the retention, a batch at most, whatever their
        tenant; returns how many. It takes no context, because it runs for
        no tenant and no principal."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: OpContext) -> int:
        """The sweep, for one tenant past its own retention: its account and
        its marks, a batch of each at most a call. Any other tenant returns 0
        and reads nothing: its marks go across tenants."""
        ...


class BillingOperatorManagerInterface(ABC):
    """The operator plane of billing: one named org's plan, read and granted.
    Takes `OperatorContext` and nothing else."""

    @abstractmethod
    async def get_billing(self, admin: OperatorContext, org_id: UUID) -> Billing: ...

    @abstractmethod
    async def comp_plan(self, admin: OperatorContext, org_id: UUID, plan: Plan | None) -> Billing:
        """Grants the org `plan` with no payment, or takes a grant back with
        None. The org is on the higher of the grant and what it pays for."""
        ...
