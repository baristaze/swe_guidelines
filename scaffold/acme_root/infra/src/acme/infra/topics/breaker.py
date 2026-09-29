"""The topics breaker: the same cost bound over the other half of the same
Valkey. `publish` is on the request path, after the durable part of a write has
landed, and it is bounded by the same request timeout, so a bus that is down
costs every write its whole timeout exactly as a cache that is down costs every
read one. The container hands this wrapper and the cache's the same `Breaker`,
because one breaker stands for one dependency and there is one Valkey behind
both.

An open breaker answers the way the dependency's own failure answers. A topic
is best effort, so that answer is not an exception: the publish is dropped and
`publish` returns False, the same answer the Valkey impl gives for a publish the
bus refuses, without paying for the refusal. An open breaker is never a publish
that landed. A caller that only wakes someone ignores the answer, since a missed
wake-up degrades to polling latency; the outbox relay keeps the row and sends it
again.

The payload is checked before the breaker is consulted. A publish of the wrong
payload type raises `PayloadMismatch` whether the breaker is open or closed:
the breaker declines to pay the timeout, never to keep the contract, and a bug
in a caller must not go quiet for a cool-down.

What the breaker does not cover:

-   `subscribe` registers a handler in this process and opens nothing, so it is
    forwarded untouched and never counted.
-   The listener is not a call anyone makes. It is one task receiving on a
    subscriber of its own, and it already has its bound: a reconnect backoff
    that grows with consecutive failures and resets on a message. Nothing waits
    on it, so it exhausts no pool, and a breaker over it would only fight the
    backoff.
-   `start()` and `close()` are the root's lifecycle, so they are forwarded
    whatever the breaker's state is; a process still opens its subscriber on a
    bus that is down, and the listener's own backoff is what carries it.
"""

from collections.abc import Callable

from acme.infra.breaker import Breaker
from acme.infra.topics import TopicHandler, TopicPayload, Topics, TopicsInterface
from acme.infra.topics.dispatch import check_payload


class TopicsBreakerImpl(TopicsInterface):
    def __init__(self, inner: TopicsInterface, breaker: Breaker) -> None:
        self._inner = inner
        self._breaker = breaker

    async def publish(self, topic: Topics, payload: TopicPayload) -> bool:
        check_payload(topic, payload)
        if not self._breaker.allows():
            return False
        with self._breaker.measured():
            return await self._inner.publish(topic, payload)

    def subscribe(self, topic: Topics, consumer: str, handler: TopicHandler) -> Callable[[], None]:
        return self._inner.subscribe(topic, consumer, handler)

    def describe(self) -> str:
        return f"{self._inner.describe()}+{self._breaker.describe()}"

    async def start(self) -> None:
        await self._inner.start()

    async def close(self) -> None:
        await self._inner.close()
