"""Inbound calls from providers, outside /v1: the version of their shape is
the provider's. A route takes no credential; it is authenticated by the
provider's signature over the body and its timestamp, checked before
anything is queued, and a worker does what the call means.

The payment processor delivers to `/webhooks/stripe`. Slack calls three
URLs, each named in the app's manifest: `/webhooks/slack/commands` for
`/acme`, `/webhooks/slack/events` for the Events API, and
`/webhooks/slack/oauth`, where Slack sends a person's browser back at the end
of an install. The last is not signed: the one-time state it carries is its
check."""

from typing import Annotated

from fastapi import APIRouter, Query, Response
from fastapi.responses import RedirectResponse

from acme.services.api.gateway.auth import Rctx
from acme.services.api.gateway.resolve import SlackService, WebhooksService
from acme.services.api.gateway.webhooks import SlackCall, StripeDelivery
from acme.services.api.types.billing import DeliveryReceivedView
from acme.services.api.types.slack import SlackEventAnswerView

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe", response_model=DeliveryReceivedView)
async def stripe_delivery(
    rctx: Rctx, webhooks: WebhooksService, delivery: StripeDelivery
) -> DeliveryReceivedView:
    return await webhooks.receive_stripe(rctx, delivery)


# An empty 200 is the acknowledgement Slack waits three seconds for; the
# answer reaches the person through the command's response_url.
@router.post("/slack/commands", status_code=200, response_class=Response)
async def slack_command(rctx: Rctx, slack: SlackService, call: SlackCall) -> None:
    return await slack.receive_command(rctx, call)


@router.post("/slack/events", response_model=SlackEventAnswerView, response_model_exclude_none=True)
async def slack_event(rctx: Rctx, slack: SlackService, call: SlackCall) -> SlackEventAnswerView:
    return await slack.receive_event(rctx, call)


@router.get("/slack/oauth", status_code=302, response_class=RedirectResponse)
async def slack_oauth(
    rctx: Rctx,
    slack: SlackService,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> str:
    return await slack.finish_install(rctx, code, state, error)
