"""The hosted impls never let the driver's error type cross the boundary."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ParamValidationError,
    ReadTimeoutError,
)

from acme.infra.base import new_id
from acme.infra.buckets import BlobNotFound, Buckets
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.exceptions import (
    BackendFailed,
    BackendUnreachable,
    InfraException,
    InfraUnavailable,
)
from acme.infra.queues import Queues
from acme.infra.queues.sqs import QueueSqsImpl
from acme.infra.secrets import SecretNotFound
from acme.infra.secrets.aws import SecretsAwsImpl


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "from the driver"}}, "Op")


class FailingPaginator:
    def __init__(self, failure: BaseException) -> None:
        self._failure = failure

    async def paginate(self, **kwargs: Any) -> AsyncIterator[Any]:
        raise self._failure
        yield  # pragma: no cover


class FailingClient:
    """Every operation, paginated or not, raises the configured failure."""

    def __init__(self, failure: BaseException) -> None:
        self._failure = failure

    def get_paginator(self, name: str) -> FailingPaginator:
        return FailingPaginator(self._failure)

    def __getattr__(self, name: str) -> Any:
        async def operation(*args: Any, **kwargs: Any) -> Any:
            raise self._failure

        return operation


class FakeSession:
    def __init__(self, failure: BaseException) -> None:
        self._failure = failure

    def client(self, *args: Any, **kwargs: Any) -> Any:
        @asynccontextmanager
        async def open_client() -> AsyncIterator[Any]:
            yield FailingClient(self._failure)

        return open_client()


async def buckets(failure: BaseException | str) -> BucketsS3Impl:
    impl = BucketsS3Impl(
        FakeSession(client_error(failure) if isinstance(failure, str) else failure),  # type: ignore[arg-type]
        endpoint_url=None,
        region="us-east-1",
        bucket_prefix="acme-local",
        timeout=timedelta(seconds=1),
    )
    await impl.start()
    return impl


async def secrets(failure: BaseException | str) -> SecretsAwsImpl:
    impl = SecretsAwsImpl(
        FakeSession(client_error(failure) if isinstance(failure, str) else failure),  # type: ignore[arg-type]
        region="us-east-1",
        name_prefix="acme/",
        timeout=timedelta(seconds=1),
    )
    await impl.start()
    return impl


async def queues(failure: BaseException) -> QueueSqsImpl:
    impl = QueueSqsImpl(
        FakeSession(failure),  # type: ignore[arg-type]
        endpoint_url=None,
        region="us-east-1",
        queue_prefix="acme-",
        timeout=timedelta(seconds=1),
    )
    await impl.start()
    return impl


async def test_not_found_codes_keep_their_shape() -> None:
    with pytest.raises(BlobNotFound):
        await (await buckets("NoSuchKey")).get(new_id(), Buckets.EXPORTS, "k")
    assert await (await buckets("404")).exists(new_id(), Buckets.EXPORTS, "k") is False
    with pytest.raises(SecretNotFound):
        await (await secrets("ResourceNotFoundException")).get(new_id(), "token")
    assert await (await secrets("ResourceNotFoundException")).has(new_id(), "token") is False


async def test_every_other_code_becomes_a_platform_exception() -> None:
    with pytest.raises(BackendFailed) as raised:
        await (await buckets("AccessDenied")).get(new_id(), Buckets.EXPORTS, "k")
    assert isinstance(raised.value, InfraException)
    assert raised.value.message == "s3 get failed with AccessDenied"
    with pytest.raises(BackendFailed):
        await (await buckets("SlowDown")).put(new_id(), Buckets.EXPORTS, "k", b"", "text/plain")
    with pytest.raises(BackendFailed):
        await (await buckets("AccessDenied")).list(new_id(), Buckets.EXPORTS, "", limit=10)
    with pytest.raises(BackendFailed) as raised:
        await (await secrets("AccessDeniedException")).get(new_id(), "token")
    assert "from the driver" not in raised.value.message
    with pytest.raises(BackendFailed):
        await (await secrets("AccessDeniedException")).delete(new_id(), "token")


@pytest.mark.parametrize(
    "failure",
    [
        EndpointConnectionError(endpoint_url="https://sqs.example.test"),
        ConnectTimeoutError(endpoint_url="https://sqs.example.test"),
        ReadTimeoutError(endpoint_url="https://sqs.example.test"),
    ],
    ids=lambda failure: type(failure).__name__,
)
async def test_a_backend_that_cannot_be_reached_is_unreachable(failure: BotoCoreError) -> None:
    with pytest.raises(BackendUnreachable) as raised:
        await (await queues(failure)).send(Queues.WEBHOOKS, b"m")
    assert isinstance(raised.value, InfraUnavailable)
    assert isinstance(raised.value, InfraException)
    # One code for "not right now": the wire carries the shape's, and the leaf
    # names what happened in the message and the traceback.
    assert raised.value.http_status == 503
    assert raised.value.code == "unavailable"
    assert raised.value.message == f"sqs send could not reach the backend: {type(failure).__name__}"
    with pytest.raises(BackendUnreachable):
        await (await buckets(failure)).get(new_id(), Buckets.EXPORTS, "k")
    with pytest.raises(BackendUnreachable):
        await (await secrets(failure)).get(new_id(), "token")


async def test_any_other_driver_error_is_a_backend_failure() -> None:
    failure = ParamValidationError(report="Body is not bytes")
    with pytest.raises(BackendFailed) as raised:
        await (await buckets(failure)).put(new_id(), Buckets.EXPORTS, "k", b"", "text/plain")
    assert raised.value.message == "s3 put failed with ParamValidationError"
    assert "Body is not bytes" not in raised.value.message
