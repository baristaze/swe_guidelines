"""The client's side of the local store in a test: a request sent straight
to the store by a URL the local impl signed, as a browser sends it, and
never through a service."""

from collections.abc import Iterable
from typing import Protocol

import httpx

from acme.infra.buckets import BucketsInterface
from acme.infra.buckets.local import BucketsLocalImpl


def local_store(buckets: BucketsInterface) -> httpx.AsyncClient:
    """A client whose requests reach the local impl's store."""
    assert isinstance(buckets, BucketsLocalImpl), "only the local impl has a store in-process"
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=buckets.store))


class Part(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def offset(self) -> int: ...

    @property
    def size_bytes(self) -> int: ...


async def send_parts(buckets: BucketsInterface, parts: Iterable[Part], data: bytes) -> None:
    """Each part's bytes of `data`, as the body of a PUT to its signed URL."""
    async with local_store(buckets) as store:
        for part in parts:
            body = data[part.offset : part.offset + part.size_bytes]
            sent = await store.put(part.url, content=body)
            assert sent.status_code == 200, sent.text
