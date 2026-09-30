"""The identity provider's webhook deliveries: the signature over the body
and its timestamp, the ids an event names, and the one check the twin and
the real client share."""

import json
import time
from datetime import timedelta
from uuid import UUID

import pytest
from workos.webhooks._verification import verify_header

from acme.integrations.exceptions import DeliveryRefused, ProviderUnavailable
from acme.integrations.identity.deliveries import sign, verified
from acme.integrations.identity.twin import TWIN_WEBHOOK_SECRET, IdentityProviderTwinImpl
from acme.integrations.identity.workos import IdentityProviderWorkOSImpl

SECRET = "whsec_test"
ORG = "org_01"
ACME_ORG = "0195f1a2-7b3c-7d4e-8f00-000000000001"


def event(event_type: str, data: dict[str, object], event_id: str = "event_1") -> bytes:
    return json.dumps(
        {"id": event_id, "event": event_type, "data": data, "created_at": "2026-09-28T10:00:00Z"}
    ).encode()


def test_sign_is_the_providers_scheme_as_its_sdk_checks_it() -> None:
    body = event("organization.updated", {"id": ORG, "external_id": ACME_ORG})
    verify_header(event_body=body, event_signature=sign(body, SECRET), secret=SECRET)


def test_a_signed_organization_event_names_the_org_it_is_about() -> None:
    body = event("organization.updated", {"id": ORG, "external_id": ACME_ORG})
    delivery = verified(body, sign(body, SECRET), SECRET)
    assert delivery.event_type == "organization.updated"
    assert delivery.organization_id == ORG
    assert delivery.organization_external_id == ACME_ORG


def test_an_event_about_a_member_names_the_organization_of_its_object() -> None:
    body = event("organization_membership.created", {"id": "om_1", "organization_id": ORG})
    delivery = verified(body, sign(body, SECRET), SECRET)
    assert (delivery.organization_id, delivery.organization_external_id) == (ORG, None)


def test_every_copy_of_an_event_has_the_same_key() -> None:
    body = event("organization.updated", {"id": ORG}, event_id="event_7")
    first = verified(body, sign(body, SECRET), SECRET)
    again = verified(body, sign(body, SECRET), SECRET)
    other = event("organization.updated", {"id": ORG}, event_id="event_8")
    assert first.key == again.key != verified(other, sign(other, SECRET), SECRET).key
    assert isinstance(first.key, UUID)


@pytest.mark.parametrize(
    "header",
    [None, "", "v1=abc", "t=abc, v1=def", "t=1, v1="],
)
def test_a_missing_or_malformed_signature_is_refused(header: str | None) -> None:
    with pytest.raises(DeliveryRefused):
        verified(event("organization.updated", {"id": ORG}), header, SECRET)


@pytest.mark.parametrize(
    "digest",
    ["\u00e9" * 64, "\uff11" * 64, "abc\udcff"],
    ids=["a byte past ASCII", "digits of another script", "a lone surrogate"],
)
def test_a_signature_outside_its_alphabet_is_a_bad_signature(digest: str) -> None:
    """A header's bytes reach the check as text, one character each, so a
    byte past ASCII is a character the compare refuses to take: it is a bad
    signature like any other, never an exception."""
    body = event("organization.updated", {"id": ORG})
    header = f"t={int(time.time() * 1000)}, v1={digest}"
    with pytest.raises(DeliveryRefused, match="did not check out"):
        verified(body, header, SECRET)


def test_a_timestamp_of_any_length_is_outside_the_window() -> None:
    body = event("organization.updated", {"id": ORG})
    for far in (int("9" * 400), -int("9" * 400)):
        with pytest.raises(DeliveryRefused, match="window"):
            verified(body, sign(body, SECRET, far), SECRET)


def test_a_body_changed_after_signing_is_refused() -> None:
    body = event("organization.updated", {"id": ORG})
    header = sign(body, SECRET)
    with pytest.raises(DeliveryRefused, match="did not check out"):
        verified(body.replace(ORG.encode(), b"org_02"), header, SECRET)


def test_another_secret_is_refused() -> None:
    body = event("organization.updated", {"id": ORG})
    with pytest.raises(DeliveryRefused, match="did not check out"):
        verified(body, sign(body, "whsec_other"), SECRET)


def test_a_delivery_signed_outside_the_window_is_refused() -> None:
    body = event("organization.updated", {"id": ORG})
    stale = int((time.time() - 600) * 1000)
    with pytest.raises(DeliveryRefused, match="window"):
        verified(body, sign(body, SECRET, stale), SECRET)


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"id": "e"}'])
def test_a_signed_body_that_is_no_event_is_refused(body: bytes) -> None:
    with pytest.raises(DeliveryRefused):
        verified(body, sign(body, SECRET), SECRET)


def test_the_twin_verifies_what_it_signs() -> None:
    twin = IdentityProviderTwinImpl()
    body, header = twin.signed_event("organization.updated", {"id": ORG, "external_id": ACME_ORG})
    assert twin.verify_delivery(body, header).organization_external_id == ACME_ORG
    with pytest.raises(DeliveryRefused):
        twin.verify_delivery(body, sign(body, "not-" + TWIN_WEBHOOK_SECRET))


def test_the_real_client_without_its_secret_refuses_every_delivery_as_unavailable() -> None:
    client = IdentityProviderWorkOSImpl(
        client_id="client_x", api_key="sk_x", timeout=timedelta(seconds=5)
    )
    body = event("organization.updated", {"id": ORG})
    with pytest.raises(ProviderUnavailable, match="ACME_WORKOS_WEBHOOK_SECRET"):
        client.verify_delivery(body, sign(body, SECRET))
    configured = IdentityProviderWorkOSImpl(
        client_id="client_x", api_key="sk_x", timeout=timedelta(seconds=5), webhook_secret=SECRET
    )
    assert configured.verify_delivery(body, sign(body, SECRET)).organization_id == ORG
