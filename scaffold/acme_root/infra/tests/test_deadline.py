"""A request's deadline, as every client keeps to it: a call with none waits
for what it waits for; a call that starts with no time left does not start;
a call still waiting at the deadline is cut there; a wait the call can tell
does not fit ends it at once; and each ends in the refusal the caller hands
in, naming why. What the call raises on its own before the deadline stays
its own. Then each AWS call a request makes keeps to it, on the clients the
process opens, whose timeout and retries would have it wait far longer."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from acme.infra.base import utcnow
from acme.infra.buckets import Buckets
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.deadline import PASSED, DeadlineReached, bounded, seconds_left
from acme.infra.exceptions import BackendUnreachable
from acme.infra.queues import Queues
from acme.infra.queues.sqs import QueueSqsImpl
from acme.infra.secrets.aws import SecretsAwsImpl


class Refused(Exception):
    """The refusal a test hands in, so the test reads which one came back."""


def after(seconds: float) -> datetime:
    return utcnow() + timedelta(seconds=seconds)


async def test_a_call_with_no_deadline_waits_for_what_it_waits_for() -> None:
    assert seconds_left(None) is None
    async with bounded(None, Refused):
        await asyncio.sleep(0.05)


async def test_a_call_with_no_time_left_does_not_start() -> None:
    started = False
    with pytest.raises(Refused) as refused:
        async with bounded(utcnow() - timedelta(seconds=1), Refused):
            started = True
    assert not started
    assert str(refused.value) == PASSED
    assert seconds_left(utcnow() - timedelta(seconds=1)) == 0.0


async def test_a_call_still_waiting_at_the_deadline_is_cut_there() -> None:
    began = time.monotonic()
    with pytest.raises(Refused) as refused:
        async with bounded(after(0.2), Refused):
            await asyncio.sleep(10)
    waited = time.monotonic() - began
    assert str(refused.value) == PASSED
    assert 0.15 <= waited < 1.0, waited


async def test_a_wait_the_call_can_tell_does_not_fit_ends_it_at_once() -> None:
    began = time.monotonic()
    with pytest.raises(Refused) as refused:
        async with bounded(after(10), Refused):
            raise DeadlineReached("asked to be called again in 30 s")
    assert str(refused.value) == "asked to be called again in 30 s"
    assert time.monotonic() - began < 0.5


async def test_what_the_call_raises_before_the_deadline_stays_its_own() -> None:
    with pytest.raises(TimeoutError, match="the client's own"):
        async with bounded(after(10), Refused):
            raise TimeoutError("the client's own")
    with pytest.raises(KeyError):
        async with bounded(after(10), Refused):
            raise KeyError("missing")


async def test_a_cut_a_library_turns_into_its_own_error_is_still_the_deadline() -> None:
    """A library that catches the cancellation and raises its own error still
    ended at the deadline, and the caller reads the refusal."""

    async def swallowing() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise RuntimeError("the library's own") from None

    with pytest.raises(Refused):
        async with bounded(after(0.1), Refused):
            await swallowing()


async def test_calls_one_after_another_share_what_is_left() -> None:
    """Each call takes two tenths, inside the three the deadline gives; two
    do not fit. So the deadline ends one of them, where a budget per call
    would end none: the second on a quick runner, the first on one slow
    enough to take a tenth before it. The test holds whichever it is."""
    deadline = after(0.3)
    answered = 0
    with pytest.raises(Refused) as refused:
        for _ in range(3):
            async with bounded(deadline, Refused):
                await asyncio.sleep(0.2)
            answered += 1
    assert str(refused.value) == PASSED
    assert answered <= 1


# Each AWS call a request makes, cut at its deadline.


class HangingClient:
    """An AWS client whose every call waits for an answer that never comes."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        async def hang(**kwargs: Any) -> Any:
            self.calls.append(name)
            if name == "get_queue_url":
                return {"QueueUrl": "http://queue"}
            await asyncio.sleep(3600)

        return hang


class HangingSession:
    def __init__(self, client: HangingClient) -> None:
        self._client = client

    def client(self, service: str, **kwargs: Any) -> Any:
        @asynccontextmanager
        async def open_client() -> AsyncIterator[Any]:
            yield self._client

        return open_client()


ORG = uuid4()

CALLS: dict[str, Callable[[Any, datetime], Awaitable[Any]]] = {
    "sqs send": lambda q, d: q.send(Queues.WEBHOOKS, b"{}", deadline=d),
    "secretsmanager get": lambda s, d: s.get(ORG, "provider", deadline=d),
    "secretsmanager put": lambda s, d: s.put(ORG, "provider", "{}", deadline=d),
    "secretsmanager delete": lambda s, d: s.delete(ORG, "provider", deadline=d),
    "s3 put": lambda b, d: b.put(
        ORG, Buckets.USER_FILE_UPLOADS, "k", b"x", "text/plain", deadline=d
    ),
    "s3 get": lambda b, d: b.get(ORG, Buckets.USER_FILE_UPLOADS, "k", deadline=d),
    "s3 exists": lambda b, d: b.exists(ORG, Buckets.USER_FILE_UPLOADS, "k", deadline=d),
}


def impl_for(call: str, session: Any, timeout: timedelta) -> Any:
    backend = call.split()[0]
    if backend == "sqs":
        return QueueSqsImpl(
            session, endpoint_url=None, region="us-east-1", queue_prefix="t-", timeout=timeout
        )
    if backend == "secretsmanager":
        return SecretsAwsImpl(session, region="us-east-1", name_prefix="t/", timeout=timeout)
    return BucketsS3Impl(
        session, endpoint_url=None, region="us-east-1", bucket_prefix="t", timeout=timeout
    )


@pytest.mark.parametrize("call", sorted(CALLS))
async def test_every_aws_call_a_request_makes_ends_at_its_deadline(call: str) -> None:
    """The client's own timeout is ten seconds, and botocore tries a timeout
    four times more; the request has a fraction of a second left, and the
    call ends then, unreachable, naming the backend and the operation."""
    impl = impl_for(call, HangingSession(HangingClient()), timedelta(seconds=10))
    await impl.start()
    began = time.monotonic()
    try:
        with pytest.raises(BackendUnreachable) as raised:
            await asyncio.wait_for(CALLS[call](impl, after(0.2)), 5)
    finally:
        await impl.close()
    waited = time.monotonic() - began
    assert raised.value.message == f"{call} could not reach the backend: {PASSED}"
    assert 0.15 <= waited < 1.0, waited
