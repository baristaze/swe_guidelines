"""An inbound delivery: its signature, and the ids it names.

The processor signs `<timestamp>.<body>` with HMAC-SHA256 under the
endpoint's secret and sends `t=<timestamp>,v1=<signature>` in the
`Stripe-Signature` header. The check is the SDK's own, with its default
replay window of five minutes, so the twin's deliveries and the real ones
pass the same code. The twin signs with `sign`, written here from the
processor's documented scheme and never from the SDK, so a test that
passes proves the two agree."""

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import stripe

from acme.integrations.exceptions import DeliveryRefused
from acme.integrations.payments.types import ORG_METADATA_KEY, ProviderDelivery

SIGNATURE_HEADER = "Stripe-Signature"
TOLERANCE_SECONDS = 300
"""The SDK's default replay window, named so a reader finds it."""


def sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """The header the processor would send with `payload`."""
    at = int(time.time()) if timestamp is None else timestamp
    signed = f"{at}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={at},v1={digest}"


def verified(payload: bytes, signature: str | None, secret: str) -> ProviderDelivery:
    """The delivery, once the SDK's check passes; `DeliveryRefused` otherwise.
    The reason names what failed and never the secret or the header."""
    if not signature:
        raise DeliveryRefused(f"no {SIGNATURE_HEADER} header")
    try:
        stripe.WebhookSignature.verify_header(
            payload.decode("utf-8"), signature, secret, TOLERANCE_SECONDS
        )
    except stripe.SignatureVerificationError:
        raise DeliveryRefused("the signature or its timestamp did not check out") from None
    except UnicodeDecodeError:
        raise DeliveryRefused("the body is not UTF-8") from None
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        raise DeliveryRefused("the body is not JSON") from None
    if not isinstance(event, dict):
        raise DeliveryRefused("the body is not an event")
    return delivery_of(event)


def delivery_of(event: Mapping[str, Any]) -> ProviderDelivery:
    """The ids an event names, read from the shape of the object it is about:
    a checkout session, a subscription, or an invoice. An event of any other
    type keeps its id and type and names nothing else."""
    try:
        event_id = str(event["id"])
        event_type = str(event["type"])
        created = datetime.fromtimestamp(int(event["created"]), UTC)
    except KeyError, TypeError, ValueError:
        raise DeliveryRefused("the body is not an event") from None
    data = event.get("data")
    obj: Mapping[str, Any] = {}
    if isinstance(data, Mapping):
        found = data.get("object")
        if isinstance(found, Mapping):
            obj = found
    customer, subscription, hint = _ids(event_type, obj)
    return ProviderDelivery(
        event_id=event_id,
        event_type=event_type,
        created=created,
        livemode=bool(event.get("livemode", False)),
        customer_id=customer,
        subscription_id=subscription,
        org_hint=hint,
    )


def _ids(event_type: str, obj: Mapping[str, Any]) -> tuple[str | None, str | None, UUID | None]:
    if event_type.startswith("checkout.session."):
        hint = _uuid(obj.get("client_reference_id")) or _org(obj.get("metadata"))
        return _id(obj.get("customer")), _id(obj.get("subscription")), hint
    if event_type.startswith("customer.subscription."):
        return _id(obj.get("customer")), _id(obj.get("id")), _org(obj.get("metadata"))
    if event_type.startswith("invoice."):
        # Since the 2025 API versions an invoice names its subscription
        # under `parent.subscription_details`; older ones at the top.
        details: Any = {}
        parent = obj.get("parent")
        if isinstance(parent, Mapping):
            details = parent.get("subscription_details") or {}
        subscription = _id(details.get("subscription")) if isinstance(details, Mapping) else None
        metadata = details.get("metadata") if isinstance(details, Mapping) else None
        return (
            _id(obj.get("customer")),
            subscription or _id(obj.get("subscription")),
            _org(metadata),
        )
    return None, None, None


def _id(value: Any) -> str | None:
    """An id, whether the object named it or was expanded in its place."""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        found = value.get("id")
        return found if isinstance(found, str) and found else None
    return None


def _org(metadata: Any) -> UUID | None:
    if isinstance(metadata, Mapping):
        return _uuid(metadata.get(ORG_METADATA_KEY))
    return None


def _uuid(value: Any) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
