"""The consumer of the payment processor's deliveries: the other side of
the webhook route. It receives from the inbound queue, finds the org a
delivery names, and applies it under that org's service context. Every
delivery is applied once: its mark lands in the same commit as the account
it changes, so a copy the queue hands over again changes nothing. A message
is deleted once it is applied, or once it can never be (no org, or an org
that is gone); any other failure leaves it to come back after its
visibility, and the queue dead-letters it past its receives."""

import asyncio
import contextlib
import json
import logging
from datetime import timedelta

from acme.infra.observability import OUTCOMES, current_traceparent, request_id_var
from acme.infra.queues import QueueMessage, Queues, QueuesInterface
from acme.integrations.payments import ProviderDelivery
from acme.om.base import EMPTY_UUID, Platform, new_id
from acme.om.billing import BillingManagerInterface
from acme.om.exceptions import InvalidCredential
from acme.om.opcontext import AppContext, AppType, RequestContext
from acme.om.tenancy import TenancyManagerInterface

log = logging.getLogger(__name__)


class DeliveryOptions(Platform):
    worker_id: str
    batch: int = 10
    wait: timedelta = timedelta(seconds=10)
    """The long poll: an empty queue answers after this, never at once."""
    visibility: timedelta = timedelta(seconds=60)
    """How long a received delivery is hidden from another receive: longer
    than an apply takes, which is two reads of the processor and one commit."""
    backoff: timedelta = timedelta(seconds=5)
    """The pause after a receive that failed, so a queue that is down is not
    asked in a tight loop."""


class DeliveryConsumer:
    def __init__(
        self,
        *,
        queues: QueuesInterface,
        billing: BillingManagerInterface,
        tenancy: TenancyManagerInterface,
        options: DeliveryOptions,
    ) -> None:
        self._queues = queues
        self._billing = billing
        self._tenancy = tenancy
        self._options = options
        self._app = AppContext(type=AppType.WORKER, version=f"worker@{options.worker_id}")
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        """Receives until `stop()`; a delivery in hand is finished first."""
        while not self._stopping.is_set():
            try:
                messages = await self._queues.receive(
                    Queues.WEBHOOKS,
                    self._options.batch,
                    self._options.wait,
                    self._options.visibility,
                )
            except Exception:
                log.exception("receiving deliveries failed")
                OUTCOMES.labels(subsystem="deliveries", outcome="receive_failed").inc()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(), self._options.backoff.total_seconds()
                    )
                continue
            for message in messages:
                await self.handle(message)
            if not messages:
                # A long poll that came back empty awaited already; one that
                # answered at once must still let the loop run the rest.
                await asyncio.sleep(0)

    async def handle(self, message: QueueMessage) -> str:
        """One delivery, end to end; answers the outcome it counted."""
        rctx = RequestContext(request_id=new_id(), app=self._app, traceparent=current_traceparent())
        token = request_id_var.set(str(rctx.request_id))
        try:
            outcome = await self._apply(rctx, message)
        except Exception:
            log.exception("delivery %s failed on receive %d", message.id, message.attempts)
            outcome = "failed"
        else:
            await self._queues.delete(Queues.WEBHOOKS, message.receipt)
        finally:
            request_id_var.reset(token)
        OUTCOMES.labels(subsystem="deliveries", outcome=outcome).inc()
        return outcome

    async def _apply(self, rctx: RequestContext, message: QueueMessage) -> str:
        try:
            delivery = ProviderDelivery.model_validate(json.loads(message.body)["delivery"])
        except ValueError, KeyError, TypeError:
            log.error("message %s is not a delivery; dropped", message.id)
            return "malformed"
        org_id = await self._billing.org_of_delivery(rctx, delivery)
        if org_id is None:
            log.warning("delivery %s names no org; dropped", delivery.event_id)
            return "unowned"
        try:
            ctx = await self._tenancy.service_context(rctx, org_id, EMPTY_UUID)
        except InvalidCredential:
            log.warning("delivery %s names org %s, which is gone", delivery.event_id, org_id)
            return "unowned"
        applied = await self._billing.apply_delivery(ctx, delivery)
        return "applied" if applied else "duplicate"
