"""The outage signal across processes, over the compose stack's Valkey: this
test's process and a second one, each with the configured infra root a
process builds at boot."""

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from acme.infra.base import new_id, utcnow
from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.settings import InfraSettings
from acme.infra.outages import Outage

pytestmark = pytest.mark.integration

PROVIDER = "identity"
CREDENTIAL = "identity_api_key"

CALLER = """
import asyncio, json, sys
from uuid import UUID

from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.settings import InfraSettings


async def main(url: str, org: str, provider: str, credential: str) -> None:
    settings = InfraSettings.model_validate(
        {"environment": "local", "cache_backend": "valkey", "valkey_url": url}
    )
    infra = InfraConfiguredImpl(settings)
    await infra.start()
    try:
        outages = infra.get_outages()
        marked = await outages.current(UUID(org), provider, credential)
        if marked is not None:
            print(json.dumps({"called": False, "retry_at": marked.retry_at.isoformat()}))
            return
        # The call goes out here and succeeds, and its success clears the pair.
        await outages.clear(UUID(org), provider, credential)
        print(json.dumps({"called": True}))
    finally:
        await infra.close()


asyncio.run(main(*sys.argv[1:]))
"""
"""A second process that calls the provider: it reads the signal before its
call and skips the call while the pair is marked."""


def settings() -> InfraSettings:
    return InfraSettings.model_validate(
        {
            "environment": "local",
            "cache_backend": "valkey",
            "valkey_url": InfraSettings().valkey_url,
        }
    )


@pytest.fixture
async def infra() -> AsyncIterator[InfraConfiguredImpl]:
    root = InfraConfiguredImpl(settings())
    await root.start()
    try:
        yield root
    finally:
        await root.close()


async def other_process(org: UUID) -> dict[str, Any]:
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CALLER,
        settings().valkey_url,
        str(org),
        PROVIDER,
        CREDENTIAL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(child.communicate(), timeout=30)
    assert child.returncode == 0, err.decode()
    answer: dict[str, Any] = json.loads(out.decode().strip().splitlines()[-1])
    return answer


async def test_one_process_marks_and_another_skips_its_call_until_a_success_clears(
    infra: InfraConfiguredImpl,
) -> None:
    outages = infra.get_outages()
    assert outages.describe().startswith("outages=shared(cache[outage]=valkey")
    org = new_id()  # an org's own credential, so no other run shares the pair
    outage = Outage(
        org_id=org,
        provider=PROVIDER,
        credential=CREDENTIAL,
        retry_at=utcnow() + timedelta(minutes=5),
    )

    # This process's calls failed together: it marks the pair.
    await outages.mark(outage)

    # The other process reads the mark before its call, and does not call.
    skipped = await other_process(org)
    assert skipped == {"called": False, "retry_at": outage.retry_at.isoformat()}

    # Skipping changes nothing: the mark holds until a call succeeds.
    assert await outages.current(org, PROVIDER, CREDENTIAL) == outage

    # A call that succeeds clears it, and the other process calls again.
    await outages.clear(org, PROVIDER, CREDENTIAL)
    called = await other_process(org)
    assert called == {"called": True}
    assert await outages.current(org, PROVIDER, CREDENTIAL) is None
