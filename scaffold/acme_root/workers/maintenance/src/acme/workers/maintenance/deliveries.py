"""The consumer of what providers deliver: the other side of the webhook
routes. The API verifies each delivery at the edge and queues it on
`Queues.WEBHOOKS` as `{"idempotency_key", "provider", "delivery"}`. The
consumer receives it, hands the delivery to the provider it names, finds
the org the delivery names, and applies it under that org's service
context.

Every delivery is applied once: what it writes takes an id derived from the
delivery's key, so a copy the queue hands over again changes nothing. A
message is deleted once it is applied, or once it can never be (malformed,
no org, or an org that is gone). Any other failure leaves it to come back,
later on each receive, and the queue dead-letters it past its receives. So
does a provider this worker does not know, since a newer one may."""

import asyncio
import contextlib
import json
import logging
import random
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID

from acme.infra.observability import OUTCOMES, current_traceparent, request_id_var
from acme.infra.queues import QueueMessage, Queues, QueuesInterface
from acme.integrations.identity import ProvidedDelivery
from acme.om.base import EMPTY_UUID, OmBase, derived_id, new_id
from acme.om.change_events import ChangeEventsManagerInterface
from acme.om.change_events.manager import audit_change_event
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.exceptions import InvalidCredential
from acme.om.tenancy import TenancyManagerInterface
from acme.timing import Draw, RetryPolicy

log = logging.getLogger(__name__)

KEPT = frozenset({"failed", "unknown_provider"})
"""The outcomes that leave the message on the queue to come back."""

MAX_VISIBILITY = timedelta(hours=12)
"""The longest the hosted queue hides a message, counted from its receive."""


class DeliveryProviderInterface[D](ABC):
    """One provider's deliveries, under the name the API queues them with."""

    @abstractmethod
    def read(self, delivery: object) -> D:
        """The delivery the message carries; ValueError when it is not one."""
        ...

    @abstractmethod
    async def org_of(self, rctx: RequestContext, delivery: D) -> UUID | None:
        """The Acme org the delivery is about; None when it names none."""
        ...

    @abstractmethod
    async def apply(self, ctx: TenantContext, delivery: D) -> bool:
        """Applies the delivery in its org. True when this call applied it,
        False when a copy was applied before."""
        ...


class IdentityDeliveriesImpl(DeliveryProviderInterface[ProvidedDelivery]):
    """The identity provider's events: each is an audit entry in the org's
    stream, under an id derived from the delivery's key and the time the
    provider made it."""

    KIND = "identity.event.received"

    def __init__(self, change_events: ChangeEventsManagerInterface) -> None:
        self._change_events = change_events

    def read(self, delivery: object) -> ProvidedDelivery:
        return ProvidedDelivery.model_validate(delivery)

    async def org_of(self, rctx: RequestContext, delivery: ProvidedDelivery) -> UUID | None:
        # The provider's organization carries the Acme org's id as its
        # external id; an organization Acme did not make carries another.
        try:
            return UUID(delivery.organization_external_id or "")
        except ValueError:
            return None

    async def apply(self, ctx: TenantContext, delivery: ProvidedDelivery) -> bool:
        event = audit_change_event(
            ctx,
            derived_id(delivery.key, delivery.created),
            self.KIND,
            ctx.org_id,
            {"event_id": delivery.event_id, "event_type": delivery.event_type},
        )
        stored = await self._change_events.append_change_event(ctx, event)
        # A copy meets the event a first handling stored, which names the
        # request of that handling and not this one.
        return stored.request_id == ctx.request_id


class DeliveryOptions(OmBase):
    worker_id: str
    batch: int = 10
    wait: timedelta = timedelta(seconds=10)
    """The long poll: an empty queue answers after this, never at once."""
    visibility: timedelta = timedelta(seconds=60)
    """How long a received delivery is hidden from another receive: longer
    than an apply takes, which is a read of the org and one commit."""
    retry: timedelta = timedelta(seconds=120)
    """The longest wait after a delivery's first kept receive, and twice the
    shortest: four times longer on each receive after it, so a delivery
    outlasts an outage of hours before the queue dead-letters it."""
    backoff: timedelta = timedelta(seconds=5)
    """The pause after a receive that failed, so a queue that is down is not
    asked in a tight loop."""


class DeliveryConsumer:
    def __init__(
        self,
        *,
        queues: QueuesInterface,
        tenancy: TenancyManagerInterface,
        providers: Mapping[str, DeliveryProviderInterface[Any]],
        options: DeliveryOptions,
        jitter: Draw = random.random,
    ) -> None:
        self._queues = queues
        self._tenancy = tenancy
        self._providers = providers
        self._options = options
        self._jitter = jitter
        # The hosted queue counts its 12 hours from the receive, and the wait
        # is set within the receive's visibility, so the cap leaves it out.
        # Four times longer on each receive, half of it drawn: the shortest
        # wait of a receive is longer than the longest of the one before.
        self._retry = RetryPolicy(
            base=options.retry.total_seconds(),
            cap=(MAX_VISIBILITY - options.visibility).total_seconds(),
            growth=4.0,
            jitter=0.5,
        )
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
            try:
                outcome = await self._apply(rctx, message)
                if outcome not in KEPT:
                    await self._queues.delete(Queues.WEBHOOKS, message.receipt)
            except Exception:
                # A delete that failed is a failure too: the copy that comes
                # back is a duplicate.
                log.exception("delivery %s failed on receive %d", message.id, message.attempts)
                outcome = "failed"
            if outcome in KEPT:
                await self._come_back_later(message)
        finally:
            request_id_var.reset(token)
        OUTCOMES.labels(subsystem="deliveries", outcome=outcome).inc()
        return outcome

    async def _come_back_later(self, message: QueueMessage) -> None:
        """Hides a kept message for longer on each receive. A change that
        fails leaves the message to come back after the receive's
        visibility."""
        wait = timedelta(seconds=self._retry.wait(message.attempts, draw=self._jitter))
        try:
            await self._queues.change_visibility(Queues.WEBHOOKS, message.receipt, wait)
        except Exception:
            log.exception(
                "delivery %s keeps its visibility of receive %d", message.id, message.attempts
            )

    async def _apply(self, rctx: RequestContext, message: QueueMessage) -> str:
        try:
            body = json.loads(message.body)
            name = body["provider"]
            raw = body["delivery"]
        except ValueError, KeyError, TypeError:
            log.error("message %s is not a delivery; dropped", message.id)
            return "malformed"
        provider = self._providers.get(name) if isinstance(name, str) else None
        if provider is None:
            log.error("message %s names provider %r, which this worker lacks", message.id, name)
            return "unknown_provider"
        try:
            delivery = provider.read(raw)
        except ValueError:
            log.error("message %s is not a %s delivery; dropped", message.id, name)
            return "malformed"
        org_id = await provider.org_of(rctx, delivery)
        if org_id is None:
            log.warning("%s delivery %s names no org; dropped", name, message.id)
            return "unowned"
        try:
            ctx = await self._tenancy.service_context(rctx, org_id, EMPTY_UUID)
        except InvalidCredential:
            log.warning("%s delivery %s names org %s, which is gone", name, message.id, org_id)
            return "unowned"
        applied = await provider.apply(ctx, delivery)
        return "applied" if applied else "duplicate"
