"""An inbound delivery from the identity provider: its signature, and the ids
it names.

The provider signs `<timestamp>.<body>` with HMAC-SHA256 under the
endpoint's secret, the timestamp in milliseconds, and sends
`t=<timestamp>, v1=<signature>` in the `WorkOS-Signature` header. The check
here holds the documented scheme and a replay window of three minutes, the
SDK's own. The twin signs with `sign`, so its deliveries and the real ones
pass the same check, and a test holds `sign` to the SDK's verifier."""

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from acme.integrations.exceptions import DeliveryRefused
from acme.integrations.identity import ProvidedDelivery

SIGNATURE_HEADER = "WorkOS-Signature"
TOLERANCE_SECONDS = 180
"""The SDK's default replay window, named so a reader finds it."""

DELIVERY_NAMESPACE = uuid.UUID("5d1b0c52-4bb3-4d6f-9d7c-3a3f3c1e2b61")
"""The namespace of a delivery's key: the same event id is the same key."""


def sign(payload: bytes, secret: str, timestamp_ms: int | None = None) -> str:
    """The header the provider would send with `payload`."""
    at = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    signed = f"{at}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={at}, v1={digest}"


def verified(payload: bytes, signature: str | None, secret: str) -> ProvidedDelivery:
    """The delivery, once the check passes; `DeliveryRefused` otherwise. The
    reason names what failed and never the secret or the header."""
    if not signature:
        raise DeliveryRefused(f"no {SIGNATURE_HEADER} header")
    try:
        stamp, digest = (part.strip() for part in signature.split(","))
        if not stamp.startswith("t=") or not digest.startswith("v1="):
            raise ValueError
        at = int(stamp[2:])
    except ValueError:
        raise DeliveryRefused("the signature header is not t=..., v1=...") from None
    if abs(time.time() - at / 1000) > TOLERANCE_SECONDS:
        raise DeliveryRefused("the signature's timestamp is outside the window")
    expected = hmac.new(secret.encode(), f"{at}.".encode() + payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest[3:], expected):
        raise DeliveryRefused("the signature did not check out")
    try:
        event = json.loads(payload)
    except json.JSONDecodeError, UnicodeDecodeError:
        raise DeliveryRefused("the body is not JSON") from None
    if not isinstance(event, Mapping):
        raise DeliveryRefused("the body is not an event")
    return delivery_of(event)


def delivery_of(event: Mapping[str, Any]) -> ProvidedDelivery:
    """The ids an event names: its own, and the organization it is about,
    read from the organization object itself or from the `organization_id`
    of the object it is about."""
    try:
        event_id = str(event["id"])
        event_type = str(event["event"])
        created = datetime.fromisoformat(str(event["created_at"]))
    except KeyError, TypeError, ValueError:
        raise DeliveryRefused("the body is not an event") from None
    data = event.get("data")
    obj: Mapping[str, Any] = data if isinstance(data, Mapping) else {}
    organization_id: str | None = None
    external_id: str | None = None
    if event_type.startswith("organization.") and "organization_id" not in obj:
        organization_id = _text(obj.get("id"))
        external_id = _text(obj.get("external_id"))
    else:
        organization_id = _text(obj.get("organization_id"))
    return ProvidedDelivery(
        key=uuid.uuid5(DELIVERY_NAMESPACE, event_id),
        event_id=event_id,
        event_type=event_type,
        created=created if created.tzinfo else created.replace(tzinfo=UTC),
        organization_id=organization_id,
        organization_external_id=external_id,
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
