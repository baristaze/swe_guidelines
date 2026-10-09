"""The holder's side of a lease: the fence on the resource's side, the
lease's clock on the holder's monotonic clock, and the ask that always
carries its idempotency key."""

import json
from uuid import UUID, uuid4

import httpx
import pytest

from acme.client.client import IDEMPOTENCY_HEADER, ApiClient
from acme.client.leases import MIN_RENEW_SECONDS, Fence, LeaseClock

DOCK = uuid4()


class Resets:
    """A stop-and-reset that counts its runs, and raises while told to."""

    def __init__(self) -> None:
        self.runs = 0
        self.failing = False

    async def __call__(self) -> None:
        self.runs += 1
        if self.failing:
            raise RuntimeError("the resource did not stop")


async def test_the_fence_refuses_a_lower_token_admits_an_equal_and_resets_for_a_higher() -> None:
    fence, reset = Fence({DOCK: 5}), Resets()
    assert await fence.admit(DOCK, 4, reset) is False
    assert await fence.admit(DOCK, 5, reset) is True
    assert reset.runs == 0, "the current holder resets nothing"
    assert await fence.admit(DOCK, 6, reset) is True
    assert reset.runs == 1 and fence.highest(DOCK) == 6
    # The holder before it is stale now.
    assert await fence.admit(DOCK, 5, reset) is False


async def test_a_higher_token_is_not_taken_when_its_reset_raises() -> None:
    fence, reset = Fence({DOCK: 5}), Resets()
    reset.failing = True
    with pytest.raises(RuntimeError):
        await fence.admit(DOCK, 6, reset)
    assert fence.highest(DOCK) == 5
    # The next admit runs the reset again, and takes the token once it ran.
    reset.failing = False
    assert await fence.admit(DOCK, 6, reset) is True
    assert reset.runs == 2 and fence.snapshot() == {DOCK: 6}


async def test_a_fresh_fence_admits_the_first_holder_after_a_reset() -> None:
    fence, reset = Fence(), Resets()
    assert await fence.admit(DOCK, 1, reset) is True
    assert reset.runs == 1


def test_the_clock_counts_from_the_send_and_renews_at_half() -> None:
    clock = LeaseClock.granted(asked=100.0, expires_in_seconds=60.0)
    assert (clock.deadline, clock.renew_at) == (160.0, 130.0)
    assert not clock.due(129.0) and clock.due(130.0)
    clock.renewed(asked=131.0, expires_in_seconds=60.0)
    assert (clock.deadline, clock.renew_at) == (191.0, 161.0)
    assert not clock.lost(190.9) and clock.lost(191.0)


def test_an_unanswered_renewal_keeps_the_deadline_and_a_refused_one_loses_the_lease() -> None:
    clock = LeaseClock.granted(asked=0.0, expires_in_seconds=60.0)
    clock.unanswered(now=30.0)
    assert (clock.deadline, clock.renew_at) == (60.0, 45.0)
    clock.unanswered(now=59.5)
    assert clock.renew_at == 59.5 + MIN_RENEW_SECONDS
    assert clock.lost(60.0) and not clock.due(60.5)
    other = LeaseClock.granted(asked=0.0, expires_in_seconds=60.0)
    other.refuse()
    assert other.lost(1.0)


async def test_an_ask_carries_its_key_and_reads_the_standing() -> None:
    seen: list[httpx.Request] = []
    request_id = uuid4()

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={
                "request": {
                    "id": str(request_id), "kind": "noop", "resource_id": str(DOCK),
                    "labels": None, "waiter_kind": None, "waiter_id": None,
                    "term_seconds": 60, "wait_until": None, "rank": 1.0,
                    "status": "waiting", "end_reason": None, "lease_id": None,
                    "created_at": "2026-10-08T00:00:00Z", "created_by": str(uuid4()),
                },
                "lease": None, "place": 1, "estimate_seconds": 42.0,
            },
        )  # fmt: skip

    async with ApiClient(
        "http://test", app="cli", app_version="cli@test", transport=httpx.MockTransport(answer)
    ) as api:
        standing = await api.ask_lease(resource_id=DOCK)
    assert standing.place == 1 and standing.request.id == request_id
    sent = seen[0]
    assert UUID(sent.headers[IDEMPOTENCY_HEADER])
    assert json.loads(sent.content)["resource_id"] == str(DOCK)
