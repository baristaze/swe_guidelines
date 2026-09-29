import asyncio
import contextlib
import json
import logging
from collections.abc import Callable

from glide import GlideClient, GlideClientConfiguration, GlideError

from acme.infra.impl.valkey import ValkeyConnection
from acme.infra.observability import OUTCOMES
from acme.infra.topics import TOPIC_PAYLOADS, TopicHandler, TopicPayload, Topics, TopicsInterface
from acme.infra.topics.dispatch import LocalSubscribers, check_payload

log = logging.getLogger(__name__)


class TopicsValkeyImpl(TopicsInterface):
    """Pub/sub on the cache: every subscribed process receives every publish.
    Publishing uses the shared client; listening uses a client of its own,
    subscribed at creation so GLIDE restores the subscription on reconnect.
    A listener the driver fails is reopened with backoff, never left dead:
    a process that stopped hearing the bus would degrade to polling latency
    for its workers and to silence for its sockets, without a line to say so."""

    RECONNECT_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0)

    def __init__(self, connection: ValkeyConnection, channel_prefix: str = "acme:topics:") -> None:
        self._connection = connection
        self._prefix = channel_prefix
        self._subscribers = LocalSubscribers()
        self._subscriber: GlideClient | None = None
        self._listener: asyncio.Task[None] | None = None
        self._failures = 0

    async def publish(self, topic: Topics, payload: TopicPayload) -> bool:
        check_payload(topic, payload)
        client = await self._connection.client()
        if client is None:
            raise RuntimeError("topics publish after the infra root closed")
        try:
            await client.publish(payload.model_dump_json(), self._prefix + topic.value)
        except GlideError:
            # A topic is best effort: the durable part of the operation has
            # landed, and the answer tells the producer the message is lost.
            log.warning(
                "publish of %s %s failed; the bus dropped it", topic.value, payload.idempotency_key
            )
            OUTCOMES.labels(subsystem="topics", outcome="publish_failed").inc()
            return False
        return True

    def subscribe(self, topic: Topics, consumer: str, handler: TopicHandler) -> Callable[[], None]:
        return self._subscribers.add(topic, consumer, handler)

    async def _open_subscriber(self) -> GlideClient:
        modes = GlideClientConfiguration.PubSubChannelModes
        subscriptions = GlideClientConfiguration.PubSubSubscriptions(
            channels_and_patterns={modes.Pattern: {self._prefix + "*"}},
            callback=None,
            context=None,
        )
        return await GlideClient.create(self._connection.configuration(subscriptions))

    async def _drop_subscriber(self) -> None:
        subscriber, self._subscriber = self._subscriber, None
        if subscriber is not None:
            with contextlib.suppress(GlideError):
                await subscriber.close()

    async def start(self) -> None:
        # The listener opens the subscriber itself, so a bus that is down at
        # boot is the listener's backoff to carry and not a process that
        # fails to start.
        self._listener = asyncio.create_task(self._listen(), name="topics-valkey-listener")

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None
        await self._drop_subscriber()

    async def _listen(self) -> None:
        """Receives until the driver fails, then reopens the subscriber after
        a backoff that grows with consecutive failures and resets on a
        message: a reopened subscriber that fails before delivering anything
        keeps climbing, one that delivers has proven the bus healthy. A
        failure is counted and logged; a bug ends the task loudly."""
        while True:
            try:
                if self._subscriber is None:
                    self._subscriber = await self._open_subscriber()
                    if self._failures:
                        log.info("topics listener reconnected after %d failures", self._failures)
                await self._receive(self._subscriber)
            except GlideError as error:
                self._failures += 1
                OUTCOMES.labels(subsystem="topics", outcome="listener_failed").inc()
                backoff = self.RECONNECT_BACKOFF_SECONDS
                delay = backoff[min(self._failures, len(backoff)) - 1]
                log.warning(
                    "topics listener failed (%s: %s); reconnecting in %.1fs",
                    type(error).__name__,
                    error,
                    delay,
                )
                await self._drop_subscriber()
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("topics listener stopped")
                raise

    async def _receive(self, subscriber: GlideClient) -> None:
        while True:
            message = await subscriber.get_pubsub_message()
            self._failures = 0
            channel = message.channel
            channel = channel.decode() if isinstance(channel, bytes) else str(channel)
            try:
                topic = Topics(channel[len(self._prefix) :])
                data = message.message
                text = data.decode() if isinstance(data, bytes) else str(data)
                payload = TOPIC_PAYLOADS[topic].model_validate(json.loads(text))
            except ValueError, KeyError:
                log.warning("ignoring malformed message on %s", channel)
                continue
            await self._subscribers.dispatch(topic, payload)

    def describe(self) -> str:
        return "topics=valkey"
