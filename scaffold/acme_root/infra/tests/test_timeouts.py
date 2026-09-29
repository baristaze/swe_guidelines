"""Every outbound client is built with a timeout from settings, and no call
goes out without one. The scan reads every source root the way a reviewer
does: each construction of an HTTP, socket, AWS, Valkey, or exporter client
names a timeout (or the AWS configuration that carries one) at the call
site. The cases below then build each client and read the timeout back."""

import ast
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import aioboto3
import pytest

from acme.infra.aws_clients import client_config
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.observability import span_exporter
from acme.infra.queues import Queues
from acme.infra.queues.sqs import QueueSqsImpl
from acme.infra.secrets.aws import SecretsAwsImpl

SOURCE_ROOTS = (
    "clients/python/src",
    "apps/cli/src",
    "services/api/src",
    "infra/src",
    "integrations/src",
    "ops/src",
    "workers/maintenance/src",
)

CLIENT_CONSTRUCTORS = {
    "AsyncClient",  # httpx
    "Client",  # httpx
    "connect",  # websockets
    "OTLPSpanExporter",
    "GlideClientConfiguration",
    "AsyncWorkOSClient",  # names a timeout on every request, over its HTTP client's
}
"""A call by one of these names is a client being built; `session.client(...)`
(an AWS client) is matched by its receiver below."""


def repository_root() -> Path:
    """The checkout this test runs in: the nearest folder above it with an
    `.env.example`, so a copy that is not yet a repository reads its own."""
    here = Path(__file__).resolve().parent
    return next(p for p in (here, *here.parents) if (p / ".env.example").is_file())


def _is_client_construction(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in CLIENT_CONSTRUCTORS
    if isinstance(func, ast.Attribute):
        if func.attr in CLIENT_CONSTRUCTORS:
            return True
        receiver = func.value
        if func.attr == "client":
            if isinstance(receiver, ast.Attribute):
                return receiver.attr.endswith("session")
            return isinstance(receiver, ast.Name) and receiver.id.endswith("session")
    return False


def _carries_a_timeout(call: ast.Call) -> bool:
    """A timeout by name, or the AWS configuration that carries one."""
    keywords = {keyword.arg for keyword in call.keywords if keyword.arg}
    return "config" in keywords or any("timeout" in name for name in keywords)


def _name_of(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _bounded_by_wait_for(tree: ast.AST) -> set[int]:
    """The calls awaited through `asyncio.wait_for(call, timeout=...)`: the
    bound is the caller's, named at the same site, for a client whose opening
    takes no timeout of its own."""
    bounded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        timed = len(node.args) > 1 or any(k.arg == "timeout" for k in node.keywords)
        if name == "wait_for" and timed and isinstance(node.args[0], ast.Call):
            bounded.add(id(node.args[0]))
    return bounded


def client_constructions() -> list[tuple[str, bool]]:
    """Every client construction under the source roots as (site, bounded)."""
    root = repository_root()
    found: list[tuple[str, bool]] = []
    for source_root in SOURCE_ROOTS:
        for path in sorted((root / source_root).rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            waited = _bounded_by_wait_for(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _is_client_construction(node):
                    site = f"{path.relative_to(root)}:{node.lineno}"
                    found.append((site, _carries_a_timeout(node) or id(node) in waited))
    return found


def test_every_client_construction_names_a_timeout() -> None:
    found = client_constructions()
    sites = {site.split(":")[0] for site, _ in found}
    assert sites >= {
        "clients/python/src/acme/client/client.py",
        "clients/python/src/acme/client/realtime.py",
        "infra/src/acme/infra/buckets/s3.py",
        "infra/src/acme/infra/queues/sqs.py",
        "infra/src/acme/infra/secrets/aws.py",
        "infra/src/acme/infra/impl/valkey.py",
        "infra/src/acme/infra/observability.py",
        "integrations/src/acme/integrations/identity/workos.py",
    }, "the scan no longer sees a client it used to; widen it before trusting it"
    unbounded = [site for site, bounded in found if not bounded]
    assert not unbounded, f"clients built without a timeout: {unbounded}"


async def test_every_aws_client_is_opened_with_the_timeout() -> None:
    session = aioboto3.Session(
        aws_access_key_id="key", aws_secret_access_key="secret", region_name="us-east-1"
    )
    timeout = timedelta(seconds=7)
    impls = (
        BucketsS3Impl(
            session, endpoint_url=None, region="us-east-1", bucket_prefix="t", timeout=timeout
        ),
        QueueSqsImpl(
            session, endpoint_url=None, region="us-east-1", queue_prefix="t-", timeout=timeout
        ),
        SecretsAwsImpl(session, region="us-east-1", name_prefix="t/", timeout=timeout),
    )
    for impl in impls:
        await impl.start()  # opened, not called: no request leaves
        try:
            config = impl._client().meta.config
            assert (config.connect_timeout, config.read_timeout) == (7.0, 7.0), impl.describe()
        finally:
            await impl.close()


def test_the_trace_exporter_is_bounded_by_the_timeout() -> None:
    exporter = span_exporter("http://127.0.0.1:1/", timedelta(seconds=3))
    assert exporter._timeout == 3.0  # pyright: ignore[reportPrivateUsage] (read back, not set)
    assert exporter._endpoint == "http://127.0.0.1:1/v1/traces"  # pyright: ignore[reportPrivateUsage]


class RecordingSqs:
    """Answers an empty queue and keeps the arguments of every receive."""

    def __init__(self) -> None:
        self.receives: list[dict[str, Any]] = []

    async def get_queue_url(self, **kwargs: Any) -> dict[str, Any]:
        return {"QueueUrl": "http://queue"}

    async def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        self.receives.append(kwargs)
        return {}


class RecordingSession:
    def __init__(self, sqs: RecordingSqs) -> None:
        self._sqs = sqs

    def client(self, service: str, **kwargs: Any) -> Any:
        @asynccontextmanager
        async def open_client() -> AsyncIterator[Any]:
            yield self._sqs

        return open_client()


@pytest.mark.parametrize("timeout_seconds", [1.0, 2.5, 10.0, 21.0, 60.0])
async def test_the_sqs_long_poll_stays_below_the_read_timeout(timeout_seconds: float) -> None:
    """A long poll on an empty queue is the endpoint holding the response on
    purpose, so its wait sits below the client's read timeout: otherwise every
    empty poll ends in a read timeout, never in an empty answer. The hosted
    queue caps the wait at twenty seconds on its side."""
    timeout = timedelta(seconds=timeout_seconds)
    sqs = RecordingSqs()
    queue = QueueSqsImpl(
        RecordingSession(sqs),  # type: ignore[arg-type]
        endpoint_url=None,
        region="r",
        queue_prefix="t-",
        timeout=timeout,
    )
    asked = (timedelta(0), timedelta(seconds=5), timedelta(seconds=20), timedelta(minutes=5))
    await queue.start()
    try:
        for wait in asked:
            assert await queue.receive(Queues.WEBHOOKS, 1, wait, timedelta(seconds=30)) == []
    finally:
        await queue.close()
    waits = [call["WaitTimeSeconds"] for call in sqs.receives]
    config: Any = client_config(timeout)  # botocore's Config carries its options untyped
    read_timeout = config.read_timeout
    assert all(0 <= w < read_timeout and w <= 20 for w in waits), (waits, read_timeout)
    assert waits[0] == 0
    assert waits == sorted(waits), "a longer wait asked for never polls shorter"
    if timeout_seconds > 21:
        assert waits[-1] == 20, "a generous read timeout leaves the hosted queue's cap in force"
