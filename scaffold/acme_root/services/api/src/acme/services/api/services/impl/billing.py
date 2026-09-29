import json
from urllib.parse import urlsplit

from acme.infra.queues import Queues, QueuesInterface
from acme.integrations.payments import PaymentsInterface
from acme.om.billing import BillingManagerInterface
from acme.om.billing.rules import PLAN_LIMITS, PLAN_PRICES, monthly_price_cents
from acme.om.billing.types.billing import Billing
from acme.om.billing.types.plan import Plan
from acme.om.exceptions import ValidationFailed
from acme.om.media import MediaManagerInterface
from acme.om.opcontext import OpContext, Permission, RequestContext
from acme.om.tasks import TasksManagerInterface
from acme.om.tenancy import TenancyManagerInterface
from acme.services.api.services.billing import (
    BillingServiceInterface,
    SignedDelivery,
    WebhooksServiceInterface,
)
from acme.services.api.types.billing import (
    BillingView,
    DeliveryReceivedView,
    OpenPortalRequest,
    PlanLimitsView,
    PlanOfferView,
    RedirectView,
    StartCheckoutRequest,
)

CHECKOUT_DONE = "checkout=done"
CHECKOUT_CANCELLED = "checkout=cancelled"

PLANS: tuple[PlanOfferView, ...] = tuple(
    PlanOfferView(
        plan=plan,
        limits=PlanLimitsView.model_validate(PLAN_LIMITS[plan]),
        flat_cents=PLAN_PRICES[plan].flat_cents,
        included_seats=PLAN_PRICES[plan].included_seats,
        per_seat_cents=PLAN_PRICES[plan].per_seat_cents,
    )
    for plan in Plan
)
"""The plans on offer, from the one table in the object model."""


def portal_url(return_url: str, origins: list[str]) -> str:
    """The page a person comes back to, held to the portal's own origins, so
    the processor never sends anyone to a page this API did not serve."""
    parts = urlsplit(return_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if parts.scheme not in ("http", "https") or origin not in origins:
        raise ValidationFailed("return_url is not a page of the portal")
    return return_url


def with_query(url: str, pair: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}{pair}"


class BillingServiceImpl(BillingServiceInterface):
    """Composes the billing manager with the counts a plan reads, the
    members, the active tasks, and the files kept, each from the namespace
    that owns it."""

    def __init__(
        self,
        billing: BillingManagerInterface,
        tenancy: TenancyManagerInterface,
        tasks: TasksManagerInterface,
        media: MediaManagerInterface,
        portal_origins: list[str],
    ) -> None:
        self._billing = billing
        self._tenancy = tenancy
        self._tasks = tasks
        self._media = media
        self._origins = portal_origins

    async def get_billing(self, ctx: OpContext) -> BillingView:
        return await self._view(ctx, await self._billing.get_billing(ctx))

    async def start_checkout(self, ctx: OpContext, body: StartCheckoutRequest) -> RedirectView:
        back = portal_url(body.return_url, self._origins)
        org = await self._tenancy.get_org(ctx)
        seats = await self._tenancy.count_members(ctx)
        start = await self._billing.start_checkout(
            ctx,
            body.plan,
            seats,
            org.name,
            with_query(back, CHECKOUT_DONE),
            with_query(back, CHECKOUT_CANCELLED),
        )
        return RedirectView(url=start.url)

    async def open_portal(self, ctx: OpContext, body: OpenPortalRequest) -> RedirectView:
        back = portal_url(body.return_url, self._origins)
        update = body.flow == "payment_method_update"
        return RedirectView(url=await self._billing.open_portal(ctx, back, update))

    async def cancel(self, ctx: OpContext) -> BillingView:
        return await self._view(ctx, await self._billing.cancel(ctx))

    async def resume(self, ctx: OpContext) -> BillingView:
        return await self._view(ctx, await self._billing.resume(ctx))

    async def _view(self, ctx: OpContext, billing: Billing) -> BillingView:
        account = billing.account
        seats = await self._tenancy.count_members(ctx)
        usage = await self._media.get_usage(ctx)
        paid = billing.paid_plan
        return BillingView(
            plan=billing.plan,
            limits=PlanLimitsView.model_validate(billing.limits),
            paid_plan=paid,
            comped_plan=billing.comped_plan,
            status=account.status if account else None,
            current_period_end=account.current_period_end if account else None,
            cancel_at_period_end=account.cancel_at_period_end if account else False,
            ends_at=billing.ends_at,
            plan_after=billing.plan_after,
            seats=seats,
            active_tasks=await self._tasks.count_active_tasks(ctx),
            storage_bytes=usage.total_size_bytes + usage.pending_size_bytes,
            monthly_cents=0 if paid is None else monthly_price_cents(paid, seats),
            payment_failed=billing.payment_failed,
            can_manage=ctx.has(Permission.MANAGE_BILLING),
            plans=PLANS,
        )


class WebhooksServiceImpl(WebhooksServiceInterface):
    """The edge of an outside producer: the check, then the queue. What the
    delivery means is the worker's to apply, under the org it names."""

    def __init__(self, payments: PaymentsInterface, queues: QueuesInterface) -> None:
        self._payments = payments
        self._queues = queues

    async def receive_stripe(
        self, rctx: RequestContext, delivery: SignedDelivery
    ) -> DeliveryReceivedView:
        verified = self._payments.verify_delivery(delivery.payload, delivery.signature)
        body = {
            "idempotency_key": str(verified.idempotency_key),
            "provider": "stripe",
            "delivery": verified.model_dump(mode="json"),
        }
        await self._queues.send(Queues.WEBHOOKS, json.dumps(body).encode(), deadline=rctx.deadline)
        return DeliveryReceivedView(received=True)
