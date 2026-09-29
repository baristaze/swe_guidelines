"""Inbound calls from providers, outside /v1: the version of their shape is
the provider's. A route takes no credential and no rate limit; it is
authenticated by the provider's signature over the body and its timestamp,
checked before anything is queued, and a worker does what the call means.

The identity provider delivers its events to `/webhooks/identity`."""

from fastapi import APIRouter

from acme.services.api.gateway.auth import Rctx
from acme.services.api.gateway.resolve import WebhooksService
from acme.services.api.gateway.webhooks import IdentityDelivery
from acme.services.api.types.webhooks import DeliveryReceivedView

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/identity", response_model=DeliveryReceivedView)
async def identity_delivery(
    rctx: Rctx, webhooks: WebhooksService, delivery: IdentityDelivery
) -> DeliveryReceivedView:
    return await webhooks.receive_identity(rctx, delivery)
