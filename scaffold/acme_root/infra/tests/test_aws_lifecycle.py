"""Each hosted impl opens its client once, at start(), holds it for every
call, and closes it at close(); nothing opens a client per call, and a call
before start() is a defect, not a silent connection."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest

from acme.infra.base import new_id
from acme.infra.buckets import Buckets
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.queues import Queues
from acme.infra.queues.sqs import QueueSqsImpl
from acme.infra.secrets.aws import SecretsAwsImpl


class IdleClient:
    """Answers every operation with an empty response."""

    def __getattr__(self, name: str) -> Any:
        async def operation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"QueueUrl": "http://queue", "MessageId": "m-1", "SecretString": "s"}

        return operation


class CountingSession:
    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0

    def client(self, service: str, **kwargs: Any) -> Any:
        @asynccontextmanager
        async def open_client() -> AsyncIterator[Any]:
            self.opened += 1
            try:
                yield IdleClient()
            finally:
                self.closed += 1

        return open_client()


def impls(session: CountingSession) -> list[Any]:
    timeout = timedelta(seconds=1)
    return [
        QueueSqsImpl(session, endpoint_url=None, region="r", queue_prefix="t-", timeout=timeout),  # type: ignore[arg-type]
        BucketsS3Impl(session, endpoint_url=None, region="r", bucket_prefix="t", timeout=timeout),  # type: ignore[arg-type]
        SecretsAwsImpl(session, region="r", name_prefix="t/", timeout=timeout),  # type: ignore[arg-type]
    ]


async def exercise(queues: QueueSqsImpl, buckets: BucketsS3Impl, secrets: SecretsAwsImpl) -> None:
    await queues.send(Queues.WEBHOOKS, b"m")
    await queues.delete(Queues.WEBHOOKS, "receipt")
    await buckets.put(new_id(), Buckets.EXPORTS, "k", b"", "text/plain")
    await buckets.exists(new_id(), Buckets.EXPORTS, "k")
    await secrets.get(new_id(), "token")
    await secrets.delete(new_id(), "token")


async def test_one_client_per_impl_for_any_number_of_calls() -> None:
    session = CountingSession()
    queues, buckets, secrets = impls(session)
    for impl in (queues, buckets, secrets):
        await impl.start()
    assert session.opened == 3
    await exercise(queues, buckets, secrets)
    await exercise(queues, buckets, secrets)
    assert (session.opened, session.closed) == (3, 0)
    for impl in (queues, buckets, secrets):
        await impl.close()
    assert (session.opened, session.closed) == (3, 3)


async def test_a_call_before_start_is_refused() -> None:
    queues, _, _ = impls(CountingSession())
    with pytest.raises(RuntimeError, match="before start"):
        await queues.send(Queues.WEBHOOKS, b"m")
