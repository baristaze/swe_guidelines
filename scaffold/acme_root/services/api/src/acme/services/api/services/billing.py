"""The billing service: the org's plan and its usage, the processor's hosted
pages, cancellation, and the processor's inbound deliveries."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from acme.om.opcontext import OpContext, RequestContext
from acme.services.api.types.billing import (
    BillingView,
    DeliveryReceivedView,
    OpenPortalRequest,
    RedirectView,
    StartCheckoutRequest,
)


@dataclass(frozen=True)
class SignedDelivery:
    """What an inbound delivery carries in: its body exactly as it arrived,
    which is what the signature is over, and the processor's signature
    header, which the gateway reads."""

    payload: bytes
    signature: str | None


class BillingServiceInterface(ABC):
    @abstractmethod
    async def get_billing(self, ctx: OpContext) -> BillingView: ...

    @abstractmethod
    async def start_checkout(self, ctx: OpContext, body: StartCheckoutRequest) -> RedirectView: ...

    @abstractmethod
    async def open_portal(self, ctx: OpContext, body: OpenPortalRequest) -> RedirectView: ...

    @abstractmethod
    async def cancel(self, ctx: OpContext) -> BillingView: ...

    @abstractmethod
    async def resume(self, ctx: OpContext) -> BillingView: ...


class WebhooksServiceInterface(ABC):
    @abstractmethod
    async def receive_stripe(
        self, rctx: RequestContext, delivery: SignedDelivery
    ) -> DeliveryReceivedView:
        """Checks the delivery's signature over its body and its timestamp,
        and queues it by the request's deadline; a delivery that fails the
        check is refused before anything is queued."""
        ...
