"""What an inbound delivery carries into a route: its body, byte for byte,
and the provider's signature headers. The gateway reads them, and the
service checks the one against the other before anything is queued."""

from typing import Annotated

from fastapi import Depends, Header, Request

from acme.services.api.services.billing import SignedDelivery
from acme.services.api.services.slack import SlackRequest


async def signed_delivery(
    request: Request,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> SignedDelivery:
    return SignedDelivery(payload=await request.body(), signature=stripe_signature)


async def slack_request(
    request: Request,
    timestamp: Annotated[str | None, Header(alias="X-Slack-Request-Timestamp")] = None,
    signature: Annotated[str | None, Header(alias="X-Slack-Signature")] = None,
    retry_num: Annotated[str | None, Header(alias="X-Slack-Retry-Num")] = None,
) -> SlackRequest:
    retry = int(retry_num) if retry_num and retry_num.isdigit() else 0
    return SlackRequest(
        payload=await request.body(), timestamp=timestamp, signature=signature, retry_num=retry
    )


StripeDelivery = Annotated[SignedDelivery, Depends(signed_delivery)]
SlackCall = Annotated[SlackRequest, Depends(slack_request)]
