"""The identity provider's webhook over the in-process app: the route takes
the body byte for byte and the signature header, checks one against the
other before anything is queued, and queues the delivery for the worker. It
takes no credential and no rate limit; the signature is its check."""

import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid5

import httpx
import pytest
from api_support import SMALL_BUDGET, build_container, client_over

from acme.infra.queues import Queues
from acme.integrations.identity.deliveries import DELIVERY_NAMESPACE, SIGNATURE_HEADER, sign
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.identity.workos import IdentityProviderWorkOSImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.services.api.container import AppContainer

ROUTE = "/webhooks/identity"
ORG = UUID("01a0eba7-0000-7000-8000-000000000001")


@pytest.fixture
def twin() -> IdentityProviderTwinImpl:
    return IdentityProviderTwinImpl()


@pytest.fixture
def container(tmp_path: Path, twin: IdentityProviderTwinImpl) -> AppContainer:
    return build_container(tmp_path, integrations=IntegrationsOverImpl(twin))


async def queued(container: AppContainer) -> list[bytes]:
    """Every body on the webhooks queue, taken off it."""
    queues = container.infra.get_queues()
    messages = await queues.receive(Queues.WEBHOOKS, 10, timedelta(0), timedelta(seconds=30))
    for message in messages:
        await queues.delete(Queues.WEBHOOKS, message.receipt)
    return [message.body for message in messages]


async def test_a_signed_delivery_is_queued_once_with_its_event(
    client: httpx.AsyncClient, container: AppContainer, twin: IdentityProviderTwinImpl
) -> None:
    body, signature = twin.signed_event(
        "organization.updated",
        {"object": "organization", "id": "org_01", "external_id": str(ORG)},
        event_id="event_01",
    )
    answered = await client.post(ROUTE, content=body, headers={SIGNATURE_HEADER: signature})
    assert answered.status_code == 200, answered.text
    assert answered.json() == {"received": True}

    [sent] = await queued(container)
    message = json.loads(sent.decode("utf-8"))
    key = str(uuid5(DELIVERY_NAMESPACE, "event_01"))
    assert message == {
        "idempotency_key": key,
        "provider": "identity",
        "delivery": twin.verify_delivery(body, signature).model_dump(mode="json"),
    }
    delivery = message["delivery"]
    assert (delivery["key"], delivery["event_id"], delivery["event_type"]) == (
        key,
        "event_01",
        "organization.updated",
    )
    assert (delivery["organization_id"], delivery["organization_external_id"]) == (
        "org_01",
        str(ORG),
    )


async def test_a_bad_or_missing_signature_is_refused_and_queues_nothing(
    client: httpx.AsyncClient, container: AppContainer, twin: IdentityProviderTwinImpl
) -> None:
    """No header, a header signed with another secret, a header no signer
    writes (a byte past ASCII in the signature, a timestamp no clock reads),
    and the right header over a body that is not byte for byte the one
    signed: each is 400 and nothing reaches the queue."""
    body, signature = twin.signed_event("user.created", {"id": "user_01"})
    reserialized = json.dumps(json.loads(body), indent=1).encode()
    stamp = signature.partition(",")[0].encode()
    cases: tuple[tuple[bytes, dict[str, str] | dict[bytes, bytes]], ...] = (
        (body, {}),
        (body, {SIGNATURE_HEADER: sign(body, "whsec_not_ours")}),
        (body, {SIGNATURE_HEADER: "not a signature"}),
        (body, {SIGNATURE_HEADER.encode(): stamp + b", v1=" + b"\xe9" * 64}),
        (body, {SIGNATURE_HEADER: sign(body, "whsec_not_ours", int("9" * 400))}),
        (reserialized, {SIGNATURE_HEADER: signature}),
    )
    for content, headers in cases:
        refused = await client.post(ROUTE, content=content, headers=headers)
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["code"] == "webhook_signature_invalid"
    depth = await container.infra.get_queues().depth(Queues.WEBHOOKS)
    assert (depth.visible, depth.in_flight) == (0, 0)


async def test_the_route_takes_no_credential_and_no_rate_limit(
    tmp_path: Path, twin: IdentityProviderTwinImpl
) -> None:
    """Past every budget the gateway keeps, from one address with no bearer,
    every signed delivery is queued."""
    container = build_container(
        tmp_path,
        integrations=IntegrationsOverImpl(twin),
        login_rate_limit=SMALL_BUDGET,
        credential_rate_limit_writes=SMALL_BUDGET,
        failed_authentication_limit=SMALL_BUDGET,
    )
    async with client_over(container) as client:
        for index in range(SMALL_BUDGET * 2):
            body, signature = twin.signed_event("user.updated", {"id": f"user_{index}"})
            answered = await client.post(ROUTE, content=body, headers={SIGNATURE_HEADER: signature})
            assert answered.status_code == 200, (index, answered.text)
        assert len(await queued(container)) == SMALL_BUDGET * 2


async def workos_answers_invalid_grant(request: httpx.Request) -> httpx.Response:
    """WorkOS as the start's credential check meets it for the application's
    own key; the webhook makes no call."""
    return httpx.Response(400, json={"error": "invalid_grant"})


@pytest.mark.parametrize("provider", ["none", "workos without a secret"])
async def test_a_process_with_no_secret_answers_503(tmp_path: Path, provider: str) -> None:
    """A process that holds no signing secret cannot check a delivery, so it
    refuses every one as unavailable, the answer the provider retries."""
    if provider == "none":
        container = build_container(tmp_path)
    else:
        workos = IdentityProviderWorkOSImpl(
            client_id="client_test",
            api_key="sk_test_not_a_key",
            timeout=timedelta(seconds=10),
            transport=httpx.MockTransport(workos_answers_invalid_grant),
            webhook_secret=None,
        )
        container = build_container(tmp_path, integrations=IntegrationsOverImpl(workos))
    body, signature = IdentityProviderTwinImpl().signed_event("user.created", {"id": "user_01"})
    async with client_over(container) as client:
        refused = await client.post(ROUTE, content=body, headers={SIGNATURE_HEADER: signature})
    assert refused.status_code == 503, refused.text
    assert refused.json()["error"]["code"] == "unavailable"
    depth = await container.infra.get_queues().depth(Queues.WEBHOOKS)
    assert (depth.visible, depth.in_flight) == (0, 0)
