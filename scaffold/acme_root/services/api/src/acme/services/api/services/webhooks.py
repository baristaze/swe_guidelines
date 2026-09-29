"""The webhooks service: the edge of a provider's deliveries. The check, then
the queue; what a delivery means is the worker's to apply."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from acme.om.opcontext import RequestContext
from acme.services.api.types.webhooks import DeliveryReceivedView


@dataclass(frozen=True)
class SignedDelivery:
    """What an inbound delivery carries in: its body exactly as it arrived,
    which is what the signature is over, and the provider's signature
    header, which the gateway reads."""

    payload: bytes
    signature: str | None


class WebhooksServiceInterface(ABC):
    @abstractmethod
    async def receive_identity(
        self, rctx: RequestContext, delivery: SignedDelivery
    ) -> DeliveryReceivedView:
        """Checks the identity provider's signature over the body and its
        timestamp, then queues the delivery by the request's deadline. A
        delivery that fails the check is refused before anything is
        queued."""
        ...
