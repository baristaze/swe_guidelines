"""A call in from Slack: its signature, what it carries, and its key.

Slack signs `v0:<timestamp>:<raw body>` with HMAC-SHA256 under the app's
signing secret and sends `v0=<hex>` in `X-Slack-Signature`, with the
timestamp in `X-Slack-Request-Timestamp`. The check is the SDK's own, over
the raw bytes, compared in constant time, and refuses a timestamp more than
five minutes from now. The twin signs with `sign`, written here from Slack's
documented scheme and never from the SDK, so a test that passes proves the
two agree.

A verified call becomes an `SlackInbound`: what the API puts on the `slack`
queue and the worker takes off it. Its key is the producer's idempotency key,
a UUID v5 over the provider's name and the delivery's own id: an event's
`event_id`, which Slack keeps across its retries, and a command's
`trigger_id`, which names one invocation."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime
from typing import Any, Literal
from urllib.parse import parse_qsl

from pydantic import BaseModel, ConfigDict
from slack_sdk.signature import SignatureVerifier

from acme.integrations.slack import SlackRequestRefused

SIGNATURE_HEADER = "X-Slack-Signature"
TIMESTAMP_HEADER = "X-Slack-Request-Timestamp"
RETRY_HEADER = "X-Slack-Retry-Num"
TOLERANCE_SECONDS = 300
"""The SDK's window, named so a reader finds it: five minutes either way."""

SLACK_NAMESPACE = uuid.UUID("5a1ac000-7ada-5000-8000-000000000000")
"""The UUID v5 namespace a Slack delivery's key is derived in."""


def sign(body: bytes, secret: str, timestamp: int | None = None) -> tuple[str, str]:
    """The timestamp and the signature header Slack would send with `body`."""
    at = str(int(time.time()) if timestamp is None else timestamp)
    base = b"v0:" + at.encode() + b":" + body
    return at, "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify(body: bytes, timestamp: str | None, signature: str | None, secret: str) -> None:
    """`SlackRequestRefused` unless the SDK's check passes. The reason names
    what failed and never the secret or the header."""
    if not timestamp or not signature:
        raise SlackRequestRefused("unsigned", "the request carries no Slack signature")
    try:
        int(timestamp)
    except ValueError:
        raise SlackRequestRefused(
            "bad_timestamp", "the request timestamp is not a number"
        ) from None
    if not SignatureVerifier(secret).is_valid(body, timestamp, signature):
        raise SlackRequestRefused(
            "bad_signature", "the signature or its timestamp did not check out"
        )


class SlackInbound(BaseModel):
    """One verified call from Slack, as the API queued it: its key, when it
    arrived, whether it is a slash command or an event, Slack's own payload,
    and which retry of Slack's it was."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    key: uuid.UUID
    received_at: datetime
    kind: Literal["command", "event"]
    payload: dict[str, Any]
    retry_num: int = 0


def delivery_key(delivery_id: str) -> uuid.UUID:
    return uuid.uuid5(SLACK_NAMESPACE, f"slack:{delivery_id}")


def command_of(body: bytes) -> dict[str, str]:
    """A slash command's form body as its fields. `SlackRequestRefused` when
    it is not one: no command, or no `trigger_id` to key it on."""
    try:
        fields = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
    except UnicodeDecodeError:
        raise SlackRequestRefused("bad_body", "the body is not UTF-8") from None
    if not fields.get("command") or not fields.get("trigger_id") or not fields.get("team_id"):
        raise SlackRequestRefused("bad_body", "the body is not a slash command")
    return fields


def event_of(body: bytes) -> dict[str, Any]:
    """An Events API body as JSON. `SlackRequestRefused` when it is not one."""
    try:
        payload = json.loads(body)
    except UnicodeDecodeError, json.JSONDecodeError:
        raise SlackRequestRefused("bad_body", "the body is not JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        raise SlackRequestRefused("bad_body", "the body is not an event")
    return payload


def inbound_command(fields: dict[str, str], received_at: datetime, retry_num: int) -> SlackInbound:
    return SlackInbound(
        key=delivery_key(fields["trigger_id"]),
        received_at=received_at,
        kind="command",
        payload=dict(fields),
        retry_num=retry_num,
    )


def inbound_event(payload: dict[str, Any], received_at: datetime, retry_num: int) -> SlackInbound:
    """An `event_callback`, keyed on its `event_id`. `SlackRequestRefused`
    when it has none."""
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise SlackRequestRefused("bad_body", "the event carries no event_id")
    return SlackInbound(
        key=delivery_key(event_id),
        received_at=received_at,
        kind="event",
        payload=payload,
        retry_num=retry_num,
    )
