"""Billing routes: the org's plan, a checkout for a paid one, the
processor's portal, and cancellation. Changing the plan is an owner's or an
admin's; everyone in the org reads it."""

from fastapi import APIRouter, Response

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.resolve import BillingService
from acme.services.api.types.billing import (
    BillingView,
    OpenPortalRequest,
    RedirectView,
    StartCheckoutRequest,
)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("", response_model=BillingView)
async def get_billing(ctx: Ctx, billing: BillingService) -> BillingView:
    return await billing.get_billing(ctx)


@router.post("/checkout", response_model=RedirectView, status_code=201)
async def start_checkout(
    ctx: Ctx, billing: BillingService, body: StartCheckoutRequest, idem: Idem
) -> Response:
    """Makes the org's customer at the processor the first time, which is a
    durable row, so the route runs under the idempotency record."""
    return await idem.run(201, lambda _: billing.start_checkout(ctx, body))


@router.post("/portal", response_model=RedirectView)
async def open_portal(ctx: Ctx, billing: BillingService, body: OpenPortalRequest) -> RedirectView:
    return await billing.open_portal(ctx, body)


@router.post("/cancel", response_model=BillingView)
async def cancel(ctx: Ctx, billing: BillingService) -> BillingView:
    """The paid plan ends at its period's end; the org keeps it until then."""
    return await billing.cancel(ctx)


@router.post("/resume", response_model=BillingView)
async def resume(ctx: Ctx, billing: BillingService) -> BillingView:
    return await billing.resume(ctx)
